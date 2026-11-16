import type { Gender, Patient } from '../types/domain'
import { ApiError } from './http'

/** Wire format of the backend `Patient` model (`models.py`) returned by `GET /patients`. */
export interface ApiPatient {
  id: number
  first_name: string
  last_name: string
  /** Serialized `datetime.date`: `YYYY-MM-DD`. */
  date_of_birth: string
  gender: string
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/
const GENDERS: readonly Gender[] = ['male', 'female', 'other', 'unknown']

/**
 * True for a real calendar date in `YYYY-MM-DD` form. Rejects values such as
 * `1990-02-30`, which `Date` would silently roll over to a different day and
 * so display a date of birth that was never recorded.
 */
export function isValidDateOnly(value: string): boolean {
  if (!DATE_ONLY.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

export function isApiPatient(value: unknown): value is ApiPatient {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.id === 'number' &&
    Number.isSafeInteger(record.id) &&
    record.id > 0 &&
    typeof record.first_name === 'string' &&
    typeof record.last_name === 'string' &&
    typeof record.date_of_birth === 'string' &&
    isValidDateOnly(record.date_of_birth) &&
    typeof record.gender === 'string'
  )
}

/**
 * Backend values are constrained to male/female/other/unknown (API validation
 * and the HL7 PID-8 mapping). Anything unrecognised is shown as "unknown"
 * rather than guessed.
 */
export function normalizeGender(value: string): Gender {
  const normalized = value.trim().toLowerCase()
  return (GENDERS as readonly string[]).includes(normalized) ? (normalized as Gender) : 'unknown'
}

/**
 * Maps a backend patient to the frontend model.
 * Fields the backend does not provide are explicitly `null` — never fabricated.
 */
export function mapApiPatient(api: ApiPatient): Patient {
  return {
    id: api.id,
    recordSource: 'api',
    firstName: api.first_name.trim(),
    lastName: api.last_name.trim(),
    dateOfBirth: api.date_of_birth,
    gender: normalizeGender(api.gender),
    mrn: null,
    phone: null,
    email: null,
    status: null,
    primaryProviderId: null,
    lastVisit: null,
    registrationSource: null,
    registeredAt: null,
  }
}

/**
 * Validates and maps a `GET /patients` response.
 * Any malformed record fails the whole response so patients are never silently dropped.
 */
export function parsePatientList(payload: unknown): Patient[] {
  if (!Array.isArray(payload)) {
    throw new ApiError('invalid_response', 'The MediBridge API returned patient data in an unexpected format.')
  }
  return payload.map((item, index) => {
    if (!isApiPatient(item)) {
      throw new ApiError(
        'invalid_response',
        `The MediBridge API returned an invalid patient record (item ${index + 1}).`,
      )
    }
    return mapApiPatient(item)
  })
}
