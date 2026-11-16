import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface Column<T> {
  key: string
  header: string
  cell: (row: T) => ReactNode
  className?: string
  /** Hide the visible header text (it stays available to screen readers). */
  hideHeader?: boolean
}

interface DataTableProps<T> {
  /** Describes the table for assistive technology. */
  caption: string
  columns: Column<T>[]
  rows: T[]
  getRowKey: (row: T) => string | number
  /** Rendered in place of the body when `rows` is empty. */
  emptyState: ReactNode
  minWidth?: string
}

/**
 * Semantic, horizontally scrollable table.
 * The scroll region is keyboard-focusable so it can be scrolled without a mouse.
 */
export function DataTable<T>({ caption, columns, rows, getRowKey, emptyState, minWidth = '760px' }: DataTableProps<T>) {
  if (rows.length === 0) return <>{emptyState}</>

  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="overflow-x-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-600"
    >
      <table className="w-full border-collapse text-left text-sm" style={{ minWidth }}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/70">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cx(
                  'px-5 py-3 text-xs font-semibold tracking-wide whitespace-nowrap text-slate-500 uppercase',
                  column.className,
                )}
              >
                <span className={column.hideHeader ? 'sr-only' : undefined}>{column.header}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={getRowKey(row)} className="transition-colors hover:bg-slate-50/70">
              {columns.map((column) => (
                <td key={column.key} className={cx('px-5 py-3.5 align-middle text-navy-800', column.className)}>
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
