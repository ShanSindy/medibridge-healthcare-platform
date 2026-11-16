import { useCallback, useEffect, useId, useRef, type MouseEvent, type ReactNode } from 'react'
import type { DataSource } from '../../config/api'
import { useAsync } from '../../hooks/useAsync'
import { cx, focusRing } from '../../lib/cx'
import { formatDate, formatTime, initials, providerName } from '../../lib/format'
import { isApiError } from '../../services/http'
import { getProvider } from '../../services/providers'
import type { Provider } from '../../types/domain'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icon'
import { Avatar, DemoBadge } from '../ui/Misc'

interface ProviderDetailDialogProps {
  /** The directory entry that was opened; shown immediately while details load. */
  provider: Provider
  source: DataSource
  onClose: () => void
  /** Reloads the directory, e.g. after discovering the provider was removed. */
  onRefreshDirectory: () => void
}

/**
 * Modal provider record. In API mode it re-reads the provider with
 * `GET /providers/{id}` so the details are current, not a copy of the list.
 */
export function ProviderDetailDialog({ provider, source, onClose, onRefreshDirectory }: ProviderDetailDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const fetchProvider = useCallback(() => getProvider(provider.id, { source }), [provider.id, source])
  const { state, reload } = useAsync(fetchProvider)

  // `showModal` provides the focus trap, Escape handling and inert background.
  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog && !dialog.open) dialog.showModal()
  }, [])

  const close = () => dialogRef.current?.close()

  const closeOnBackdrop = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target === dialogRef.current) close()
  }

  // Prefer the freshly loaded record; fall back to the directory entry for the header.
  const current = state.status === 'success' ? state.data : provider
  const name = providerName(current)

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={closeOnBackdrop}
      className="m-auto w-full max-w-lg rounded-xl bg-white p-0 text-navy-900 shadow-xl backdrop:bg-navy-950/40 backdrop:backdrop-blur-[2px] max-sm:max-w-[calc(100vw-2rem)]"
    >
      <div className="flex items-start gap-3 border-b border-slate-100 px-5 py-4">
        <Avatar initials={initials(current.firstName, current.lastName)} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-[17px] font-semibold text-navy-900">
            {name}
          </h2>
          <p className="text-sm text-slate-500">{current.credentials ?? `Provider ID ${current.id}`}</p>
        </div>
        <button
          type="button"
          onClick={close}
          className={cx('rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-navy-900', focusRing)}
        >
          <Icon name="close" className="size-5" />
          <span className="sr-only">Close provider details</span>
        </button>
      </div>

      <div className="px-5 py-4">
        {state.status === 'loading' && (
          <div role="status" aria-live="polite">
            <span className="sr-only">Loading provider details…</span>
            <div aria-hidden="true" className="animate-pulse space-y-4 py-1">
              {Array.from({ length: 4 }, (_, index) => (
                <div key={index} className="flex gap-4">
                  <div className="h-3 w-24 rounded bg-slate-200" />
                  <div className="h-3 flex-1 rounded bg-slate-100" />
                </div>
              ))}
            </div>
          </div>
        )}

        {state.status === 'error' && (
          <div role="alert" className="py-2 text-center">
            <div className="mx-auto flex size-11 items-center justify-center rounded-full bg-rose-50 text-rose-600">
              <Icon name="alert" className="size-5" />
            </div>
            <p className="mt-3 text-sm font-semibold text-navy-900">Unable to load provider details</p>
            <p className="mx-auto mt-1 max-w-sm text-sm text-slate-600">{state.error.message}</p>
            <div className="mt-4">
              {isApiError(state.error, 'not_found') ? (
                <Button
                  onClick={() => {
                    onRefreshDirectory()
                    close()
                  }}
                >
                  <Icon name="refresh" className="size-4" />
                  Refresh directory
                </Button>
              ) : (
                <Button onClick={reload}>
                  <Icon name="refresh" className="size-4" />
                  Try again
                </Button>
              )}
            </div>
          </div>
        )}

        {state.status === 'success' && <ProviderFields provider={state.data} />}
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
        {source === 'api' ? (
          <Badge tone="brand" dot>
            Live API data
          </Badge>
        ) : (
          <DemoBadge />
        )}
        <Button onClick={close}>Close</Button>
      </div>
    </dialog>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 py-2.5">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="col-span-2 min-w-0 text-sm break-words text-navy-900">{children}</dd>
    </div>
  )
}

function NotProvided() {
  return <span className="text-slate-400">Not provided</span>
}

function ProviderFields({ provider }: { provider: Provider }) {
  const linkClasses = cx('rounded text-brand-700 hover:text-brand-800 hover:underline', focusRing)
  const fromApi = provider.recordSource === 'api'

  return (
    <>
      <dl className="divide-y divide-slate-100">
        <Field label="Specialty">{provider.specialty}</Field>
        <Field label="Email">
          {provider.email ? (
            <a href={`mailto:${provider.email}`} className={linkClasses}>
              {provider.email}
            </a>
          ) : (
            <NotProvided />
          )}
        </Field>
        <Field label="Phone">
          {provider.phone ? (
            <a href={`tel:${provider.phone.replace(/[^\d+]/g, '')}`} className={cx(linkClasses, 'tabular-nums')}>
              {provider.phone}
            </a>
          ) : (
            <NotProvided />
          )}
        </Field>
        {provider.department !== null && <Field label="Department">{provider.department}</Field>}
        {provider.location !== null && <Field label="Location">{provider.location}</Field>}
        {provider.acceptingNewPatients !== null && (
          <Field label="New patients">
            {provider.acceptingNewPatients ? (
              <Badge tone="success" dot>
                Accepting new patients
              </Badge>
            ) : (
              <Badge tone="neutral" dot>
                Panel full
              </Badge>
            )}
          </Field>
        )}
        <Field label="Provider ID">
          <span className="tabular-nums">{provider.id}</span>
        </Field>
        {provider.createdAt !== null && (
          <Field label="Record created">
            <time dateTime={provider.createdAt}>
              {formatDate(provider.createdAt)} at {formatTime(provider.createdAt)}
            </time>
          </Field>
        )}
      </dl>
      {fromApi && (
        <p className="mt-3 text-xs text-slate-500">
          Credentials, department, location and new-patient availability are not stored by the MediBridge API.
        </p>
      )}
    </>
  )
}
