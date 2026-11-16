import { describe, expect, it } from 'vitest'
import { ApiError } from './http'
import { isApiProvider, mapApiProvider, parseProvider, parseProviderList } from './providerMapper'

// Synthetic test fixture — not a real person.
const apiProvider = {
  id: 7,
  first_name: 'Jordan',
  last_name: 'Sample',
  specialty: 'Cardiology',
  email: 'jordan.sample@example.com',
  phone: '555-0100',
  created_at: '2026-03-04T15:30:00Z',
}

describe('mapApiProvider', () => {
  it('maps snake_case backend fields to the frontend model', () => {
    expect(mapApiProvider(apiProvider)).toMatchObject({
      id: 7,
      recordSource: 'api',
      firstName: 'Jordan',
      lastName: 'Sample',
      specialty: 'Cardiology',
      email: 'jordan.sample@example.com',
      phone: '555-0100',
      createdAt: '2026-03-04T15:30:00Z',
    })
  })

  it('leaves fields the backend does not store as null instead of inventing them', () => {
    expect(mapApiProvider(apiProvider)).toMatchObject({
      prefix: null,
      credentials: null,
      department: null,
      location: null,
      acceptingNewPatients: null,
    })
  })

  it.each([
    ['null', null],
    ['missing', undefined],
    ['empty', ''],
    ['whitespace', '   '],
  ])('treats a %s email or phone as not provided', (_label, value) => {
    const provider = mapApiProvider({ ...apiProvider, email: value, phone: value })
    expect(provider.email).toBeNull()
    expect(provider.phone).toBeNull()
  })

  it('trims surrounding whitespace from text fields', () => {
    const provider = mapApiProvider({
      ...apiProvider,
      first_name: '  Jordan ',
      last_name: ' Sample  ',
      specialty: ' Cardiology ',
      phone: ' 555-0100 ',
    })
    expect(provider).toMatchObject({ firstName: 'Jordan', lastName: 'Sample', specialty: 'Cardiology', phone: '555-0100' })
  })
})

describe('isApiProvider', () => {
  it('accepts a complete record and one without optional contact details', () => {
    expect(isApiProvider(apiProvider)).toBe(true)
    expect(isApiProvider({ ...apiProvider, email: null, phone: null })).toBe(true)
    const { email: _email, phone: _phone, ...withoutContact } = apiProvider
    void _email
    void _phone
    expect(isApiProvider(withoutContact)).toBe(true)
  })

  it.each([
    ['null', null],
    ['an array', [apiProvider]],
    ['a string', 'provider'],
    ['a missing id', { ...apiProvider, id: undefined }],
    ['a string id', { ...apiProvider, id: '7' }],
    ['a zero id', { ...apiProvider, id: 0 }],
    ['a negative id', { ...apiProvider, id: -3 }],
    ['a fractional id', { ...apiProvider, id: 1.5 }],
    ['a missing first name', { ...apiProvider, first_name: undefined }],
    ['a numeric last name', { ...apiProvider, last_name: 42 }],
    ['a null specialty', { ...apiProvider, specialty: null }],
    ['a numeric email', { ...apiProvider, email: 5 }],
    ['an object phone', { ...apiProvider, phone: {} }],
    ['a missing created_at', { ...apiProvider, created_at: undefined }],
    ['an unparseable created_at', { ...apiProvider, created_at: 'yesterday-ish' }],
  ])('rejects %s', (_label, value) => {
    expect(isApiProvider(value)).toBe(false)
  })
})

describe('parseProviderList', () => {
  it('maps every record', () => {
    const providers = parseProviderList([apiProvider, { ...apiProvider, id: 8, email: null }])
    expect(providers.map((provider) => provider.id)).toEqual([7, 8])
    expect(providers[1].email).toBeNull()
  })

  it('returns an empty list for an empty response', () => {
    expect(parseProviderList([])).toEqual([])
  })

  it.each([
    ['an object', { items: [apiProvider] }],
    ['null', null],
    ['a string', 'ok'],
  ])('rejects %s instead of a list', (_label, payload) => {
    expect(() => parseProviderList(payload)).toThrowError(ApiError)
  })

  it('rejects the whole response when one record is malformed, naming its position', () => {
    try {
      parseProviderList([apiProvider, { ...apiProvider, specialty: 12 }])
      throw new Error('expected a failure')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).kind).toBe('invalid_response')
      expect((error as ApiError).message).toContain('item 2')
    }
  })
})

describe('parseProvider', () => {
  it('maps a single provider', () => {
    expect(parseProvider(apiProvider).id).toBe(7)
  })

  it('rejects a list or a malformed record', () => {
    expect(() => parseProvider([apiProvider])).toThrowError(ApiError)
    expect(() => parseProvider({ ...apiProvider, id: 'x' })).toThrowError(ApiError)
  })
})
