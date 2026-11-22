from psycopg.errors import UniqueViolation

from database import get_connection
from hl7_parser import (
    extract_hl7_message_metadata,
    parse_hl7_message,
    process_hl7_adt_message,
)
from hl7_tracking import record_hl7_message

HL7_MRN_SYSTEM = "https://medibridge.example.org/mrn"

# UNIQUE (system, value) on patient_identifiers: one patient per MRN in this system.
PATIENT_IDENTIFIER_CONSTRAINT = "unique_patient_identifier"


def register_patient_from_hl7(message: str) -> dict:
    """
    Register a patient from an HL7 ADT^A04 message.

    Patient creation, MRN insertion, and message tracking
    occur in one database transaction.
    """
    segments = parse_hl7_message(message)

    if segments["MSH"][0][8] != "ADT^A04":
        raise ValueError("Patient registration requires an ADT^A04 message.")

    patient = process_hl7_adt_message(message)
    metadata = extract_hl7_message_metadata(segments)

    with get_connection() as conn:
        with conn.cursor() as cur:
            is_new_message = record_hl7_message(cur, metadata)

            if not is_new_message:
                return {
                    "status": "duplicate",
                    "mrn": patient["mrn"],
                    "message_control_id": metadata["message_control_id"],
                }

            existing_id = _patient_id_for_mrn(cur, patient["mrn"])

            if existing_id is not None:
                return _already_exists(existing_id, patient["mrn"])

            try:
                # Savepoint: if a concurrent registration commits the same MRN first,
                # only the patient and identifier inserts are undone; the message
                # tracking row and the surrounding transaction stay usable.
                with conn.transaction():
                    patient_id = _insert_patient_with_mrn(cur, patient)
            except UniqueViolation as exc:
                if exc.diag.constraint_name != PATIENT_IDENTIFIER_CONSTRAINT:
                    raise
                # Assumes READ COMMITTED (PostgreSQL's default; not set anywhere by
                # MediBridge): this statement takes a new snapshot that includes the
                # committed winner. Under REPEATABLE READ or SERIALIZABLE the winner may
                # be invisible here, and the violation is then re-raised below.
                existing_id = _patient_id_for_mrn(cur, patient["mrn"])
                if existing_id is None:
                    raise
                return _already_exists(existing_id, patient["mrn"])

    return {"status": "created", "patient_id": patient_id, "mrn": patient["mrn"]}


def _patient_id_for_mrn(cur, mrn: str) -> int | None:
    cur.execute(
        """
        SELECT patient_id
        FROM patient_identifiers
        WHERE system = %s AND value = %s
        """,
        (HL7_MRN_SYSTEM, mrn),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _already_exists(patient_id: int, mrn: str) -> dict:
    return {"status": "already_exists", "patient_id": patient_id, "mrn": mrn}


def _insert_patient_with_mrn(cur, patient: dict) -> int:
    cur.execute(
        """
        INSERT INTO patients
            (first_name, last_name, date_of_birth, gender)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (
            patient["first_name"],
            patient["last_name"],
            patient["date_of_birth"],
            patient["gender"],
        ),
    )

    patient_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO patient_identifiers
            (patient_id, system, value)
        VALUES (%s, %s, %s)
        """,
        (patient_id, HL7_MRN_SYSTEM, patient["mrn"]),
    )

    return patient_id


def update_patient_from_hl7(message: str) -> dict:
    """
    Update an existing patient from an HL7 ADT^A08 message.

    Message tracking and patient updates occur in
    the same PostgreSQL transaction.
    """
    segments = parse_hl7_message(message)

    if segments["MSH"][0][8] != "ADT^A08":
        raise ValueError("Patient updates require an ADT^A08 message.")

    patient = process_hl7_adt_message(message)
    metadata = extract_hl7_message_metadata(segments)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Record the message before modifying patient data.
            is_new_message = record_hl7_message(cur, metadata)

            if not is_new_message:
                return {
                    "status": "duplicate",
                    "mrn": patient["mrn"],
                    "message_control_id": metadata["message_control_id"],
                }

            # Update the patient identified by the MRN.
            cur.execute(
                """
                UPDATE patients AS p
                SET
                    first_name = %s,
                    last_name = %s,
                    date_of_birth = %s,
                    gender = %s
                FROM patient_identifiers AS pi
                WHERE pi.patient_id = p.id
                  AND pi.system = %s
                  AND pi.value = %s
                RETURNING p.id
                """,
                (
                    patient["first_name"],
                    patient["last_name"],
                    patient["date_of_birth"],
                    patient["gender"],
                    HL7_MRN_SYSTEM,
                    patient["mrn"],
                ),
            )

            updated = cur.fetchone()

            if updated is None:
                raise ValueError("Cannot update patient: MRN not found.")

            patient_id = updated[0]

    return {"status": "updated", "patient_id": patient_id, "mrn": patient["mrn"]}
