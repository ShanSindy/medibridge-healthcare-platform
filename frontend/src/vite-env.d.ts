/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the MediBridge FastAPI backend (not used until API integration). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
