import { expect, request, test, type APIRequestContext } from "@playwright/test";
import type { ProjectSettings } from "../../lib/types";

async function apiClient(): Promise<APIRequestContext> {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("The disposable E2E API is not configured.");
  return request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${apiKey}` } });
}

async function createProject(api: APIRequestContext, name: string): Promise<string> {
  const response = await api.post("/api/v1/projects", {
    data: { name, slug: `leases-${crypto.randomUUID()}`, description: "Variable work lease acceptance." }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { id: string }).id;
}

async function getSettings(api: APIRequestContext, projectId: string): Promise<ProjectSettings> {
  const response = await api.get(`/api/v1/projects/${projectId}/settings`);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as ProjectSettings;
}

async function configure(api: APIRequestContext, projectId: string, values: Partial<ProjectSettings>) {
  const current = await getSettings(api, projectId);
  const response = await api.patch(`/api/v1/projects/${projectId}/settings`, {
    data: { expected_revision: current.revision, ...values }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as ProjectSettings;
}

test("work lease durations validate, persist, and stay scoped to the selected project", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const projectId = await createProject(api, "Variable lease settings");
    const otherProjectId = await createProject(api, "Independent lease settings");
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(projectId);
    const details = page.getByRole("region", { name: "Project details", exact: true });
    const defaultMinutes = details.getByLabel("Default (minutes)", { exact: true });
    const minimumMinutes = details.getByLabel("Minimum (minutes)", { exact: true });
    const maximumMinutes = details.getByLabel("Maximum (minutes)", { exact: true });
    const save = details.getByRole("button", { name: "Save lease durations" });
    await expect(defaultMinutes).toHaveValue("15");
    await expect(minimumMinutes).toHaveValue("10");
    await expect(maximumMinutes).toHaveValue("120");
    await expect(save).toBeDisabled();
    const screenshotPath = testInfo.outputPath(`workspace-variable-leases-${testInfo.project.name}.png`);
    await details.screenshot({ path: screenshotPath });
    await testInfo.attach("Workspace lease settings", { path: screenshotPath, contentType: "image/png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);

    await defaultMinutes.fill("9");
    await expect(save).toBeDisabled();
    await expect(details.getByRole("alert")).toContainText("Minimum must be at most Default");
    await defaultMinutes.fill("25");
    await minimumMinutes.fill("0");
    await expect(save).toBeDisabled();
    await minimumMinutes.fill("1.5");
    await expect(save).toBeDisabled();
    await minimumMinutes.fill("20");
    await maximumMinutes.fill("24");
    await expect(save).toBeDisabled();
    await maximumMinutes.fill("240");
    const responsePromise = page.waitForResponse((response) => response.request().method() === "PATCH"
      && response.url().endsWith(`/projects/${projectId}/settings`));
    await save.click();
    const response = await responsePromise;
    expect(response.ok()).toBe(true);
    expect(response.request().postDataJSON()).toMatchObject({
      lease_default_minutes: 25, lease_minimum_minutes: 20, lease_maximum_minutes: 240
    });
    await expect(save).toBeDisabled();
    expect(await getSettings(api, projectId)).toMatchObject({
      lease_default_minutes: 25, lease_minimum_minutes: 20, lease_maximum_minutes: 240
    });
    await defaultMinutes.fill("30");
    await page.locator("#project-select").selectOption(otherProjectId);
    await expect(defaultMinutes).toHaveValue("15");
    await expect(minimumMinutes).toHaveValue("10");
    await expect(maximumMinutes).toHaveValue("120");
    await page.locator("#project-select").selectOption(projectId);
    await expect(defaultMinutes).toHaveValue("25");
    await expect(maximumMinutes).toHaveValue("240");
    await page.reload();
    await expect(defaultMinutes).toHaveValue("25");
    await expect(minimumMinutes).toHaveValue("20");
    await expect(maximumMinutes).toHaveValue("240");
  } finally {
    await api.dispose();
  }
});

test("lease drafts survive background refresh and require review after a settings revision conflict", async ({ page }) => {
  const api = await apiClient();
  try {
    const projectId = await createProject(api, "Lease settings revision conflicts");
    let sendSync: ((message: string) => void) | undefined;
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
      sendSync = (message) => socket.send(message);
    });
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(projectId);
    const defaultMinutes = page.getByLabel("Default (minutes)", { exact: true });
    const save = page.getByRole("button", { name: "Save lease durations" });
    await expect(defaultMinutes).toHaveValue("15");
    await defaultMinutes.fill("25");
    await expect.poll(() => Boolean(sendSync)).toBe(true);
    const background = page.waitForResponse((response) => response.request().method() === "GET"
      && response.url().endsWith(`/projects/${projectId}/settings`));
    sendSync!(JSON.stringify({ type: "invalidate", scope: "projects", revision: 1 }));
    await background;
    await expect(defaultMinutes).toHaveValue("25");
    await expect(save).toBeEnabled();

    await configure(api, projectId, { lease_default_minutes: 20, lease_maximum_minutes: 240 });
    sendSync!(JSON.stringify({ type: "invalidate", scope: "projects", revision: 2 }));
    await expect(page.getByText("Saved durations: Default 20 minutes;", { exact: false })).toBeVisible();
    await expect(defaultMinutes).toHaveValue("25");
    await expect(save).toBeDisabled();
    await page.getByRole("button", { name: "I reviewed the saved durations" }).click();
    await expect(defaultMinutes).toBeEnabled();
    await save.click();
    await expect(page.locator(".toast[role=status]")).toContainText("Work lease durations saved.");
    await expect(save).toBeDisabled();
    expect(await getSettings(api, projectId)).toMatchObject({ lease_default_minutes: 25, lease_maximum_minutes: 120 });

    // An editor can also win the race after the last refresh and before this tab saves.
    await defaultMinutes.fill("30");
    await configure(api, projectId, { lease_default_minutes: 35 });
    await save.click();
    await expect(page.getByText("Saved durations: Default 35 minutes;", { exact: false })).toBeVisible();
    await expect(defaultMinutes).toHaveValue("30");
    await expect(save).toBeDisabled();
    await page.getByRole("button", { name: "I reviewed the saved durations" }).click();
    await save.click();
    await expect(save).toBeDisabled();
    expect((await getSettings(api, projectId)).lease_default_minutes).toBe(30);
  } finally {
    await api.dispose();
  }
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((settle) => { resolve = settle; });
  return { promise, resolve };
}

test("a settings refresh during a pending lease save cannot leave a successful save conflicted", async ({ page }) => {
  const api = await apiClient();
  const releaseSave = deferred();
  try {
    const projectId = await createProject(api, "Lease save refresh race");
    const saveCommitted = deferred();
    let sendSync: ((message: string) => void) | undefined;
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
      sendSync = (message) => socket.send(message);
    });
    await page.route(`**/api/mnemonic/projects/${projectId}/settings`, async (route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      saveCommitted.resolve();
      await releaseSave.promise;
      await route.fulfill({ response });
    });
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(projectId);
    const defaultMinutes = page.getByLabel("Default (minutes)", { exact: true });
    await expect(defaultMinutes).toHaveValue("15");
    await defaultMinutes.fill("25");
    await page.getByRole("button", { name: "Save lease durations" }).click();
    await saveCommitted.promise;
    await expect.poll(() => Boolean(sendSync)).toBe(true);
    sendSync!(JSON.stringify({ type: "invalidate", scope: "projects", revision: 1 }));
    await expect(page.getByText("Saved durations: Default 25 minutes;", { exact: false })).toBeVisible();
    releaseSave.resolve();
    await expect(page.locator(".toast[role=status]")).toContainText("Work lease durations saved.");
    await expect(page.getByRole("button", { name: "I reviewed the saved durations" })).toHaveCount(0);
    await expect(defaultMinutes).toBeEnabled();
    await expect(defaultMinutes).toHaveValue("25");
    await defaultMinutes.fill("30");
    await expect(page.getByRole("button", { name: "Save lease durations" })).toBeEnabled();
  } finally {
    releaseSave.resolve();
    await api.dispose();
  }
});
