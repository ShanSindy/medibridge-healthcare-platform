import { useDeferredValue, useMemo, useState } from 'react'
import { SignInPanel } from '../components/auth/SignInPanel'
import { ProviderDetailDialog } from '../components/providers/ProviderDetailDialog'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { CheckboxField, SearchField, SelectField, type SelectOption } from '../components/ui/FormFields'
import { Icon } from '../components/ui/Icon'
import { Avatar, Callout, DemoBadge, ResultCount } from '../components/ui/Misc'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { DATA_SOURCE, type DataSource } from '../config/api'
import { useAsync } from '../hooks/useAsync'
import { useAuthSession } from '../hooks/useAuthSession'
import { cx, focusRing } from '../lib/cx'
import { formatClock, initials, providerName } from '../lib/format'
import {
  filterProviders,
  hasAcceptingStatus,
  hasActiveProviderFilters,
  providerSpecialties,
} from '../lib/providerFilters'
import { signOut, type AuthUser } from '../services/auth'
import { isApiError } from '../services/http'
import { listProviders } from '../services/providers'
import type { Provider } from '../types/domain'

export function ProvidersPage() {
  return DATA_SOURCE === 'api' ? <ApiProvidersView /> : <ProviderDirectoryView source="demo" user={null} />
}

function ApiProvidersView() {
  const { session, endedReason } = useAuthSession()
  if (!session) {
    return (
      <SignInPanel
        endedReason={endedReason}
        resourceLabel="the provider directory"
        accessNote="Any active MediBridge account can view providers."
      />
    )
  }
  // Keyed by token so a new sign-in always refetches.
  return <ProviderDirectoryView key={session.accessToken} source="api" user={session.user} />
}

const fetchDemoProviders = () => listProviders({ source: 'demo' })
const fetchApiProviders = () => listProviders({ source: 'api' })

function ProviderDirectoryView({ source, user }: { source: DataSource; user: AuthUser | null }) {
  const { state, reload, refresh, refreshing, lastSuccessAt } = useAsync(
    source === 'api' ? fetchApiProviders : fetchDemoProviders,
  )

  if (state.status === 'loading') {
    return (
      <LoadingState
        label={source === 'api' ? 'Loading providers from the MediBridge API' : 'Loading providers'}
      />
    )
  }

  if (state.status === 'error') {
    if (isApiError(state.error, 'forbidden')) return <ForbiddenState user={user} />
    // A 401 ends the session, which swaps this view for the sign-in form.
    return <ErrorState title="Unable to load providers" message={state.error.message} onRetry={reload} />
  }

  return (
    <div className="space-y-5">
      {source === 'api' && (
        <Callout title="Live data from the MediBridge API">
          The provider endpoint returns name, specialty, email and phone. Credentials, department, location and
          new-patient availability are not stored by the backend, so they are not shown.
        </Callout>
      )}
      <ProviderDirectory
        providers={state.data}
        source={source}
        refreshing={refreshing}
        lastRefreshedAt={lastSuccessAt}
        onRefresh={refresh}
      />
    </div>
  )
}

function ForbiddenState({ user }: { user: AuthUser | null }) {
  return (
    <Card>
      <EmptyState
        icon="lock"
        title="You don’t have access to the provider directory"
        description={
          <>
            {user && (
              <>
                Signed in as <strong className="font-medium text-navy-800">{user.username}</strong> ({user.role}).{' '}
              </>
            )}
            Your account does not have permission to view providers. Ask an administrator for access, or sign in with
            a different account.
          </>
        }
        action={<Button onClick={signOut}>Sign out</Button>}
      />
    </Card>
  )
}

interface ProviderDirectoryProps {
  providers: Provider[]
  source: DataSource
  refreshing: boolean
  lastRefreshedAt: number | null
  onRefresh: () => void
}

