"""
Load a fixed set of synthetic demonstration records into an empty MediBridge database.

    python seed_demo_data.py --database medibridge_demo --confirm-synthetic-demo
    python seed_demo_data.py --database medibridge_demo --dry-run

Creates fictional patients (with MRN identifiers), providers and appointments in one
transaction. It refuses to run unless the patient, provider, appointment and identifier
tables are all empty, and it never updates or deletes anything. It creates no users,
passwords or audit events; create users separately with provision_user.py.

All records are synthetic. Do not use this tool against a database that holds real data.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import errors

from database import get_connection

EXIT_SEEDED = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_REFUSED_TARGET = 3
EXIT_NOT_EMPTY = 4
EXIT_DATABASE_UNAVAILABLE = 5

MRN_SYSTEM = "https://medibridge.example.org/mrn"
SEEDED_TABLES = ("patients", "patient_identifiers", "providers", "appointments")
PROTECTED_DATABASES = frozenset(
    {"medibridge", "medibridge_test", "medibridge_ci_test", "postgres", "template0", "template1"}
)
REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
LOCK_TIMEOUT = "10s"

# (first name, last name, date of birth, gender); MRNs are DEMO-000001 onwards.
PATIENTS = (
    ("Avery", "Demo", "1985-03-14", "female"),
    ("Blake", "Demo", "1972-07-09", "male"),
    ("Casey", "Sample", "2001-11-30", "other"),
    ("Drew", "Sample", "1958-05-21", "male"),
    ("Emery", "Example", "1995-01-12", "female"),
    ("Finley", "Example", "1940-09-03", "female"),
    ("Gray", "Fictional", "1989-12-24", "male"),
    ("Harper", "Fictional", "2010-04-17", "female"),
    ("Indigo", "Placeholder", "1966-08-08", "unknown"),
    ("Jules", "Placeholder", "1978-02-28", "male"),
    ("Kai", "Synthetic", "1999-06-05", "other"),
    ("Logan", "Synthetic", "1951-10-19", "female"),
    ("Morgan", "Testcase", "1983-01-01", "male"),
    ("Noa", "Testcase", "2004-03-23", "female"),
    ("Oakley", "Mockdata", "1969-11-11", "male"),
    ("Parker", "Mockdata", "1992-07-30", "female"),
)

# (first name, last name, specialty, email or None, phone or None)
PROVIDERS = (
    ("Ava", "Linden", "Cardiology", "ava.linden@example.org", "555-0101"),
    ("Ben", "Okafor", "Family Medicine", "ben.okafor@example.org", "555-0102"),
    ("Cora", "Marsh", "Neurology", "cora.marsh@example.org", None),
    ("Dev", "Patel", "Pediatrics", "dev.patel@example.org", "555-0104"),
    ("Elena", "Vasquez", "Endocrinology", None, "555-0105"),
    ("Femi", "Adeyemi", "Dermatology", "femi.adeyemi@example.org", None),
)

# (patient index, provider index, day offset from the anchor, start hour UTC,
#  minutes, status, reason). Each provider's slots never overlap.
APPOINTMENTS = (
    (0, 0, -10, 14, 30, "completed", "Synthetic follow-up visit"),
    (1, 0, -3, 15, 30, "completed", "Synthetic annual review"),
    (2, 0, 2, 14, 30, "scheduled", "Synthetic consultation"),
    (3, 0, 2, 15, 30, "scheduled", "Synthetic medication review"),
    (4, 1, -7, 16, 20, "completed", "Synthetic check-up"),
    (5, 1, -1, 16, 20, "cancelled", "Synthetic check-up"),
    (6, 1, 1, 16, 20, "scheduled", "Synthetic check-up"),
    (7, 1, 1, 17, 20, "scheduled", "Synthetic vaccination visit"),
    (8, 2, -5, 18, 45, "completed", "Synthetic neurology assessment"),
    (9, 2, 3, 18, 45, "scheduled", "Synthetic neurology assessment"),
    (10, 2, 3, 19, 45, "cancelled", "Synthetic follow-up visit"),
    (11, 3, -2, 15, 30, "completed", "Synthetic well-child visit"),
    (12, 3, 4, 15, 30, "scheduled", "Synthetic well-child visit"),
    (13, 3, 4, 16, 30, "scheduled", "Synthetic follow-up visit"),
    (14, 4, -6, 20, 30, "completed", "Synthetic endocrine review"),
    (15, 4, 5, 20, 30, "scheduled", "Synthetic endocrine review"),
    (0, 4, 5, 21, 30, "scheduled", "Synthetic results discussion"),
    (1, 5, -4, 17, 15, "cancelled", "Synthetic skin check"),
    (2, 5, 6, 17, 15, "scheduled", "Synthetic skin check"),
    (3, 5, 6, 18, 30, "scheduled", "Synthetic follow-up visit"),
)


def default_anchor() -> datetime:
    """Midnight UTC of the current day: every timestamp in one run derives from this."""
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def appointment_rows(anchor: datetime) -> list[tuple]:
    rows = []
    for patient, provider, day, hour, minutes, status, reason in APPOINTMENTS:
        starts_at = anchor + timedelta(days=day, hours=hour)
        rows.append(
            (patient, provider, starts_at, starts_at + timedelta(minutes=minutes), status, reason)
        )
    return rows


def plan(anchor: datetime) -> dict:
    statuses = {}
    for *_, status, _ in APPOINTMENTS:
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "patients": len(PATIENTS),
        "patient_identifiers": len(PATIENTS),
        "providers": len(PROVIDERS),
        "appointments": len(APPOINTMENTS),
        "appointment_statuses": statuses,
        "anchor": anchor.isoformat(),
    }


class SeedRefused(Exception):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def nonempty_tables(cur) -> list[str]:
    found = []
    for table in SEEDED_TABLES:
        # Table names come from the fixed SEEDED_TABLES tuple, never from input.
        cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table})")  # noqa: S608
        if cur.fetchone()[0]:
            found.append(table)
    return found


def seed_in_transaction(conn, anchor: datetime) -> dict:
    """
    Inserts the dataset using the caller's open transaction; the caller commits.

    LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE conflicts with itself and with every
    write, so a second initializer waits here and then finds the tables non-empty.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute(
            "LOCK TABLE patients, patient_identifiers, providers, appointments "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        occupied = nonempty_tables(cur)
        if occupied:
            raise SeedRefused(
                f"refusing to seed: these tables already contain rows: {', '.join(occupied)}",
                EXIT_NOT_EMPTY,
            )
        patient_ids = []
        for number, (first, last, born, gender) in enumerate(PATIENTS, start=1):
            cur.execute(
                "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (first, last, born, gender),
            )
            patient_id = cur.fetchone()[0]
            patient_ids.append(patient_id)
            cur.execute(
                "INSERT INTO patient_identifiers (patient_id, system, value) VALUES (%s, %s, %s)",
                (patient_id, MRN_SYSTEM, f"DEMO-{number:06d}"),
            )
        provider_ids = []
        for first, last, specialty, email, phone in PROVIDERS:
            cur.execute(
                "INSERT INTO providers (first_name, last_name, specialty, email, phone) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (first, last, specialty, email, phone),
            )
            provider_ids.append(cur.fetchone()[0])
        for patient, provider, starts_at, ends_at, status, reason in appointment_rows(anchor):
            cur.execute(
                "INSERT INTO appointments "
                "(patient_id, provider_id, starts_at, ends_at, status, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (patient_ids[patient], provider_ids[provider], starts_at, ends_at, status, reason),
            )
    return plan(anchor)


def check_target(database: str, environ) -> None:
    missing = [name for name in REQUIRED_SETTINGS if not environ.get(name)]
    if missing:
        raise SeedRefused(f"missing database settings: {', '.join(missing)}", EXIT_REFUSED_TARGET)
    if environ["DB_NAME"] != database:
        raise SeedRefused(
            "--database does not match the configured DB_NAME; nothing was changed",
            EXIT_REFUSED_TARGET,
        )
    if database in PROTECTED_DATABASES:
        raise SeedRefused(
            f"refusing to seed the protected database {database}", EXIT_REFUSED_TARGET
        )


def parse_anchor(value: str) -> datetime:
    anchor = datetime.fromisoformat(value)
    if anchor.utcoffset() is None:
        raise argparse.ArgumentTypeError("the anchor must include a UTC offset")
    return anchor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python seed_demo_data.py",
        description="Load synthetic demonstration records into an empty MediBridge database.",
        epilog=(
            "Exit codes: 0 seeded (or dry run completed), 1 unexpected error, 2 invalid "
            "arguments, 3 target refused, 4 tables not empty, 5 database unavailable or "
            "not migrated."
        ),
    )
    parser.add_argument("--database", required=True, help="must equal the configured DB_NAME")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--confirm-synthetic-demo",
        action="store_true",
        help="write the synthetic dataset (required to change anything)",
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="check the target and show the plan; no writes"
    )
    parser.add_argument(
        "--anchor",
        type=parse_anchor,
        help="reference time for appointment dates, e.g. 2031-01-06T00:00:00+00:00 "
        "(default: today 00:00 UTC)",
    )
    return parser


