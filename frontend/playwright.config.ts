import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.MNEMONIC_E2E_WEB_URL ?? "http://127.0.0.1:33000";

export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/e2e/global.setup.ts",
  outputDir: "./test-results/playwright",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    permissions: ["clipboard-read", "clipboard-write"]
  },
  projects: [
    { name: "chromium-desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "chromium-narrow", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } } }
  ]
});
