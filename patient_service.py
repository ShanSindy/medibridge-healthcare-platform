"""
Patient reads.

get_all_patients, get_patient_by_id and search_patients use the shared SQLAlchemy engine
through db_engine.read_only_connection(): PostgreSQL read-only transactions with the
engine's statement and lock timeouts. They back the REST patient list and lookup and the
FHIR Patient read and search; HTTP status codes, authorization and FHIR serialization
stay in the routers and fhir.py.

Patient writes (REST create, update, delete with their audit events, and HL7
registration and update) still use database.get_connection() in their own transactions,
unchanged. The identifier reads used by the FHIR routes stay in database.py.
"""

from sqlalchemy import bindparam, column, func, literal_column, select, table

from db_engine import read_only_connection

# The columns the reads select, in the order of the returned dictionaries. Untyped, so
# values come back exactly as psycopg returns them (date_of_birth as a date), as before.
PATIENTS = table(
    "patients",
    column("id"),
    column("first_name"),
    column("last_name"),
    column("date_of_birth"),
    column("gender"),
)
PATIENT_IDENTIFIERS = table(
    "patient_identifiers",
    column("patient_id"),
    column("system"),
    column("value"),
)


def get_all_patients() -> list[dict]:
    """Every patient, ordered by id."""
    with read_only_connection() as conn:
        rows = conn.execute(select(PATIENTS).order_by(PATIENTS.c.id)).mappings().all()

    return [dict(row) for row in rows]


def get_patient_by_id(patient_id: int) -> dict | None:
    """The patient with this id, or None."""
    query = select(PATIENTS).where(PATIENTS.c.id == bindparam("patient_id"))

    with read_only_connection() as conn:
        row = conn.execute(query, {"patient_id": patient_id}).mappings().one_or_none()

    return dict(row) if row is not None else None


def search_patients(
    family: str | None = None,
    given: str | None = None,
    identifier: str | None = None,
    count: int = 10,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    One page of matching patients ordered by id, and the total number of matches.

    family and given are case-insensitive prefix matches (ILIKE), with `%`, `_` and
    backslash in the value matched literally. identifier matches a patient that has an
    identifier with exactly this value; if it contains `|`, the part before the first `|`
    must also match the identifier's system. Each patient appears once, however many of
    its identifiers match. Values are used as given: nothing is trimmed or normalized.

    Both queries run in one read-only READ COMMITTED transaction, as before: each sees the
    data committed when it starts, so a patient created or deleted between them can make
    the total differ from what the page shows.
    """

    def prefix_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    conditions = []
    params: dict[str, object] = {}

    if family is not None:
        conditions.append(PATIENTS.c.last_name.ilike(bindparam("family")))
        params["family"] = prefix_pattern(family)

    if given is not None:
        conditions.append(PATIENTS.c.first_name.ilike(bindparam("given")))
        params["given"] = prefix_pattern(given)

    if identifier is not None:
        has_identifier = [PATIENT_IDENTIFIERS.c.patient_id == PATIENTS.c.id]
        if "|" in identifier:
            system, value = identifier.split("|", 1)
            has_identifier.append(PATIENT_IDENTIFIERS.c.system == bindparam("system"))
            params["system"] = system
        else:
            value = identifier
        has_identifier.append(PATIENT_IDENTIFIERS.c.value == bindparam("value"))
        params["value"] = value
        conditions.append(
            select(literal_column("1")).where(*has_identifier).correlate(PATIENTS).exists()
        )

    page = (
        select(PATIENTS)
        .where(*conditions)
        .order_by(PATIENTS.c.id)
        .limit(bindparam("limit"))
        .offset(bindparam("offset"))
    )
    matches = select(func.count()).select_from(PATIENTS).where(*conditions)

    with read_only_connection() as conn:
        rows = conn.execute(page, {**params, "limit": count, "offset": offset}).mappings().all()
        total = conn.execute(matches, params).scalar_one()

    return [dict(row) for row in rows], total
