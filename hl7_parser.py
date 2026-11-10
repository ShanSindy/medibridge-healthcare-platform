import re
from datetime import datetime

def parse_hl7_message(message: str) -> dict:
    """
    Parse an HL7 v2 message into a dictionary of segments.

    This initial parser supports pipe-delimited HL7 messages.
    More advanced validation and field handling will be
    added in later steps.
    """
    if not message or not message.strip():
        raise ValueError("HL7 message cannot be empty.")


    segments = {}

    for line in message.strip().splitlines():
        line = line.strip()

        if not line:
            continue

        fields = line.split("|")
        segment_name = fields[0]

        if not segments and segment_name != "MSH":
            raise ValueError(
                "HL7 message must begin with an MSH segment."
            )



        if not segment_name:
            raise ValueError("HL7 segment name cannot be empty.")

        if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", segment_name):
            raise ValueError(
                f"Invalid HL7 segment name: {segment_name}"
            )

        segments.setdefault(segment_name, []).append(fields)


    if "MSH" not in segments:
        raise ValueError(
            "HL7 message must contain an MSH segment."
        )

    if len(segments["MSH"]) != 1:
        raise ValueError(
            "HL7 message must contain exactly one MSH segment."
        )


    if "PID" not in segments:
        raise ValueError(
            "HL7 message must contain a PID segment."
        )

    if len(segments["PID"]) != 1:
        raise ValueError(
            "HL7 message must contain exactly one PID segment."
        )


    msh_fields = segments["MSH"][0]



    if len(msh_fields) <= 8:
        raise ValueError(
            "HL7 MSH segment is missing the message type."
        )

    if len(msh_fields) <= 9 or not msh_fields[9].strip():
        raise ValueError(
            "HL7 MSH segment must contain a message control ID."
        )

    message_type = msh_fields[8]
    supported_message_types = {"ADT^A04", "ADT^A08"}

    if message_type not in supported_message_types:
        raise ValueError(
            "Unsupported HL7 message type. Expected ADT^A04 or ADT^A08."
        )

    return segments


def extract_hl7_message_metadata(segments: dict) -> dict:
    """
    Extract message identification information from MSH.

    The sending application, sending facility, and message
    control ID will be used for duplicate detection.
    """
    if "MSH" not in segments or not segments["MSH"]:
        raise ValueError("HL7 message must contain an MSH segment.")

    msh_fields = segments["MSH"][0]

    if len(msh_fields) <= 9:
        raise ValueError("HL7 MSH segment is missing required metadata.")

    sending_application = msh_fields[2].strip()
    sending_facility = msh_fields[3].strip()
    message_type = msh_fields[8].strip()
    message_control_id = msh_fields[9].strip()

    if not sending_application or not sending_facility:
        raise ValueError(
            "HL7 message must identify its sending application and facility."
        )

    if not message_control_id:
        raise ValueError("HL7 message control ID cannot be empty.")

    return {
        "sending_application": sending_application,
        "sending_facility": sending_facility,
        "message_control_id": message_control_id,
        "message_type": message_type,
    }



def extract_patient_from_pid(segments: dict) -> dict:
    """
    Extract patient demographics from an HL7 PID segment.

    Supports basic HL7 v2.5 patient identifiers,
    names, dates of birth, and gender values.
    """
    if "PID" not in segments or not segments["PID"]:
        raise ValueError("HL7 message must contain a PID segment.")

    fields = segments["PID"][0]

    if len(fields) <= 8:
        raise ValueError("PID segment is missing required fields.")

    # PID-3: Patient identifier
    identifier_components = fields[3].split("^")
    mrn = identifier_components[0].strip()

    if not mrn:
        raise ValueError("PID segment must contain a patient MRN.")


    # PID-5: Patient name (family^given)
    name_components = fields[5].split("^")

    last_name = name_components[0].strip()
    first_name = (
        name_components[1].strip()
        if len(name_components) > 1
        else ""
    )

    if not first_name or not last_name:
        raise ValueError(
            "PID segment must contain a first and last name."
        )



    # PID-7: Date of birth (YYYYMMDD)
    raw_dob = fields[7].strip()

    if len(raw_dob) != 8 or not raw_dob.isascii() or not raw_dob.isdigit():
        raise ValueError(
            "PID segment must contain a valid date of birth in YYYYMMDD format."
        )

    try:
        date_of_birth = datetime.strptime(
            raw_dob, "%Y%m%d"
        ).date().isoformat()
    except ValueError:
        raise ValueError(
            "PID segment must contain a valid date of birth in YYYYMMDD format."
        ) from None


    # PID-8: Administrative sex
    gender_mapping = {
        "M": "male",
        "F": "female",
        "O": "other",
        "U": "unknown"
    }

    gender = gender_mapping.get(
        fields[8].upper(), "unknown"
    )

    return {
        "mrn": mrn,
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": date_of_birth,
        "gender": gender
    }

def process_hl7_adt_message(message: str) -> dict:
    """
    Parse an HL7 ADT^A04 registration message and
    return validated patient information.
    """
    segments = parse_hl7_message(message)

    patient = extract_patient_from_pid(segments)

    return patient
