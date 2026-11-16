import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSystemStatus, type SystemStatus } from './systemStatus'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const check = (status: SystemStatus, id: string) => status.checks.find((item) => item.id === id)

afterEach(() => {
  vi.useRealTimers()
})

describe('getSystemStatus — API mode', () => {
  it('reports the API as operational when GET /health says healthy', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'healthy', service: 'MediBridge API' }))
    vi.stubGlobal('fetch', fetchMock)

    const status = await getSystemStatus({ source: 'api' })

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/health$/)
    expect(check(status, 'api')?.state).toBe('operational')
  })

  it('never reports the database or integrations as healthy, because the backend does not expose them', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ status: 'healthy' })))

    const status = await getSystemStatus({ source: 'api' })

    expect(check(status, 'database')?.state).toBe('not_reported')
    expect(check(status, 'integrations')?.state).toBe('not_reported')
    expect(status.checks.filter((item) => item.state === 'operational')).toHaveLength(1)
  })

  it.each([
    ['a different status value', { status: 'degraded' }],
    ['no status field', { service: 'MediBridge API' }],
    ['a non-object body', 'ok'],
  ])('reports unavailable when /health responds with %s', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)))

    const status = await getSystemStatus({ source: 'api' })

    expect(check(status, 'api')?.state).toBe('unavailable')
  })

  it('reports unavailable, without throwing, when the API cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const status = await getSystemStatus({ source: 'api' })

    expect(check(status, 'api')?.state).toBe('unavailable')
    expect(check(status, 'api')?.detail).toBeTruthy()
  })

  it('reports unavailable when the API returns a server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'boom' }, 503)))

    const status = await getSystemStatus({ source: 'api' })

    expect(check(status, 'api')?.state).toBe('unavailable')
    expect(check(status, 'api')?.detail).not.toContain('boom')
  })
})

describe('getSystemStatus — demo mode', () => {
  it('makes no network request and marks backend services as not connected', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const pending = getSystemStatus({ source: 'demo' })
    await vi.runAllTimersAsync()
    const status = await pending

    expect(fetchMock).not.toHaveBeenCalled()
    expect(check(status, 'demo-data')?.state).toBe('operational')
    for (const id of ['api', 'database', 'integrations']) {
      expect(check(status, id)?.state).toBe('not_connected')
    }
  })
})
