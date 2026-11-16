import { ApiError } from './http'
import { loadSettings } from './settingsStore'

/**
 * Resolves synthetic demo data asynchronously, mimicking a network request.
 *
 * Latency and failure simulation are controlled from Settings → Demo
 * environment. Data is cloned so callers can never mutate the demo source.
 * Live API requests go through `apiRequest` in `./http` instead.
 */
export function demoRequest<T>(produce: () => T): Promise<T> {
  const { latencyMs, simulateFailure } = loadSettings().demo

  return new Promise<T>((resolve, reject) => {
    setTimeout(() => {
      if (simulateFailure) {
        reject(
          new ApiError(
            'network',
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
