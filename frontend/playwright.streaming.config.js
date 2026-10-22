import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const python = resolve(
  process.platform === 'win32' ? '../venv/Scripts/python.exe' : '../venv/bin/python',
)

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'streaming.spec.js',
  outputDir: 'streaming-test-results',
  workers: 1,
  reporter: 'list',
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:15173',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } },
    },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command: `"${python}" -m uvicorn tests.streaming_app:app --app-dir ../backend --host 127.0.0.1 --port 18080`,
      url: 'http://127.0.0.1:18080/api/health',
      reuseExistingServer: false,
      env: { PYTHONIOENCODING: 'utf-8' },
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 15173',
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      env: { API_PROXY_TARGET: 'http://127.0.0.1:18080' },
    },
  ],
})
