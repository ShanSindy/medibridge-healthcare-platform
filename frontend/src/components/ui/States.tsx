import type { ReactNode } from 'react'
import { Button } from './Button'
import { Icon, type IconName } from './Icon'

interface LoadingStateProps {
  label: string
  /** Number of skeleton rows to render. */
  rows?: number
}

export function LoadingState({ label, rows = 6 }: LoadingStateProps) {
  return (
    <div role="status" aria-live="polite" className="rounded-xl border border-slate-200/80 bg-white p-5">
      <span className="sr-only">{label}…</span>
      <div aria-hidden="true" className="animate-pulse space-y-4">
        <div className="h-4 w-1/3 rounded bg-slate-200" />
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="flex items-center gap-4">
            <div className="size-9 shrink-0 rounded-full bg-slate-100" />
            <div className="h-3 flex-1 rounded bg-slate-100" />
            <div className="hidden h-3 w-24 rounded bg-slate-100 sm:block" />
            <div className="hidden h-3 w-16 rounded bg-slate-100 md:block" />
          </div>
        ))}
      </div>
    </div>
  )
}

interface EmptyStateProps {
  icon?: IconName
  title: string
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ icon = 'inbox', title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <Icon name={icon} className="size-5" />
      </div>
      <h3 className="mt-4 text-sm font-semibold text-navy-900">{title}</h3>
      {description && <p className="mt-1 max-w-md text-sm text-slate-500">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
}

export function ErrorState({ title = 'Unable to load data', message, onRetry }: ErrorStateProps) {
  return (
    <div role="alert" className="rounded-xl border border-rose-200 bg-white px-6 py-10 text-center">
      <div className="mx-auto flex size-11 items-center justify-center rounded-full bg-rose-50 text-rose-600">
        <Icon name="alert" className="size-5" />
      </div>
      <h2 className="mt-4 text-sm font-semibold text-navy-900">{title}</h2>
      <p className="mx-auto mt-1 max-w-lg text-sm text-slate-600">{message}</p>
      {onRetry && (
        <Button className="mt-5" onClick={onRetry}>
          <Icon name="refresh" className="size-4" />
          Try again
        </Button>
      )}
    </div>
  )
}
