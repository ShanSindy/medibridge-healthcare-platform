import { useCallback, useEffect, useState } from 'react'

export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: Error }
  | { status: 'success'; data: T }

/**
 * Runs an async fetcher on mount and exposes loading / error / success state.
 *
 * `fetcher` must be referentially stable (a module-level function or a
 * memoised callback); changing it triggers a refetch.
 *
 * `reload` clears the view back to the loading state (used by "Try again").
 * `refresh` refetches in the background and keeps the current data on screen
 * until the new result arrives; a failed refresh surfaces as the error state.
 */
export function useAsync<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false

    fetcher().then(
      (data) => {
        if (cancelled) return
        setState({ status: 'success', data })
        setLastSuccessAt(Date.now())
        setRefreshing(false)
      },
      (error: unknown) => {
        if (cancelled) return
        setState({
          status: 'error',
          error: error instanceof Error ? error : new Error('An unexpected error occurred.'),
        })
        setRefreshing(false)
      },
    )

    return () => {
      cancelled = true
    }
  }, [fetcher, attempt])

  const reload = useCallback(() => {
    setState({ status: 'loading' })
    setAttempt((value) => value + 1)
  }, [])

  const refresh = useCallback(() => {
    setRefreshing(true)
    setAttempt((value) => value + 1)
  }, [])

  return { state, reload, refresh, refreshing, lastSuccessAt }
}
