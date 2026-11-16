import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, ApiError, buildUrl, extractErrorDetail } from './http'

const BASE = 'http://api.test'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

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
  vi.useRealTimers()
})

describe('buildUrl', () => {
  it('joins base URL and path with exactly one slash', () => {
    expect(buildUrl('http://localhost:8000', '/patients')).toBe('http://localhost:8000/patients')
    expect(buildUrl('http://localhost:8000/', 'patients')).toBe('http://localhost:8000/patients')
    expect(buildUrl('/api', '/patients')).toBe('/api/patients')
  })
})

describe('extractErrorDetail', () => {
  it('reads FastAPI string and validation-list details', () => {
    expect(extractErrorDetail({ detail: 'Patient not found' })).toBe('Patient not found')
    expect(extractErrorDetail({ detail: [{ msg: 'field required' }, { msg: 'bad date' }] })).toBe(
      'field required; bad date',
    )
  })

  it('returns null when there is no usable detail', () => {
    expect(extractErrorDetail(null)).toBeNull()
    expect(extractErrorDetail({ message: 'x' })).toBeNull()
    expect(extractErrorDetail({ detail: [] })).toBeNull()
  })
})

describe('apiRequest', () => {
  it('sends Accept and Bearer headers without cookies and returns parsed JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ ok: true }]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest('/patients', { baseUrl: BASE, token: 'abc' })).resolves.toEqual([{ ok: true }])

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${BASE}/patients`)
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('omit')
    expect(init.headers).toMatchObject({ Accept: 'application/json', Authorization: 'Bearer abc' })
  })

  it('omits the Authorization header when no token is given', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}))
    vi.stubGlobal('fetch', fetchMock)
    await apiRequest('/health', { baseUrl: BASE })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.headers).not.toHaveProperty('Authorization')
  })

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [422, 'validation'],
    [500, 'server'],
    [503, 'server'],
    [418, 'http'],
  ] as const)('maps HTTP %i to "%s"', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'nope' }, status)))
    const error = await captureError(apiRequest('/x', { baseUrl: BASE }))
    expect(error.kind).toBe(kind)
    expect(error.status).toBe(status)
  })

  it('does not leak server error details for 5xx responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'psycopg traceback' }, 500)))
    const error = await captureError(apiRequest('/x', { baseUrl: BASE }))
    expect(error.message).not.toContain('psycopg')
  })

  it('handles error responses with non-JSON bodies', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 })))
    const error = await captureError(apiRequest('/x', { baseUrl: BASE }))
    expect(error.kind).toBe('server')
  })

  it('reports a successful response that is not JSON as invalid', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>index</html>', { status: 200 })))
    const error = await captureError(apiRequest('/x', { baseUrl: BASE }))
    expect(error.kind).toBe('invalid_response')
  })

  it('reports network failures (server down, DNS, CORS) as network errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await captureError(apiRequest('/x', { baseUrl: BASE }))
    expect(error.kind).toBe('network')
    expect(error.message).toContain(BASE)
  })

  it('aborts and reports a timeout when the server does not respond', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
          }),
      ),
    )
    const pending = captureError(apiRequest('/x', { baseUrl: BASE, timeoutMs: 1000 }))
    await vi.advanceTimersByTimeAsync(1000)
    const error = await pending
    expect(error.kind).toBe('timeout')
  })
})
