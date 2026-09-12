import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import type { PromptDetail, PromptLibraryPage } from "../../lib/prompts";
import { closeDetail, selectWork, workCard } from "./surface";

async function client() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: {
    Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json"
  }});
}
async function project(api: APIRequestContext) {
  const response = await api.post("/api/v1/projects", { data: { name: `Prompt library ${crypto.randomUUID().slice(0, 8)}` } });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as { id: string; name: string };
}
async function openLibrary(page: Page, projectId: string) {
  await page.goto("/settings/prompts");
  await page.locator("#project-select").selectOption(projectId);
  await expect(page.getByRole("region", { name: "Prompt directory" })).toBeVisible();
}
async function readPrompt(api: APIRequestContext, projectId: string, id = "recall-pointer") {
  const response = await api.get(`/api/v1/projects/${projectId}/prompts/${id}`);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as PromptDetail;
}

test("prompt directory has a bottom macro glossary and a right editor with copy, save, and keyboard focus", async ({ page }, testInfo) => {
  const api = await client();
  try {
    const selected = await project(api);
    const catalog = await (await api.get(`/api/v1/projects/${selected.id}/prompts`)).json() as PromptLibraryPage;
    await openLibrary(page, selected.id);
    await expect(page.locator(".prompt-table tbody tr")).toHaveCount(7);
    await expect(page.locator(".prompt-table thead th")).toHaveText(["Name / purpose", "Size", "Created", "Updated"]);
    for (const macro of catalog.macros) await expect(page.locator(".prompt-glossary").getByText(macro.macro, { exact: true })).toBeVisible();
    expect(await page.locator(".prompt-glossary").evaluate((element) => element.compareDocumentPosition(document.querySelector(".prompt-table")!) & Node.DOCUMENT_POSITION_PRECEDING)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath("prompt-library.png"), fullPage: true });
    const trigger = page.getByRole("button", { name: "Recall pointer", exact: true });
    await trigger.click();
    const drawer = page.getByRole("dialog", { name: "Recall pointer", exact: true });
    const original = await readPrompt(api, selected.id);
    const content = drawer.getByRole("textbox", { name: "Prompt content" });
    await expect(content).toHaveValue(original.content);
    await expect(drawer.getByRole("button", { name: "Close prompt" })).toBeFocused();
    await expect.poll(async () => drawer.evaluate((element) => Math.abs(element.getBoundingClientRect().right - innerWidth))).toBeLessThan(2);
    await content.fill("# Agent instructions\n\nRead $WORK_ITEM_ID in $PROJECT_NAME.\n");
    await expect(page.locator("#project-select")).toBeDisabled();
    await drawer.getByRole("button", { name: "Copy contents" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(await content.inputValue());
    await page.screenshot({ path: testInfo.outputPath("prompt-editor.png"), fullPage: false });
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.keyboard.press("Escape");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await expect.poll(async () => (await readPrompt(api, selected.id)).content).toBe("# Agent instructions\n\nRead $WORK_ITEM_ID in $PROJECT_NAME.\n");
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(page.locator("#project-select")).toBeEnabled();
    await page.getByRole("searchbox").fill("completion report");
    await expect(page.locator(".prompt-table tbody tr")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Job completion report", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("a concurrent file change preserves the draft and blocks overwrite until the saved prompt is reviewed", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api);
    const original = await readPrompt(api, selected.id);
    await openLibrary(page, selected.id);
    await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
    const drawer = page.getByRole("dialog");
    const content = drawer.getByRole("textbox", { name: "Prompt content" });
    await content.fill("My draft for $WORK_ITEM_ID.");
    const competing = await api.put(`/api/v1/projects/${selected.id}/prompts/recall-pointer`, {
      data: { content: "A second editor’s instructions.", expected_revision: original.revision }
    });
    expect(competing.ok(), await competing.text()).toBe(true);
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByText("This prompt changed since you opened it.", { exact: false })).toBeVisible();
    await expect(content).toHaveValue("My draft for $WORK_ITEM_ID.");
    await expect(drawer.locator(".prompt-conflict pre")).toHaveText("A second editor’s instructions.");
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await drawer.getByRole("button", { name: "I reviewed the saved prompt" }).click();
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(async () => (await readPrompt(api, selected.id)).content).toBe("My draft for $WORK_ITEM_ID.");
  } finally { await api.dispose(); }
});

test("a lost save response retains the draft and reconciles the saved result before another edit", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api);
    let writes = 0;
    await page.route(`**/api/mnemonic/projects/${selected.id}/prompts/recall-pointer`, async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      writes += 1;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      await route.fulfill({ status: 502, contentType: "application/json", body: '{"detail":"Save response lost."}' });
    });
    await openLibrary(page, selected.id);
    await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("textbox", { name: "Prompt content" }).fill("A saved draft whose response is lost.");
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByText("The save outcome is uncertain.", { exact: false })).toBeVisible();
    await expect(drawer.locator(".prompt-conflict pre")).toHaveText("A saved draft whose response is lost.");
    await expect(drawer.getByRole("textbox", { name: "Prompt content" })).toHaveValue("A saved draft whose response is lost.");
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await drawer.getByRole("button", { name: "I reviewed the saved prompt" }).click();
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    expect(writes).toBe(1);
  } finally { await api.dispose(); }
});

