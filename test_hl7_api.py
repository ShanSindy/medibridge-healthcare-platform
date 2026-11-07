
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_hl7_adt_endpoint_success():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00010|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000010^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    expected_result = {
        "status": "created",
        "patient_id": 123,
        "mrn": "MRN-000010"
    }

    with patch(
        "main.register_patient_from_hl7",
        return_value=expected_result
    ) as mock_register:
        response = client.post(
            "/hl7/adt",
            content=message,
            headers={"Content-Type": "text/plain"}
        )

    assert response.status_code == 200
    assert response.json() == expected_result
    mock_register.assert_called_once_with(message)

def test_hl7_adt_endpoint_invalid_message():
    invalid_message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A08|MSG00011|P|2.5\r"
        "PID|1||MRN-000011^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    response = client.post(
        "/hl7/adt",
        content=invalid_message,
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 400

    assert response.json() == {
        "detail": "Unsupported HL7 message type. Expected ADT^A04."
    }

def test_hl7_adt_endpoint_unsupported_content_type():
    response = client.post(
        "/hl7/adt",
        json={
            "message": "This should not be accepted as JSON"
        }
    )

    assert response.status_code == 415

    assert response.json() == {
        "detail": "Content-Type must be text/plain."
    }

def test_hl7_adt_endpoint_empty_message():
    response = client.post(
        "/hl7/adt",
        content="",
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 400

    assert response.json() == {
        "detail": "HL7 message cannot be empty."
    }

def test_hl7_adt_endpoint_duplicate_registration():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00012|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000012^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    expected_result = {
        "status": "already_exists",
        "patient_id": 123,
        "mrn": "MRN-000012"
    }

    with patch(
        "main.register_patient_from_hl7",
        return_value=expected_result
    ) as mock_register:
        response = client.post(
            "/hl7/adt",
            content=message,
            headers={"Content-Type": "text/plain"}
        )

    assert response.status_code == 200
    assert response.json() == expected_result
    mock_register.assert_called_once_with(message)
