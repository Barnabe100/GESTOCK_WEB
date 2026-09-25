import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// En développement, /api est relayé vers le backend FastAPI (même origine côté navigateur) et
// /platform-api vers la console TechNova (processus distinct, ADR-0031).
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';
const platformTarget = process.env.VITE_PLATFORM_API_PROXY_TARGET ?? 'http://localhost:8001';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/platform-api': { target: platformTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'node',
  },
});
