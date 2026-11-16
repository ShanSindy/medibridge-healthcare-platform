import { demoAppointments } from '../data/demo/appointments'
import { demoPatients } from '../data/demo/patients'
import { demoProviders } from '../data/demo/providers'
import { patientName, providerName } from '../lib/format'
import type { Appointment, AppointmentDetails, Patient, Provider } from '../types/domain'
import { demoRequest } from './demoRequest'

/** Joins appointments with patient and provider display details. */
export function toAppointmentDetails(
  appointments: Appointment[],
  patients: Patient[],
  providers: Provider[],
): AppointmentDetails[] {
  const patientsById = new Map(patients.map((patient) => [patient.id, patient]))
  const providersById = new Map(providers.map((provider) => [provider.id, provider]))

  return appointments
    .map((appointment) => {
      const patient = patientsById.get(appointment.patientId)
      const provider = providersById.get(appointment.providerId)
      return {
        ...appointment,
        patientName: patient ? patientName(patient) : `Patient #${appointment.patientId}`,
        patientMrn: patient?.mrn ?? '—',
        providerName: provider ? providerName(provider) : `Provider #${appointment.providerId}`,
        providerSpecialty: provider?.specialty ?? '—',
      }
    })
    .sort((a, b) => a.startsAt.localeCompare(b.startsAt))
}

/**
 * Lists appointments with patient and provider details, ordered by start time.
 *
 * Future integration: `GET {API_BASE_URL}/appointments` returns ids only, so
 * names will be resolved from the patients/providers endpoints (or a joined
 * backend view) before reaching the UI.
 */
export function listAppointments(): Promise<AppointmentDetails[]> {
  return demoRequest(() => toAppointmentDetails(demoAppointments, demoPatients, demoProviders))
}
