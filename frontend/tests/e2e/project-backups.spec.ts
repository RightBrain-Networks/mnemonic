import { readFile } from "node:fs/promises";
import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";

type Project = { id: string; name: string; slug: string; description: string };
type Work = { id: string; version: number; title: string };

async function client(): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
}

async function createProject(api: APIRequestContext, label: string): Promise<Project> {
  const response = await api.post("/api/v1/projects", {
    data: { name: `${label} ${crypto.randomUUID().slice(0, 8)}`, slug: `backup-${crypto.randomUUID()}`, description: "Original project description" }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json() as Promise<Project>;
}

async function createWork(api: APIRequestContext, project: Project, title: string): Promise<Work> {
  const response = await api.post(`/api/v1/projects/${project.id}/work-items`, {
    data: { title, summary: "Project backup acceptance", priority: 12,
      initial_checkpoint: { prompt: "Preserve this work during a project restore.", source_client: "playwright-api", source_session_id: `backup-${crypto.randomUUID()}`, tags: ["backup-acceptance"], source_metadata: {} } }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { work_item: Work }).work_item;
}

async function openBackups(page: Page, project: Project) {
  await page.goto("/settings");
  await page.locator("#project-select").selectOption(project.id);
  const panel = page.getByRole("region", { name: "Project backups", exact: true });
  await panel.scrollIntoViewIfNeeded();
  await expect(panel.getByText("Checking stored backups…")).toBeHidden();
  return panel;
}

async function downloadBackup(page: Page, project: Project) {
  const panel = await openBackups(page, project);
  await panel.getByRole("button", { name: "Back up now", exact: true }).click();
  await expect(panel.locator(".backup-notice")).toHaveText(`Backup created for ${project.name}.`);
  const first = panel.getByRole("listitem").first();
  const downloading = page.waitForEvent("download");
  await first.getByRole("link", { name: /^Download / }).click();
  const download = await downloading;
  const buffer = await readFile((await download.path())!);
  expect(download.suggestedFilename()).toMatch(/\.bz2$/);
  expect(buffer.subarray(0, 3).toString()).toBe("BZh");
  return { name: download.suggestedFilename(), mimeType: "application/x-bzip2", buffer };
}

test("project backups download compressed data, prune oldest archives and restore only the selected project", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const api = await client();
  try {
    const source = await createProject(api, "Backup source");
    const other = await createProject(api, "Unaffected project");
    const original = await createWork(api, source, "Original source work");
    const unrelated = await createWork(api, other, "Other project work stays intact");
    const archive = await downloadBackup(page, source);
    const panel = page.getByRole("region", { name: "Project backups", exact: true });
    await expect(panel.getByRole("listitem")).toHaveCount(1);
    await expect(panel.locator("time")).toHaveAttribute("datetime", /^\d{4}-\d{2}-\d{2}T/);
    await expect(panel).toContainText("Less than a minute ago");
    await expect(panel).toContainText("Artifact file contents are managed by your separate file backup system.");
    for (let count = 0; count < 2; count++) {
      await panel.getByRole("button", { name: "Back up now", exact: true }).click();
      await expect(panel.getByRole("button", { name: "Back up now", exact: true })).toBeEnabled();
    }
    await expect(panel.getByRole("listitem")).toHaveCount(2);
    await expect(panel.getByRole("link", { name: `Download ${archive.name}` })).toHaveCount(0);

    const changed = await api.patch(`/api/v1/projects/${source.id}`, { data: { description: "After the archived snapshot" } });
    expect(changed.ok(), await changed.text()).toBe(true);
    const later = await createWork(api, source, "Work added after the snapshot");
    const otherBefore = await (await api.get(`/api/v1/projects/${other.id}`)).json();
    const otherWorkBefore = await (await api.get(`/api/v1/projects/${other.id}/work-items/${unrelated.id}/context`)).json();
    await panel.getByLabel("Backup archive", { exact: true }).setInputFiles(archive);
    await expect(panel.getByRole("button", { name: "Upload and restore" })).toBeDisabled();
    await panel.getByLabel(`Type ${source.slug} to confirm replacement`).fill("wrong-project");
    await expect(panel.getByRole("button", { name: "Upload and restore" })).toBeDisabled();
    await panel.getByLabel(`Type ${source.slug} to confirm replacement`).fill(source.slug);
    const viewport = page.viewportSize()!;
    const panelHeight = await panel.evaluate((element) => element.getBoundingClientRect().height);
    await page.setViewportSize({ width: viewport.width, height: Math.ceil(panelHeight) + 320 });
    await panel.scrollIntoViewIfNeeded();
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    await panel.screenshot({ path: testInfo.outputPath("project-backups.png") });
    await testInfo.attach("Project backup settings", { path: testInfo.outputPath("project-backups.png"), contentType: "image/png" });
    await page.setViewportSize(viewport);
    const reloaded = page.waitForEvent("load");
    await panel.getByRole("button", { name: "Upload and restore" }).click();
    await reloaded;
    await expect(page.locator("#project-select")).toHaveValue(source.id);
    await expect(page.getByLabel("Description", { exact: false })).toHaveValue(source.description);
    const restored = await api.get(`/api/v1/projects/${source.id}/work-items/${original.id}/context`);
    expect(restored.ok(), await restored.text()).toBe(true);
    const absent = await api.get(`/api/v1/projects/${source.id}/work-items/${later.id}/context`);
    expect(absent.status()).toBe(404);
    expect(await (await api.get(`/api/v1/projects/${other.id}`)).json()).toEqual(otherBefore);
    expect(await (await api.get(`/api/v1/projects/${other.id}/work-items/${unrelated.id}/context`)).json()).toEqual(otherWorkBefore);
  } finally { await api.dispose(); }
});

test("restore rejects plain, empty, oversized, corrupt and wrong-project archives without changing projects", async ({ page }) => {
  test.setTimeout(120_000);
  const api = await client();
  try {
    const source = await createProject(api, "Archive origin");
    const target = await createProject(api, "Restore rejection target");
    const sourceWork = await createWork(api, source, "Protected archive origin work");
    const targetWork = await createWork(api, target, "Protected target work");
    const archive = await downloadBackup(page, source);
    const panel = await openBackups(page, target);
    for (const file of [
      { name: "backup.sql", buffer: Buffer.from("SELECT 1;") },
      { name: "empty.bz2", buffer: Buffer.alloc(0) },
      { name: "oversized.bz2", buffer: Buffer.alloc(2 * 1024 * 1024 + 1) }
    ]) {
      await panel.getByLabel("Backup archive", { exact: true }).setInputFiles({ ...file, mimeType: "application/octet-stream" });
      await expect(panel.getByRole("alert")).toBeVisible();
      await expect(panel.getByRole("button", { name: "Upload and restore" })).toBeDisabled();
    }
    for (const file of [
      { name: "corrupt.bz2", mimeType: "application/x-bzip2", buffer: Buffer.from("BZh9not-a-valid-archive") },
      archive
    ]) {
      await panel.getByLabel("Backup archive", { exact: true }).setInputFiles(file);
      await panel.getByLabel(`Type ${target.slug} to confirm replacement`).fill(target.slug);
      const response = page.waitForResponse((value) => value.url().endsWith(`/projects/${target.id}/restore`) && value.request().method() === "POST");
      await panel.getByRole("button", { name: "Upload and restore" }).click();
      expect((await response).status()).toBeGreaterThanOrEqual(400);
      await expect(panel.getByRole("alert")).toContainText(file === archive ? /another project/i : /bzip2|invalid|corrupt/i);
      await expect(panel.getByRole("button", { name: "Back up now" })).toBeEnabled();
    }
    for (const [project, work] of [[source, sourceWork], [target, targetWork]] as const) {
      expect((await api.get(`/api/v1/projects/${project.id}/work-items/${work.id}/context`)).ok()).toBe(true);
      expect((await (await api.get(`/api/v1/projects/${project.id}`)).json()).description).toBe(project.description);
    }
    const unauthorized = await api.get(`/projects/${source.id}/backups`);
    expect(unauthorized.status()).toBe(404);
    const genericProxy = await page.request.get(`/api/mnemonic/projects/${source.id}/backups`);
    expect(genericProxy.status()).toBe(404);
    const crossOrigin = await page.request.post(`/api/backups/projects/${source.id}/backups`, { headers: { Origin: "https://attacker.example" } });
    expect(crossOrigin.status()).toBe(403);
  } finally { await api.dispose(); }
});

test("a lost backup response blocks duplicate actions until the user reloads and inspects", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api, "Uncertain backup outcome");
    let attempts = 0;
    await page.route(`**/api/backups/projects/${project.id}/backups`, async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      attempts++;
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      await route.abort("failed");
    });
    const panel = await openBackups(page, project);
    await panel.getByRole("button", { name: "Back up now" }).click();
    await expect(panel.getByRole("alert")).toContainText("The outcome is uncertain.");
    await expect(panel.getByRole("button", { name: "Back up now" })).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("navigation", { name: "Workspace navigation" }).getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await panel.getByRole("button", { name: "Refresh backups" }).click();
    await expect(panel.getByRole("listitem")).toHaveCount(1);
    expect(attempts).toBe(1);
    const reloaded = page.waitForEvent("load");
    await panel.getByRole("button", { name: "Reload and inspect" }).click();
    await reloaded;
    await expect(page.getByRole("region", { name: "Project backups", exact: true }).getByRole("listitem")).toHaveCount(1);
    expect(attempts).toBe(1);
  } finally { await api.dispose(); }
});

