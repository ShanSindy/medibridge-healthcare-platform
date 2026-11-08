
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
    client = TestClient(app)

    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108160000||ADT^A01|MSG00027|P|2.5\r"
        "EVN|A01|20261108160000\r"
        "PID|1||MRN-000027^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    response = client.post(
        "/hl7/adt",
        content=message,
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unsupported HL7 message type. Expected ADT^A04 or ADT^A08."
    )


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

def test_hl7_adt_endpoint_a08_update():
    client = TestClient(app)
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108150000||ADT^A08|MSG00026|P|2.5\r"
        "EVN|A08|20261108150000\r"
        "PID|1||MRN-000020^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    expected_result = {
        "status": "updated",
        "patient_id": 13,
        "mrn": "MRN-000020"
    }

    with patch(
        "main.update_patient_from_hl7",
        return_value=expected_result
    ) as mock_update:
        response = client.post(
            "/hl7/adt",
            content=message,
            headers={"Content-Type": "text/plain"}
        )

    assert response.status_code == 200
    assert response.json() == expected_result
    mock_update.assert_called_once_with(message)

def test_hl7_adt_endpoint_invalid_content_type():
    client = TestClient(app)

    response = client.post(
        "/hl7/adt",
        json={"message": "ADT^A04"}
    )

    assert response.status_code == 415
    assert response.json()["detail"] == (
        "Content-Type must be text/plain."
    )
