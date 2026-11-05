
from models import Patient


def patient_to_fhir(patient: Patient) -> dict:
    return {
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
