import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from '../config/api'
import { getAccessToken, getAuthState, signIn, signOut, subscribeAuth } from './auth'
import { ApiError } from './http'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const tokenResponse = { access_token: 'jwt-token', token_type: 'bearer', expires_in: 1800 }
const meResponse = { id: 4, username: 'test.scheduler', email: 'scheduler@example.com', role: 'scheduler', is_active: true }

afterEach(() => {
  signOut()
  vi.useRealTimers()
})

describe('signIn', () => {
  it('posts form-encoded credentials to /auth/login and stores an in-memory session', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(tokenResponse))
      .mockResolvedValueOnce(jsonResponse(meResponse))
    vi.stubGlobal('fetch', fetchMock)
    const listener = vi.fn()
    const unsubscribe = subscribeAuth(listener)

    const before = Date.now()
    const session = await signIn('test.scheduler', 'not-a-real-password')
    unsubscribe()

    const [loginUrl, loginInit] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(loginUrl).toBe(`${API_BASE_URL}/auth/login`)
    expect(loginInit.method).toBe('POST')
    expect(loginInit.body).toBeInstanceOf(URLSearchParams)
    expect((loginInit.body as URLSearchParams).get('username')).toBe('test.scheduler')

    const [meUrl, meInit] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(meUrl).toBe(`${API_BASE_URL}/auth/me`)
    expect(meInit.headers).toMatchObject({ Authorization: 'Bearer jwt-token' })

    expect(session.user).toEqual({ id: 4, username: 'test.scheduler', role: 'scheduler' })
    expect(session.expiresAt).toBeGreaterThan(before)
    expect(session.expiresAt).toBeLessThanOrEqual(Date.now() + 1800 * 1000)
    expect(getAccessToken()).toBe('jwt-token')
    expect(listener).toHaveBeenCalled()
  })

  it('reports incorrect credentials (401) without creating a session', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'Incorrect username or password' }, 401)),
    )
    await expect(signIn('someone', 'wrong')).rejects.toMatchObject({
      kind: 'unauthorized',
      message: 'Incorrect username or password.',
    })
    expect(getAuthState().session).toBeNull()
  })

  it.each([
    ['missing access_token', { token: 'x' }],
    ['empty access_token', { ...tokenResponse, access_token: '' }],
    ['non-bearer token_type', { ...tokenResponse, token_type: 'mac' }],
    ['zero expires_in', { ...tokenResponse, expires_in: 0 }],
    ['negative expires_in', { ...tokenResponse, expires_in: -60 }],
    ['string expires_in', { ...tokenResponse, expires_in: '1800' }],
  ])('rejects a token response with %s', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)))
    await expect(signIn('someone', 'pw')).rejects.toMatchObject({ kind: 'invalid_response' })
    expect(getAuthState().session).toBeNull()
  })

  it('does not hide network failures during sign-in', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await signIn('someone', 'pw').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ kind: 'network' })
    expect(getAuthState().session).toBeNull()
  })

  it('still signs in if the profile request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(tokenResponse))
        .mockRejectedValueOnce(new TypeError('Failed to fetch')),
    )
    const session = await signIn('someone', 'pw')
    expect(session.user).toBeNull()
    expect(getAccessToken()).toBe('jwt-token')
  })
})

describe('session lifecycle', () => {
  async function startSession() {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(jsonResponse(tokenResponse)).mockResolvedValueOnce(jsonResponse(meResponse)),
    )
    return signIn('someone', 'pw')
  }

  it('expires the session once the token lifetime has passed', async () => {
    const session = await startSession()
    expect(getAccessToken(session.expiresAt - 1)).toBe('jwt-token')
    expect(getAccessToken(session.expiresAt)).toBeNull()
    expect(getAuthState()).toEqual({ session: null, endedReason: 'expired' })
  })

  it('discards the token on sign-out', async () => {
    await startSession()
    signOut()
    expect(getAccessToken()).toBeNull()
    expect(getAuthState().endedReason).toBe('signed_out')
  })

  it('ends an idle session automatically when the token expires, without any request', async () => {
    vi.useFakeTimers()
    const session = await startSession()
    const listener = vi.fn()
    const unsubscribe = subscribeAuth(listener)

    await vi.advanceTimersByTimeAsync(session.expiresAt - Date.now() - 1)
    expect(getAuthState().session).not.toBeNull()

    await vi.advanceTimersByTimeAsync(1)
    unsubscribe()
    expect(getAuthState()).toEqual({ session: null, endedReason: 'expired' })
    expect(listener).toHaveBeenCalled()
  })

  it('cancels the expiry timer on sign-out so the reason stays "signed_out"', async () => {
    vi.useFakeTimers()
    await startSession()
    signOut()
    await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000)
    expect(getAuthState().endedReason).toBe('signed_out')
  })
})
