import { demoProviders } from '../data/demo/providers'
import type { Provider } from '../types/domain'
import { demoRequest } from './demoRequest'

/**
 * Lists providers.
 *
 * Future integration: `GET {API_BASE_URL}/providers`, or the FHIR
 * `GET /fhir/PractitionerRole?specialty=...` search for specialty filtering.
 */
export function listProviders(): Promise<Provider[]> {
  return demoRequest(() =>
    [...demoProviders].sort((a, b) => a.lastName.localeCompare(b.lastName)),
  )
}
