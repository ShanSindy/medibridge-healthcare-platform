import type { ReactNode } from 'react'
import type { AsyncState } from '../../hooks/useAsync'
import { ErrorState, LoadingState } from './States'

interface AsyncContentProps<T> {
  state: AsyncState<T>
  onRetry: () => void
  loadingLabel: string
  errorTitle?: string
  children: (data: T) => ReactNode
}

/** Renders the loading / error / success branches of an `AsyncState`. */
export function AsyncContent<T>({ state, onRetry, loadingLabel, errorTitle, children }: AsyncContentProps<T>) {
  if (state.status === 'loading') return <LoadingState label={loadingLabel} />
  if (state.status === 'error') {
    return <ErrorState title={errorTitle} message={state.error.message} onRetry={onRetry} />
  }
  return <>{children(state.data)}</>
}