def main(argv=None, *, environ=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    anchor = args.anchor or default_anchor()
    try:
        check_target(args.database, environ)
        out.write(
            f"Target: database {environ['DB_NAME']} on {environ['DB_HOST']}:{environ['DB_PORT']}"
            f" as {environ['DB_USER']}.\n"
        )
        summary = plan(anchor)
        out.write(f"Plan: {summary}\n")
        with get_connection() as conn:
            if args.dry_run:
                conn.read_only = True
                with conn.cursor() as cur:
                    occupied = nonempty_tables(cur)
                state = f"tables not empty: {', '.join(occupied)}" if occupied else "tables empty"
                out.write(f"Dry run: {state}; nothing was written.\n")
                return EXIT_SEEDED if not occupied else EXIT_NOT_EMPTY
            seed_in_transaction(conn, anchor)
    except SeedRefused as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return exc.exit_code
    except (errors.UndefinedTable, errors.LockNotAvailable) as exc:
        reason = (
            "the schema is missing; run `python migrate.py` first"
            if isinstance(exc, errors.UndefinedTable)
            else "another process is using the tables; try again later"
        )
        sys.stderr.write(f"Error: {reason}; nothing was written\n")
        return EXIT_DATABASE_UNAVAILABLE
    except psycopg.OperationalError:
        sys.stderr.write("Error: could not connect to the configured database\n")
        return EXIT_DATABASE_UNAVAILABLE
    except psycopg.Error as exc:
        sys.stderr.write(
            f"Error: unexpected database error (SQLSTATE {exc.sqlstate}); nothing was written\n"
        )
        return EXIT_UNEXPECTED
    out.write("Seeded the synthetic dataset; the transaction committed.\n")
    return EXIT_SEEDED


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    sys.exit(main())
