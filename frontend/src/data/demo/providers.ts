/**
 * SYNTHETIC DEMO DATA — not real people.
 * Phone numbers use the reserved fictional 555-01XX range and email
 * addresses use the reserved `.example` domain.
 */
import type { Provider } from '../../types/domain'

type ProviderSeed = [
  prefix: string | null,
  firstName: string,
  lastName: string,
  credentials: string,
  specialty: string,
  department: string,
  location: string,
  acceptingNewPatients: boolean,
]

const SEEDS: ProviderSeed[] = [
  ['Dr.', 'Elena', 'Vasquez', 'MD', 'Family Medicine', 'Primary Care', 'Northside Clinic', true],
  ['Dr.', 'James', 'Whitaker', 'MD', 'Internal Medicine', 'Primary Care', 'Northside Clinic', false],
  ['Dr.', 'Aisha', 'Rahman', 'MD', 'Endocrinology', 'Specialty Care', 'Medical Pavilion B', true],
  ['Dr.', 'Michael', 'Chen', 'MD, FACC', 'Cardiology', 'Heart & Vascular', 'Medical Pavilion A', false],
  ['Dr.', 'Sarah', 'Goldberg', 'DO', 'Neurology', 'Neurosciences', 'Medical Pavilion A', true],
  ['Dr.', 'Robert', 'Mensah', 'MD', 'Pulmonology', 'Specialty Care', 'Medical Pavilion B', true],
  ['Dr.', 'Olivia', 'Grant', 'MD', 'Pediatrics', 'Women & Children', 'Riverside Pediatrics', true],
  ['Dr.', 'Henry', 'Adeyemi', 'MD', 'Geriatric Medicine', 'Primary Care', 'Northside Clinic', false],
  ['Dr.', 'Natalie', 'Brooks', 'MD', 'Orthopedic Surgery', 'Musculoskeletal', 'Surgical Center', true],
  [null, 'Jordan', 'Ellis', 'PMHNP-BC', 'Psychiatry', 'Behavioral Health', 'Virtual Care', true],
  ['Dr.', 'Camille', 'Dubois', 'MD', 'Dermatology', 'Specialty Care', 'Medical Pavilion B', false],
  ['Dr.', 'Thomas', 'Rivera', 'MD', 'Gastroenterology', 'Digestive Health', 'Surgical Center', true],
]

export const demoProviders: Provider[] = SEEDS.map(
  ([prefix, firstName, lastName, credentials, specialty, department, location, accepting], index) => ({
    id: index + 1,
    recordSource: 'demo',
    createdAt: null,
    prefix,
    firstName,
    lastName,
    credentials,
    specialty,
    department,
    location,
    acceptingNewPatients: accepting,
    email: `${firstName}.${lastName}@medibridge.example`.toLowerCase(),
    phone: `(206) 555-01${String(50 + index).padStart(2, '0')}`,
  }),
)
