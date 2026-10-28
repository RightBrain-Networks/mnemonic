import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

test("sidebar navigation keeps the workspace mounted through menu links and browser history", async ({ page }, testInfo) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  await page.addInitScript((projectId) => localStorage.setItem("mnemonic.project", projectId), state.projectId);
  let sendReady: (() => void) | undefined;
  let syncConnections = 0;
  await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
    syncConnections += 1;
    sendReady = () => socket.send(JSON.stringify({ type: "ready", revision: 0 }));
  });
  await page.route("**/job-completion-reports/count", (route) => route.fulfill({ json: {
    project_id: state.projectId, undismissed_count: "7", as_of_sequence: "0"
  } }));
  await page.goto("/work-items");
  await page.locator("#project-select").selectOption(state.projectId);
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  const toggle = navigation.getByRole("button", { name: "Project settings" });
  await toggle.click();
  const resources = navigation.getByRole("button", { name: "Resources", exact: true });
  await resources.click();
  await expect(page.locator(".summary-nav-count")).toHaveText("7");
  const picker = page.locator("#project-select");
  await expect(picker).toBeEnabled();
  await expect.poll(() => Boolean(sendReady)).toBe(true);

  // Live-sync readiness schedules a catalog refresh after bootstrap. The picker
  // intentionally disables during that read, including before Query. Complete a
  // held refresh before observing navigation so startup cannot mimic a remount.
  let releaseCatalog!: () => void;
  const catalogReleased = new Promise<void>((resolve) => { releaseCatalog = resolve; });
  let catalogHeld = false;
  const catalogPattern = /\/api\/mnemonic\/projects\?/;
  await page.route(catalogPattern, async (route) => {
    catalogHeld = true;
    await catalogReleased;
    await route.continue();
  });
  try {
    sendReady!();
    await expect(page.locator(".sync-status")).toHaveText("Live Updates");
    await expect.poll(() => catalogHeld).toBe(true);
    await expect(picker).toBeDisabled();
    releaseCatalog();
    await expect(picker).toBeEnabled();
  } finally {
    releaseCatalog();
    await page.unroute(catalogPattern);
  }

  const sidebar = await page.locator(".sidebar").elementHandle();
  const documentRequests: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()
      && request.resourceType() === "document") documentRequests.push(request.url());
  });
  // Watch transient resets too: the final appearance alone misses the flash.
  await page.evaluate(() => {
    const resets: string[] = [];
    Object.assign(window, { sidebarResets: resets });
    new MutationObserver(() => {
      if ((document.querySelector("#project-select") as HTMLSelectElement).disabled) resets.push("project picker disabled");
      if (document.querySelector(".summary-nav-count")?.textContent !== "7") resets.push("badge reset");
      if (document.querySelector(".settings-nav")?.getAttribute("data-expanded") !== "true") resets.push("menu collapsed");
      if (document.querySelector(".resources-nav")?.getAttribute("data-expanded") !== "true") resets.push("resources collapsed");
    }).observe(document.querySelector(".sidebar")!, { subtree: true, childList: true, attributes: true });
  });

  for (const label of ["Summaries", "Needs Attention", "Artifacts", "Transcripts", "Workspace", "Tags", "Prompts", "Code reviews", "Backup & export", "Work items"]) {
    await (label === "Code reviews" ? navigation.locator(".settings-nav") : navigation).getByRole("link", { name: label, exact: label !== "Summaries" }).click();
    await expect(page.locator("h1")).toContainText(label === "Work items" ? `Work items: ${state.projectName}` : `${label}.`);
    expect(documentRequests).toEqual([]);
    expect(await sidebar!.evaluate((element) => element.isConnected)).toBe(true);
    await expect(navigation.locator('[aria-current="page"]')).toContainText(label);
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(resources).toHaveAttribute("aria-expanded", "true");
  }
  await page.goBack();
  await expect(page.locator("h1")).toHaveText("Backup & export.");
  await page.goForward();
  await expect(page.locator("h1")).toContainText(`Work items: ${state.projectName}`);
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await page.getByRole("link", { name: "Mnemonic home" }).click();
  await expect(page).toHaveURL("/");
  expect(documentRequests).toEqual([]);
  expect(syncConnections).toBe(1);
  await expect(page.locator(".sync-status")).toHaveText("Live Updates");
  expect(await sidebar!.evaluate((element) => element.isConnected)).toBe(true);
  expect(await page.evaluate(() => (window as unknown as { sidebarResets: string[] }).sidebarResets)).toEqual([]);
  const screenshot = testInfo.outputPath("sidebar-navigation.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach("Sidebar after navigating all sections", { path: screenshot, contentType: "image/png" });
});


