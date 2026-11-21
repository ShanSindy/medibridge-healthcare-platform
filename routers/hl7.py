"""HL7 v2 ADT message intake (A04 registration, A08 update) with optional HL7 ACK responses."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from auth_dependencies import require_permission
from hl7_ack import generate_hl7_ack
from hl7_parser import parse_hl7_message
from hl7_service import register_patient_from_hl7, update_patient_from_hl7

router = APIRouter()


@router.post(
    "/hl7/adt",
    tags=["HL7 Integration"],
    dependencies=[Depends(require_permission("patients:write"))],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {
                    "schema": {"type": "string", "description": "Raw HL7 ADT^A04 message"}
                }
            },
        }
    },
)
async def receive_hl7_adt(request: Request):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type != "text/plain":
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain.")

    raw_message = (await request.body()).decode("utf-8", errors="replace")

    wants_hl7_ack = "application/hl7-v2" in request.headers.get("accept", "").lower()

    try:
        segments = parse_hl7_message(raw_message)
        message_type = segments["MSH"][0][8]

        if message_type == "ADT^A04":
            result = register_patient_from_hl7(raw_message)

        elif message_type == "ADT^A08":
            result = update_patient_from_hl7(raw_message)

        else:
            raise ValueError("Unsupported HL7 message type.")

    except ValueError as exc:
        if wants_hl7_ack and "segments" in locals():
            ack = generate_hl7_ack(segments, acknowledgment_code="AR", error_message=str(exc))
            return Response(content=ack, media_type="application/hl7-v2", status_code=400)

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except Exception:
        if wants_hl7_ack and "segments" in locals():
            ack = generate_hl7_ack(
                segments,
                acknowledgment_code="AE",
                error_message="Internal application processing error",
            )
            return Response(content=ack, media_type="application/hl7-v2", status_code=500)

        raise

    if wants_hl7_ack:
        ack = generate_hl7_ack(segments, acknowledgment_code="AA")
        return Response(content=ack, media_type="application/hl7-v2")

    return result
