import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function apiClient() {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("The disposable E2E API is not configured.");
  return request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${apiKey}` } });
}

async function createProject(api: APIRequestContext, name: string) {
  const response = await api.post("/api/v1/projects", {
    data: { name, slug: `rubric-${crypto.randomUUID()}` }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { id: string }).id;
}

test("priority rubric loads existing Markdown, persists edits, and follows the selected project", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const projectId = await createProject(api, "Priority guidance");
    const otherId = await createProject(api, "Independent priority guidance");
    const original = await readFile("../backend/src/mnemonic_api/priority_rubric.md", "utf8");
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(projectId);
    const card = page.getByRole("region", { name: "Priority rubric", exact: true });
    const editor = card.getByRole("textbox", { name: "Rubric (Markdown)", exact: true });
    const save = card.getByRole("button", { name: "Save priority rubric", exact: true });
    await expect(editor).toHaveValue(original);
    await expect(save).toBeDisabled();
    await card.scrollIntoViewIfNeeded();
    const screenshot = testInfo.outputPath(`workspace-priority-rubric-${testInfo.project.name}.png`);
    const viewport = page.viewportSize()!;
    await page.setViewportSize({ ...viewport, height: 1100 });
    await card.screenshot({ path: screenshot });
    await page.setViewportSize(viewport);
    await testInfo.attach("Workspace priority rubric", { path: screenshot, contentType: "image/png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await editor.fill(" \n");
    await expect(save).toBeDisabled();
    const custom = "# Our project priorities\n\n- **Customer impact** first.\n- Preserve data. 📄\n";
    await editor.fill(custom);
    await save.click();
    await expect(save).toBeDisabled();
    await expect(page.getByText("Priority rubric saved.", { exact: true })).toBeVisible();
    const endpoint = `/api/v1/projects/${projectId}/priority-rubric`;
    expect((await (await api.get(endpoint)).json()).content).toBe(custom);
    const settings = await (await api.get(`/api/v1/projects/${projectId}/settings`)).json();
    expect(settings).not.toHaveProperty("priority_rubric");
    await page.reload();
    await expect(editor).toHaveValue(custom);
    await editor.fill("Unsaved draft for the first project");
    await page.locator("#project-select").selectOption(otherId);
    await expect(editor).toHaveValue(original);
    await page.locator("#project-select").selectOption(projectId);
    await expect(editor).toHaveValue(custom);
  } finally {
    await api.dispose();
  }
});

test("priority rubric retains drafts and requires review after a conflicting save", async ({ page }) => {
  const api = await apiClient();
  try {
    const projectId = await createProject(api, "Priority conflict");
    // Keep the stale editor open to exercise the server's compare-and-set response.
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.goto("/settings/workspace");
    await page.locator("#project-select").selectOption(projectId);
    const card = page.getByRole("region", { name: "Priority rubric", exact: true });
    const editor = card.getByRole("textbox", { name: "Rubric (Markdown)", exact: true });
    await expect(editor).toHaveValue(/Assign work priority deliberately/);
    const draft = "# My unsaved rubric";
    await editor.fill(draft);
    const endpoint = `/api/v1/projects/${projectId}/priority-rubric`;
    const prior = await (await api.get(endpoint)).json();
    const changed = await api.patch(endpoint, {
      data: { content: "# Another person's update", expected_revision: prior.revision }
    });
    expect(changed.ok()).toBe(true);
    const save = card.getByRole("button", { name: "Save priority rubric", exact: true });
    await save.click();
    await expect(card.getByText(/Your draft has been kept/)).toBeVisible();
    await expect(editor).toHaveValue(draft);
    await expect(save).toBeDisabled();
    await card.getByRole("button", { name: "Load saved rubric", exact: true }).click();
    await expect(card.getByRole("textbox", { name: "Currently saved rubric", exact: true })).toHaveValue("# Another person's update");
    await card.getByRole("button", { name: "I reviewed the saved rubric" }).click();
    await save.click();
    await expect(page.getByText("Priority rubric saved.", { exact: true })).toBeVisible();
    expect((await (await api.get(endpoint)).json()).content).toBe(draft);
  } finally {
    await api.dispose();
  }
});
