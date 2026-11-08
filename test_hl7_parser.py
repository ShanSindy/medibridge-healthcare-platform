
from hl7_parser import (
    parse_hl7_message,
    extract_patient_from_pid,
    process_hl7_adt_message
)
import pytest


def test_parse_hl7_adt_a04():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00001|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000005^^^HOSPITAL^MR||Morgan^Alex||19920415|M"
    )

    result = parse_hl7_message(message)

    assert "MSH" in result
    assert "EVN" in result
    assert "PID" in result

    assert len(result["MSH"]) == 1
    assert len(result["PID"]) == 1

    assert result["MSH"][0][8] == "ADT^A04"
    assert result["PID"][0][3] == "MRN-000005^^^HOSPITAL^MR"
    assert result["PID"][0][5] == "Morgan^Alex"
    
def test_extract_patient_from_pid():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00001|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000005^^^HOSPITAL^MR||Morgan^Alex||19920415|M"
    )

    segments = parse_hl7_message(message)

    patient = extract_patient_from_pid(segments)

    assert patient["mrn"] == "MRN-000005"
    assert patient["first_name"] == "Alex"
    assert patient["last_name"] == "Morgan"
    assert patient["date_of_birth"] == "1992-04-15"
    assert patient["gender"] == "male"

def test_extract_patient_missing_pid():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00001|P|2.5\r"
        "EVN|A04|20261107120000"
    )

    segments = parse_hl7_message(message)

    with pytest.raises(
        ValueError,
        match="HL7 message must contain a PID segment"
    ):
        extract_patient_from_pid(segments)

def test_extract_patient_missing_mrn():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00002|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||||Morgan^Alex||19920415|M"
    )

    segments = parse_hl7_message(message)

    with pytest.raises(
        ValueError,
        match="PID segment must contain a patient MRN"
    ):
        extract_patient_from_pid(segments)

def test_extract_patient_missing_name():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00003|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000006^^^HOSPITAL^MR||||19920415|M"
    )

    segments = parse_hl7_message(message)

    with pytest.raises(
        ValueError,
        match="PID segment must contain a first and last name"
    ):
        extract_patient_from_pid(segments)

@pytest.mark.parametrize(
    "invalid_dob",
    ["", "1992-04-15", "19921345", "20260230"]
)
def test_extract_patient_invalid_dob(invalid_dob):
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00004|P|2.5\r"
        "EVN|A04|20261107120000\r"
        f"PID|1||MRN-000007^^^HOSPITAL^MR||Morgan^Alex||{invalid_dob}|M"
    )

    segments = parse_hl7_message(message)

    with pytest.raises(
        ValueError,
        match="PID segment must contain a valid date of birth"
    ):
        extract_patient_from_pid(segments)


def test_parse_unsupported_hl7_message_type():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A01|MSG00005|P|2.5\r"
        "PID|1||MRN-000008^^^HOSPITAL^MR||Morgan^Alex||19920415|M"
    )

    with pytest.raises(
        ValueError,
        match=r"Unsupported HL7 message type\. Expected ADT\^A04 or ADT\^A08\."
    ):
        parse_hl7_message(message)


        parse_hl7_message(message)

def test_process_hl7_adt_message():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00006|P|2.5\r"
        "EVN|A04|20261107120000\r"
        "PID|1||MRN-000009^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    patient = process_hl7_adt_message(message)

    assert patient == {
        "mrn": "MRN-000009",
        "first_name": "Jordan",
        "last_name": "Smith",
        "date_of_birth": "1995-07-22",
        "gender": "female"
    }

def test_parse_hl7_adt_a08():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108120000||ADT^A08|MSG00021|P|2.5\r"
        "EVN|A08|20261108120000\r"
        "PID|1||MRN-000020^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    segments = parse_hl7_message(message)

    assert "MSH" in segments
    assert "EVN" in segments
    assert "PID" in segments
    assert segments["MSH"][0][8] == "ADT^A08"
