import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from '../config/api'
import { getAuthState, signIn, signOut } from './auth'
import { ApiError } from './http'
import { listPatients } from './patients'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Synthetic test fixtures — not real people.
const backendPatients = [
  { id: 2, first_name: 'Zoe', last_name: 'Sample', date_of_birth: '1985-02-01', gender: 'female' },
  { id: 1, first_name: 'Alex', last_name: 'Example', date_of_birth: '1970-12-31', gender: 'male' },
  { id: 3, first_name: 'Sam', last_name: 'Example', date_of_birth: '2001-07-04', gender: 'unknown' },
]

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('Expected listPatients to fail')
}

afterEach(() => {
  signOut()
  vi.useRealTimers()
})

describe('listPatients — demo mode', () => {
  it('returns synthetic demo records without any network access', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const pending = listPatients({ source: 'demo' })
    await vi.runAllTimersAsync()
    const patients = await pending

    expect(fetchMock).not.toHaveBeenCalled()
    expect(patients.length).toBeGreaterThan(0)
    expect(patients.every((p) => p.recordSource === 'demo')).toBe(true)
    expect(patients.every((p) => p.mrn?.startsWith('DEMO-'))).toBe(true)
  })
})

describe('listPatients — API mode', () => {
  it('fetches GET /patients with the Bearer token and maps, then sorts by name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(backendPatients))
    vi.stubGlobal('fetch', fetchMock)

    const patients = await listPatients({ source: 'api', getToken: () => 'token-123' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_BASE_URL}/patients`)
    expect(init.headers).toMatchObject({ Authorization: 'Bearer token-123' })

    expect(patients.map((p) => `${p.lastName}, ${p.firstName}`)).toEqual([
      'Example, Alex',
      'Example, Sam',
      'Sample, Zoe',
    ])
    expect(patients[0]).toMatchObject({ id: 1, recordSource: 'api', dateOfBirth: '1970-12-31', gender: 'male' })
  })

  it('leaves fields the backend does not return as null', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([backendPatients[0]])))
    const [patient] = await listPatients({ source: 'api', getToken: () => 'token' })
    expect(patient).toMatchObject({
      mrn: null,
      phone: null,
      email: null,
      status: null,
      primaryProviderId: null,
      lastVisit: null,
      registrationSource: null,
      registeredAt: null,
    })
  })

  it('returns an empty list when the backend has no patients', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([])))
    await expect(listPatients({ source: 'api', getToken: () => 'token' })).resolves.toEqual([])
  })

  it('requires sign-in and makes no request when there is no token', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const error = await captureError(listPatients({ source: 'api', getToken: () => null }))
    expect(error.kind).toBe('unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ends the session when the backend rejects the token (401)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'Invalid or missing authentication credentials' }, 401)),
    )
    const onUnauthorized = vi.fn()
    const error = await captureError(listPatients({ source: 'api', getToken: () => 'expired', onUnauthorized }))
    expect(error.kind).toBe('unauthorized')
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('reports insufficient role (403) without ending the session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Insufficient permissions' }, 403)))
    const onUnauthorized = vi.fn()
    const error = await captureError(listPatients({ source: 'api', getToken: () => 'viewer', onUnauthorized }))
    expect(error.kind).toBe('forbidden')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('reports network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await captureError(listPatients({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('network')
  })

  it('reports server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Internal Server Error' }, 500)))
    const error = await captureError(listPatients({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('server')
  })

  it('succeeds on retry after a transient failure', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Service Unavailable' }, 503))
      .mockResolvedValueOnce(jsonResponse(backendPatients))
    vi.stubGlobal('fetch', fetchMock)
    const options = { source: 'api' as const, getToken: () => 'token' }

    expect((await captureError(listPatients(options))).kind).toBe('network')
    expect((await captureError(listPatients(options))).kind).toBe('server')
    await expect(listPatients(options)).resolves.toHaveLength(3)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('with the real session store, a 401 ends the session and retries require signing in again', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({ access_token: 'jwt', token_type: 'bearer', expires_in: 1800 }))
        .mockResolvedValueOnce(jsonResponse({ id: 1, username: 'test.user', role: 'clinician' }))
        .mockResolvedValueOnce(jsonResponse({ detail: 'Invalid or missing authentication credentials' }, 401)),
    )
    await signIn('test.user', 'not-a-real-password')
    expect(getAuthState().session).not.toBeNull()

    expect((await captureError(listPatients({ source: 'api' }))).kind).toBe('unauthorized')
    expect(getAuthState()).toEqual({ session: null, endedReason: 'expired' })

    const fetchCallsBeforeRetry = vi.mocked(fetch).mock.calls.length
    expect((await captureError(listPatients({ source: 'api' }))).kind).toBe('unauthorized')
    expect(vi.mocked(fetch).mock.calls.length).toBe(fetchCallsBeforeRetry)
  })

  it.each([
    ['an empty body', ''],
    ['an object instead of a list', { items: backendPatients }],
    ['a malformed record', [{ id: 1, first_name: 'Only' }]],
  ])('rejects %s as an invalid response', async (_label, body) => {
    const response = body === '' ? new Response('', { status: 200 }) : jsonResponse(body)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    const error = await captureError(listPatients({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('invalid_response')
  })
})
