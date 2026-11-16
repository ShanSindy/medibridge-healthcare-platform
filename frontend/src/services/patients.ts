import { DATA_SOURCE, type DataSource } from '../config/api'
import { demoPatients } from '../data/demo/patients'
import type { Patient } from '../types/domain'
import { endSession, getAccessToken } from './auth'
import { demoRequest } from './demoRequest'
import { apiRequest, ApiError, isApiError } from './http'
import { parsePatientList } from './patientMapper'

export interface ListPatientsOptions {
  /** Defaults to the configured `VITE_DATA_SOURCE`. */
  source?: DataSource
  /** Returns the current access token (API mode). */
  getToken?: () => string | null
  /** Called when the backend rejects the token, so the session can be ended. */
  onUnauthorized?: () => void
}

function sortByName(patients: Patient[]): Patient[] {
  return [...patients].sort(
    (a, b) =>
      a.lastName.localeCompare(b.lastName) || a.firstName.localeCompare(b.firstName) || a.id - b.id,
  )
}

/**
 * Lists patients, sorted by last then first name.
 *
 * - Demo mode: synthetic records, no network access.
 * - API mode: `GET /patients` with the signed-in user's Bearer token.
 *   Requires the backend `patients:read` permission (admin, clinician or scheduler).
 */
export async function listPatients({
  source = DATA_SOURCE,
  getToken = getAccessToken,
  onUnauthorized = () => endSession('expired'),
}: ListPatientsOptions = {}): Promise<Patient[]> {
  if (source === 'demo') {
    return demoRequest(() => sortByName(demoPatients))
  }

  const token = getToken()
  if (!token) {
    throw new ApiError('unauthorized', 'Sign in to view patients from the MediBridge API.', 401)
  }

  let payload: unknown
  try {
    payload = await apiRequest('/patients', { token })
  } catch (error) {
    if (isApiError(error, 'unauthorized')) onUnauthorized()
    throw error
  }

  return sortByName(parsePatientList(payload))
}
