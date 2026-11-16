import { describe, expect, it } from 'vitest'
import { ApiError } from './http'
import {
  isApiPatient,
  isValidDateOnly,
  mapApiPatient,
  normalizeGender,
  parsePatientList,
  type ApiPatient,
} from './patientMapper'

// Synthetic test fixture — not a real person.
const apiPatient: ApiPatient = {
  id: 7,
  first_name: 'Test',
  last_name: 'Patient',
  date_of_birth: '1990-04-15',
  gender: 'female',
}

describe('mapApiPatient', () => {
  it('maps backend snake_case fields to the frontend model', () => {
    const patient = mapApiPatient(apiPatient)
    expect(patient).toMatchObject({
      id: 7,
      recordSource: 'api',
      firstName: 'Test',
      lastName: 'Patient',
      dateOfBirth: '1990-04-15',
      gender: 'female',
    })
  })

  it('sets every field the backend does not provide to null instead of inventing values', () => {
    const patient = mapApiPatient(apiPatient)
    expect(patient.mrn).toBeNull()
    expect(patient.phone).toBeNull()
    expect(patient.email).toBeNull()
    expect(patient.status).toBeNull()
    expect(patient.primaryProviderId).toBeNull()
    expect(patient.lastVisit).toBeNull()
    expect(patient.registrationSource).toBeNull()
  })

  it('trims whitespace from names', () => {
    const patient = mapApiPatient({ ...apiPatient, first_name: '  Test ', last_name: ' Patient  ' })
    expect(patient.firstName).toBe('Test')
    expect(patient.lastName).toBe('Patient')
  })
})

describe('normalizeGender', () => {
  it('accepts the backend values case-insensitively', () => {
    expect(normalizeGender('male')).toBe('male')
    expect(normalizeGender('FEMALE')).toBe('female')
    expect(normalizeGender(' Other ')).toBe('other')
    expect(normalizeGender('unknown')).toBe('unknown')
  })

  it('treats unrecognised values as unknown rather than guessing', () => {
    expect(normalizeGender('M')).toBe('unknown')
    expect(normalizeGender('')).toBe('unknown')
  })
})

describe('isValidDateOnly', () => {
  it('accepts real calendar dates, including leap days', () => {
    expect(isValidDateOnly('1990-04-15')).toBe(true)
    expect(isValidDateOnly('2024-02-29')).toBe(true)
    expect(isValidDateOnly('1900-01-01')).toBe(true)
  })

  it.each(['1990-02-30', '2023-02-29', '2023-13-01', '2023-00-10', '2023-04-31', '90-04-15', '1990-4-5', ''])(
    'rejects %j instead of rolling it over to another day',
    (value) => {
      expect(isValidDateOnly(value)).toBe(false)
    },
  )
})

describe('isApiPatient', () => {
  it('accepts a valid record and ignores extra fields', () => {
    expect(isApiPatient(apiPatient)).toBe(true)
    expect(isApiPatient({ ...apiPatient, created_at: '2026-01-01T00:00:00Z' })).toBe(true)
  })

  it.each([
    ['null', null],
    ['a string', 'patient'],
    ['missing id', { ...apiPatient, id: undefined }],
    ['non-integer id', { ...apiPatient, id: 1.5 }],
    ['string id', { ...apiPatient, id: '7' }],
    ['zero id', { ...apiPatient, id: 0 }],
    ['negative id', { ...apiPatient, id: -3 }],
    ['an array', [apiPatient]],
    ['an impossible date_of_birth', { ...apiPatient, date_of_birth: '1990-02-30' }],
    ['missing first_name', { ...apiPatient, first_name: undefined }],
    ['null last_name', { ...apiPatient, last_name: null }],
    ['missing date_of_birth', { ...apiPatient, date_of_birth: undefined }],
    ['datetime date_of_birth', { ...apiPatient, date_of_birth: '1990-04-15T00:00:00' }],
    ['missing gender', { ...apiPatient, gender: undefined }],
  ])('rejects %s', (_label, value) => {
    expect(isApiPatient(value)).toBe(false)
  })
})

describe('parsePatientList', () => {
  it('maps every record in the list', () => {
    const patients = parsePatientList([apiPatient, { ...apiPatient, id: 8, gender: 'male' }])
    expect(patients.map((p) => p.id)).toEqual([7, 8])
    expect(patients.every((p) => p.recordSource === 'api')).toBe(true)
  })

  it('returns an empty list for an empty response', () => {
    expect(parsePatientList([])).toEqual([])
  })

  it('rejects a payload that is not an array', () => {
    expect(() => parsePatientList({ items: [apiPatient] })).toThrow(ApiError)
    expect(() => parsePatientList(null)).toThrow(/unexpected format/)
  })

  it('fails the whole response if any record is malformed, identifying the item', () => {
    let caught: unknown
    try {
      parsePatientList([apiPatient, { id: 9 }])
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).kind).toBe('invalid_response')
    expect((caught as ApiError).message).toContain('item 2')
  })
})