test("sidebar navigation restores a work deep link on Back and clears it on a fresh library visit", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) < 801, "The narrow work sheet covers the sidebar while open.");
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  await page.addInitScript((projectId) => localStorage.setItem("mnemonic.project", projectId), state.projectId);
  await page.goto(`/work-items?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  const sidebar = await page.locator(".sidebar").elementHandle();
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await page.goBack();
  await expect(page).toHaveURL(`/work-items?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  await navigation.getByRole("link", { name: "Work items" }).click();
  await expect(page).toHaveURL("/work-items");
  await expect(page.locator(".detail-title")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(`/work-items?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await navigation.getByRole("link", { name: "Work items" }).click();
  await expect(page).toHaveURL("/work-items");
  await expect(page.locator(".detail-title")).toHaveCount(0);
  expect(await sidebar!.evaluate((element) => element.isConnected)).toBe(true);
});

test("sidebar navigation restores the artifact URL project after project changes", async ({ page }) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  const api = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  try {
    const created = await api.post("/api/v1/projects", { data: {
      name: "Sidebar navigation project", slug: `sidebar-${crypto.randomUUID()}`
    } });
    expect(created.ok()).toBe(true);
    const other = await created.json() as { id: string };
    await page.goto(`/artifacts?project=${state.projectId}&work=${state.historicalCompletion.workItemId}`);
    const filter = page.locator(".artifact-filter-note");
    await expect(filter).toContainText(state.historicalCompletion.workItemId);
    const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
    await navigation.getByRole("link", { name: "Artifacts", exact: true }).click();
    await expect(filter).toHaveCount(0);
    await page.goBack();
    await expect(filter).toContainText(state.historicalCompletion.workItemId);
    await page.goForward();
    await expect(filter).toHaveCount(0);
    const picker = page.locator("#project-select");
    await expect(picker).toHaveValue(state.projectId);
    await navigation.getByRole("link", { name: "Summaries" }).click();
    await expect(page).toHaveURL("/summaries");
    await picker.selectOption(other.id);
    await page.goBack();
    await expect(page).toHaveURL(`/artifacts?project=${state.projectId}`);
    await expect(picker).toHaveValue(state.projectId);
    await picker.selectOption(other.id);
    await expect(page).toHaveURL(`/artifacts?project=${other.id}`);
    await navigation.getByRole("link", { name: "Summaries" }).click();
    await expect(page).toHaveURL("/summaries");
    await page.goBack();
    await expect(page).toHaveURL(`/artifacts?project=${other.id}`);
    await expect(picker).toHaveValue(other.id);
  } finally { await api.dispose(); }
});

test("sidebar navigation remains available while a manual backup is being queued", async ({ page }) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  await page.goto("/work-items");
  await page.locator("#project-select").selectOption(state.projectId);
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await navigation.getByRole("button", { name: "Project settings" }).click();
  await navigation.getByRole("link", { name: "Backup & export", exact: true }).click();
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/backups/projects/${state.projectId}/backups`, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    await released;
    await route.fulfill({ status: 422, json: { error: { code: "invalid_backup", message: "Injected backup rejection" } } });
  });
  try {
    const panel = page.getByRole("region", { name: "Project backups", exact: true });
    await panel.getByRole("button", { name: "Back up now", exact: true }).click();
    await expect(page.locator("#project-select")).toBeEnabled();
    await navigation.getByRole("link", { name: "Work items" }).click();
    await expect(page).toHaveURL("/work-items");
    release();
    await page.goBack();
    await expect(page).toHaveURL("/settings/backups");
    await expect(page.getByText("Wait for the backup action to finish before leaving this page.")).toHaveCount(0);
  } finally { release(); }
});