test("changing projects clears the chosen restore file and ignores a late archive list", async ({ page }) => {
  const api = await client();
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  try {
    const source = await createProject(api, "Stale archive list");
    const destination = await createProject(api, "Fresh archive list");
    let started!: () => void;
    const requested = new Promise<void>((resolve) => { started = resolve; });
    let settled!: () => void;
    const completed = new Promise<void>((resolve) => { settled = resolve; });
    await page.route(`**/api/backups/projects/${source.id}/backups`, async (route) => {
      started();
      await released;
      try {
        await route.fulfill({ json: { project_id: source.id, retention_count: 2, backups: [{
          filename: "stale-project.json.bz2", created_at: "2026-01-01T00:00:00Z", size_bytes: 123
        }] } });
      } catch { /* The project switch aborts this stale request. */ }
      finally { settled(); }
    });
    await page.goto("/settings");
    await page.locator("#project-select").selectOption(source.id);
    await requested;
    let panel = page.getByRole("region", { name: "Project backups", exact: true });
    await panel.getByLabel("Backup archive", { exact: true }).setInputFiles({ name: "selected.bz2", mimeType: "application/x-bzip2", buffer: Buffer.from("BZh9") });
    await panel.getByLabel(`Type ${source.slug} to confirm replacement`).fill(source.slug);
    await page.locator("#project-select").selectOption(destination.id);
    release();
    await completed;
    panel = page.getByRole("region", { name: "Project backups", exact: true });
    await expect(panel.getByText("No backups yet for this project.")).toBeVisible();
    await expect(panel.getByText("stale-project.json.bz2")).toHaveCount(0);
    await expect(panel.getByLabel("Backup archive", { exact: true })).toHaveValue("");
    await expect(panel.getByLabel(`Type ${destination.slug} to confirm replacement`)).toHaveValue("");
    await expect(panel.getByRole("button", { name: "Upload and restore" })).toBeDisabled();
  } finally { release(); await api.dispose(); }
});

