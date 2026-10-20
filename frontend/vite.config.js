import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The SPA always calls a relative /api: nginx proxies it in the container,
// this dev proxy does the same for `npm run dev` against a local gateway.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.API_TARGET || 'http://localhost:8080', changeOrigin: true } },
  },
  build: { sourcemap: true, chunkSizeWarningLimit: 900 },
  test: { environment: 'jsdom', globals: true },
})
