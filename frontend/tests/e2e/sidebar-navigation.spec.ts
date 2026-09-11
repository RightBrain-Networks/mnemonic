import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

test("sidebar navigation keeps the workspace mounted through menu links and browser history", async ({ page }, testInfo) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  await page.route("**/job-completion-reports/count", (route) => route.fulfill({ json: {
    project_id: state.projectId, undismissed_count: "7", as_of_sequence: "0"
  } }));
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  const toggle = navigation.getByRole("button", { name: "Project settings" });
  await toggle.click();
  const resources = navigation.getByRole("button", { name: "Resources", exact: true });
  await resources.click();
  await expect(page.locator(".summary-nav-count")).toHaveText("7");
  await expect(page.locator(".sync-status")).toHaveText("Live Updates");

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

  for (const label of ["Summaries", "Needs Attention", "Artifacts", "Transcripts", "Workspace", "Prompts", "Code reviews", "Backups", "Work library"]) {
    await navigation.getByRole("link", { name: label, exact: label !== "Summaries" }).click();
    await expect(page.locator("h1")).toContainText(label === "Work library" ? `Work library: ${state.projectName}` : `${label}.`);
    expect(documentRequests).toEqual([]);
    expect(await sidebar!.evaluate((element) => element.isConnected)).toBe(true);
    await expect(navigation.locator('[aria-current="page"]')).toContainText(label);
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(resources).toHaveAttribute("aria-expanded", "true");
  }
  await page.goBack();
  await expect(page.locator("h1")).toHaveText("Backups.");
  await page.goForward();
  await expect(page.locator("h1")).toContainText(`Work library: ${state.projectName}`);
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await page.getByRole("link", { name: "Mnemonic home" }).click();
  await expect(page).toHaveURL("/");
  expect(documentRequests).toEqual([]);
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
  await page.goto(`/?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  const sidebar = await page.locator(".sidebar").elementHandle();
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await page.goBack();
  await expect(page).toHaveURL(`/?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  await navigation.getByRole("link", { name: "Work library" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.locator(".detail-title")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(`/?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  await navigation.getByRole("link", { name: "Summaries" }).click();
  await expect(page).toHaveURL("/summaries");
  await navigation.getByRole("link", { name: "Work library" }).click();
  await expect(page).toHaveURL("/");
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

test("sidebar navigation and browser Back retain a pending backup panel", async ({ page }) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await navigation.getByRole("button", { name: "Project settings" }).click();
  await navigation.getByRole("link", { name: "Backups", exact: true }).click();
  await expect(page).toHaveURL("/settings/backups");
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/backups/projects/${state.projectId}/backups`, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    await released;
    // A definitive failure releases the lock without creating an archive.
    await route.fulfill({ status: 422, json: { error: { code: "invalid_backup", message: "Injected backup rejection" } } });
  });
  try {
    const panel = page.getByRole("region", { name: "Project backups", exact: true });
    const originalPanel = await panel.elementHandle();
    await panel.getByRole("button", { name: "Back up now", exact: true }).click();
    await expect(page.locator("#project-select")).toBeDisabled();
    await navigation.getByRole("button", { name: "Resources", exact: true }).click();
    await navigation.getByRole("link", { name: "Transcripts", exact: true }).click();
    await expect(page).toHaveURL("/settings/backups");
    expect(await originalPanel!.evaluate((element) => element.isConnected)).toBe(true);
    await navigation.getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL("/settings/backups");
    await page.goBack();
    await expect(page.getByText("Wait for the backup action to finish before leaving this page.")).toBeVisible();
    await expect(page).toHaveURL("/settings/backups");
    expect(await originalPanel!.evaluate((element) => element.isConnected)).toBe(true);
    release();
    await expect(panel.getByRole("button", { name: "Back up now", exact: true })).toBeEnabled();
    await navigation.getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL("/");
  } finally { release(); }
});