for (const outcome of ["pending", "uncertain"] as const) {
  test(`a ${outcome} restore survives catalog outages and omitted projects without losing its operation lock`, async ({ page }) => {
    test.setTimeout(90_000);
    const api = await client();
    let release!: () => void;
    const released = new Promise<void>((resolve) => { release = resolve; });
    try {
      const project = await createProject(api, `Catalog outage during ${outcome} restore`);
      const archive = await downloadBackup(page, project);
      const changed = await api.patch(`/api/v1/projects/${project.id}`, { data: { description: "Changed after archive" } });
      expect(changed.ok(), await changed.text()).toBe(true);
      let catalogMode: "healthy" | "error" | "empty" = "healthy";
      await page.route(/\/api\/mnemonic\/projects(?:\?.*)?$/, async (route) => {
        if (catalogMode === "error") await route.fulfill({ status: 503, json: { detail: "Injected project catalog outage" } });
        else if (catalogMode === "empty") await route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } });
        else await route.continue();
      });
      let requests = 0;
      let forwarded!: () => void;
      const committed = new Promise<void>((resolve) => { forwarded = resolve; });
      await page.route(`**/api/backups/projects/${project.id}/restore`, async (route) => {
        requests++;
        const response = await route.fetch();
        expect(response.ok(), await response.text()).toBe(true);
        forwarded();
        if (outcome === "uncertain") { await route.abort("failed"); return; }
        await released;
        try { await route.fulfill({ response }); } catch { /* Best-effort cleanup if an assertion failed. */ }
      });
      const panel = page.getByRole("region", { name: "Project backups", exact: true });
      await panel.getByLabel("Backup archive", { exact: true }).setInputFiles(archive);
      await panel.getByLabel(`Type ${project.slug} to confirm replacement`).fill(project.slug);
      await panel.getByRole("button", { name: "Upload and restore" }).click();
      await committed;
      if (outcome === "uncertain") await expect(panel.getByRole("alert")).toContainText("The outcome is uncertain.");
      const originalPanel = await panel.elementHandle();

      catalogMode = "error";
      await page.locator(".page-heading").getByRole("button", { name: "Refresh", exact: true }).click();
      await expect(page.getByText("Injected project catalog outage", { exact: true })).toBeVisible();
      await expect(panel).toBeVisible();
      await expect(panel.getByRole("button", { name: "Back up now" })).toBeDisabled();
      await expect(panel.getByLabel("Backup archive", { exact: true })).toBeDisabled();
      await expect(page.locator("#project-select")).toBeDisabled();

      // A successful but incomplete catalog must not switch to another project
      // or replace the component that owns the outstanding restore response.
      catalogMode = "empty";
      await page.getByRole("button", { name: "Try again", exact: true }).click();
      await expect(page.getByText("Injected project catalog outage", { exact: true })).toBeHidden();
      await expect(panel).toBeVisible();
      await expect(page.locator("#project-select")).toHaveValue(project.id);
      await expect(panel.getByRole("button", { name: "Back up now" })).toBeDisabled();
      await expect(panel.getByRole("button", { name: outcome === "pending" ? "Restoring project…" : "Upload and restore" })).toBeDisabled();
      expect(await originalPanel!.evaluate((element) => element.isConnected)).toBe(true);
      expect(requests).toBe(1);

      catalogMode = "healthy";
      const reloaded = page.waitForEvent("load");
      if (outcome === "pending") release();
      else await panel.getByRole("button", { name: "Reload and inspect" }).click();
      await reloaded;
      await expect(page.getByLabel("Description", { exact: false })).toHaveValue(project.description);
      await expect(page.locator("#project-select")).toBeEnabled();
      expect(requests).toBe(1);
    } finally { release(); await api.dispose(); }
  });
}
