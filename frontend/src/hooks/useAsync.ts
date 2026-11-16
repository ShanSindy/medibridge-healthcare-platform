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
 */
export function useAsync<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false

    fetcher().then(
      (data) => {
        if (!cancelled) setState({ status: 'success', data })
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({
            status: 'error',
            error: error instanceof Error ? error : new Error('An unexpected error occurred.'),
          })
        }
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

  return { state, reload }
}
