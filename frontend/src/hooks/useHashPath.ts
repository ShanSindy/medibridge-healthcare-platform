import { useSyncExternalStore } from 'react'

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

function getSnapshot() {
  return window.location.hash
}

/**
 * Minimal hash-based router: `#/patients` → `/patients`.
 *
 * Hash routing works with any static host and needs no server rewrites or
 * extra dependencies. It can be swapped for a full router later without
 * changing pages, since routes are defined centrally in `src/routes.ts`.
 */
export function useHashPath(): string {
  const hash = useSyncExternalStore(subscribe, getSnapshot)
  const path = hash.replace(/^#/, '').split('?')[0]
  return path === '' ? '/' : path
}
