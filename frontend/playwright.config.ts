import { defineConfig, devices } from '@playwright/test';

/**
 * Tests de bout en bout contre la pile réelle (backend FastAPI + PostgreSQL + frontend).
 * Pré-requis et données : voir e2e/README.md. Les tests ne démarrent aucun serveur.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    locale: 'fr-FR',
    trace: 'retain-on-failure',
    launchOptions: process.env.E2E_CHROMIUM_PATH
      ? { executablePath: process.env.E2E_CHROMIUM_PATH }
      : {},
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] }, grepInvert: /@mobile/ },
    {
      name: 'mobile',
      use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, hasTouch: true },
      grep: /@mobile/,
    },
  ],
});
