
from database import get_connection
from hl7_parser import process_hl7_adt_message


HL7_MRN_SYSTEM = "https://medibridge.example.org/mrn"


def register_patient_from_hl7(message: str) -> dict:
    """
    Register a patient from a validated HL7 ADT^A04 message.

    The patient and MRN are inserted in one transaction.
    """
    patient = process_hl7_adt_message(message)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT patient_id
                FROM patient_identifiers
                WHERE system = %s AND value = %s
                """,
                (HL7_MRN_SYSTEM, patient["mrn"])
            )

            existing = cur.fetchone()

            if existing:
                return {
                    "status": "already_exists",
                    "patient_id": existing[0],
                    "mrn": patient["mrn"]
                }

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
                    patient["gender"]
                )
            )

            patient_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO patient_identifiers
                    (patient_id, system, value)
                VALUES (%s, %s, %s)
                """,
                (
                    patient_id,
                    HL7_MRN_SYSTEM,
                    patient["mrn"]
                )
            )

    return {
        "status": "created",
        "patient_id": patient_id,
        "mrn": patient["mrn"]
    }
