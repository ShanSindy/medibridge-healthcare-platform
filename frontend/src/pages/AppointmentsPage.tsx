import { useDeferredValue, useMemo, useState } from 'react'
import { AppointmentStatusBadge } from '../components/domain/StatusBadges'
import { AsyncContent } from '../components/ui/AsyncContent'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { DataTable, type Column } from '../components/ui/DataTable'
import { SearchField, SelectField, type SelectOption } from '../components/ui/FormFields'
import { Icon } from '../components/ui/Icon'
import { DemoBadge, ResultCount } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'
import { useAsync } from '../hooks/useAsync'
import { DEMO_NOW, isSameLocalDay } from '../lib/clock'
import { formatTimeRange, formatWeekdayDate, matchesSearch } from '../lib/format'
import { listAppointments } from '../services/appointments'
import type { AppointmentDetails, AppointmentStatus } from '../types/domain'

type StatusFilter = 'all' | AppointmentStatus
type RangeFilter = 'all' | 'today' | 'upcoming' | 'past'

const STATUS_OPTIONS: SelectOption<StatusFilter>[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const RANGE_OPTIONS: SelectOption<RangeFilter>[] = [
  { value: 'all', label: 'All dates' },
  { value: 'today', label: 'Today' },
  { value: 'upcoming', label: 'Upcoming' },
  { value: 'past', label: 'Past' },
]

function inRange(appointment: AppointmentDetails, range: RangeFilter): boolean {
  const start = new Date(appointment.startsAt)
  switch (range) {
    case 'today':
      return isSameLocalDay(start, DEMO_NOW)
    case 'upcoming':
      return start >= DEMO_NOW
    case 'past':
      return start < DEMO_NOW
    default:
      return true
  }
}

const COLUMNS: Column<AppointmentDetails>[] = [
  {
    key: 'when',
    header: 'Date & time',
    cell: (appointment) => (
      <div>
        <p className="font-medium whitespace-nowrap text-navy-900">
          <time dateTime={appointment.startsAt}>{formatWeekdayDate(appointment.startsAt)}</time>
        </p>
        <p className="text-xs whitespace-nowrap text-slate-500 tabular-nums">
          {formatTimeRange(appointment.startsAt, appointment.endsAt)}
        </p>
      </div>
    ),
  },
  {
    key: 'patient',
    header: 'Patient',
    cell: (appointment) => (
      <div>
        <p className="font-medium text-navy-900">{appointment.patientName}</p>
        <p className="text-xs text-slate-500">{appointment.patientMrn}</p>
      </div>
    ),
  },
  {
    key: 'provider',
    header: 'Provider',
    cell: (appointment) => (
      <div>
        <p>{appointment.providerName}</p>
        <p className="text-xs text-slate-500">{appointment.providerSpecialty}</p>
      </div>
    ),
  },
  {
    key: 'reason',
    header: 'Reason',
    cell: (appointment) => appointment.reason ?? <span className="text-slate-400">Not specified</span>,
  },
  {
    key: 'visit',
    header: 'Visit type',
    cell: (appointment) => (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-slate-600">
        <Icon name={appointment.mode === 'telehealth' ? 'video' : 'pin'} className="size-4 text-slate-400" />
        {appointment.location}
      </span>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    cell: (appointment) => <AppointmentStatusBadge status={appointment.status} />,
  },
]

export function AppointmentsPage() {
  const { state, reload } = useAsync(listAppointments)
  return (
    <AsyncContent
      state={state}
      onRetry={reload}
      loadingLabel="Loading appointments"
      errorTitle="Unable to load appointments"
    >
      {(appointments) => <AppointmentSchedule appointments={appointments} />}
    </AsyncContent>
  )
}

function AppointmentSchedule({ appointments }: { appointments: AppointmentDetails[] }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [range, setRange] = useState<RangeFilter>('all')
  const deferredQuery = useDeferredValue(query)

  const filtered = useMemo(
    () =>
      appointments.filter(
        (appointment) =>
          (status === 'all' || appointment.status === status) &&
          inRange(appointment, range) &&
          matchesSearch(
            deferredQuery,
            appointment.patientName,
            appointment.patientMrn,
            appointment.providerName,
            appointment.providerSpecialty,
            appointment.reason,
          ),
      ),
    [appointments, status, range, deferredQuery],
  )

  const hasFilters = query !== '' || status !== 'all' || range !== 'all'
  const clearFilters = () => {
    setQuery('')
    setStatus('all')
    setRange('all')
  }

  return (
    <Card
      title="Appointment schedule"
      description="Synthetic visits generated relative to today’s date."
      actions={<DemoBadge />}
      bodyClassName="p-0"
    >
      <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 lg:flex-row lg:items-center">
        <SearchField
          label="Search appointments"
          placeholder="Search patient, provider or reason"
          value={query}
          onChange={setQuery}
          className="lg:max-w-sm lg:flex-1"
        />
        <div className="grid grid-cols-2 gap-3 lg:flex">
          <SelectField
            label="Filter by date range"
            hideLabel
            value={range}
            options={RANGE_OPTIONS}
            onChange={setRange}
            className="lg:w-40"
          />
          <SelectField
            label="Filter by status"
            hideLabel
            value={status}
            options={STATUS_OPTIONS}
            onChange={setStatus}
            className="lg:w-44"
          />
        </div>
        <div className="lg:ml-auto">
          <ResultCount shown={filtered.length} total={appointments.length} noun="appointments" />
        </div>
      </div>

      <DataTable
        caption="Appointments (synthetic demo data)"
        columns={COLUMNS}
        rows={filtered}
        getRowKey={(appointment) => appointment.id}
        minWidth="980px"
        emptyState={
          <EmptyState
            icon="calendar"
            title={hasFilters ? 'No appointments match your filters' : 'No appointments scheduled'}
            description={
              hasFilters ? 'Adjust the date range, status or search terms.' : 'Booked appointments will appear here.'
            }
            action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        }
      />
    </Card>
  )
}
