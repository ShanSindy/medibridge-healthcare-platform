import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

interface StatCardProps {
  label: string
  value: string
  icon: IconName
  /** Short supporting context shown under the value. */
  detail: ReactNode
  href?: string
  linkLabel?: string
}

/** A single headline metric. Deliberately a number, not a chart. */
export function StatCard({ label, value, icon, detail, href, linkLabel }: StatCardProps) {
  return (
    <div className="flex flex-col rounded-xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <span className="flex size-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700">
          <Icon name={icon} className="size-[18px]" />
        </span>
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-navy-900 tabular-nums">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{detail}</p>
      {href && linkLabel && (
        <a
          href={href}
          className="mt-4 inline-flex items-center gap-1 self-start rounded text-sm font-medium text-brand-700 hover:text-brand-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
        >
          {linkLabel}
          <Icon name="chevronRight" className="size-4" />
        </a>
      )}
    </div>
  )
}
