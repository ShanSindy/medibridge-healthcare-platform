import { useState } from 'react'
import { cx } from '../../lib/cx'

export interface BarSeries {
  key: string
  label: string
  color: string
}

export interface BarDatum {
  id: string
  /** Short label under the bar. */
  axisLabel: string
  /** Full label used in the tooltip and the accessible table. */
  title: string
  values: Record<string, number>
  /** Marks a reference bar such as "today". */
  highlighted?: boolean
}

interface BarChartProps {
  data: BarDatum[]
  /** Stacked bottom-to-top in this order. */
  series: BarSeries[]
  /** Accessible summary of what the chart shows. */
  label: string
  /** Column header for the category in the accessible table, e.g. "Day". */
  categoryLabel: string
  /** Show an axis label on every nth bar. */
  labelEvery?: number
}

function niceScale(max: number): { top: number; ticks: number[] } {
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
  const step = steps.find((candidate) => max / candidate <= 4) ?? Math.ceil(max / 4)
  const top = Math.max(step, Math.ceil(max / step) * step)
  const ticks: number[] = []
  for (let value = 0; value <= top; value += step) ticks.push(value)
  return { top, ticks }
}

/**
 * Responsive stacked bar chart built from plain elements: no chart library
 * and no fixed pixel width. Hovering a column shows its values; the same
 * numbers are available to assistive technology as a table.
 */
export function BarChart({ data, series, label, categoryLabel, labelEvery = 1 }: BarChartProps) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null)

  const totals = data.map((datum) => series.reduce((sum, item) => sum + (datum.values[item.key] ?? 0), 0))
  const { top, ticks } = niceScale(Math.max(1, ...totals))
  const active = activeIndex === null ? undefined : data[activeIndex]
  // Anchor the label rhythm on the highlighted bar so its label never collides with a neighbor.
  const labelAnchor = Math.max(0, data.findIndex((datum) => datum.highlighted))

  return (
    <figure className="m-0">
      {series.length > 1 && (
        <ul className="mb-4 flex flex-wrap gap-x-4 gap-y-1">
          {series.map((item) => (
            <li key={item.key} className="flex items-center gap-1.5 text-xs text-slate-600">
              <span aria-hidden="true" className="size-2.5 rounded-sm" style={{ backgroundColor: item.color }} />
              {item.label}
            </li>
          ))}
        </ul>
      )}

      <div role="img" aria-label={label} className="flex gap-2">
        <div aria-hidden="true" className="relative h-44 w-6 shrink-0 text-[11px] text-slate-400 tabular-nums">
          {ticks.map((tick) => (
            <span
              key={tick}
              className="absolute right-0 translate-y-1/2 leading-none"
              style={{ bottom: `${(tick / top) * 100}%` }}
            >
              {tick}
            </span>
          ))}
        </div>

        <div className="min-w-0 flex-1">
          <div className="relative h-44" onPointerLeave={() => setActiveIndex(null)}>
            {ticks.map((tick) => (
              <span
                key={tick}
                aria-hidden="true"
                className={cx('absolute inset-x-0 border-t', tick === 0 ? 'border-slate-300' : 'border-slate-100')}
                style={{ bottom: `${(tick / top) * 100}%` }}
              />
            ))}

            <div className="absolute inset-0 flex items-end gap-1">
              {data.map((datum, index) => {
                const visible = series.filter((item) => (datum.values[item.key] ?? 0) > 0)
                return (
                  <div
                    key={datum.id}
                    className={cx(
                      'flex h-full min-w-0 flex-1 items-end justify-center rounded-t-md transition-colors',
                      activeIndex === index && 'bg-slate-100/80',
                    )}
                    onPointerEnter={() => setActiveIndex(index)}
                    onPointerDown={() => setActiveIndex(index)}
                  >
                    <div
                      className="flex w-full max-w-8 flex-col-reverse gap-0.5"
                      style={{ height: `${(totals[index] / top) * 100}%` }}
                    >
                      {visible.map((item, position) => (
                        <span
                          key={item.key}
                          className={cx('w-full', position === visible.length - 1 && 'rounded-t')}
                          style={{ flexGrow: datum.values[item.key], flexBasis: 0, backgroundColor: item.color }}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>

            {active && activeIndex !== null && (
              <div
                aria-hidden="true"
                className={cx(
                  'pointer-events-none absolute top-0 z-10 w-max max-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg',
                  activeIndex < data.length / 2 ? 'translate-x-2' : '-translate-x-full',
                )}
                style={{
                  left: `${((activeIndex + (activeIndex < data.length / 2 ? 1 : 0)) / data.length) * 100}%`,
                }}
              >
                <p className="font-semibold text-navy-900">{active.title}</p>
                <ul className="mt-1 space-y-0.5">
                  {series.map((item) => (
                    <li key={item.key} className="flex items-center gap-1.5 text-slate-600">
                      <span className="size-2 rounded-sm" style={{ backgroundColor: item.color }} />
                      <span className="flex-1">{item.label}</span>
                      <span className="pl-3 font-medium text-navy-900 tabular-nums">{active.values[item.key] ?? 0}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div aria-hidden="true" className="mt-2 flex gap-1 text-[11px] text-slate-500">
            {data.map((datum, index) => (
              <span
                key={datum.id}
                className={cx(
                  'flex min-w-0 flex-1 justify-center whitespace-nowrap',
                  datum.highlighted && 'font-semibold text-navy-900',
                )}
              >
                {(index - labelAnchor) % labelEvery === 0 ? datum.axisLabel : ''}
              </span>
            ))}
          </div>
        </div>
      </div>

      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">{categoryLabel}</th>
            {series.map((item) => (
              <th key={item.key} scope="col">
                {item.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((datum) => (
            <tr key={datum.id}>
              <th scope="row">{datum.title}</th>
              {series.map((item) => (
                <td key={item.key}>{datum.values[item.key] ?? 0}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}
