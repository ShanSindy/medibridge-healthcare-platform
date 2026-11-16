import { cx, focusRing } from '../../lib/cx'

interface SegmentedControlProps<T extends string> {
  /** Accessible name for the group, e.g. "Breakdown". */
  label: string
  options: Array<{ value: T; label: string }>
  value: T
  onChange: (value: T) => void
}

/** Compact switch between a few views of the same data. */
export function SegmentedControl<T extends string>({ label, options, value, onChange }: SegmentedControlProps<T>) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg bg-slate-100 p-0.5">
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={cx(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
              focusRing,
              selected ? 'bg-white text-navy-900 shadow-sm' : 'text-slate-600 hover:text-navy-900',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
