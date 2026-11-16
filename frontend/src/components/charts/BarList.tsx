import type { CountSlice } from '../../types/domain'

interface BarListProps {
  rows: CountSlice[]
  /** Fill color for the bars. */
  color: string
}

/**
 * Horizontal share-of-total bars. Every count and percentage is printed, so
 * the bars themselves are decorative.
 */
export function BarList({ rows, color }: BarListProps) {
  const total = rows.reduce((sum, row) => sum + row.count, 0)

  return (
    <dl className="space-y-4">
      {rows.map((row) => {
        const percent = total === 0 ? 0 : Math.round((row.count / total) * 100)
        return (
          <div key={row.label}>
            <div className="flex items-baseline justify-between text-sm">
              <dt className="font-medium text-navy-800">{row.label}</dt>
              <dd className="text-slate-500 tabular-nums">
                <span className="font-semibold text-navy-900">{row.count}</span> · {percent}%
              </dd>
            </div>
            <div aria-hidden="true" className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full transition-[width] duration-300"
                style={{ width: `${percent}%`, backgroundColor: color }}
              />
            </div>
          </div>
        )
      })}
    </dl>
  )
}
