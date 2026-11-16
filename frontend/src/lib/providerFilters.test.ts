import { describe, expect, it } from 'vitest'
import type { Provider } from '../types/domain'
import {
  filterProviders,
  hasAcceptingStatus,
  hasActiveProviderFilters,
  NO_PROVIDER_FILTERS,
  providerSpecialties,
} from './providerFilters'

// Synthetic test fixtures — not real people.
function apiProvider(id: number, firstName: string, lastName: string, specialty: string, extra: Partial<Provider> = {}): Provider {
  return {
    id,
    recordSource: 'api',
    firstName,
    lastName,
    specialty,
    email: null,
    phone: null,
    createdAt: '2026-01-01T09:00:00Z',
    prefix: null,
    credentials: null,
    department: null,
    location: null,
    acceptingNewPatients: null,
    ...extra,
  }
}

const providers: Provider[] = [
  apiProvider(1, 'Alex', 'Example', 'Cardiology', { email: 'alex@example.com', phone: '555-0101' }),
  apiProvider(2, 'Zoë', 'Sample', 'Neurology'),
  apiProvider(12, 'Sam', 'Example', 'Cardiology', { phone: '555-0112' }),
  {
    ...apiProvider(20, 'Dana', 'Demo', 'Pediatrics'),
    recordSource: 'demo',
    prefix: 'Dr.',
    department: 'Women & Children',
    location: 'Riverside Pediatrics',
    acceptingNewPatients: true,
  },
  { ...apiProvider(21, 'Lee', 'Demo', 'Pediatrics'), recordSource: 'demo', acceptingNewPatients: false },
]

const ids = (result: Provider[]) => result.map((provider) => provider.id)
const search = (query: string) => ids(filterProviders(providers, { ...NO_PROVIDER_FILTERS, query }))

describe('filterProviders — search', () => {
  it('returns everything when no filter is set', () => {
    expect(filterProviders(providers, NO_PROVIDER_FILTERS)).toHaveLength(providers.length)
    expect(search('   ')).toHaveLength(providers.length)
  })

  it('matches first name, last name and full name, ignoring case', () => {
    expect(search('alex')).toEqual([1])
    expect(search('EXAMPLE')).toEqual([1, 12])
    expect(search('sam example')).toEqual([12])
  })

  it('matches the display prefix when a record has one', () => {
    expect(search('dr. dana')).toEqual([20])
  })

  it('ignores accents', () => {
    expect(search('zoe')).toEqual([2])
  })

  it('matches specialty, email and phone', () => {
    expect(search('neuro')).toEqual([2])
    expect(search('alex@example')).toEqual([1])
    expect(search('555-0112')).toEqual([12])
  })

  it('matches the provider ID', () => {
    expect(search('ID 12')).toEqual([12])
  })

  it('matches department and location when the record has them', () => {
    expect(search('riverside')).toEqual([20])
    expect(search('women')).toEqual([20])
  })

  it('does not match on fields a record does not have', () => {
    expect(search('null')).toEqual([])
    expect(search('no such provider')).toEqual([])
  })
})

describe('filterProviders — specialty and availability', () => {
  it('filters by exact specialty', () => {
    expect(ids(filterProviders(providers, { ...NO_PROVIDER_FILTERS, specialty: 'Cardiology' }))).toEqual([1, 12])
  })

  it('combines specialty with search', () => {
    expect(ids(filterProviders(providers, { query: 'alex', specialty: 'Cardiology', acceptingOnly: false }))).toEqual([1])
    expect(filterProviders(providers, { query: 'alex', specialty: 'Neurology', acceptingOnly: false })).toEqual([])
  })

  it('keeps only providers known to be accepting new patients', () => {
    expect(ids(filterProviders(providers, { ...NO_PROVIDER_FILTERS, acceptingOnly: true }))).toEqual([20])
  })

  it('does not treat an unknown availability as accepting', () => {
    const apiOnly = providers.filter((provider) => provider.recordSource === 'api')
    expect(filterProviders(apiOnly, { ...NO_PROVIDER_FILTERS, acceptingOnly: true })).toEqual([])
  })
})

describe('providerSpecialties', () => {
  it('lists each specialty once, alphabetically', () => {
    expect(providerSpecialties(providers)).toEqual(['Cardiology', 'Neurology', 'Pediatrics'])
    expect(providerSpecialties([])).toEqual([])
  })
})

describe('hasAcceptingStatus', () => {
  it('is false for API records, which carry no availability, and true for demo records', () => {
    expect(hasAcceptingStatus(providers.filter((provider) => provider.recordSource === 'api'))).toBe(false)
    expect(hasAcceptingStatus(providers)).toBe(true)
    expect(hasAcceptingStatus([])).toBe(false)
  })
})

describe('hasActiveProviderFilters', () => {
  it('detects any active filter', () => {
    expect(hasActiveProviderFilters(NO_PROVIDER_FILTERS)).toBe(false)
    expect(hasActiveProviderFilters({ ...NO_PROVIDER_FILTERS, query: '  ' })).toBe(false)
    expect(hasActiveProviderFilters({ ...NO_PROVIDER_FILTERS, query: 'a' })).toBe(true)
    expect(hasActiveProviderFilters({ ...NO_PROVIDER_FILTERS, specialty: 'Cardiology' })).toBe(true)
    expect(hasActiveProviderFilters({ ...NO_PROVIDER_FILTERS, acceptingOnly: true })).toBe(true)
  })
})
