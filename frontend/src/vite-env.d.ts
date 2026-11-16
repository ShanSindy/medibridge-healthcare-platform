/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `demo` (default) or `api`. Selects the Patients page data source. */
  readonly VITE_DATA_SOURCE?: string
  /** Base URL of the MediBridge FastAPI backend. Defaults to http://localhost:8000. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
