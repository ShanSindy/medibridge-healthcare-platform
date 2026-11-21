from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auth_dependencies import get_current_user
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated_admin():
    original_overrides = app.dependency_overrides.copy()

    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": "test_admin",
        "email": "admin@example.com",
        "role": "admin",
        "is_active": True,
    }

    yield

    app.dependency_overrides.clear()
    app.dependency_overrides.update(original_overrides)


def test_hl7_adt_endpoint_success():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00010|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000010^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    expected_result = {"status": "created", "patient_id": 123, "mrn": "MRN-000010"}

    with patch("main.register_patient_from_hl7", return_value=expected_result) as mock_register:
        response = client.post("/hl7/adt", content=message, headers={"Content-Type": "text/plain"})

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

    response = client.post("/hl7/adt", content=message, headers={"Content-Type": "text/plain"})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unsupported HL7 message type. Expected ADT^A04 or ADT^A08."
    )


def test_hl7_adt_endpoint_empty_message():
    response = client.post("/hl7/adt", content="", headers={"Content-Type": "text/plain"})

    assert response.status_code == 400

    assert response.json() == {"detail": "HL7 message cannot be empty."}


def test_hl7_adt_endpoint_duplicate_registration():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00012|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000012^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    expected_result = {"status": "already_exists", "patient_id": 123, "mrn": "MRN-000012"}

    with patch("main.register_patient_from_hl7", return_value=expected_result) as mock_register:
        response = client.post("/hl7/adt", content=message, headers={"Content-Type": "text/plain"})

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

    expected_result = {"status": "updated", "patient_id": 13, "mrn": "MRN-000020"}

    with patch("main.update_patient_from_hl7", return_value=expected_result) as mock_update:
        response = client.post("/hl7/adt", content=message, headers={"Content-Type": "text/plain"})

    assert response.status_code == 200
    assert response.json() == expected_result
    mock_update.assert_called_once_with(message)


def test_hl7_adt_endpoint_invalid_content_type():
    client = TestClient(app)

    response = client.post("/hl7/adt", json={"message": "ADT^A04"})

    assert response.status_code == 415
    assert response.json()["detail"] == ("Content-Type must be text/plain.")


