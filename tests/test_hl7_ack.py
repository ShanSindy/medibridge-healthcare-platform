import pytest

from hl7_ack import generate_hl7_ack
from hl7_parser import parse_hl7_message


def sample_hl7_segments():
    message = (
        "MSH|^~\\&|HOSPITAL_APP|GENERAL_HOSPITAL|"
        "MediBridge|Integration|20261109110000||"
        "ADT^A04|MSG12345|P|2.5\r"
        "PID|1||MRN-10001||Doe^Jane||19900101|F\r"
    )

    return parse_hl7_message(message)


def test_generate_hl7_ack_success():
    segments = sample_hl7_segments()

    ack = generate_hl7_ack(segments)

    assert "MSH|^~\\&|MediBridge|Integration|" in ack
    assert "|HOSPITAL_APP|GENERAL_HOSPITAL|" in ack
    assert "|ACK^A04|" in ack
    assert "MSA|AA|MSG12345" in ack
    assert ack.endswith("\r")


def test_generate_hl7_ack_application_error():
    segments = sample_hl7_segments()

    ack = generate_hl7_ack(
        segments, acknowledgment_code="AE", error_message="Database processing failed"
    )

    assert "MSA|AE|MSG12345|Database processing failed" in ack


def test_generate_hl7_ack_reject():
    segments = sample_hl7_segments()

    ack = generate_hl7_ack(segments, acknowledgment_code="AR", error_message="Invalid message")

    assert "MSA|AR|MSG12345|Invalid message" in ack


def test_generate_hl7_ack_invalid_code():
    segments = sample_hl7_segments()

    with pytest.raises(ValueError, match="Invalid HL7 acknowledgment code"):
        generate_hl7_ack(segments, acknowledgment_code="XX")


def test_generate_hl7_ack_missing_msh():
    with pytest.raises(ValueError, match="Original HL7 message must contain MSH"):
        generate_hl7_ack({})


def test_generate_hl7_ack_missing_control_id():
    segments = sample_hl7_segments()
    segments["MSH"][0][9] = ""

    with pytest.raises(ValueError, match="Original message control ID is required"):
        generate_hl7_ack(segments)


def test_generate_hl7_ack_sanitizes_error():
    segments = sample_hl7_segments()

    ack = generate_hl7_ack(
        segments, acknowledgment_code="AE", error_message="Database|error\rUnexpected\nfailure"
    )

    assert "Database error Unexpected failure" in ack
    assert ack.count("\r") == 2
