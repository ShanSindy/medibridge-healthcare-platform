/**
 * Application preferences, persisted to this browser's localStorage only.
 * Nothing here is sent to the MediBridge backend.
 */

export interface AppSettings {
  organizationName: string
  timeZone: string
  dateFormat: 'MMM d, yyyy' | 'MM/dd/yyyy' | 'yyyy-MM-dd'
  notifications: {
    appointmentReminders: boolean
    integrationFailures: boolean
    weeklySummary: boolean
  }
  demo: {
    /** Artificial delay applied to demo requests, so loading states are visible. */
    latencyMs: number
    /** Makes every demo request fail, so error states can be reviewed. */
    simulateFailure: boolean
  }
}

const STORAGE_KEY = 'medibridge.settings.v1'

export const DEFAULT_SETTINGS: AppSettings = {
  organizationName: 'MediBridge Demo Health System',
  timeZone: 'America/Los_Angeles',
  dateFormat: 'MMM d, yyyy',
  notifications: {
    appointmentReminders: true,
    integrationFailures: true,
    weeklySummary: false,
  },
  demo: {
    latencyMs: 450,
    simulateFailure: false,
  },
}

export function loadSettings(): AppSettings {
  const defaults = structuredClone(DEFAULT_SETTINGS)
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaults
    const stored = JSON.parse(raw) as Partial<AppSettings>
    return {
      ...defaults,
      ...stored,
      notifications: { ...defaults.notifications, ...stored.notifications },
      demo: { ...defaults.demo, ...stored.demo },
    }
  } catch {
    return defaults
  }
}

/** Returns false when storage is unavailable (e.g. private browsing restrictions). */
export function saveSettings(settings: AppSettings): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    return true
  } catch {
    return false
  }
}

export function clearSettings(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage unavailable; nothing to clear.
  }
}
