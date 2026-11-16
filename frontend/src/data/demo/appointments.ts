/**
 * SYNTHETIC DEMO DATA — appointment times are generated relative to the
 * moment the app loads so "today" and "upcoming" always have content.
 */
import { atDayOffset, DEMO_NOW } from '../../lib/clock'
import type { Appointment, VisitMode } from '../../types/domain'
import { demoProviders } from './providers'

type AppointmentSeed = [
  patientId: number,
  providerId: number,
  dayOffset: number,
  hour: number,
  minute: number,
  durationMinutes: number,
  reason: string,
  mode: VisitMode,
  cancelled?: boolean,
]

const SEEDS: AppointmentSeed[] = [
  // Today
  [1, 1, 0, 8, 30, 30, 'Annual wellness visit', 'in-person'],
  [4, 1, 0, 9, 15, 20, 'Blood pressure follow-up', 'in-person'],
  [8, 6, 0, 10, 0, 30, 'Asthma management review', 'in-person'],
  [16, 10, 0, 11, 0, 45, 'Medication management', 'telehealth'],
  [11, 8, 0, 13, 30, 30, 'Fall risk assessment', 'in-person'],
  [18, 9, 0, 14, 15, 30, 'Post-operative check — knee', 'in-person', true],
  [7, 4, 0, 15, 0, 40, 'Echocardiogram results review', 'in-person'],
  [23, 8, 0, 16, 0, 20, 'Medication reconciliation', 'telehealth'],
  // Upcoming
  [2, 2, 1, 9, 0, 30, 'Diabetes follow-up', 'in-person'],
  [3, 3, 1, 10, 30, 30, 'Thyroid panel review', 'telehealth'],
  [10, 7, 1, 14, 0, 30, 'Well-child visit', 'in-person'],
  [14, 9, 2, 8, 45, 30, 'Physical therapy referral review', 'in-person'],
  [17, 11, 2, 11, 15, 20, 'Skin lesion evaluation', 'in-person'],
  [20, 12, 3, 9, 30, 45, 'Colonoscopy consultation', 'in-person'],
  [22, 5, 3, 13, 0, 40, 'Migraine follow-up', 'telehealth'],
  [9, 3, 4, 10, 0, 30, 'Insulin pump education', 'in-person'],
  [13, 2, 5, 15, 30, 30, 'Routine follow-up', 'in-person', true],
  [19, 7, 6, 9, 0, 30, 'Immunization visit', 'in-person'],
  [5, 1, 7, 11, 0, 45, 'New patient intake', 'in-person'],
  [12, 2, 8, 8, 30, 45, 'New patient intake', 'in-person'],
  // Past
  [23, 8, -1, 10, 30, 30, 'Annual wellness visit', 'in-person'],
  [8, 6, -2, 14, 0, 30, 'Pulmonary function test', 'in-person'],
  [2, 2, -3, 10, 0, 30, 'Hypertension check', 'in-person'],
  [18, 9, -4, 11, 0, 30, 'Pre-operative assessment', 'in-person'],
  [21, 12, -5, 15, 0, 30, 'Abdominal pain consult', 'in-person', true],
  [16, 10, -7, 16, 0, 45, 'Initial psychiatric evaluation', 'telehealth'],
  [22, 5, -11, 9, 30, 40, 'EEG results review', 'in-person'],
  [1, 1, -12, 9, 0, 30, 'Lab results review', 'in-person'],
  [14, 9, -14, 13, 0, 30, 'Knee pain evaluation', 'in-person'],
  [13, 2, -60, 9, 0, 30, 'Routine check-up', 'in-person'],
]

const providerLocation = new Map(demoProviders.map((provider) => [provider.id, provider.location]))

export const demoAppointments: Appointment[] = SEEDS.map(
  ([patientId, providerId, dayOffset, hour, minute, duration, reason, mode, cancelled], index) => {
    const startsAt = atDayOffset(dayOffset, hour, minute)
    const endsAt = new Date(startsAt.getTime() + duration * 60_000)
    return {
      id: index + 1,
      patientId,
      providerId,
      startsAt: startsAt.toISOString(),
      endsAt: endsAt.toISOString(),
      reason,
      mode,
      location: mode === 'telehealth' ? 'Telehealth' : (providerLocation.get(providerId) ?? 'Unassigned'),
      status: cancelled ? 'cancelled' : endsAt < DEMO_NOW ? 'completed' : 'scheduled',
    }
  },
)
