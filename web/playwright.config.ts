import { defineConfig, devices } from "@playwright/test";

// The browser test runs against the real Gateway and Agent API with the scripted fake model,
// all started fresh by e2e/servers.py (needs Postgres: make db).
const port = 5174;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://localhost:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "uv run python web/e2e/servers.py",
    cwd: "..",
    url: `http://localhost:${port}`,
    env: { WEB_PORT: String(port) },
    reuseExistingServer: false,
    timeout: 90_000,
    stdout: "ignore",
    // SIGTERM, not the default SIGKILL, so servers.py stops its servers and drops its database.
    gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
  },
});
