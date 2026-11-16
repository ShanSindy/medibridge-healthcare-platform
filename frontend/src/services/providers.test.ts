import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from '../config/api'
import { getAuthState, signIn, signOut } from './auth'
import { ApiError } from './http'
import { getProvider, listProviders } from './providers'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Synthetic test fixtures — not real people.
const backendProviders = [
  { id: 2, first_name: 'Zoe', last_name: 'Sample', specialty: 'Neurology', email: null, phone: null, created_at: '2026-02-01T09:00:00Z' },
  { id: 1, first_name: 'Alex', last_name: 'Example', specialty: 'Cardiology', email: 'alex@example.com', phone: '555-0101', created_at: '2026-01-01T09:00:00Z' },
  { id: 3, first_name: 'Sam', last_name: 'Example', specialty: 'Cardiology', email: null, phone: '555-0103', created_at: '2026-03-01T09:00:00Z' },
]

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('Expected the request to fail')
}

afterEach(() => {
  signOut()
  vi.useRealTimers()
})

describe('providers — demo mode', () => {
  it('lists synthetic records without any network access', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const pending = listProviders({ source: 'demo' })
    await vi.runAllTimersAsync()
    const providers = await pending

    expect(fetchMock).not.toHaveBeenCalled()
    expect(providers.length).toBeGreaterThan(0)
    expect(providers.every((provider) => provider.recordSource === 'demo')).toBe(true)
    expect(providers.every((provider) => provider.email?.endsWith('.example'))).toBe(true)
  })

  it('loads one synthetic record by id without any network access', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const pending = getProvider(1, { source: 'demo' })
    await vi.runAllTimersAsync()

    expect((await pending).id).toBe(1)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reports an unknown demo id as not found', async () => {
    vi.useFakeTimers()
    const pending = captureError(getProvider(9999, { source: 'demo' }))
    await vi.runAllTimersAsync()
    expect((await pending).kind).toBe('not_found')
  })
})

