import { DATA_SOURCE, type DataSource } from '../config/api'
import { demoRequest } from './demoRequest'
import { apiRequest } from './http'

/**
 * `not_connected` means the dashboard is not configured to ask, and
 * `not_reported` means the backend has no way to say. Neither is ever
 * presented as healthy.
 */
export type CheckState = 'operational' | 'unavailable' | 'not_connected' | 'not_reported'

export interface SystemCheck {
  id: string
  name: string
  state: CheckState
  detail: string
}

export interface SystemStatus {
  checks: SystemCheck[]
}

const DEMO_NOT_CONNECTED = 'Demo mode does not contact the backend.'

/**
 * Reports only what the selected data source can actually observe. Probe
 * failures are returned as `unavailable` checks rather than thrown, so the
 * status panel can always render.
 */
export function getSystemStatus({ source = DATA_SOURCE }: { source?: DataSource } = {}): Promise<SystemStatus> {
  return source === 'api' ? getApiStatus() : getDemoStatus()
}

async function getDemoStatus(): Promise<SystemStatus> {
  let dataService: SystemCheck
  try {
    await demoRequest(() => true)
    dataService = {
      id: 'demo-data',
      name: 'Demo data service',
      state: 'operational',
      detail: 'Synthetic in-browser records are loading normally.',
    }
  } catch (error) {
    dataService = {
      id: 'demo-data',
      name: 'Demo data service',
      state: 'unavailable',
      detail: error instanceof Error ? error.message : 'The demo data service did not respond.',
    }
  }

  return {
    checks: [
      dataService,
      { id: 'api', name: 'MediBridge API', state: 'not_connected', detail: DEMO_NOT_CONNECTED },
      { id: 'database', name: 'PostgreSQL database', state: 'not_connected', detail: DEMO_NOT_CONNECTED },
      { id: 'integrations', name: 'HL7 v2 and FHIR endpoints', state: 'not_connected', detail: DEMO_NOT_CONNECTED },
    ],
  }
}

function reportsHealthy(payload: unknown): boolean {
  return typeof payload === 'object' && payload !== null && (payload as { status?: unknown }).status === 'healthy'
}

/** `GET /health` is public and reports only that the API process is up. */
async function getApiStatus(): Promise<SystemStatus> {
  let api: SystemCheck
  try {
    api = reportsHealthy(await apiRequest('/health'))
      ? { id: 'api', name: 'MediBridge API', state: 'operational', detail: 'GET /health reported healthy.' }
      : {
          id: 'api',
          name: 'MediBridge API',
          state: 'unavailable',
          detail: 'GET /health responded but did not report a healthy status.',
        }
  } catch (error) {
    api = {
      id: 'api',
      name: 'MediBridge API',
      state: 'unavailable',
      detail: error instanceof Error ? error.message : 'The API did not respond.',
    }
  }

  return {
    checks: [
      api,
      {
        id: 'database',
        name: 'PostgreSQL database',
        state: 'not_reported',
        detail: 'The health endpoint does not report database status.',
      },
      {
        id: 'integrations',
        name: 'HL7 v2 and FHIR endpoints',
        state: 'not_reported',
        detail: 'The backend exposes no status endpoint for these.',
      },
    ],
  }
}
