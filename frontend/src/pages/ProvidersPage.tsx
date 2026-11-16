import { useDeferredValue, useMemo, useState } from 'react'
import { AsyncContent } from '../components/ui/AsyncContent'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { CheckboxField, SearchField, SelectField, type SelectOption } from '../components/ui/FormFields'
import { Icon } from '../components/ui/Icon'
import { Avatar, DemoBadge, ResultCount } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'
import { useAsync } from '../hooks/useAsync'
import { cx, focusRing } from '../lib/cx'
import { initials, matchesSearch, providerName } from '../lib/format'
import { listProviders } from '../services/providers'
import type { Provider } from '../types/domain'

export function ProvidersPage() {
  const { state, reload } = useAsync(listProviders)
  return (
    <AsyncContent state={state} onRetry={reload} loadingLabel="Loading providers" errorTitle="Unable to load providers">
      {(providers) => <ProviderDirectory providers={providers} />}
    </AsyncContent>
  )
}

function ProviderDirectory({ providers }: { providers: Provider[] }) {
  const [query, setQuery] = useState('')
  const [specialty, setSpecialty] = useState('all')
  const [acceptingOnly, setAcceptingOnly] = useState(false)
  const deferredQuery = useDeferredValue(query)

  const specialtyOptions = useMemo<SelectOption<string>[]>(() => {
    const specialties = [...new Set(providers.map((provider) => provider.specialty))].sort()
    return [
      { value: 'all', label: 'All specialties' },
      ...specialties.map((value) => ({ value, label: value })),
    ]
  }, [providers])

  const filtered = useMemo(
    () =>
      providers.filter(
        (provider) =>
          (specialty === 'all' || provider.specialty === specialty) &&
          (!acceptingOnly || provider.acceptingNewPatients) &&
          matchesSearch(
            deferredQuery,
            providerName(provider),
            provider.specialty,
            provider.department,
            provider.location,
          ),
      ),
    [providers, specialty, acceptingOnly, deferredQuery],
  )

  const hasFilters = query !== '' || specialty !== 'all' || acceptingOnly
  const clearFilters = () => {
    setQuery('')
    setSpecialty('all')
    setAcceptingOnly(false)
  }

  return (
    <div className="space-y-5">
      <Card bodyClassName="p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
          <SearchField
            label="Search providers"
            placeholder="Search by name, specialty, department or location"
            value={query}
            onChange={setQuery}
            className="lg:max-w-md lg:flex-1"
          />
          <SelectField
            label="Filter by specialty"
            hideLabel
            value={specialty}
            options={specialtyOptions}
            onChange={setSpecialty}
            className="lg:w-56"
          />
          <CheckboxField label="Accepting new patients" checked={acceptingOnly} onChange={setAcceptingOnly} />
          <div className="flex items-center gap-3 lg:ml-auto">
            <ResultCount shown={filtered.length} total={providers.length} noun="providers" />
            <DemoBadge />
          </div>
        </div>
      </Card>

      {filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon="search"
            title={hasFilters ? 'No providers match your filters' : 'No providers found'}
            description={hasFilters ? 'Try a different search or specialty.' : 'Providers will appear here once added.'}
            action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        </Card>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="Providers (synthetic demo data)">
          {filtered.map((provider) => (
            <li key={provider.id}>
              <ProviderCard provider={provider} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ProviderCard({ provider }: { provider: Provider }) {
  const name = providerName(provider)
  const linkClasses = cx('flex items-center gap-2 rounded text-slate-600 hover:text-brand-700', focusRing)

  return (
    <article
      aria-label={name}
      className="flex h-full flex-col rounded-xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]"
    >
      <div className="flex items-start gap-3">
        <Avatar initials={initials(provider.firstName, provider.lastName)} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-semibold text-navy-900">{name}</h2>
          <p className="text-sm text-slate-500">{provider.credentials}</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Badge tone="brand">{provider.specialty}</Badge>
        {provider.acceptingNewPatients ? (
          <Badge tone="success" dot>
            Accepting new patients
          </Badge>
        ) : (
          <Badge tone="neutral" dot>
            Panel full
          </Badge>
        )}
      </div>

      <dl className="mt-4 space-y-2 border-t border-slate-100 pt-4 text-sm">
        <div>
          <dt className="sr-only">Department and location</dt>
          <dd className="flex items-center gap-2 text-slate-600">
            <Icon name="pin" className="size-4 shrink-0 text-slate-400" />
            <span className="truncate">
              {provider.department} · {provider.location}
            </span>
          </dd>
        </div>
        <div>
          <dt className="sr-only">Phone</dt>
          <dd>
            <a href={`tel:${provider.phone.replace(/[^\d+]/g, '')}`} className={linkClasses}>
              <Icon name="phone" className="size-4 shrink-0 text-slate-400" />
              <span className="tabular-nums">{provider.phone}</span>
            </a>
          </dd>
        </div>
        <div>
          <dt className="sr-only">Email</dt>
          <dd>
            <a href={`mailto:${provider.email}`} className={linkClasses}>
              <Icon name="mail" className="size-4 shrink-0 text-slate-400" />
              <span className="truncate">{provider.email}</span>
            </a>
          </dd>
        </div>
      </dl>
    </article>
  )
}
