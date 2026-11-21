from datetime import UTC, datetime
from uuid import uuid4


def generate_hl7_ack(
    original_segments: dict, acknowledgment_code: str = "AA", error_message: str | None = None
) -> str:
    """
    Generate a basic HL7 v2 acknowledgment message.

    Supported acknowledgment codes:
    AA - Application Accept
    AE - Application Error
    AR - Application Reject
    """

    if acknowledgment_code not in {"AA", "AE", "AR"}:
        raise ValueError("Invalid HL7 acknowledgment code.")

    if "MSH" not in original_segments or not original_segments["MSH"]:
        raise ValueError("Original HL7 message must contain MSH.")

    msh = original_segments["MSH"][0]

    if len(msh) <= 9 or not msh[9].strip():
        raise ValueError("Original message control ID is required.")

    original_control_id = msh[9].strip()
    original_message_type = msh[8].split("^")
    trigger_event = original_message_type[1] if len(original_message_type) > 1 else ""

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    ack_control_id = str(uuid4())

    sending_application = msh[2] if len(msh) > 2 else ""
    sending_facility = msh[3] if len(msh) > 3 else ""

    ack_msh = (
        f"MSH|^~\\&|MediBridge|Integration|"
        f"{sending_application}|{sending_facility}|"
        f"{timestamp}||ACK^{trigger_event}|"
        f"{ack_control_id}|P|2.5"
    )

    ack_msa = f"MSA|{acknowledgment_code}|{original_control_id}"

    if error_message:
        safe_error = error_message.replace("\r", " ").replace("\n", " ").replace("|", " ")
        ack_msa += f"|{safe_error}"

    return f"{ack_msh}\r{ack_msa}\r"
