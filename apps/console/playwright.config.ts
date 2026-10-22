import { defineConfig } from "@playwright/test";

// G6b: the console against a real local stack (Supabase CLI, the API and the worker), §13.
// `make test-e2e` builds the console first; Supabase must already be running (`supabase start`).

const repo = new URL("../..", import.meta.url).pathname;
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      name: "api",
      command: "uv run --frozen python -m sunkcost_api.app",
      cwd: repo,
      url: "http://127.0.0.1:8000/readyz",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        DATABASE_URL: "postgresql://sunkcost_api:sunkcost_api_local@127.0.0.1:54322/postgres",
        SUPABASE_URL: supabaseUrl,
        CONSOLE_ORIGIN: "http://127.0.0.1:3000",
        LOG_LEVEL: "WARNING",
      },
    },
    {
      name: "worker",
      command: "uv run --frozen python -m sunkcost_worker.service.main",
      cwd: repo,
      url: "http://127.0.0.1:8081/healthz",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        DATABASE_URL: "postgresql://sunkcost_worker:sunkcost_worker_local@127.0.0.1:54322/postgres",
        // The public RFC 8032 test key (never valid in production), as in docker-compose.yml.
        BUNDLE_SIGNING_KEY: "nWGxne/9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A=",
        BUNDLE_SIGNING_KEY_ID: "rfc8032-test-1",
        POLL_INTERVAL_S: "0.5",
        SCHEDULE_INTERVAL_S: "86400",
        HEALTH_PORT: "8081",
        LOG_LEVEL: "WARNING",
      },
    },
    {
      name: "console",
      command: "pnpm start",
      url: "http://127.0.0.1:3000/login",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
