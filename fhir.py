
from models import Patient



def patient_to_fhir(
    patient: Patient,
    identifiers: list[dict] | None = None
) -> dict:
    resource = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "name": [
            {
                "use": "official",
                "family": patient.last_name,
                "given": [patient.first_name]
            }
        ],
        "gender": patient.gender,
        "birthDate": patient.date_of_birth.isoformat()
    }

    if identifiers:
        resource["identifier"] = [
            {
                "system": identifier["system"],
                "value": identifier["value"]
            }
            for identifier in identifiers
        ]

    return resource


def create_operation_outcome(
    code: str,
    diagnostics: str,
    severity: str = "error"
) -> dict:
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics
            }
        ]
    }
