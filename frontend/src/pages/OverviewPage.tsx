import { AppointmentStatusBadge } from '../components/domain/StatusBadges'
import { AsyncContent } from '../components/ui/AsyncContent'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { Icon, type IconName } from '../components/ui/Icon'
import { DemoBadge } from '../components/ui/Misc'
import { StatCard } from '../components/ui/StatCard'
import { EmptyState } from '../components/ui/States'
import { useAsync } from '../hooks/useAsync'
import { DEMO_NOW } from '../lib/clock'
import { cx, focusRing } from '../lib/cx'
import {
  durationMinutes,
  formatLongDate,
  formatNumber,
  formatRelative,
  formatTime,
} from '../lib/format'
import { routeHref } from '../routes'
import { getDashboardOverview } from '../services/dashboard'
import type {
  ActivityCategory,
  ActivityEvent,
  AppointmentDetails,
  AppointmentStatus,
  DashboardOverview,
} from '../types/domain'

export function OverviewPage() {
  const { state, reload } = useAsync(getDashboardOverview)
  return (
    <AsyncContent state={state} onRetry={reload} loadingLabel="Loading dashboard" errorTitle="Unable to load dashboard">
      {(data) => <OverviewContent data={data} />}
    </AsyncContent>
  )
}

function OverviewContent({ data }: { data: DashboardOverview }) {
  const { summary, todaysAppointments, recentActivity } = data

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-slate-600">{formatLongDate(DEMO_NOW)}</p>
        <DemoBadge />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Patients"
          value={formatNumber(summary.patients.total)}
          icon="users"
          detail={`${summary.patients.active} active · ${summary.patients.pending} pending intake`}
          href={routeHref('/patients')}
          linkLabel="View patients"
        />
        <StatCard
          label="Providers"
          value={formatNumber(summary.providers.total)}
          icon="stethoscope"
          detail={`${summary.providers.acceptingNewPatients} accepting new patients`}
          href={routeHref('/providers')}
          linkLabel="View directory"
        />
        <StatCard
          label="Appointments today"
          value={formatNumber(summary.appointments.today)}
          icon="calendar"
          detail={`${summary.appointments.upcomingWeek} more scheduled in the next 7 days`}
          href={routeHref('/appointments')}
          linkLabel="View schedule"
        />
        <StatCard
          label="HL7 messages (24h)"
          value={formatNumber(summary.integration.hl7Messages24h)}
          icon="activity"
          detail={`${summary.integration.hl7Failures24h} failed · ${formatNumber(summary.integration.fhirRequests24h)} FHIR requests`}
          href={routeHref('/hl7')}
          linkLabel="Integration details"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card
          title="Today’s schedule"
          description={`${todaysAppointments.length} appointments across all providers`}
          className="lg:col-span-2"
          bodyClassName="p-0"
          actions={<TextLink href={routeHref('/appointments')}>All appointments</TextLink>}
        >
          <TodaySchedule appointments={todaysAppointments} />
        </Card>

        <Card title="Recent activity" description="Platform and integration events" bodyClassName="p-0">
          <ActivityFeed events={recentActivity} />
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card
          title="Appointments overview"
          description={`Status of ${summary.appointments.total} appointments in the demo schedule`}
        >
          <StatusBreakdown counts={summary.appointments.byStatus} total={summary.appointments.total} />
        </Card>

        <Card
          title="Interoperability readiness"
          description="Backend capabilities and their dashboard integration status"
          className="lg:col-span-2"
          bodyClassName="p-0"
        >
          <IntegrationReadiness />
        </Card>
      </div>
    </div>
  )
}

function TextLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      href={href}
      className={cx(
        'inline-flex items-center gap-1 rounded text-sm font-medium text-brand-700 hover:text-brand-800',
        focusRing,
      )}
    >
      {children}
      <Icon name="chevronRight" className="size-4" />
    </a>
  )
}

function TodaySchedule({ appointments }: { appointments: AppointmentDetails[] }) {
  if (appointments.length === 0) {
    return (
      <EmptyState
        icon="calendar"
        title="No appointments today"
        description="There are no visits on today’s schedule."
      />
    )
  }

  return (
    <ul className="divide-y divide-slate-100">
      {appointments.map((appointment) => (
        <li key={appointment.id} className="flex items-center gap-4 px-5 py-3.5">
          <div className="w-[4.5rem] shrink-0">
            <p className="text-sm font-semibold text-navy-900 tabular-nums">{formatTime(appointment.startsAt)}</p>
            <p className="text-xs text-slate-500">{durationMinutes(appointment.startsAt, appointment.endsAt)} min</p>
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-navy-900">
              {appointment.patientName}
              <span className="ml-2 text-xs font-normal text-slate-400">{appointment.patientMrn}</span>
            </p>
            <p className="truncate text-sm text-slate-500">
              {appointment.reason ?? 'Visit'} · {appointment.providerName}
            </p>
          </div>
          <p className="hidden shrink-0 items-center gap-1.5 text-xs text-slate-500 md:flex">
            <Icon name={appointment.mode === 'telehealth' ? 'video' : 'pin'} className="size-3.5" />
            {appointment.location}
          </p>
          <AppointmentStatusBadge status={appointment.status} />
        </li>
      ))}
    </ul>
  )
}