describe('listProviders — API mode', () => {
  it('fetches GET /providers with the Bearer token, maps, and sorts by name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(backendProviders))
    vi.stubGlobal('fetch', fetchMock)

    const providers = await listProviders({ source: 'api', getToken: () => 'token-123' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_BASE_URL}/providers`)
    expect(init.method ?? 'GET').toBe('GET')
    expect(init.headers).toMatchObject({ Authorization: 'Bearer token-123' })
    expect(providers.map((provider) => `${provider.lastName}, ${provider.firstName}`)).toEqual([
      'Example, Alex',
      'Example, Sam',
      'Sample, Zoe',
    ])
    expect(providers[0]).toMatchObject({ id: 1, recordSource: 'api', specialty: 'Cardiology', phone: '555-0101' })
  })

  it('never mixes demo records into an API result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(backendProviders)))
    const providers = await listProviders({ source: 'api', getToken: () => 'token' })
    expect(providers).toHaveLength(3)
    expect(providers.every((provider) => provider.recordSource === 'api')).toBe(true)
    expect(providers.every((provider) => provider.department === null && provider.acceptingNewPatients === null)).toBe(true)
  })

  it('returns an empty list when the backend has no providers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([])))
    await expect(listProviders({ source: 'api', getToken: () => 'token' })).resolves.toEqual([])
  })

  it('requires sign-in and makes no request when there is no token', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const error = await captureError(listProviders({ source: 'api', getToken: () => null }))
    expect(error.kind).toBe('unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ends the session when the backend rejects the token (401)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Invalid credentials' }, 401)))
    const onUnauthorized = vi.fn()
    const error = await captureError(listProviders({ source: 'api', getToken: () => 'expired', onUnauthorized }))
    expect(error.kind).toBe('unauthorized')
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('reports insufficient permissions (403) without ending the session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Insufficient permissions' }, 403)))
    const onUnauthorized = vi.fn()
    const error = await captureError(listProviders({ source: 'api', getToken: () => 'token', onUnauthorized }))
    expect(error.kind).toBe('forbidden')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('reports network failures and does not fall back to demo data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await captureError(listProviders({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('network')
  })

  it('reports server errors without leaking the response detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'psycopg.OperationalError: secret-host' }, 500)))
    const error = await captureError(listProviders({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('server')
    expect(error.message).not.toContain('secret-host')
  })

  it.each([
    ['an object instead of a list', { items: backendProviders }],
    ['a malformed record', [backendProviders[0], { ...backendProviders[1], specialty: null }]],
  ])('rejects %s as an invalid response', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)))
    const error = await captureError(listProviders({ source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('invalid_response')
  })

  it('succeeds on retry after a failure', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse(backendProviders))
    vi.stubGlobal('fetch', fetchMock)

    await captureError(listProviders({ source: 'api', getToken: () => 'token' }))
    await expect(listProviders({ source: 'api', getToken: () => 'token' })).resolves.toHaveLength(3)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('with the real session store: a 401 signs the user out and blocks further requests', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ access_token: 'jwt-abc', token_type: 'bearer', expires_in: 1800 }))
      .mockResolvedValueOnce(jsonResponse({ id: 1, username: 'tester', email: 't@example.com', role: 'viewer', is_active: true }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Token expired' }, 401))
    vi.stubGlobal('fetch', fetchMock)

    await signIn('tester', 'not-a-real-password')
    expect(getAuthState().session).not.toBeNull()

    const error = await captureError(listProviders({ source: 'api' }))
    expect(error.kind).toBe('unauthorized')
    expect(getAuthState().session).toBeNull()
    expect(getAuthState().endedReason).toBe('expired')

    await captureError(listProviders({ source: 'api' }))
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})

describe('getProvider — API mode', () => {
  it('fetches GET /providers/{id} with the Bearer token and maps the record', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(backendProviders[1]))
    vi.stubGlobal('fetch', fetchMock)

    const provider = await getProvider(1, { source: 'api', getToken: () => 'token-123' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_BASE_URL}/providers/1`)
    expect(init.headers).toMatchObject({ Authorization: 'Bearer token-123' })
    expect(provider).toMatchObject({ id: 1, firstName: 'Alex', recordSource: 'api', createdAt: '2026-01-01T09:00:00Z' })
  })

  it('reports a provider that no longer exists (404)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Provider not found' }, 404)))
    const onUnauthorized = vi.fn()
    const error = await captureError(getProvider(77, { source: 'api', getToken: () => 'token', onUnauthorized }))
    expect(error.kind).toBe('not_found')
    expect(error.status).toBe(404)
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it.each([0, -1, 1.5, Number.NaN])('rejects the invalid id %s without making a request', async (id) => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const error = await captureError(getProvider(id, { source: 'api', getToken: () => 'token' }))
    expect(error.kind).toBe('not_found')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('makes no request without a token, and ends the session on 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'Invalid credentials' }, 401))
    vi.stubGlobal('fetch', fetchMock)

    const signedOut = await captureError(getProvider(1, { source: 'api', getToken: () => null }))
    expect(signedOut.kind).toBe('unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()

    const onUnauthorized = vi.fn()
    const rejected = await captureError(getProvider(1, { source: 'api', getToken: () => 'expired', onUnauthorized }))
    expect(rejected.kind).toBe('unauthorized')
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('reports forbidden, network and malformed responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'Insufficient permissions' }, 403)))
    expect((await captureError(getProvider(1, { source: 'api', getToken: () => 'token' }))).kind).toBe('forbidden')

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    expect((await captureError(getProvider(1, { source: 'api', getToken: () => 'token' }))).kind).toBe('network')

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([backendProviders[0]])))
    expect((await captureError(getProvider(1, { source: 'api', getToken: () => 'token' }))).kind).toBe('invalid_response')
  })
})
