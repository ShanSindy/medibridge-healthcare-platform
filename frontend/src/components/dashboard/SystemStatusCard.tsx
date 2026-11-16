import type { AsyncState } from '../../hooks/useAsync'
import { formatClock } from '../../lib/format'
import type { CheckState, SystemStatus } from '../../services/systemStatus'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Card } from '../ui/Card'

const STATE_BADGES: Record<CheckState, { tone: BadgeTone; label: string }> = {
  operational: { tone: 'success', label: 'Operational' },
  unavailable: { tone: 'danger', label: 'Unavailable' },
  not_connected: { tone: 'neutral', label: 'Not connected' },
  not_reported: { tone: 'neutral', label: 'Not reported' },
}

interface SystemStatusCardProps {
  state: AsyncState<SystemStatus>
  /** When the checks last completed, or null if they have not yet. */
  checkedAt: number | null
}

export function SystemStatusCard({ state, checkedAt }: SystemStatusCardProps) {
  return (
    <Card
      title="System status"
      description={checkedAt === null ? 'Not checked yet' : `Last checked ${formatClock(checkedAt)}`}
      bodyClassName="p-0"
    >
      {state.status === 'loading' && (
        <p role="status" className="px-5 py-6 text-sm text-slate-500">
          Checking services…
        </p>
      )}

      {state.status === 'error' && (
        <p role="alert" className="px-5 py-6 text-sm text-rose-700">
          Status checks could not run: {state.error.message}
        </p>
      )}

      {state.status === 'success' && (
        <ul className="divide-y divide-slate-100">
          {state.data.checks.map((check) => {
            const badge = STATE_BADGES[check.state]
            return (
              <li key={check.id} className="flex items-start justify-between gap-3 px-5 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-navy-900">{check.name}</p>
                  <p className="text-xs text-slate-500">{check.detail}</p>
                </div>
                <Badge tone={badge.tone} dot className="shrink-0">
                  {badge.label}
                </Badge>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
