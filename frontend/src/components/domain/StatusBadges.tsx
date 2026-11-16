import type { AppointmentStatus, PatientStatus } from '../../types/domain'
import { Badge, type BadgeTone } from '../ui/Badge'

const PATIENT_STATUS: Record<PatientStatus, { label: string; tone: BadgeTone }> = {
  active: { label: 'Active', tone: 'success' },
  pending: { label: 'Pending intake', tone: 'warning' },
  inactive: { label: 'Inactive', tone: 'neutral' },
}

const APPOINTMENT_STATUS: Record<AppointmentStatus, { label: string; tone: BadgeTone }> = {
  scheduled: { label: 'Scheduled', tone: 'info' },
  completed: { label: 'Completed', tone: 'success' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}

export function PatientStatusBadge({ status }: { status: PatientStatus }) {
  const { label, tone } = PATIENT_STATUS[status]
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  )
}

export function AppointmentStatusBadge({ status }: { status: AppointmentStatus }) {
  const { label, tone } = APPOINTMENT_STATUS[status]
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  )
}
