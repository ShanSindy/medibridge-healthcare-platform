import { demoPatients } from '../data/demo/patients'
import type { Patient } from '../types/domain'
import { demoRequest } from './demoRequest'

/**
 * Lists patients.
 *
 * Future integration: `GET {API_BASE_URL}/patients` (requires auth),
 * mapping `first_name`, `last_name`, `date_of_birth` and `gender`
 * to the frontend `Patient` model.
 */
export function listPatients(): Promise<Patient[]> {
  return demoRequest(() =>
    [...demoPatients].sort(
      (a, b) => a.lastName.localeCompare(b.lastName) || a.firstName.localeCompare(b.firstName),
    ),
  )
}
