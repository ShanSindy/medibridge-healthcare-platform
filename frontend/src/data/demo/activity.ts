/**
 * SYNTHETIC DEMO DATA — illustrative platform activity and integration metrics.
 * These are not produced by the backend.
 */
import { minutesBeforeNow } from '../../lib/clock'
import type { ActivityEvent, DashboardSummary } from '../../types/domain'

export const demoActivity: ActivityEvent[] = [
  {
    id: 'act-1',
    category: 'hl7',
    title: 'ADT^A04 message processed',
    description: 'New patient registration received for DEMO-100221.',
    occurredAt: minutesBeforeNow(6),
    outcome: 'success',
  },
  {
    id: 'act-2',
    category: 'appointment',
    title: 'Appointment cancelled',
    description: 'Post-operative knee check for Victor Reyes was cancelled.',
    occurredAt: minutesBeforeNow(18),
    outcome: 'success',
  },
  {
    id: 'act-3',
    category: 'fhir',
    title: 'FHIR Patient search',
    description: 'GET /fhir/Patient?family=Carter returned 1 resource.',
    occurredAt: minutesBeforeNow(34),
    outcome: 'success',
  },
  {
    id: 'act-4',
    category: 'hl7',
    title: 'ADT^A08 message rejected',
    description: 'Validation failed: PID-7 (date of birth) is missing.',
    occurredAt: minutesBeforeNow(52),
    outcome: 'failure',
  },
  {
    id: 'act-5',
    category: 'patient',
    title: 'Patient record updated',
    description: 'Contact details updated for Sofia Petrova.',
    occurredAt: minutesBeforeNow(75),
    outcome: 'success',
  },
  {
    id: 'act-6',
    category: 'fhir',
    title: 'PractitionerRole specialty search',
    description: 'Search for specialty "Cardiology" returned 1 resource.',
    occurredAt: minutesBeforeNow(110),
    outcome: 'success',
  },
  {
    id: 'act-7',
    category: 'security',
    title: 'Administrator sign-in',
    description: 'Demo administrator account signed in.',
    occurredAt: minutesBeforeNow(160),
    outcome: 'success',
  },
  {
    id: 'act-8',
    category: 'hl7',
    title: 'ADT^A08 message processed',
    description: 'Demographics update applied for Jamal Carter.',
    occurredAt: minutesBeforeNow(230),
    outcome: 'success',
  },
]

export const demoIntegrationMetrics: DashboardSummary['integration'] = {
  hl7Messages24h: 142,
  hl7Failures24h: 3,
  fhirRequests24h: 1186,
}

/**
 * Synthetic HL7 v2 message volume by hour of day (index 0 = midnight).
 * Sums to `demoIntegrationMetrics.hl7Messages24h`.
 */
export const demoHl7VolumeByHourOfDay: number[] = [
  1, 1, 1, 1, 1, 2, 4, 7, 10, 14, 13, 9, 8, 12, 13, 9, 8, 7, 6, 5, 4, 3, 2, 1,
]

/** How many hours ago each synthetic rejected message arrived. */
export const demoHl7RejectedHoursAgo: number[] = [1, 6, 11]
