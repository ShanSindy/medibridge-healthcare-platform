/**
 * Authentication against the MediBridge FastAPI backend.
 *
 * Uses the backend's existing model: `POST /auth/login` (form-encoded
 * username/password) issues a short-lived JWT, which is sent as a Bearer
 * token. Authorization (roles/permissions) is enforced by the backend.
 *
 * The access token is held in memory only — never in localStorage or
 * sessionStorage — so it is discarded on sign-out or page reload. The
 * backend has no refresh-token or logout endpoint; signing out simply
 * discards the token client-side.
 */
import { apiRequest, ApiError, isApiError } from './http'

export interface AuthUser {
  id: number
  username: string
  role: string
}

export interface AuthSession {
  accessToken: string
  /** Epoch milliseconds after which the token is treated as expired. */
  expiresAt: number
  /** Null if `/auth/me` could not be loaded; authorization still happens server-side. */
  user: AuthUser | null
}

export type SessionEndReason = 'expired' | 'signed_out' | null

export interface AuthState {
  session: AuthSession | null
  endedReason: SessionEndReason
}

/** Treat tokens as expired slightly early to avoid using one mid-expiry. */
const EXPIRY_SKEW_MS = 30_000

let state: AuthState = { session: null, endedReason: null }
const listeners = new Set<() => void>()
const MAX_TIMER_DELAY_MS = 2 ** 31 - 1
/** Ends the session when the token expires, even if no request is made. */
let expiryTimer: ReturnType<typeof setTimeout> | null = null

function setState(next: AuthState) {
  if (expiryTimer !== null) {
    clearTimeout(expiryTimer)
    expiryTimer = null
  }
  state = next
  if (next.session) {
    // Clears patient data from screen and prompts sign-in on an idle, expired session.
    // setTimeout overflows above 2^31-1 ms; getAccessToken() still enforces expiry.
    const delay = Math.min(Math.max(0, next.session.expiresAt - Date.now()), MAX_TIMER_DELAY_MS)
    expiryTimer = setTimeout(() => getAccessToken(), delay)
  }
  listeners.forEach((listener) => listener())
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Stable snapshot for `useSyncExternalStore`. */
export function getAuthState(): AuthState {
  return state
}

/** Returns a usable access token, or null (clearing the session) if it has expired. */
export function getAccessToken(now: number = Date.now()): string | null {
  const { session } = state
  if (!session) return null
  if (now >= session.expiresAt) {
    endSession('expired')
    return null
  }
  return session.accessToken
}

export function endSession(reason: Exclude<SessionEndReason, null>): void {
  if (state.session) setState({ session: null, endedReason: reason })
}

export function signOut(): void {
  endSession('signed_out')
}

function parseTokenResponse(payload: unknown): { accessToken: string; expiresInSeconds: number } {
  if (typeof payload === 'object' && payload !== null) {
    const { access_token, token_type, expires_in } = payload as Record<string, unknown>
    if (
      typeof access_token === 'string' &&
      access_token.length > 0 &&
      typeof token_type === 'string' &&
      token_type.toLowerCase() === 'bearer' &&
      typeof expires_in === 'number' &&
      Number.isFinite(expires_in) &&
      expires_in > 0
    ) {
      return { accessToken: access_token, expiresInSeconds: expires_in }
    }
  }
  throw new ApiError('invalid_response', 'The sign-in response from the MediBridge API was not in the expected format.')
}

function parseUser(payload: unknown): AuthUser | null {
  if (typeof payload !== 'object' || payload === null) return null
  const { id, username, role } = payload as Record<string, unknown>
  if (typeof id === 'number' && typeof username === 'string' && typeof role === 'string') {
    return { id, username, role }
  }
  return null
}

/**
 * Signs in with the backend and starts an in-memory session.
 * Credentials are sent only to the configured API and are never stored.
 */
export async function signIn(username: string, password: string): Promise<AuthSession> {
  let payload: unknown
  try {
    payload = await apiRequest('/auth/login', {
      method: 'POST',
      body: new URLSearchParams({ username, password }),
    })
  } catch (error) {
    if (isApiError(error, 'unauthorized')) {
      throw new ApiError('unauthorized', 'Incorrect username or password.', 401)
    }
    throw error
  }

  const { accessToken, expiresInSeconds } = parseTokenResponse(payload)
  const lifetimeMs = expiresInSeconds * 1000
  const expiresAt = Date.now() + (lifetimeMs > EXPIRY_SKEW_MS * 2 ? lifetimeMs - EXPIRY_SKEW_MS : lifetimeMs)

  // Profile is informational (display name and role); failure here is not fatal.
  let user: AuthUser | null
  try {
    user = parseUser(await apiRequest('/auth/me', { token: accessToken }))
  } catch {
    user = null
  }

  const session: AuthSession = { accessToken, expiresAt, user }
  setState({ session, endedReason: null })
  return session
}
