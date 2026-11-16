/**
 * API configuration.
 *
 * The dashboard currently runs entirely on synthetic in-browser demo data.
 * `API_BASE_URL` is defined so services can switch to the FastAPI backend
 * without touching UI components. Nothing calls it yet.
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const DATA_SOURCE: 'demo' | 'api' = 'demo'