test("project prompt edits drive both clipboard actions, preserve unknown macros, and remain isolated", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api), other = await project(api);
    const otherOriginal = await readPrompt(api, other.id);
    const create = await api.post(`/api/v1/projects/${selected.id}/work-items`, { data: {
      title: "Prompt clipboard acceptance", summary: "A literal $PROJECT_ID in a work summary.", priority: 23,
      initial_checkpoint: { prompt: "Check both clipboard actions.", source_client: "playwright-api", source_session_id: "prompt-library" }
    }});
    expect(create.ok(), await create.text()).toBe(true);
    const { work_item: work } = await create.json() as { work_item: { id: string } };
    await openLibrary(page, selected.id);
    await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("textbox", { name: "Prompt content" }).fill("$PROJECT_NAME\n$WORK_ITEM_ID: $WORK_ITEM_TITLE\n$WORK_ITEM_SUMMARY\n$UNKNOWN_MACRO");
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await drawer.getByRole("button", { name: "Close prompt" }).click();
    await page.getByRole("link", { name: "Work library", exact: true }).click();
    const expected = `${selected.name}\n${work.id}: Prompt clipboard acceptance\nA literal $PROJECT_ID in a work summary.\n$UNKNOWN_MACRO`;
    await workCard(page, "Prompt clipboard acceptance").getByRole("button", { name: /Copy recall pointer/ }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(expected);
    await page.evaluate(() => navigator.clipboard.writeText("sentinel"));
    const pane = await selectWork(page, "Prompt clipboard acceptance");
    await pane.getByRole("button", { name: "Copy recall pointer", exact: true }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(expected);
    await closeDetail(page);
    expect((await readPrompt(api, other.id)).content).toBe(otherOriginal.content);
    await page.route(`**/api/mnemonic/projects/${selected.id}/prompts/recall-pointer/render`, async (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Prompt file unavailable."}' }));
    await page.evaluate(() => navigator.clipboard.writeText("do not replace"));
    await workCard(page, "Prompt clipboard acceptance").getByRole("button", { name: /Copy recall pointer/ }).click();
    await expect(page.locator(".toast")).toContainText("Prompt file unavailable");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("do not replace");
  } finally { await api.dispose(); }
});

test("an unavailable prompt never opens an empty editable fallback", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api);
    await openLibrary(page, selected.id);
    await page.route(`**/api/mnemonic/projects/${selected.id}/prompts/recall-pointer`, async (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Prompt file unavailable."}' }));
    await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByRole("alert")).toContainText("Prompt file unavailable");
    await expect(drawer.getByRole("textbox")).toHaveCount(0);
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await expect(drawer.getByRole("button", { name: "Copy contents" })).toBeDisabled();
  } finally { await api.dispose(); }
});

