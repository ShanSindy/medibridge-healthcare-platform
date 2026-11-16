import type { Provider } from '../types/domain'
import { matchesSearch, providerName } from './format'

export interface ProviderFilters {
  /** Free-text search across name, specialty, contact details, ID and location. */
  query: string
  /** A specialty name, or `'all'`. */
  specialty: string
  /** When true, keep only providers known to be accepting new patients. */
  acceptingOnly: boolean
}

export const NO_PROVIDER_FILTERS: ProviderFilters = { query: '', specialty: 'all', acceptingOnly: false }

/** Distinct specialties in the given records, alphabetically. */
export function providerSpecialties(providers: Provider[]): string[] {
  return [...new Set(providers.map((provider) => provider.specialty))].sort((a, b) => a.localeCompare(b))
}

/**
 * True when the records say whether a provider accepts new patients.
 * The backend does not, so that filter is hidden for API records.
 */
export function hasAcceptingStatus(providers: Provider[]): boolean {
  return providers.some((provider) => provider.acceptingNewPatients !== null)
}

export function filterProviders(providers: Provider[], filters: ProviderFilters): Provider[] {
  return providers.filter(
    (provider) =>
      (filters.specialty === 'all' || provider.specialty === filters.specialty) &&
      (!filters.acceptingOnly || provider.acceptingNewPatients === true) &&
      matchesSearch(
        filters.query,
        providerName(provider),
        provider.specialty,
        provider.email,
        provider.phone,
        `ID ${provider.id}`,
        provider.department,
        provider.location,
      ),
  )
}

export function hasActiveProviderFilters(filters: ProviderFilters): boolean {
  return filters.query.trim() !== '' || filters.specialty !== 'all' || filters.acceptingOnly
}
