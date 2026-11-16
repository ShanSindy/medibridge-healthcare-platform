import { useSyncExternalStore } from 'react'
import { getAuthState, subscribeAuth, type AuthState } from '../services/auth'

/** Current in-memory authentication state; re-renders on sign-in/out. */
export function useAuthSession(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState)
}
