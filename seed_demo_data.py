"""
Load a fixed set of synthetic demonstration records into a MediBridge database that an
operator has deliberately authorized for synthetic demonstrations.

    python seed_demo_data.py --database medibridge_demo --dry-run
    python seed_demo_data.py --database medibridge_demo --confirm-synthetic-demo

Creates fictional patients (with MRN identifiers), providers and appointments in one
transaction. It never updates or deletes anything and creates no users, passwords or
audit events; create users separately with provision_user.py.

Target authorization (fail closed). Every condition is checked on the connection that
seeds, inside the seeding transaction, before any table is locked or written:

- --database equals the configured DB_NAME, the connected current_database(), and is
  not one of PROTECTED_DATABASES;
- the connected role equals DB_USER, and the port is the configured one;
- the database carries the operator's authorization marker: the comment on its public
  schema is exactly "medibridge-synthetic-demo-target:<database name>:<database OID>".
  The command never sets this marker. Binding it to the name and OID makes a renamed
  database, a copy made from it as a template, or a dump restored elsewhere fail the check.

The marker protects against accidental targeting. It is not an access-control system: a
database owner or superuser can set it on any database.

All records are synthetic. Do not use this tool against a database that holds real data.
"""

import argparse
import contextlib
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import errors

EXIT_SEEDED = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_REFUSED_TARGET = 3
EXIT_NOT_EMPTY = 4
EXIT_DATABASE_UNAVAILABLE = 5
EXIT_OUTCOME_UNKNOWN = 6

MRN_SYSTEM = "https://medibridge.example.org/mrn"
SEEDED_TABLES = ("patients", "patient_identifiers", "providers", "appointments")
# Must also be empty: earlier HL7 traffic or audit events would describe records that the
# demonstration data does not contain. Users and schema_migrations may hold rows.
REQUIRED_EMPTY_TABLES = (*SEEDED_TABLES, "hl7_messages", "audit_logs")
PROTECTED_DATABASES = frozenset(
    {"medibridge", "medibridge_test", "medibridge_ci_test", "postgres", "template0", "template1"}
)
AUTHORIZATION_PREFIX = "medibridge-synthetic-demo-target:"
REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
DEFAULT_ENV_FILE = Path(__file__).resolve().with_name(".env")
DOTENV_DISABLED_VALUES = {"1", "true", "t", "yes", "y"}  # python-dotenv's own rule

# Bounds for this command's own session only; nothing else is reconfigured.
CONNECT_TIMEOUT_SECONDS = 10
LOCK_TIMEOUT = "10s"  # waiting for the table locks (another writer or initializer)
STATEMENT_TIMEOUT = "60s"
APPLICATION_NAME = "medibridge-seed-demo-data"
NOT_COMMITTED = "nothing was written (the transaction was not committed)"

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


@dataclass(frozen=True)
class Target:
    host: str
    port: str
    dbname: str
    user: str
    password: str = field(repr=False)


def safe_text(value) -> str:
    """For terminal output: control characters are shown escaped, never interpreted."""
    text = str(value)
    if text.isprintable():
        return text
    return text.encode("unicode_escape").decode("ascii")


# --- configuration (no connection) -------------------------------------------------------


def load_settings(environ=None, env_file: Path | None = None) -> dict:
    """Injected settings as given; otherwise the environment, then .env next to this script
    for names the environment does not set. os.environ is not modified."""
    if environ is not None:
        return dict(environ)
    env_file = DEFAULT_ENV_FILE if env_file is None else env_file
    values = {}
    disabled = os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in DOTENV_DISABLED_VALUES
    if not disabled and env_file.is_file():
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    values.update(os.environ)
    return values


def resolve_target(database: str, settings) -> Target:
    missing = [name for name in REQUIRED_SETTINGS if not settings.get(name)]
    if missing:
        raise SeedRefused(f"missing database settings: {', '.join(missing)}", EXIT_REFUSED_TARGET)
    if settings["DB_NAME"] != database:
        raise SeedRefused(
            "--database does not match the configured DB_NAME; nothing was changed",
            EXIT_REFUSED_TARGET,
        )
    if database in PROTECTED_DATABASES:
        raise SeedRefused(
            f"refusing to seed the protected database {database}", EXIT_REFUSED_TARGET
        )
    return Target(
        host=settings["DB_HOST"],
        port=str(settings["DB_PORT"]),
        dbname=settings["DB_NAME"],
        user=settings["DB_USER"],
        password=settings["DB_PASSWORD"],
    )


