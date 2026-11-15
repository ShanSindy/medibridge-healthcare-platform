from fhir import provider_to_practitioner_role


def test_provider_to_practitioner_role():
    provider = {
        "id": 7,
        "first_name": "Sarah",
        "last_name": "Johnson",
        "specialty": "Cardiology",
        "email": "sarah@example.com",
        "phone": "555-0100",
    }

    resource = provider_to_practitioner_role(provider)

    assert resource["resourceType"] == "PractitionerRole"
    assert resource["id"] == "7"
    assert resource["practitioner"]["reference"] == "Practitioner/7"
    assert resource["specialty"][0]["text"] == "Cardiology"


def test_practitioner_role_uses_provider_specific_values():
    provider = {
        "id": 12,
        "first_name": "James",
        "last_name": "Wilson",
        "specialty": "Neurology",
    }

    resource = provider_to_practitioner_role(provider)

    assert resource["id"] == "12"
    assert resource["practitioner"]["reference"] == "Practitioner/12"
    assert resource["specialty"] == [{"text": "Neurology"}]
