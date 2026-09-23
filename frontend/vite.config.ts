import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// En développement, /api est relayé vers le backend FastAPI (même origine côté navigateur).
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
  test: {
    environment: 'node',
  },
});
