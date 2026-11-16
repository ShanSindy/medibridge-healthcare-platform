import { API_BASE_URL } from '../config/api'

export type ApiErrorKind =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'validation'
  | 'server'
  | 'http'
  | 'network'
  | 'timeout'
  | 'invalid_response'

/** Error raised by data services. `kind` drives which UI state is shown. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | undefined

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
  }
}

export function isApiError(error: unknown, kind?: ApiErrorKind): error is ApiError {
  return error instanceof ApiError && (kind === undefined || error.kind === kind)
}

export interface ApiRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** Bearer access token. Omitted for public endpoints such as login. */
  token?: string | null
  body?: BodyInit
  timeoutMs?: number
  baseUrl?: string
}

const DEFAULT_TIMEOUT_MS = 15_000

export function buildUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`
}

/** Extracts FastAPI's `detail` message (string or validation error list). */
export function extractErrorDetail(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) return null
  const { detail } = payload as { detail: unknown }
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === 'object' && item !== null && 'msg' in item ? String(item.msg) : null))
      .filter((message): message is string => Boolean(message))
    return messages.length ? messages.join('; ') : null
  }
  return null
}

function errorFromResponse(status: number, payload: unknown): ApiError {
  const detail = extractErrorDetail(payload)
  if (status === 401) {
    return new ApiError('unauthorized', 'You are not signed in, or your session has expired.', status)
  }
  if (status === 403) {
    return new ApiError('forbidden', 'Your account does not have permission to access this resource.', status)
  }
  if (status === 404) return new ApiError('not_found', detail ?? 'The requested resource was not found.', status)
  if (status === 422) return new ApiError('validation', detail ?? 'The request was not valid.', status)
  if (status >= 500) {
    return new ApiError('server', `The MediBridge API encountered an error (HTTP ${status}). Please try again.`, status)
  }
  return new ApiError('http', detail ?? `The request failed (HTTP ${status}).`, status)
}

const NOT_JSON = Symbol('not-json')

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return NOT_JSON
  }
}

/**
 * Sends a JSON API request to the MediBridge backend and returns the parsed body.
 *
 * Callers are responsible for validating the returned shape. All failures are
 * normalised to `ApiError` so the UI can show the right state.
 */
export async function apiRequest(path: string, options: ApiRequestOptions = {}): Promise<unknown> {
  const { method = 'GET', token, body, timeoutMs = DEFAULT_TIMEOUT_MS, baseUrl = API_BASE_URL } = options
  const url = buildUrl(baseUrl, path)

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  let payload: unknown
  try {
    response = await fetch(url, { method, headers, body, signal: controller.signal, credentials: 'omit' })
    payload = await readJson(response)
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError('timeout', `The MediBridge API did not respond within ${Math.round(timeoutMs / 1000)} seconds.`)
    }
    throw new ApiError(
      'network',
      `Could not reach the MediBridge API at ${baseUrl}. Check that the backend is running and that this origin is allowed to call it (CORS), or use the development proxy.`,
    )
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) throw errorFromResponse(response.status, payload === NOT_JSON ? null : payload)
  if (payload === NOT_JSON) {
    throw new ApiError('invalid_response', 'The MediBridge API returned a response that is not valid JSON.', response.status)
  }
  return payload
}
