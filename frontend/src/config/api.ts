/**
 * API configuration.
 *
 * `VITE_DATA_SOURCE` selects where the Patients page reads from:
 *   - `demo` (default): synthetic in-browser data, no network access.
 *   - `api`: the MediBridge FastAPI backend at `VITE_API_BASE_URL`.
 *
 * All other pages use synthetic demo data in both modes.
 */

export type DataSource = 'demo' | 'api'

export const DEFAULT_API_BASE_URL = 'http://localhost:8000'

/** Anything other than "api" (case-insensitive) falls back to the safe demo mode. */
export function resolveDataSource(raw: string | undefined): DataSource {
  return raw?.trim().toLowerCase() === 'api' ? 'api' : 'demo'
}

/**
 * Accepts an absolute http(s) URL or a same-origin path such as `/api`.
 * Trims whitespace and trailing slashes. Empty or malformed values (for
 * example a Windows path produced by shell path conversion) fall back to the
 * default with a warning, rather than sending requests somewhere unexpected.
 */
export function resolveApiBaseUrl(raw: string | undefined): string {
  const value = raw?.trim().replace(/\/+$/, '')
  if (!value) return DEFAULT_API_BASE_URL
  if (/^https?:\/\/[^/\s]+/i.test(value) || /^\/[^/\\]/.test(value)) return value
  console.warn(
    `Ignoring invalid VITE_API_BASE_URL "${value}"; expected an http(s) URL or a path starting with "/". Using ${DEFAULT_API_BASE_URL}.`,
  )
  return DEFAULT_API_BASE_URL
}

export const DATA_SOURCE: DataSource = resolveDataSource(import.meta.env.VITE_DATA_SOURCE)

export const API_BASE_URL: string = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
