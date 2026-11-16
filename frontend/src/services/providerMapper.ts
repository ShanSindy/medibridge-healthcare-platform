import type { Provider } from '../types/domain'
import { ApiError } from './http'

/** Wire format of the backend `Provider` model (`models.py`) returned by `/providers`. */
export interface ApiProvider {
  id: number
  first_name: string
  last_name: string
  specialty: string
  email?: string | null
  phone?: string | null
  /** Serialized `datetime`, ISO 8601. */
  created_at: string
}

function isOptionalString(value: unknown): value is string | null | undefined {
  return value === null || value === undefined || typeof value === 'string'
}

export function isApiProvider(value: unknown): value is ApiProvider {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.id === 'number' &&
    Number.isSafeInteger(record.id) &&
    record.id > 0 &&
    typeof record.first_name === 'string' &&
    typeof record.last_name === 'string' &&
    typeof record.specialty === 'string' &&
    isOptionalString(record.email) &&
    isOptionalString(record.phone) &&
    typeof record.created_at === 'string' &&
    !Number.isNaN(Date.parse(record.created_at))
  )
}

/** Blank optional values are treated the same as missing ones. */
function optionalText(value: string | null | undefined): string | null {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

/**
 * Maps a backend provider to the frontend model.
 * Fields the backend does not provide are explicitly `null` — never fabricated.
 */
export function mapApiProvider(api: ApiProvider): Provider {
  return {
    id: api.id,
    recordSource: 'api',
    firstName: api.first_name.trim(),
    lastName: api.last_name.trim(),
    specialty: api.specialty.trim(),
    email: optionalText(api.email),
    phone: optionalText(api.phone),
    createdAt: api.created_at,
    prefix: null,
    credentials: null,
    department: null,
    location: null,
    acceptingNewPatients: null,
  }
}

/** Validates and maps a single-provider response (`GET /providers/{id}`). */
export function parseProvider(payload: unknown): Provider {
  if (!isApiProvider(payload)) {
    throw new ApiError('invalid_response', 'The MediBridge API returned provider data in an unexpected format.')
  }
  return mapApiProvider(payload)
}

/**
 * Validates and maps a `GET /providers` response.
 * Any malformed record fails the whole response so providers are never silently dropped.
 */
export function parseProviderList(payload: unknown): Provider[] {
  if (!Array.isArray(payload)) {
    throw new ApiError('invalid_response', 'The MediBridge API returned provider data in an unexpected format.')
  }
  return payload.map((item, index) => {
    if (!isApiProvider(item)) {
      throw new ApiError(
        'invalid_response',
        `The MediBridge API returned an invalid provider record (item ${index + 1}).`,
      )
    }
    return mapApiProvider(item)
  })
}
