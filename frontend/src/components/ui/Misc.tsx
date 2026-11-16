import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { Badge } from './Badge'
import { Icon } from './Icon'

/** Compact label marking content as synthetic demo data. */
export function DemoBadge() {
  return (
    <Badge tone="warning" dot>
      Demo data
    </Badge>
  )
}

interface CalloutProps {
  title: string
  children: ReactNode
  tone?: 'info' | 'warning'
  className?: string
}

export function Callout({ title, children, tone = 'info', className }: CalloutProps) {
  const styles =
    tone === 'warning'
      ? 'border-amber-200 bg-amber-50/70 text-amber-900 [&_svg]:text-amber-600'
      : 'border-sky-200 bg-sky-50/70 text-sky-950 [&_svg]:text-sky-600'
  return (
    <div className={cx('flex gap-3 rounded-xl border px-4 py-3.5 text-sm', styles, className)}>
      <Icon name={tone === 'warning' ? 'alert' : 'info'} className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-semibold">{title}</p>
        <div className="mt-0.5 opacity-90">{children}</div>
      </div>
    </div>
  )
}

interface AvatarProps {
  initials: string
  size?: 'sm' | 'md' | 'lg'
}

export function Avatar({ initials, size = 'md' }: AvatarProps) {
  const sizes = { sm: 'size-8 text-xs', md: 'size-9 text-xs', lg: 'size-12 text-sm' }
  return (
    <span
      aria-hidden="true"
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-full bg-navy-100 font-semibold text-navy-700',
        sizes[size],
      )}
    >
      {initials}
    </span>
  )
}

interface CodeBlockProps {
  label: string
  code: string
}

/** Scrollable, keyboard-focusable code sample. */
export function CodeBlock({ label, code }: CodeBlockProps) {
  return (
    <figure className="overflow-hidden rounded-lg border border-navy-800 bg-navy-950">
      <figcaption className="border-b border-navy-800 px-4 py-2 text-xs font-medium text-navy-300">{label}</figcaption>
      <pre
        tabIndex={0}
        className="overflow-x-auto p-4 font-mono text-[12.5px] leading-relaxed text-navy-100 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-400"
      >
        <code>{code}</code>
      </pre>
    </figure>
  )
}

interface ResultCountProps {
  shown: number
  total: number
  noun: string
}

export function ResultCount({ shown, total, noun }: ResultCountProps) {
  return (
    <p className="text-sm text-slate-500" aria-live="polite">
      Showing <span className="font-medium text-navy-800 tabular-nums">{shown}</span> of{' '}
      <span className="tabular-nums">{total}</span> {noun}
    </p>
  )
}