const ACTIVITY_ICONS: Record<ActivityCategory, IconName> = {
  hl7: 'arrows',
  fhir: 'braces',
  appointment: 'calendar',
  patient: 'users',
  security: 'shield',
}

function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) {
    return <EmptyState icon="activity" title="No recent activity" description="New events will appear here." />
  }

  return (
    <ol className="divide-y divide-slate-100">
      {events.map((event) => {
        const failed = event.outcome === 'failure'
        return (
          <li key={event.id} className="flex gap-3 px-5 py-3.5">
            <span
              className={cx(
                'mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg',
                failed ? 'bg-rose-50 text-rose-600' : 'bg-slate-100 text-navy-600',
              )}
            >
              <Icon name={ACTIVITY_ICONS[event.category]} className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <p className="text-sm font-medium text-navy-900">{event.title}</p>
                {failed && <Badge tone="danger">Failed</Badge>}
              </div>
              <p className="mt-0.5 text-sm text-slate-500">{event.description}</p>
              <p className="mt-1 text-xs text-slate-400">
                <time dateTime={event.occurredAt}>{formatRelative(event.occurredAt)}</time>
              </p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

const STATUS_ROWS: Array<{ status: AppointmentStatus; label: string }> = [
  { status: 'scheduled', label: 'Scheduled' },
  { status: 'completed', label: 'Completed' },
  { status: 'cancelled', label: 'Cancelled' },
]

/** Single-series magnitude bars; every value is printed, so bars are decorative. */
function StatusBreakdown({ counts, total }: { counts: Record<AppointmentStatus, number>; total: number }) {
  return (
    <dl className="space-y-4">
      {STATUS_ROWS.map(({ status, label }) => {
        const count = counts[status]
        const percent = total === 0 ? 0 : Math.round((count / total) * 100)
        return (
          <div key={status}>
            <div className="flex items-baseline justify-between text-sm">
              <dt className="font-medium text-navy-800">{label}</dt>
              <dd className="text-slate-500 tabular-nums">
                <span className="font-semibold text-navy-900">{count}</span> · {percent}%
              </dd>
            </div>
            <div aria-hidden="true" className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-brand-500" style={{ width: `${percent}%` }} />
            </div>
          </div>
        )
      })}
    </dl>
  )
}

const READINESS: Array<{ icon: IconName; name: string; detail: string; ready: boolean; href?: string }> = [
  {
    icon: 'braces',
    name: 'FHIR R4 API',
    detail: 'CapabilityStatement, Patient, Practitioner and PractitionerRole endpoints.',
    ready: true,
    href: '/fhir',
  },
  {
    icon: 'arrows',
    name: 'HL7 v2 ADT intake',
    detail: 'ADT^A04 registrations and ADT^A08 updates via POST /hl7/adt.',
    ready: true,
    href: '/hl7',
  },
  {
    icon: 'shield',
    name: 'Audit logging',
    detail: 'GET /audit-logs, restricted to the audit:read permission.',
    ready: true,
    href: '/audit-logs',
  },
  {
    icon: 'lock',
    name: 'Dashboard authentication & live data',
    detail: 'This dashboard currently uses synthetic in-browser data only.',
    ready: false,
  },
]

function IntegrationReadiness() {
  return (
    <ul className="divide-y divide-slate-100">
      {READINESS.map((item) => (
        <li key={item.name} className="flex items-center gap-4 px-5 py-3.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-navy-600">
            <Icon name={item.icon} className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            {item.href ? (
              <a
                href={routeHref(item.href)}
                className={cx('rounded text-sm font-medium text-navy-900 hover:text-brand-700', focusRing)}
              >
                {item.name}
              </a>
            ) : (
              <p className="text-sm font-medium text-navy-900">{item.name}</p>
            )}
            <p className="text-sm text-slate-500">{item.detail}</p>
          </div>
          {item.ready ? (
            <Badge tone="brand" dot>
              Backend ready
            </Badge>
          ) : (
            <Badge tone="warning" dot>
              Planned
            </Badge>
          )}
        </li>
      ))}
    </ul>
  )
}
