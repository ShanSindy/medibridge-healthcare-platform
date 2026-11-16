/**
 * SYNTHETIC DEMO DATA — not real patients.
 * Names, dates of birth, MRNs and contact details are fabricated.
 * Phone numbers use the reserved fictional 555-01XX range and email
 * addresses use the reserved `example.com` domain.
 */
import { atDayOffset } from '../../lib/clock'
import type { Gender, Patient, PatientStatus, RegistrationSource } from '../../types/domain'

type PatientSeed = [
  firstName: string,
  lastName: string,
  dateOfBirth: string,
  gender: Gender,
  status: PatientStatus,
  primaryProviderId: number | null,
  lastVisitDaysAgo: number | null,
  source: RegistrationSource,
]

const SEEDS: PatientSeed[] = [
  ['Avery', 'Thompson', '1984-03-12', 'female', 'active', 1, 12, 'manual'],
  ['Marcus', 'Bennett', '1971-08-29', 'male', 'active', 2, 3, 'hl7'],
  ['Priya', 'Raman', '1992-11-04', 'female', 'active', 3, 30, 'manual'],
  ['Diego', 'Alvarez', '1958-01-17', 'male', 'active', 1, 6, 'manual'],
  ['Hannah', 'Okafor', '2001-06-23', 'female', 'pending', null, null, 'hl7'],
  ['Theodore', 'Lin', '1966-09-30', 'male', 'inactive', 5, 410, 'manual'],
  ['Sofia', 'Petrova', '1989-12-02', 'female', 'active', 4, 18, 'manual'],
  ['Jamal', 'Carter', '1977-04-14', 'male', 'active', 6, 2, 'hl7'],
  ['Mei', 'Nakamura', '1995-02-08', 'female', 'active', 3, 45, 'manual'],
  ['Elijah', 'Brooks', '2014-07-19', 'male', 'active', 7, 21, 'manual'],
  ['Grace', 'Whitfield', '1949-10-11', 'female', 'active', 8, 9, 'manual'],
  ['Omar', 'Haddad', '1983-05-27', 'male', 'pending', null, null, 'hl7'],
  ['Lucía', 'Moreno', '1999-03-03', 'female', 'active', 2, 60, 'manual'],
  ['Samuel', 'Osei', '1962-12-21', 'male', 'active', 9, 14, 'hl7'],
  ['Isabel', 'Fontaine', '1974-08-08', 'female', 'inactive', 4, 540, 'manual'],
  ['Kai', 'Holloway', '1990-01-25', 'other', 'active', 10, 7, 'manual'],
  ['Nora', 'Lindqvist', '1986-06-30', 'female', 'active', 11, 33, 'manual'],
  ['Victor', 'Reyes', '1953-02-14', 'male', 'active', 9, 4, 'hl7'],
  ['Amara', 'Nwosu', '2016-09-09', 'female', 'active', 7, 40, 'manual'],
  ['Benjamin', 'Hale', '1968-11-28', 'male', 'active', 12, 26, 'manual'],
  ['Leila', 'Farouk', '1993-04-06', 'female', 'pending', null, null, 'hl7'],
  ['Ethan', 'Park', '1980-10-15', 'male', 'active', 5, 11, 'manual'],
  ['Ruth', 'Abernathy', '1941-07-02', 'female', 'active', 8, 1, 'manual'],
  ['Daniel', 'Kowalski', '1997-12-12', 'unknown', 'inactive', null, 720, 'manual'],
]

export const demoPatients: Patient[] = SEEDS.map(
  ([firstName, lastName, dateOfBirth, gender, status, primaryProviderId, lastVisitDaysAgo, source], index) => ({
    id: index + 1,
    mrn: `DEMO-${100201 + index}`,
    firstName,
    lastName,
    dateOfBirth,
    gender,
    status,
    primaryProviderId,
    lastVisit: lastVisitDaysAgo === null ? null : atDayOffset(-lastVisitDaysAgo, 10).toISOString(),
    registrationSource: source,
    phone: `(425) 555-01${String(index).padStart(2, '0')}`,
    email: `${firstName.normalize('NFD').replace(/[̀-ͯ]/g, '')}.${lastName}@example.com`.toLowerCase(),
  }),
)
