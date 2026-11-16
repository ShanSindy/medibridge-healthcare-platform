import { loadSettings } from './settingsStore'

export class ApiError extends Error {
  readonly status: number | undefined

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Resolves synthetic demo data asynchronously, mimicking a network request.
 *
 * Latency and failure simulation are controlled from Settings → Demo
 * environment. Data is cloned so callers can never mutate the demo source.
 *
 * When FastAPI integration is added, services will call a real HTTP client
 * (using `API_BASE_URL`) instead of this function, keeping the same
 * `Promise<T>` contract so pages and hooks do not change.
 */
export function demoRequest<T>(produce: () => T): Promise<T> {
  const { latencyMs, simulateFailure } = loadSettings().demo

  return new Promise<T>((resolve, reject) => {
    window.setTimeout(() => {
      if (simulateFailure) {
        reject(
          new ApiError(
            'The demo data service is simulating a network failure. Turn off “Simulate API failure” in Settings to restore data.',
            503,
          ),
        )
        return
      }
      resolve(structuredClone(produce()))
    }, latencyMs)
  })
}
