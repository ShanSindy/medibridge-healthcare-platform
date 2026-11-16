import { DATA_SOURCE, type DataSource } from '../config/api'
import { demoProviders } from '../data/demo/providers'
import type { Provider } from '../types/domain'
import { endSession, getAccessToken } from './auth'
import { demoRequest } from './demoRequest'
import { apiRequest, ApiError, isApiError } from './http'
import { parseProvider, parseProviderList } from './providerMapper'

export interface ProviderRequestOptions {
  /** Defaults to the configured `VITE_DATA_SOURCE`. */
  source?: DataSource
  /** Returns the current access token (API mode). */
  getToken?: () => string | null
  /** Called when the backend rejects the token, so the session can be ended. */
  onUnauthorized?: () => void
}

function sortByName(providers: Provider[]): Provider[] {
  return [...providers].sort(
    (a, b) =>
      a.lastName.localeCompare(b.lastName) || a.firstName.localeCompare(b.firstName) || a.id - b.id,
  )
}

/** Authenticated GET. Makes no request without a token, and reports a rejected token. */
async function authorizedGet(
  path: string,
  getToken: () => string | null,
  onUnauthorized: () => void,
): Promise<unknown> {
  const token = getToken()
  if (!token) {
    throw new ApiError('unauthorized', 'Sign in to view providers from the MediBridge API.', 401)
  }
  try {
    return await apiRequest(path, { token })
  } catch (error) {
    if (isApiError(error, 'unauthorized')) onUnauthorized()
    throw error
  }
}

/**
 * Lists providers, sorted by last then first name.
 *
 * - Demo mode: synthetic records, no network access.
 * - API mode: `GET /providers` with the signed-in user's Bearer token.
 *   Requires the backend `providers:read` permission (every role has it).
 *
 * The endpoint has no pagination, search or filter parameters.
 */
export async function listProviders({
  source = DATA_SOURCE,
  getToken = getAccessToken,
  onUnauthorized = () => endSession('expired'),
}: ProviderRequestOptions = {}): Promise<Provider[]> {
  if (source === 'demo') {
    return demoRequest(() => sortByName(demoProviders))
  }
  return sortByName(parseProviderList(await authorizedGet('/providers', getToken, onUnauthorized)))
}

/**
 * Loads one provider.
 *
 * - Demo mode: looked up in the synthetic records.
 * - API mode: `GET /providers/{id}`. A provider deleted since the list was
 *   loaded is reported as a `not_found` error.
 */
export async function getProvider(
  id: number,
  {
    source = DATA_SOURCE,
    getToken = getAccessToken,
    onUnauthorized = () => endSession('expired'),
  }: ProviderRequestOptions = {},
): Promise<Provider> {
  if (!Number.isSafeInteger(id) || id <= 0) {
    throw new ApiError('not_found', 'This provider could not be found.', 404)
  }

  if (source === 'demo') {
    const provider = await demoRequest(() => demoProviders.find((candidate) => candidate.id === id) ?? null)
    if (!provider) throw new ApiError('not_found', 'This provider could not be found.', 404)
    return provider
  }

  try {
    return parseProvider(await authorizedGet(`/providers/${id}`, getToken, onUnauthorized))
  } catch (error) {
    if (isApiError(error, 'not_found')) {
      throw new ApiError('not_found', 'This provider no longer exists. It may have been removed.', 404)
    }
    throw error
  }
}