def parse_anchor(value: str) -> datetime:
    try:
        anchor = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "the anchor must be an ISO 8601 date and time, e.g. 2031-01-06T00:00:00+00:00"
        ) from None
    if anchor.utcoffset() is None:
        raise argparse.ArgumentTypeError("the anchor must include a UTC offset")
    return anchor


# --- database ----------------------------------------------------------------------------


def open_connection(target: Target, *, read_only: bool):
    """One bounded READ COMMITTED connection; settings apply to this session only."""
    try:
        conn = psycopg.connect(
            host=target.host,
            port=target.port,
            dbname=target.dbname,
            user=target.user,
            password=target.password,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            application_name=APPLICATION_NAME,
            options=f"-c lock_timeout={LOCK_TIMEOUT} -c statement_timeout={STATEMENT_TIMEOUT}",
        )
    except psycopg.Error:
        # The driver's message is not shown: it can echo connection parameters.
        raise SeedRefused(
            f"could not connect to {safe_text(target.host)}:{safe_text(target.port)} as "
            f"{safe_text(target.user)} within {CONNECT_TIMEOUT_SECONDS} s (server unreachable, "
            f"database missing or authentication failed); {NOT_COMMITTED}",
            EXIT_DATABASE_UNAVAILABLE,
        ) from None
    # The emptiness check after LOCK TABLE must see rows committed by a concurrent
    # initializer while this one waited: that needs a new snapshot per statement.
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    conn.read_only = read_only
    return conn


def authorization_marker(database: str, oid: int) -> str:
    return f"{AUTHORIZATION_PREFIX}{database}:{oid}"


def verify_target(conn, target: Target, database: str) -> dict:
    """Identity and authorization of the session that will seed; refuses on any mismatch."""
    actual_db, current_user, session_user, oid, marker, isolation = conn.execute(
        "SELECT current_database(), current_user, session_user, d.oid, "
        "obj_description(n.oid, 'pg_namespace'), current_setting('transaction_isolation') "
        "FROM pg_database d, pg_namespace n "
        "WHERE d.datname = current_database() AND n.nspname = 'public'"
    ).fetchone()
    problems = []
    if not (actual_db == database == target.dbname):
        problems.append(f"connected database is {safe_text(actual_db)}")
    if actual_db in PROTECTED_DATABASES:
        problems.append(f"{safe_text(actual_db)} is protected")
    if not (current_user == session_user == target.user):
        problems.append(f"connected role is {safe_text(current_user)}")
    if str(conn.info.port) != target.port:
        problems.append(f"connected port is {safe_text(conn.info.port)}")
    if isolation != "read committed":
        problems.append(f"transaction isolation is {safe_text(isolation)}, not read committed")
    if problems:
        raise SeedRefused(
            "the connected target does not match the requested one ("
            + "; ".join(problems)
            + f"); {NOT_COMMITTED}",
            EXIT_REFUSED_TARGET,
        )
    expected = authorization_marker(actual_db, oid)
    if marker != expected:
        if marker and marker.startswith(AUTHORIZATION_PREFIX):
            reason = (
                "its synthetic-demo authorization marker names another database "
                "(renamed, copied or restored?)"
            )
        else:
            reason = "it is not authorized for synthetic demonstration data"
        raise SeedRefused(
            f"refusing to seed {safe_text(actual_db)}: {reason}. If this database was created "
            "for synthetic demonstrations, its owner can authorize it with:\n"
            f"  COMMENT ON SCHEMA public IS '{expected}';\n{NOT_COMMITTED}",
            EXIT_REFUSED_TARGET,
        )
    return {"database": actual_db, "role": current_user, "oid": oid}


def nonempty_tables(cur, tables=REQUIRED_EMPTY_TABLES) -> list[str]:
    found = []
    for table in tables:
        # Table names come from fixed module constants, never from input.
        cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table})")  # noqa: S608
        if cur.fetchone()[0]:
            found.append(table)
    return found


def seed_in_transaction(conn, anchor: datetime) -> dict:
    """
    Locks, checks and inserts using the caller's open transaction; the caller commits.

    LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE conflicts with itself and with every
    write, so a second initializer waits here (at most the session's lock_timeout) and,
    under READ COMMITTED, then sees the first one's committed rows and refuses.
    """
    with conn.cursor() as cur:
        cur.execute(f"LOCK TABLE {', '.join(REQUIRED_EMPTY_TABLES)} IN SHARE ROW EXCLUSIVE MODE")
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


