import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./tests",
  testMatch: ["research.spec.ts", "chat.spec.ts"],
  fullyParallel: true,
  workers: 2,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:5187",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "bun run vite --host 127.0.0.1 --port 5187 --strictPort",
    url: "http://127.0.0.1:5187",
    reuseExistingServer: !process.env.CI,
  },
})
