/**
 * Frontend domain models.
 *
 * Field names are camelCase. The FastAPI backend uses snake_case
 * (e.g. `first_name`, `date_of_birth`, `starts_at`); when real API
 * integration is added, map responses at the service boundary in
 * `src/services/` so components never depend on wire formats.
 *
 * Fields marked "frontend demo field" do not exist in the current
 * backend models and are shown for UI demonstration only.
 */

export type Gender = 'male' | 'female' | 'other' | 'unknown'

/** Frontend demo field. */
export type PatientStatus = 'active' | 'inactive' | 'pending'

/** Frontend demo field. `hl7` = registered via an HL7 ADT^A04 message. */
export type RegistrationSource = 'manual' | 'hl7'

/** Where a record came from: synthetic demo data or the FastAPI backend. */
export type RecordSource = 'demo' | 'api'

/**
 * A patient record.
 *
 * `id`, names, date of birth and gender are provided by the backend
 * (`GET /patients`). Every other field is `null` when the source does not
 * provide it — the UI must show it as unavailable, never invent a value.
 */
export interface Patient {
  id: number
  recordSource: RecordSource
  /** Medical record number. Demo only; not returned by `GET /patients`. */
  mrn: string | null
  firstName: string
  lastName: string
  /** ISO calendar date, `YYYY-MM-DD`. */
  dateOfBirth: string
  gender: Gender
  phone: string | null
  email: string | null
  status: PatientStatus | null
  primaryProviderId: number | null
  /** ISO timestamp of the most recent completed visit. */
  lastVisit: string | null
  registrationSource: RegistrationSource | null
  /** ISO timestamp the record was created. Demo only; not returned by `GET /patients`. */
  registeredAt: string | null
}

export interface Provider {
  id: number
  /** Display prefix such as "Dr."; null for non-physician clinicians. */
  prefix: string | null
  firstName: string
  lastName: string
  credentials: string
  specialty: string
  department: string
  email: string
  phone: string
  location: string
  acceptingNewPatients: boolean
}

/** Mirrors the backend `Appointment.status` literal. */
export type AppointmentStatus = 'scheduled' | 'completed' | 'cancelled'

export type VisitMode = 'in-person' | 'telehealth'

export interface Appointment {
  id: number
  patientId: number
  providerId: number
  /** ISO timestamp with timezone, as required by the backend. */
  startsAt: string
  endsAt: string
  reason: string | null
  status: AppointmentStatus
  mode: VisitMode
  location: string
}

/** Appointment joined with the patient and provider display details. */
export interface AppointmentDetails extends Appointment {
  patientName: string
  patientMrn: string
  providerName: string
  providerSpecialty: string
}

export type ActivityCategory = 'hl7' | 'fhir' | 'appointment' | 'patient' | 'security'

export interface ActivityEvent {
  id: string
  category: ActivityCategory
  title: string
  description: string
  occurredAt: string
  outcome: 'success' | 'failure'
}

export interface DashboardSummary {
  patients: { total: number; active: number; pending: number }
  providers: { total: number; acceptingNewPatients: number }
  appointments: {
    today: number
    upcomingWeek: number
    total: number
    byStatus: Record<AppointmentStatus, number>
  }
  integration: { hl7Messages24h: number; hl7Failures24h: number; fhirRequests24h: number }
}

/** New patient records in one calendar month, split by registration source. */
export interface RegistrationMonth {
  /** ISO timestamp of the first day of the month (local time). */
  monthStart: string
  manual: number
  hl7: number
}

/** Appointments starting on one local calendar day, split by status. */
export interface AppointmentDay {
  /** ISO timestamp of local midnight. */
  date: string
  isToday: boolean
  completed: number
  scheduled: number
  cancelled: number
}

/** HL7 v2 messages received in one clock hour. */
export interface Hl7Hour {
  hourStart: string
  accepted: number
  rejected: number
}

export interface CountSlice {
  label: string
  count: number
}

export interface DashboardTrends {
  registrationsByMonth: RegistrationMonth[]
  /** Registrations older than the charted window. */
  registrationsBeforeWindow: number
  appointmentsByDay: AppointmentDay[]
  hl7ByHour: Hl7Hour[]
}

export interface PatientDemographics {
  gender: CountSlice[]
  ageGroups: CountSlice[]
}

export interface DashboardOverview {
  summary: DashboardSummary
  trends: DashboardTrends
  demographics: PatientDemographics
  todaysAppointments: AppointmentDetails[]
  recentActivity: ActivityEvent[]
}
