import pytest
from pydantic import ValidationError

from models import ProviderCreate


def test_create_valid_provider():
    provider = ProviderCreate(
        first_name="Emily",
        last_name="Carter",
        specialty="Neurology",
        email="emily.carter@example.com",
        phone="615-555-0199",
    )

    assert provider.first_name == "Emily"
    assert provider.last_name == "Carter"
    assert provider.specialty == "Neurology"
    assert provider.email == "emily.carter@example.com"
    assert provider.phone == "615-555-0199"


def test_provider_optional_contact_information():
    provider = ProviderCreate(
        first_name="Michael",
        last_name="Reed",
        specialty="Dermatology",
    )

    assert provider.email is None
    assert provider.phone is None


@pytest.mark.parametrize(
    "field",
    ["first_name", "last_name", "specialty"],
)
def test_provider_rejects_blank_required_fields(field):
    data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
    }

    data[field] = "   "

    with pytest.raises(ValidationError):
        ProviderCreate(**data)


def test_provider_trims_whitespace():
    provider = ProviderCreate(
        first_name="  Emily  ",
        last_name="  Carter  ",
        specialty="  Neurology  ",
    )

    assert provider.first_name == "Emily"
    assert provider.last_name == "Carter"
    assert provider.specialty == "Neurology"


def test_provider_normalizes_email():
    provider = ProviderCreate(
        first_name="Emily",
        last_name="Carter",
        specialty="Neurology",
        email="  EMILY.CARTER@EXAMPLE.COM  ",
    )

    assert provider.email == "emily.carter@example.com"


@pytest.mark.parametrize(
    "email",
    [
        "invalid-email",
        "missing-at-symbol.com",
        "user@invalid",
    ],
)
def test_provider_rejects_invalid_email(email):
    with pytest.raises(ValidationError):
        ProviderCreate(
            first_name="Emily",
            last_name="Carter",
            specialty="Neurology",
            email=email,
        )


def test_provider_rejects_overlong_first_name():
    with pytest.raises(ValidationError):
        ProviderCreate(
            first_name="A" * 101,
            last_name="Carter",
            specialty="Neurology",
        )
