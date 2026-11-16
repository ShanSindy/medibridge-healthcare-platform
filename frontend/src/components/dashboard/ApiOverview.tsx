import { useAsync, type AsyncState } from '../../hooks/useAsync'
import { useAuthSession } from '../../hooks/useAuthSession'
import { cx, focusRing } from '../../lib/cx'
import { formatNumber } from '../../lib/format'
import { routeHref } from '../../routes'
import { getAccessToken } from '../../services/auth'
import { patientDemographics } from '../../services/dashboardTrends'
import { isApiError } from '../../services/http'
import { listPatients } from '../../services/patients'
import { getSystemStatus } from '../../services/systemStatus'
import type { PatientDemographics } from '../../types/domain'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { Callout } from '../ui/Misc'
import { StatCard } from '../ui/StatCard'
import { EmptyState, ErrorState, LoadingState } from '../ui/States'
import { DashboardToolbar } from './DashboardToolbar'
import { SystemStatusCard } from './SystemStatusCard'
import { DemographicsCard } from './TrendCards'

interface ApiPatientMetrics {
  total: number
  demographics: PatientDemographics
}

/** Resolves to null when nobody is signed in, so no request is made. */
async function fetchPatientMetrics(): Promise<ApiPatientMetrics | null> {
  if (!getAccessToken()) return null
  const patients = await listPatients({ source: 'api' })
  return { total: patients.length, demographics: patientDemographics(patients, new Date()) }
}

const fetchApiStatus = () => getSystemStatus({ source: 'api' })

/**
 * Dashboard for API mode. It shows only what the backend actually returns:
 * patient totals and demographics from `GET /patients`, and API reachability
 * from `GET /health`. Nothing is filled in from demo data.
 */
export function ApiOverview() {
  const { session } = useAuthSession()
  // Keyed by token so signing in or out refetches.
  return <ApiOverviewContent key={session?.accessToken ?? 'signed-out'} />
}

function ApiOverviewContent() {
  const metrics = useAsync(fetchPatientMetrics)
  const status = useAsync(fetchApiStatus)
  const refreshing = metrics.refreshing || status.refreshing
  // While signed out nothing is requested, so there is no live refresh to report.
  const signedOut = metrics.state.status === 'success' && metrics.state.data === null

  function refreshAll() {
    metrics.refresh()
    status.refresh()
  }

  return (
    <div className="space-y-6">
      <DashboardToolbar
        lastRefreshedAt={signedOut ? null : metrics.lastSuccessAt}
        refreshing={refreshing}
        disabled={metrics.state.status === 'loading' || refreshing}
        onRefresh={refreshAll}
        badge={
          <Badge tone="brand" dot>
            Live API data
          </Badge>
        }
      />

      <PatientMetrics state={metrics.state} onRetry={metrics.reload} />

      <Callout title="Some dashboard sections are not available in API mode">
        The backend has no summary, trend or activity-feed endpoints, and this dashboard does not yet load providers,
        appointments or HL7 message history from the API. Those sections are left out here rather than filled with
        demo data.
      </Callout>

      <SystemStatusCard state={status.state} checkedAt={status.lastSuccessAt} />
    </div>
  )
}

interface PatientMetricsProps {
  state: AsyncState<ApiPatientMetrics | null>
  onRetry: () => void
}

function PatientMetrics({ state, onRetry }: PatientMetricsProps) {
  if (state.status === 'loading') return <LoadingState label="Loading patient metrics from the MediBridge API" />

  if (state.status === 'error') {
    if (isApiError(state.error, 'forbidden')) {
      return (
        <Card>
          <EmptyState
            icon="lock"
            title="Your account can’t view patient metrics"
            description="Patient totals require the admin, clinician or scheduler role."
          />
        </Card>
      )
    }
    // A 401 ends the session, which remounts this view in its signed-out state.
    return <ErrorState title="Unable to load patient metrics" message={state.error.message} onRetry={onRetry} />
  }

  if (state.data === null) {
    return (
      <Card>
        <EmptyState
          icon="lock"
          title="Sign in to load live metrics"
          description="Patient totals and demographics come from the MediBridge API and need an authenticated session."
          action={
            <a
              href={routeHref('/patients')}
              className={cx(
                'inline-flex items-center rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-700',
                focusRing,
              )}
            >
              Go to sign in
            </a>
          }
        />
      </Card>
    )
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <StatCard
        label="Total patients"
        value={formatNumber(state.data.total)}
        icon="users"
        detail="Records returned by GET /patients"
        href={routeHref('/patients')}
        linkLabel="View patients"
      />
      <div className="lg:col-span-2">
        <DemographicsCard demographics={state.data.demographics} />
      </div>
    </div>
  )
}
