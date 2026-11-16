import type { ReactNode } from 'react'
import { DEMO_NOW } from '../../lib/clock'
import { cx } from '../../lib/cx'
import { formatClock, formatLongDate } from '../../lib/format'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icon'

interface DashboardToolbarProps {
  /** When the dashboard data last loaded successfully, or null if it has not. */
  lastRefreshedAt: number | null
  refreshing: boolean
  disabled: boolean
  onRefresh: () => void
  /** Data-source badge shown beside the refresh button. */
  badge: ReactNode
}

export function DashboardToolbar({ lastRefreshedAt, refreshing, disabled, onRefresh, badge }: DashboardToolbarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <p className="font-medium text-slate-600">{formatLongDate(DEMO_NOW)}</p>
        <p className="text-slate-500" aria-live="polite">
          {lastRefreshedAt === null ? 'No successful refresh yet' : `Last refreshed ${formatClock(lastRefreshedAt)}`}
        </p>
      </div>
      <div className="flex items-center gap-3">
        {badge}
        <Button onClick={onRefresh} disabled={disabled}>
          <Icon name="refresh" className={cx('size-4', refreshing && 'animate-spin')} />
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>
    </div>
  )
}