test("browser history and project shortcuts preserve an unsaved prompt draft", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api);
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(selected.id);
    await page.getByRole("link", { name: "Prompts", exact: true }).click();
    await page.getByRole("button", { name: "Recall pointer", exact: true }).click();
    const drawer = page.getByRole("dialog");
    const content = drawer.getByRole("textbox", { name: "Prompt content" });
    await content.fill("Keep this draft when navigating away.");
    await expect(page.locator("#project-select")).toBeDisabled();
    await drawer.getByRole("button", { name: "Close prompt" }).focus();
    await page.keyboard.press("1");
    await expect(page.locator("#project-select")).toHaveValue(selected.id);
    await page.goBack();
    await expect(page).toHaveURL(/\/settings\/prompts$/);
    await expect(content).toHaveValue("Keep this draft when navigating away.");
    page.once("dialog", (dialog) => dialog.accept());
    await drawer.getByRole("button", { name: "Close prompt" }).click();
    await expect(drawer).toHaveCount(0);
    await expect(page.locator("#project-select")).toBeEnabled();
  } finally { await api.dispose(); }
});

test("report instructions initialize during typing and changed instructions still need explicit review", async ({ page }) => {
  const api = await client();
  try {
    const selected = await project(api);
    const response = await api.post(`/api/v1/projects/${selected.id}/work-items`, { data: {
      title: "Report prompt initialization", summary: "Keep the report revision while the author types.", priority: 1,
      initial_checkpoint: { prompt: "Verify report authoring instructions.", source_client: "playwright-api", source_session_id: "report-prompt-race" }
    }});
    expect(response.ok(), await response.text()).toBe(true);
    const { work_item: work } = await response.json() as { work_item: { id: string } };
    let releasePrompt!: () => void;
    const promptReady = new Promise<void>((resolve) => { releasePrompt = resolve; });
    await page.route(`**/api/mnemonic/projects/${selected.id}/settings?work_item_id=${work.id}`, async (route) => {
      await promptReady;
      await route.continue();
    });
    await page.goto("/");
    await page.locator("#project-select").selectOption(selected.id);
    const pane = await selectWork(page, "Report prompt initialization");
    await pane.getByLabel(/^Checkpoint text/).fill("The report prompt initialization has been checked.");
    const summary = pane.getByRole("textbox", { name: /^Human summary/ });
    await summary.fill("The prompt settings are ready.");
    await expect(pane.getByText("Loading project report instructions…", { exact: true })).toBeVisible();
    releasePrompt();
    await summary.pressSequentially(" The author can keep typing while they load.", { delay: 5 });
    await expect(pane.getByRole("button", { name: "Complete work", exact: true })).toBeEnabled();
    await expect(summary).toHaveValue("The prompt settings are ready. The author can keep typing while they load.");
    const original = await readPrompt(api, selected.id, "job-completion-report");
    const changed = await api.put(`/api/v1/projects/${selected.id}/prompts/job-completion-report`, {
      data: { content: "Describe the result for $WORK_ITEM_TITLE in familiar words.", expected_revision: original.revision }
    });
    expect(changed.ok(), await changed.text()).toBe(true);
    await pane.getByRole("button", { name: "Review current prompt", exact: true }).click();
    await expect(pane.getByRole("button", { name: /^Use reviewed revision/ })).toBeVisible();
    await expect(pane.locator(".job-report-prompt")).toHaveText("Describe the result for Report prompt initialization in familiar words.");
    let closeouts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith(`/work-items/${work.id}/complete`)) closeouts += 1;
    });
    await pane.getByRole("button", { name: "Complete work", exact: true }).click();
    await expect(pane.getByText("Project instructions changed. Review the current report prompt", { exact: false })).toBeVisible();
    expect(closeouts).toBe(0);
    await pane.getByRole("button", { name: /^Use reviewed revision/ }).click();
    await pane.getByRole("button", { name: "Complete work", exact: true }).click();
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Done");
    expect(closeouts).toBe(1);
  } finally { await api.dispose(); }
});
