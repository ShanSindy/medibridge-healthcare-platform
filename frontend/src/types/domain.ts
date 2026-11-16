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

export interface Patient {
  id: number
  /** Synthetic medical record number (frontend demo field). */
  mrn: string
  firstName: string
  lastName: string
  /** ISO calendar date, `YYYY-MM-DD`. */
  dateOfBirth: string
  gender: Gender
  phone: string
  email: string
  status: PatientStatus
  primaryProviderId: number | null
  /** ISO timestamp of the most recent completed visit. */
  lastVisit: string | null
  registrationSource: RegistrationSource
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

export interface DashboardOverview {
  summary: DashboardSummary
  todaysAppointments: AppointmentDetails[]
  recentActivity: ActivityEvent[]
}
