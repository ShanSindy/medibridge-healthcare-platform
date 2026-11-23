# Healthcare interoperability

What MediBridge implements of HL7 v2 and HL7 FHIR R4, how it behaves, and where it
stops. All examples are synthetic. MediBridge has not been tested against live hospital
or EHR systems and has not been checked with HL7 or FHIR validation tools, so it does not
claim conformance to either standard.

## HL7 v2 and FHIR in brief

- **HL7 version 2** is the long-established messaging standard inside hospitals. Systems
  send pipe-delimited event messages; an *ADT* (admission, discharge, transfer) message
  tells other systems that a patient was registered or that their details changed.
  The receiver usually answers with an acknowledgment (ACK).
- **HL7 FHIR** is a newer, resource-oriented standard. Data such as a Patient or a
  Practitioner is exposed as JSON (or XML) resources over a REST API, with standard
  read and search operations.

MediBridge receives HL7 v2 ADT events and serves its records as FHIR R4 resources.
Diagrams of both flows are in [architecture.md](architecture.md#hl7-adt-processing).

## HL7 v2 ADT intake

### Endpoint

`POST /hl7/adt`, one message per request, requires a bearer token with the
`patients:write` permission (admin, clinician). Messages travel over HTTP; there is no
MLLP listener.

| Request header | Requirement |
|---|---|
| `Content-Type` | Must be `text/plain` (a `charset` parameter is allowed); anything else returns `415` |
| `Accept` | Optional. If it contains `application/hl7-v2`, the response is an HL7 ACK instead of JSON |

Segments may be separated by carriage returns (standard HL7) or line breaks.

### Supported messages

| Message | Effect |
|---|---|
| `ADT^A04` (register a patient) | Creates the patient and stores the MRN as an identifier, unless the MRN already exists |
| `ADT^A08` (update patient information) | Updates name, date of birth and gender of the patient with that MRN |

Any other message type is rejected.

### Validation and field mapping

The message must start with an `MSH` segment and contain exactly one `MSH` and one `PID`
segment. Every segment name must be an upper-case letter followed by two upper-case
letters or digits.

| Field | Used for | Rule |
|---|---|---|
| MSH-3, MSH-4 | Sending application and facility | Required; part of the duplicate key |
| MSH-9 | Message type | `ADT^A04` or `ADT^A08` |
| MSH-10 | Message control ID | Required; part of the duplicate key |
| MSH-12 | Version | **Not checked** |
| PID-3 | MRN | First component, required. Stored with system `https://medibridge.example.org/mrn`; the assigning authority is ignored |
| PID-5 | Name | `family^given`, both required |
| PID-7 | Date of birth | Exactly `YYYYMMDD` and a real date (no time component) |
| PID-8 | Gender | `M`, `F`, `O`, `U` → `male`, `female`, `other`, `unknown`; any other value becomes `unknown` |

### Processing and transactions

Validation happens before anything is written. Then one database transaction records
the message (sending application, facility and control ID in `hl7_messages`) and makes
the patient change. JSON results:

| Situation | Result | What is written |
|---|---|---|
| New A04, MRN not yet known | `{"status": "created", "patient_id": …, "mrn": …}` | Message record, patient, MRN identifier |
| New A04, MRN already registered | `{"status": "already_exists", "patient_id": …, "mrn": …}` | Message record only; the patient is not changed |
| New A08, MRN found | `{"status": "updated", "patient_id": …, "mrn": …}` | Message record, patient update |
| New A08, MRN not found | `400 {"detail": "Cannot update patient: MRN not found."}` | Nothing: the transaction is rolled back |
| Same sending application, facility and control ID as an earlier processed message | `{"status": "duplicate", "mrn": …, "message_control_id": …}` | Nothing |
| Validation error | `400 {"detail": "<reason>"}` | Nothing |
| Unexpected processing error | `500` | Nothing: the transaction is rolled back |

Only successfully processed messages are recorded, so a corrected message can be resent
with the same control ID after a failure. No audit event is written for HL7 processing.

Patient identity for HL7 is the pair (system, value) in `patient_identifiers`, protected by
the `unique_patient_identifier` constraint. The system is always the constant
`https://medibridge.example.org/mrn` and the value is the first component of PID-3; the
assigning authority is not part of the identity, so the same MRN from two authorities is
treated as one patient.

Two A04 messages for the same MRN with different control IDs can be processed at the same
time. Which path the second one takes depends on timing:

- If the first registration has committed before the second looks up the MRN, the second
  takes the normal sequential path and returns `already_exists`.
- If both look up the MRN before either commits, the second waits on the uniqueness
  constraint until the first commits. Its own patient row is then rolled back inside a
  savepoint, it returns `already_exists` for the first patient, and its message is still
  recorded. With `Accept: application/hl7-v2` the response is an `AA` acknowledgment for its
  own control ID. If the first registration rolls back instead, the second creates the
  patient.

Both paths give the same result: one patient, one identifier and both messages recorded.
The recovery relies on PostgreSQL's default READ COMMITTED isolation, which MediBridge does
not change. Under REPEATABLE READ or SERIALIZABLE the waiting registration may not see the
first patient and fails with the original `UniqueViolation` instead. Other database errors
are not converted and fail as before. A08 updates are unchanged.

### Acknowledgments

When the client sends `Accept: application/hl7-v2`, the response has content type
`application/hl7-v2` and an ACK body:

| Code | Meaning | HTTP status | When |
|---|---|---|---|
| `AA` | Application accept | 200 | Created, already exists, updated, or duplicate |
| `AR` | Application reject | 400 | Validation errors found after the message structure was parsed (PID fields, sender fields, unknown MRN for A08), with the reason in MSA-3 |
| `AE` | Application error | 500 | Unexpected processing errors, with the text `Internal application processing error` |

If the message cannot be parsed at all (for example no `MSH` segment, or an unsupported
message type), there is nothing to acknowledge and the response is a JSON `400` even when
an ACK was requested.

The ACK's MSH segment names MediBridge as the sender, addresses the original sender
(MSH-3 and MSH-4), carries a new UUID as its control ID and declares version `2.5`
regardless of the incoming version. MSA-2 echoes the original control ID.

### Example: ADT^A04 registration

A synthetic registration message (save it as `adt-a04.hl7`; see
[api.md](api.md#hl7-adt-intake) for the request):

```text
MSH|^~\&|SYN_REG|SYN_CLINIC|MEDIBRIDGE|MEDIBRIDGE|20261121090000||ADT^A04|SYN-MSG-0001|P|2.5
EVN|A04|20261121090000
PID|1||SYN-000123^^^SYN_CLINIC^MR||Example^Riley||19900215|U
```

JSON response: `{"status": "created", "patient_id": 43, "mrn": "SYN-000123"}`.

With `Accept: application/hl7-v2`, the response is an ACK like this (timestamp and ACK
control ID vary; segments are separated by carriage returns):

```text
MSH|^~\&|MediBridge|Integration|SYN_REG|SYN_CLINIC|20261121150000||ACK^A04|<uuid>|P|2.5
MSA|AA|SYN-MSG-0001
```

Sending the same message again returns `"status": "duplicate"` (or `AA`). An `ADT^A08`
for MRN `SYN-000123` with a new control ID updates Riley Example's name, date of birth
or gender.

## FHIR R4

### Scope

- FHIR version **4.0.1** (R4), JSON only.
- Read and search for **Patient**, **Practitioner** and **PractitionerRole**, plus the
  public **CapabilityStatement** at `GET /fhir/metadata`.
- No create, update or delete through FHIR, and no other resource types (for example
  there is no Appointment resource).

| Endpoint | Permission | Response content type |
|---|---|---|
| `GET /fhir/metadata` | Public | `application/json` |
| `GET /fhir/Patient/{patient_id}`, `GET /fhir/Patient` | `patients:read` | `application/json` |
| `GET /fhir/Practitioner/{practitioner_id}`, `GET /fhir/Practitioner` | `providers:read` | `application/fhir+json` |
| `GET /fhir/PractitionerRole/{role_id}`, `GET /fhir/PractitionerRole` | `providers:read` | `application/fhir+json` |

The CapabilityStatement lists `application/fhir+json` as its format, but the metadata
and Patient endpoints currently respond with `application/json`.

### Resource mapping

| Resource | Source | Elements |
|---|---|---|
| Patient | `patients`, `patient_identifiers` | `id`; `name` (official, family and given); `gender`; `birthDate`; `identifier` (system and value, when the patient has identifiers, such as HL7 MRNs) |
| Practitioner | `providers` | `id`; `name` (official); `telecom` email and phone when present |
| PractitionerRole | `providers` | `id` (the provider's ID: one role per provider); `practitioner` reference `Practitioner/{id}`; `specialty` as text |

### Search and pagination

| Resource | Parameters |
|---|---|
| Patient | `family`, `given` (case-insensitive prefix match); `identifier` as `value` or `system\|value` (exact match) |
| Practitioner | `family`, `given` (case-insensitive prefix match) |
| PractitionerRole | `specialty` (case-insensitive prefix match on the specialty text, not FHIR token matching) |
| All | `_count` 1 to 100 (default 10); `_offset` 0 or more (default 0) |

Several parameters combine with AND. Results are ordered by ID. Searches return a
`searchset` Bundle with `total` (all matches), one `entry` per resource with an absolute
`fullUrl`, and `link` entries `self`, `next` (when more results exist) and `previous`
(when `_offset` > 0). The link URLs are **relative** (path and query only).

Example: `GET /fhir/Patient?family=Exam&_count=2` (abbreviated):

```json
{
  "resourceType": "Bundle",
  "type": "searchset",
  "total": 3,
  "link": [
    {"relation": "self", "url": "/fhir/Patient?family=Exam&_count=2&_offset=0"},
    {"relation": "next", "url": "/fhir/Patient?family=Exam&_count=2&_offset=2"}
  ],
  "entry": [
    {
      "fullUrl": "http://localhost:8000/fhir/Patient/42",
      "resource": {
        "resourceType": "Patient",
        "id": "42",
        "name": [{"use": "official", "family": "Example", "given": ["Avery"]}],
        "gender": "female",
        "birthDate": "1985-03-14"
      }
    }
  ]
}
```

### Errors

| Situation | Response |
|---|---|
| Resource not found | `404` `OperationOutcome`, code `not-found`, for example `Patient not found` (`application/fhir+json`) |
| Invalid parameters (non-numeric ID, `_count` outside 1 to 100, negative `_offset`) | `422` `OperationOutcome`, code `invalid`, `Invalid FHIR request parameters` (`application/fhir+json`) |
| Missing or invalid token | `401` plain FastAPI JSON, not an `OperationOutcome` |
| Insufficient permission | `403` plain FastAPI JSON, not an `OperationOutcome` |

```json
{"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found", "diagnostics": "Patient not found"}]}
```

## Limitations

- HL7 messages are accepted over HTTP only; there is no MLLP transport.
- Only `ADT^A04` and `ADT^A08`; other ADT events and message types are rejected.
- MSH-12 (version) is not validated, and ACKs always declare version `2.5`.
- HL7 patient writes do not create audit events.
- FHIR is JSON only, read and search only, for three resource types.
- The metadata and Patient endpoints respond with `application/json` rather than
  `application/fhir+json`.
- Bundle `link` URLs are relative.
- Authentication and authorization errors on FHIR endpoints are plain JSON, not
  `OperationOutcome`.
- No HL7 or FHIR validator has been run against these interfaces.
- No integration testing with live hospital or EHR systems.
