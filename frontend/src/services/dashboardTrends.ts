import { ageInYears } from '../lib/format'
import type {
  Appointment,
  AppointmentDay,
  CountSlice,
  Gender,
  Hl7Hour,
  Patient,
  PatientDemographics,
  RegistrationMonth,
} from '../types/domain'

/**
 * Pure aggregations behind the dashboard charts. They take the records and a
 * reference time as arguments so they can be reused for any data source.
 */

export function registrationsByMonth(
  patients: Array<Pick<Patient, 'registeredAt' | 'registrationSource'>>,
  now: Date,
  months = 12,
): { months: RegistrationMonth[]; beforeWindow: number } {
  const buckets: RegistrationMonth[] = []
  for (let back = months - 1; back >= 0; back -= 1) {
    buckets.push({
      monthStart: new Date(now.getFullYear(), now.getMonth() - back, 1).toISOString(),
      manual: 0,
      hl7: 0,
    })
  }

  let beforeWindow = 0
  for (const patient of patients) {
    // Records without a registration date or source cannot be placed on the chart.
    if (patient.registeredAt === null || patient.registrationSource === null) continue
    const registered = new Date(patient.registeredAt)
    const back = (now.getFullYear() - registered.getFullYear()) * 12 + now.getMonth() - registered.getMonth()
    if (back < 0) continue
    if (back >= months) {
      beforeWindow += 1
      continue
    }
    buckets[months - 1 - back][patient.registrationSource] += 1
  }

  return { months: buckets, beforeWindow }
}

export function appointmentsByDay(
  appointments: Array<Pick<Appointment, 'startsAt' | 'status'>>,
  now: Date,
  daysBefore = 7,
  daysAfter = 6,
): AppointmentDay[] {
  const days: AppointmentDay[] = []
  const indexByDay = new Map<string, number>()

  for (let offset = -daysBefore; offset <= daysAfter; offset += 1) {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset)
    indexByDay.set(date.toDateString(), days.length)
    days.push({ date: date.toISOString(), isToday: offset === 0, completed: 0, scheduled: 0, cancelled: 0 })
  }

  for (const appointment of appointments) {
    const index = indexByDay.get(new Date(appointment.startsAt).toDateString())
    if (index !== undefined) days[index][appointment.status] += 1
  }

  return days
}

/**
 * Lays hour-of-day volumes out as the 24 clock hours ending with the current
 * hour. A rejected message counts towards its hour's total.
 */
export function hl7ByHour(volumeByHourOfDay: number[], rejectedHoursAgo: number[], now: Date): Hl7Hour[] {
  const hours: Hl7Hour[] = []
  for (let back = 23; back >= 0; back -= 1) {
    const hourStart = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours() - back)
    const total = volumeByHourOfDay[hourStart.getHours()] ?? 0
    const rejected = Math.min(total, rejectedHoursAgo.filter((value) => value === back).length)
    hours.push({ hourStart: hourStart.toISOString(), accepted: total - rejected, rejected })
  }
  return hours
}

const GENDER_ROWS: Array<{ gender: Gender; label: string }> = [
  { gender: 'female', label: 'Female' },
  { gender: 'male', label: 'Male' },
  { gender: 'other', label: 'Other' },
  { gender: 'unknown', label: 'Unknown' },
]

const AGE_GROUPS: Array<{ label: string; min: number; max: number }> = [
  { label: '0–17', min: 0, max: 17 },
  { label: '18–39', min: 18, max: 39 },
  { label: '40–64', min: 40, max: 64 },
  { label: '65+', min: 65, max: Infinity },
]

export function patientDemographics(
  patients: Array<Pick<Patient, 'gender' | 'dateOfBirth'>>,
  now: Date,
): PatientDemographics {
  const gender: CountSlice[] = GENDER_ROWS.map((row) => ({
    label: row.label,
    count: patients.filter((patient) => patient.gender === row.gender).length,
  }))

  const ages = patients.map((patient) => ageInYears(patient.dateOfBirth, now))
  const ageGroups: CountSlice[] = AGE_GROUPS.map((group) => ({
    label: group.label,
    count: ages.filter((age) => age >= group.min && age <= group.max).length,
  }))

  return { gender, ageGroups }
}
