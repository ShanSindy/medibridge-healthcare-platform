import { useId, type ReactNode } from 'react'
import { cx } from '../../lib/cx'

interface CardProps {
  title?: string
  description?: ReactNode
  /** Controls rendered on the right of the header (filters, links, badges). */
  actions?: ReactNode
  children: ReactNode
  className?: string
  /** Override body padding, e.g. `p-0` for edge-to-edge tables. */
  bodyClassName?: string
}

export function Card({ title, description, actions, children, className, bodyClassName = 'p-5' }: CardProps) {
  const headingId = useId()

  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cx(
        'rounded-xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)]',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            {title && (
              <h2 id={headingId} className="text-[15px] font-semibold text-navy-900">
                {title}
              </h2>
            )}
            {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}