def test_hl7_adt_endpoint_returns_ack():
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from database import get_connection
    from main import app

    client = TestClient(app)

    control_id = f"ACKTEST-{uuid4().hex}"
    mrn = f"MRN-{uuid4().hex[:12]}"

    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        f"ADT^A04|{control_id}|P|2.5\r"
        f"PID|1||{mrn}||Doe^Jane||19900101|F\r"
    )

    try:
        response = client.post(
            "/hl7/adt",
            content=message,
            headers={"Content-Type": "text/plain", "Accept": "application/hl7-v2"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/hl7-v2")
        assert f"MSA|AA|{control_id}" in response.text
        assert "|ACK^A04|" in response.text

    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM patient_identifiers
                    WHERE system = %s AND value = %s
                    RETURNING patient_id
                    """,
                    ("https://medibridge.example.org/mrn", mrn),
                )
                row = cur.fetchone()

                if row is not None:
                    cur.execute("DELETE FROM patients WHERE id = %s", (row[0],))

                cur.execute(
                    """
                    DELETE FROM hl7_messages
                    WHERE sending_application = %s
                      AND sending_facility = %s
                      AND message_control_id = %s
                    """,
                    ("HOSPITAL_APP", "GENERAL_HOSPITAL", control_id),
                )


def test_hl7_adt_endpoint_returns_rejection_ack():
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    control_id = f"REJECT-{uuid4().hex}"

    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        f"ADT^A08|{control_id}|P|2.5\r"
        "PID|1||MRN-NOT-FOUND-999||Doe^Jane||19900101|F\r"
    )

    response = client.post(
        "/hl7/adt",
        content=message,
        headers={"Content-Type": "text/plain", "Accept": "application/hl7-v2"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/hl7-v2")
    assert f"MSA|AR|{control_id}" in response.text
    assert "Cannot update patient" in response.text


def test_hl7_adt_endpoint_duplicate_returns_ack():
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from database import get_connection
    from main import app

    client = TestClient(app)

    control_id = f"DUPACK-{uuid4().hex}"
    mrn = f"MRN-{uuid4().hex[:12]}"

    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        f"ADT^A04|{control_id}|P|2.5\r"
        f"PID|1||{mrn}||Doe^Jane||19900101|F\r"
    )

    headers = {"Content-Type": "text/plain", "Accept": "application/hl7-v2"}

    try:
        first_response = client.post("/hl7/adt", content=message, headers=headers)

        second_response = client.post("/hl7/adt", content=message, headers=headers)

        assert first_response.status_code == 200
        assert second_response.status_code == 200

        assert f"MSA|AA|{control_id}" in first_response.text
        assert f"MSA|AA|{control_id}" in second_response.text

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM patient_identifiers
                    WHERE system = %s AND value = %s
                    """,
                    ("https://medibridge.example.org/mrn", mrn),
                )
                assert cur.fetchone()[0] == 1

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM hl7_messages
                    WHERE sending_application = %s
                      AND sending_facility = %s
                      AND message_control_id = %s
                    """,
                    ("HOSPITAL_APP", "GENERAL_HOSPITAL", control_id),
                )
                assert cur.fetchone()[0] == 1

    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM patient_identifiers
                    WHERE system = %s AND value = %s
                    RETURNING patient_id
                    """,
                    ("https://medibridge.example.org/mrn", mrn),
                )
                row = cur.fetchone()

                if row is not None:
                    cur.execute("DELETE FROM patients WHERE id = %s", (row[0],))

                cur.execute(
                    """
                    DELETE FROM hl7_messages
                    WHERE sending_application = %s
                      AND sending_facility = %s
                      AND message_control_id = %s
                    """,
                    ("HOSPITAL_APP", "GENERAL_HOSPITAL", control_id),
                )


def test_hl7_adt_endpoint_returns_application_error_ack(monkeypatch):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)

    control_id = f"ERRORACK-{uuid4().hex}"

    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        f"ADT^A04|{control_id}|P|2.5\r"
        "PID|1||MRN-ERROR-TEST||Doe^Jane||19900101|F\r"
    )

    def simulate_database_failure(_message):
        raise RuntimeError("Simulated database failure")

    monkeypatch.setattr(main, "register_patient_from_hl7", simulate_database_failure)

    response = client.post(
        "/hl7/adt",
        content=message,
        headers={"Content-Type": "text/plain", "Accept": "application/hl7-v2"},
    )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/hl7-v2")
    assert f"MSA|AE|{control_id}" in response.text
    assert "Internal application processing error" in response.text

    # Internal exception details must not be exposed.
    assert "Simulated database failure" not in response.text


def test_hl7_adt_endpoint_defaults_to_json(monkeypatch):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    import main

    control_id = f"JSONTEST-{uuid4().hex}"

    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        f"ADT^A04|{control_id}|P|2.5\r"
        "PID|1||MRN-JSON-TEST||Doe^Jane||19900101|F\r"
    )

    def simulate_registration(_message):
        return {"status": "created", "patient_id": 123, "mrn": "MRN-JSON-TEST"}

    monkeypatch.setattr(main, "register_patient_from_hl7", simulate_registration)

    client = TestClient(main.app)

    response = client.post("/hl7/adt", content=message, headers={"Content-Type": "text/plain"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "created", "patient_id": 123, "mrn": "MRN-JSON-TEST"}


def test_hl7_adt_endpoint_malformed_message_with_ack_requested():
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    message = "PID|1||MRN-INVALID||Doe^Jane||19900101|F\r"

    response = client.post(
        "/hl7/adt",
        content=message,
        headers={"Content-Type": "text/plain", "Accept": "application/hl7-v2"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert "MSH" in response.json()["detail"]
