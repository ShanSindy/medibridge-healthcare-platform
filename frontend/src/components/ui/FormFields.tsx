import { useId } from 'react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

const controlClasses =
  'block w-full rounded-lg border-0 bg-white py-2 text-sm text-navy-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-brand-600 focus:outline-none disabled:bg-slate-50 disabled:text-slate-500'

interface SearchFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
}

/** Search input with a visually hidden (but accessible) label. */
export function SearchField({ label, value, onChange, placeholder, className }: SearchFieldProps) {
  const id = useId()
  return (
    <div className={cx('relative', className)}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <Icon
        name="search"
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400"
      />
      <input
        id={id}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className={cx(controlClasses, 'pr-3 pl-9')}
      />
    </div>
  )
}

export interface SelectOption<V extends string> {
  value: V
  label: string
}

interface SelectFieldProps<V extends string> {
  label: string
  value: V
  options: ReadonlyArray<SelectOption<V>>
  onChange: (value: V) => void
  /** Hide the label visually (still announced to screen readers). */
  hideLabel?: boolean
  className?: string
  hint?: string
}

export function SelectField<V extends string>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
  className,
  hint,
}: SelectFieldProps<V>) {
  const id = useId()
  const hintId = `${id}-hint`
  return (
    <div className={className}>
      <label htmlFor={id} className={hideLabel ? 'sr-only' : 'mb-1.5 block text-sm font-medium text-navy-800'}>
        {label}
      </label>
      <select
        id={id}
        value={value}
        aria-describedby={hint ? hintId : undefined}
        onChange={(event) => onChange(event.target.value as V)}
        className={cx(controlClasses, 'pr-8 pl-3')}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint && (
        <p id={hintId} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      )}
    </div>
  )
}

interface TextFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  hint?: string
  required?: boolean
  error?: string
  maxLength?: number
  type?: 'text' | 'password'
  autoComplete?: string
  disabled?: boolean
}

export function TextField({
  label,
  value,
  onChange,
  hint,
  required,
  error,
  maxLength,
  type = 'text',
  autoComplete,
  disabled,
}: TextFieldProps) {
  const id = useId()
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-navy-800">
        {label}
        {required && (
          <span className="text-rose-600" aria-hidden="true">
            {' '}
            *
          </span>
        )}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        maxLength={maxLength}
        autoComplete={autoComplete}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
        className={cx(controlClasses, 'px-3', error && 'ring-rose-400 focus:ring-rose-600')}
      />
      {hint && (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1.5 text-xs font-medium text-rose-700">
          {error}
        </p>
      )}
    </div>
  )
}

interface CheckboxFieldProps {
  label: string
  description?: string
  checked: boolean
  onChange: (checked: boolean) => void
}

export function CheckboxField({ label, description, checked, onChange }: CheckboxFieldProps) {
  const id = useId()
  return (
    <div className="flex gap-3">
      <div className="flex h-6 items-center">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby={description ? `${id}-description` : undefined}
          className="size-4 rounded border-slate-300 accent-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
        />
      </div>
      <div className="text-sm">
        <label htmlFor={id} className="font-medium text-navy-800">
          {label}
        </label>
        {description && (
          <p id={`${id}-description`} className="text-slate-500">
            {description}
          </p>
        )}
      </div>
    </div>
  )
}