function ProviderDirectory({ providers, source, refreshing, lastRefreshedAt, onRefresh }: ProviderDirectoryProps) {
  const [query, setQuery] = useState('')
  const [selectedSpecialty, setSelectedSpecialty] = useState('all')
  const [acceptingOnly, setAcceptingOnly] = useState(false)
  const [openProvider, setOpenProvider] = useState<Provider | null>(null)
  const deferredQuery = useDeferredValue(query)

  const specialties = useMemo(() => providerSpecialties(providers), [providers])
  const specialtyOptions = useMemo<SelectOption<string>[]>(
    () => [{ value: 'all', label: 'All specialties' }, ...specialties.map((value) => ({ value, label: value }))],
    [specialties],
  )
  // A refresh can remove the selected specialty; fall back to showing everything.
  const specialty = specialties.includes(selectedSpecialty) ? selectedSpecialty : 'all'
  const showAcceptingFilter = useMemo(() => hasAcceptingStatus(providers), [providers])

  const filtered = useMemo(
    () =>
      filterProviders(providers, {
        query: deferredQuery,
        specialty,
        acceptingOnly: showAcceptingFilter && acceptingOnly,
      }),
    [providers, deferredQuery, specialty, acceptingOnly, showAcceptingFilter],
  )

  const hasFilters = hasActiveProviderFilters({ query, specialty, acceptingOnly: showAcceptingFilter && acceptingOnly })
  const clearFilters = () => {
    setQuery('')
    setSelectedSpecialty('all')
    setAcceptingOnly(false)
  }

  return (
    <div className="space-y-5">
      <Card bodyClassName="p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
          <SearchField
            label="Search providers"
            placeholder={
              source === 'api'
                ? 'Search by name, specialty, email, phone or ID'
                : 'Search by name, specialty, department or location'
            }
            value={query}
            onChange={setQuery}
            className="lg:max-w-md lg:flex-1"
          />
          <SelectField
            label="Filter by specialty"
            hideLabel
            value={specialty}
            options={specialtyOptions}
            onChange={setSelectedSpecialty}
            className="lg:w-56"
          />
          {showAcceptingFilter && (
            <CheckboxField label="Accepting new patients" checked={acceptingOnly} onChange={setAcceptingOnly} />
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-slate-100 pt-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <ResultCount shown={filtered.length} total={providers.length} noun="providers" />
            <p className="text-sm text-slate-500" aria-live="polite">
              {lastRefreshedAt === null ? '' : `Last refreshed ${formatClock(lastRefreshedAt)}`}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {source === 'api' ? (
              <Badge tone="brand" dot>
                Live API data
              </Badge>
            ) : (
              <DemoBadge />
            )}
            <Button onClick={onRefresh} disabled={refreshing}>
              <Icon name="refresh" className={cx('size-4', refreshing && 'animate-spin')} />
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </Button>
          </div>
        </div>
      </Card>

      {filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon="search"
            title={hasFilters ? 'No providers match your filters' : 'No providers found'}
            description={
              hasFilters
                ? 'Try a different search or specialty.'
                : source === 'api'
                  ? 'The MediBridge API returned no provider records.'
                  : 'Providers will appear here once added.'
            }
            action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        </Card>
      ) : (
        <ul
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
          aria-label={source === 'api' ? 'Providers' : 'Providers (synthetic demo data)'}
        >
          {filtered.map((provider) => (
            <li key={provider.id}>
              <ProviderCard provider={provider} onOpen={() => setOpenProvider(provider)} />
            </li>
          ))}
        </ul>
      )}

      {openProvider && (
        <ProviderDetailDialog
          key={openProvider.id}
          provider={openProvider}
          source={source}
          onClose={() => setOpenProvider(null)}
          onRefreshDirectory={onRefresh}
        />
      )}
    </div>
  )
}

function ProviderCard({ provider, onOpen }: { provider: Provider; onOpen: () => void }) {
  const name = providerName(provider)
  const linkClasses = cx('flex items-center gap-2 rounded text-slate-600 hover:text-brand-700', focusRing)
  const place = [provider.department, provider.location].filter((value) => value !== null).join(' · ')

  return (
    <article
      aria-label={name}
      className="flex h-full flex-col rounded-xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-shadow hover:shadow-[0_4px_12px_rgba(16,24,40,0.08)]"
    >
      <div className="flex items-start gap-3">
        <Avatar initials={initials(provider.firstName, provider.lastName)} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-semibold text-navy-900">{name}</h2>
          <p className="text-sm text-slate-500">{provider.credentials ?? `Provider ID ${provider.id}`}</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Badge tone="brand">{provider.specialty}</Badge>
        {provider.acceptingNewPatients === true && (
          <Badge tone="success" dot>
            Accepting new patients
          </Badge>
        )}
        {provider.acceptingNewPatients === false && (
          <Badge tone="neutral" dot>
            Panel full
          </Badge>
        )}
      </div>

      <dl className="mt-4 space-y-2 border-t border-slate-100 pt-4 text-sm">
        {place && (
          <div>
            <dt className="sr-only">Department and location</dt>
            <dd className="flex items-center gap-2 text-slate-600">
              <Icon name="pin" className="size-4 shrink-0 text-slate-400" />
              <span className="truncate">{place}</span>
            </dd>
          </div>
        )}
        <div>
          <dt className="sr-only">Phone</dt>
          <dd>
            {provider.phone ? (
              <a href={`tel:${provider.phone.replace(/[^\d+]/g, '')}`} className={linkClasses}>
                <Icon name="phone" className="size-4 shrink-0 text-slate-400" />
                <span className="tabular-nums">{provider.phone}</span>
              </a>
            ) : (
              <span className="flex items-center gap-2 text-slate-400">
                <Icon name="phone" className="size-4 shrink-0" />
                No phone on file
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="sr-only">Email</dt>
          <dd>
            {provider.email ? (
              <a href={`mailto:${provider.email}`} className={linkClasses}>
                <Icon name="mail" className="size-4 shrink-0 text-slate-400" />
                <span className="truncate">{provider.email}</span>
              </a>
            ) : (
              <span className="flex items-center gap-2 text-slate-400">
                <Icon name="mail" className="size-4 shrink-0" />
                No email on file
              </span>
            )}
          </dd>
        </div>
      </dl>

      <div className="mt-auto pt-4">
        <button
          type="button"
          onClick={onOpen}
          className={cx(
            'inline-flex items-center gap-1 rounded text-sm font-medium text-brand-700 hover:text-brand-800',
            focusRing,
          )}
        >
          View details
          <span className="sr-only"> for {name}</span>
          <Icon name="chevronRight" className="size-4" />
        </button>
      </div>
    </article>
  )
}
