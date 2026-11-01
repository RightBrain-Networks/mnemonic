import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { expireLease } from "./database";
import { closeDetail, openWorkActions, selectWork } from "./surface";

async function apiClient() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: {
    Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json"
  } });
}

async function createProject(api: APIRequestContext) {
  const response = await api.post("/api/v1/projects", { data: { name: `UI tweaks ${crypto.randomUUID().slice(0, 8)}` } });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as { id: string };
}

async function createWork(api: APIRequestContext, projectId: string, title: string) {
  const response = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title, summary: "Disposable UI acceptance fixture.", status: "pending", priority: 40,
    tags: null, discovered_from_work_item_id: null,
    initial_checkpoint: { prompt: "Check the dashboard interactions.", source_client: "playwright-api",
      source_session_id: crypto.randomUUID(), source_model: null, tags: [], source_metadata: {} }
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()).work_item as { id: string; title: string };
}

test("Dropped badge counts every dropped item and disappears when the last one returns to Pending", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    const work = await Promise.all(["First dropped item", "Second dropped item"].map((title) => createWork(api, project.id, title)));
    for (const item of work) {
      const claim = await api.post(`/api/v1/projects/${project.id}/work-items/${item.id}/claim`, { data: {
        holder_client: "playwright-api", holder_session_id: crypto.randomUUID(),
        session_transcript: null, claim_request_id: crypto.randomUUID()
      } });
      expect(claim.ok(), await claim.text()).toBe(true);
    }
    await page.clock.install();
    await page.goto(`/work-items?project=${project.id}`);
    const dropped = page.getByRole("button", { name: "Dropped", exact: true });
    const badge = dropped.locator(".attention-nav-count");
    await expect(dropped).toBeVisible();
    await expect(badge).toHaveCount(0);
    for (const item of work) await expireLease(project.id, item.id);
    await page.clock.fastForward(16 * 60 * 1000);
    await expect(badge).toHaveText("2");
    await expect(dropped).toHaveAccessibleDescription("2 dropped items");
    await page.screenshot({ path: testInfo.outputPath("dropped-count.png"), fullPage: true });
    await dropped.click();
    for (const [index, item] of work.entries()) {
      const pane = await selectWork(page, item.title);
      await expect(pane.getByRole("button", { name: "Delete work item", exact: true })).toHaveCount(0);
      await expect(pane.getByRole("button", { name: /Merge as duplicate/ })).toHaveCount(0);
      const menu = await openWorkActions(pane);
      await expect(menu.getByRole("menuitem", { name: "Delete work item", exact: true })).toBeEnabled();
      await expect(menu.getByRole("menuitem", { name: /Merge as duplicate/ })).toBeEnabled();
      if (!index) await page.screenshot({ path: testInfo.outputPath("work-actions.png") });
      await menu.getByRole("menuitem", { name: `Pending ${item.title}`, exact: true }).click();
      await closeDetail(page);
      if (!index) await expect(badge).toHaveText("1");
      else await expect(badge).toHaveCount(0);
    }
    const other = await createProject(api);
    await page.locator("#project-select").selectOption(other.id);
    await expect(badge).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("decision and prompt searches use slash without taking keystrokes from fields or dialogs", async ({ page }) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    for (const route of ["/knowledge/decisions", "/settings/prompts"]) {
      await page.goto(`${route}?project=${project.id}`);
      const search = page.getByRole("searchbox");
      await expect(search).toBeVisible();
      await expect(search).toHaveAttribute("aria-keyshortcuts", "/");
      await page.getByRole("heading", { level: 1 }).click();
      await page.keyboard.press("/");
      await expect(search).toBeFocused();
      await expect(search).toHaveValue("");
      await page.keyboard.press("/");
      await expect(search).toHaveValue("/");
      await search.fill("");
      await page.getByRole("heading", { level: 1 }).click();
      await page.keyboard.press("Control+/");
      await expect(search).not.toBeFocused();
      if (route.startsWith("/knowledge")) {
        await page.getByRole("button", { name: "Upload", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Upload decision document" });
        await dialog.getByRole("button", { name: "Cancel", exact: true }).focus();
        await page.keyboard.press("/");
        await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      } else {
        await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Recall pointer", exact: true });
        const close = dialog.getByRole("button", { name: "Close prompt", exact: true });
        await close.focus();
        await page.keyboard.press("/");
        await expect(close).toBeFocused();
      }
    }
  } finally { await api.dispose(); }
});

test("decision file uploads detect their format and refuse to reuse a previous valid file after a bad selection", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    await page.goto(`/knowledge/decisions?project=${project.id}`);
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload decision document" });
    const file = dialog.getByLabel("Document file");
    const save = dialog.getByRole("button", { name: "Upload document", exact: true });
    await file.setInputFiles({ name: "Plain decision.txt", mimeType: "text/plain", buffer: Buffer.from("This is plain text.\n") });
    await expect(save).toBeEnabled();
    await file.setInputFiles({ name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from([0xff, 0xfe]) });
    await expect(dialog.getByRole("alert")).toContainText("UTF-8");
    await expect(save).toBeDisabled();
    await file.setInputFiles({ name: "Plain decision.txt", mimeType: "text/plain", buffer: Buffer.from("This is plain text.\n") });
    await expect(save).toBeEnabled();
    await expect(dialog.getByRole("textbox", { name: "Document contents", exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("combobox", { name: "Format", exact: true })).toHaveCount(0);
    await dialog.screenshot({ path: testInfo.outputPath("plain-text-upload.png") });
    await save.click();
    await expect(dialog).toBeHidden();
    const response = await api.get(`/api/v1/projects/${project.id}/knowledge/decisions?q=Plain%20decision`);
    expect(response.ok(), await response.text()).toBe(true);
    const saved = (await response.json()).items[0];
    expect(saved.media_type).toBe("text/plain");
    const content = await api.get(`/api/v1/projects/${project.id}/knowledge/decisions/${saved.decision_id}`);
    expect((await content.json()).content).toBe("This is plain text.\n");
  } finally { await api.dispose(); }
});
