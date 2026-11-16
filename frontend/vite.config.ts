/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Not VITE_-prefixed, so it is never exposed to browser code.
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxyTarget = env.API_PROXY_TARGET || 'http://localhost:8000'

  /**
   * Same-origin proxy for local development and `vite preview`.
   * With VITE_API_BASE_URL=/api, browser requests go to /api/* on the Vite
   * server and are forwarded to FastAPI, so the backend needs no CORS config.
   */
  const proxy = {
    '/api': {
      target: apiProxyTarget,
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ''),
    },
  }

  return {
    plugins: [react(), tailwindcss()],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts'],
      restoreMocks: true,
      unstubGlobals: true,
    },
  }
})
