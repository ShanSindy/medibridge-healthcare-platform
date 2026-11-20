from models import Patient


def patient_to_fhir(patient: Patient, identifiers: list[dict] | None = None) -> dict:
    resource = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "name": [{"use": "official", "family": patient.last_name, "given": [patient.first_name]}],
        "gender": patient.gender,
        "birthDate": patient.date_of_birth.isoformat(),
    }

    if identifiers:
        resource["identifier"] = [
            {"system": identifier["system"], "value": identifier["value"]}
            for identifier in identifiers
        ]

    return resource


def create_operation_outcome(code: str, diagnostics: str, severity: str = "error") -> dict:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}],
    }


def provider_to_fhir(provider: dict) -> dict:
    resource = {
        "resourceType": "Practitioner",
        "id": str(provider["id"]),
        "name": [
            {"use": "official", "family": provider["last_name"], "given": [provider["first_name"]]}
        ],
    }

    telecom = []

    if provider.get("email"):
        telecom.append({"system": "email", "value": provider["email"]})

    if provider.get("phone"):
        telecom.append({"system": "phone", "value": provider["phone"]})

    if telecom:
        resource["telecom"] = telecom

    return resource


def provider_to_practitioner_role(provider: dict) -> dict:
    return {
        "resourceType": "PractitionerRole",
        "id": str(provider["id"]),
        "practitioner": {"reference": f"Practitioner/{provider['id']}"},
        "specialty": [{"text": provider["specialty"]}],
    }