def failure_before_commit(exc: BaseException) -> SeedRefused:
    """COMMIT was not sent, so PostgreSQL cannot have committed anything."""
    if isinstance(exc, SeedRefused):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return SeedRefused(f"interrupted; {NOT_COMMITTED}", EXIT_UNEXPECTED)
    if isinstance(exc, errors.UndefinedTable):
        return SeedRefused(
            f"the schema is missing; run `python migrate.py` first; {NOT_COMMITTED}",
            EXIT_DATABASE_UNAVAILABLE,
        )
    if isinstance(exc, errors.LockNotAvailable | errors.QueryCanceled):
        return SeedRefused(
            "another session is using the tables, or the database did not respond within "
            f"this command's timeouts; try again later; {NOT_COMMITTED}",
            EXIT_DATABASE_UNAVAILABLE,
        )
    if isinstance(exc, psycopg.OperationalError):
        return SeedRefused(
            f"the database connection failed; {NOT_COMMITTED}", EXIT_DATABASE_UNAVAILABLE
        )
    if isinstance(exc, psycopg.Error):
        return SeedRefused(
            f"unexpected database error (SQLSTATE {exc.sqlstate}); {NOT_COMMITTED}",
            EXIT_UNEXPECTED,
        )
    return SeedRefused(f"unexpected error ({type(exc).__name__}); {NOT_COMMITTED}", EXIT_UNEXPECTED)


def commit_or_report(conn) -> None:
    try:
        conn.commit()
    except BaseException as exc:
        if isinstance(exc, psycopg.Error) and exc.sqlstate and not conn.closed:
            raise SeedRefused(
                f"the database rejected the commit (SQLSTATE {exc.sqlstate}); {NOT_COMMITTED}",
                EXIT_UNEXPECTED,
            ) from None
        raise SeedRefused(
            "the outcome of the commit is unknown: the dataset may or may not have been "
            "written. Do not run the command again until you have checked the patient count "
            "(docs/getting-started.md, step 7).",
            EXIT_OUTCOME_UNKNOWN,
        ) from None


def run_seed(target: Target, database: str, anchor: datetime, *, dry_run: bool, out) -> int:
    conn = open_connection(target, read_only=dry_run)
    try:
        try:
            identity = verify_target(conn, target, database)
            out.write(
                f"Verified: database {safe_text(identity['database'])} (OID {identity['oid']}) "
                f"as {safe_text(identity['role'])}, authorized for synthetic demonstrations.\n"
            )
            if dry_run:
                with conn.cursor() as cur:
                    occupied = nonempty_tables(cur)
                state = f"tables not empty: {', '.join(occupied)}" if occupied else "tables empty"
                out.write(
                    f"Dry run: {state}; nothing was written. This is advisory only: the tables "
                    "are checked again, under lock, when seeding.\n"
                )
                return EXIT_NOT_EMPTY if occupied else EXIT_SEEDED
            seed_in_transaction(conn, anchor)
        except BaseException as exc:
            with contextlib.suppress(Exception):  # COMMIT was never sent
                conn.rollback()
            raise failure_before_commit(exc) from None
        commit_or_report(conn)
    finally:
        conn.close()
    out.write("Seeded the synthetic dataset; the transaction committed.\n")
    return EXIT_SEEDED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python seed_demo_data.py",
        description=(
            "Load synthetic demonstration records into a MediBridge database authorized for "
            "synthetic demonstrations (see docs/getting-started.md, step 7)."
        ),
        epilog=(
            "Exit codes: 0 seeded (or dry run found empty tables), 1 unexpected error, "
            "2 invalid arguments, 3 target refused or not authorized, 4 tables not empty, "
            "5 database unavailable, busy or not migrated, 6 outcome unknown (check before "
            "retrying)."
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
        "--dry-run",
        action="store_true",
        help="check the target and show the plan in a read-only transaction; no writes",
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
    anchor = args.anchor or default_anchor()
    try:
        target = resolve_target(args.database, load_settings(environ))
        out.write(
            f"Target: database {safe_text(target.dbname)} on {safe_text(target.host)}:"
            f"{safe_text(target.port)} as {safe_text(target.user)}.\n"
        )
        out.write(f"Plan: {plan(anchor)}\n")
        return run_seed(target, args.database, anchor, dry_run=args.dry_run, out=out)
    except SeedRefused as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
