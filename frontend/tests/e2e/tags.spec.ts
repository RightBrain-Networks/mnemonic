import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import { createProject } from "./task-fixtures";

test.use({ actionTimeout: 10000 });

async function addTag(page: Page, name: string, description: string, color?: string) {
  await page.getByRole("button", { name: "New tag", exact: true }).click();
  const form = page.locator(".tag-edit-form");
  await form.getByRole("textbox", { name: /^Name/ }).fill(name);
  await form.getByLabel("Description", { exact: true }).fill(description);
  if (color) await form.getByLabel("Tag color hex").fill(color);
  await form.getByRole("button", { name: "Save tag", exact: true }).click();
  await expect(form).toHaveCount(0);
}
async function addWork(api: APIRequestContext, projectId: string, title: string, tags: string[] | null) {
  const response = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title, summary: "Exercise project label selection and filtering.", tags, discovered_from_work_item_id: null,
    client_operation_id: crypto.randomUUID(),
    initial_checkpoint: { prompt: "Validate project label behavior.", source_client: "codex", source_session_id: "tag-acceptance" }
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()).work_item;
}

test("project tags can be managed, ordered, selected and combined on work items", async ({ page }, testInfo) => {
  test.setTimeout(90000);
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    await page.goto("/settings/tags");
    const list = page.getByRole("list", { name: "Project tags" });
    await expect(list.getByRole("listitem")).toHaveCount(3);
    for (const name of ["code-review", "remediation", "dir"]) {
      const row = list.getByRole("listitem").filter({ has: page.locator(".work-tag", { hasText: new RegExp(`^${name}$`) }) });
      await expect(row.getByText("System", { exact: true })).toBeVisible();
      await expect(row.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
      await expect(row.getByRole("button", { name: "Delete", exact: true })).toHaveCount(0);
    }
    await addTag(page, "Needs Design", "Use when design decisions are needed.", "#000000");
    await addTag(page, "Frontend", "Changes to the dashboard.");
    const design = list.getByRole("listitem").filter({ hasText: "needs-design" });
    const frontend = list.getByRole("listitem").filter({ hasText: "frontend" });
    await frontend.getByRole("button", { name: "Move frontend up" }).click();
    await expect.poll(() => list.locator(".work-tag").allTextContents()).toEqual(["code-review", "remediation", "dir", "frontend", "needs-design"]);
    await frontend.dragTo(list.getByRole("listitem").first());
    await expect(list.locator(".work-tag").first()).toHaveText("frontend");
    await design.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".tag-edit-form").getByLabel("Description").fill("Work requiring a design review.");
    await page.getByRole("button", { name: "Save tag", exact: true }).click();
    await expect(design).toContainText("Work requiring a design review.");
    await page.getByRole("button", { name: "Dismiss notification", exact: true }).click();
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    await page.screenshot({ path: testInfo.outputPath("project-tags.png"), fullPage: true, animations: "disabled" });

    await addWork(api, project.id, "Design only", ["needs-design"]);
    await addWork(api, project.id, "Frontend only", ["frontend"]);
    await addWork(api, project.id, "Both tags", ["needs-design", "frontend"]);
    await addWork(api, project.id, "No tags", null);
    await page.goto(`/work-items?project=${project.id}`);
    const filters = page.getByRole("group", { name: "Filter by tags", exact: true });
    const cards = page.locator(".work-queue-list .work-item-card");
    await expect(cards).toHaveCount(4);
    await expect(page.getByRole("button", { name: "More filters" })).toHaveCount(0);
    await expect(filters.locator(".work-tag").first()).toHaveText("frontend");
    await filters.getByRole("button", { name: "needs-design", exact: true }).click();
    await expect(cards).toHaveCount(2);
    await filters.getByRole("button", { name: "frontend", exact: true }).click();
    await expect(cards).toHaveCount(3);
    await filters.getByText("AND", { exact: true }).click();
    await expect(filters.getByRole("radio", { name: "AND", exact: true })).toBeChecked();
    await expect(cards).toHaveCount(1);
    await expect(cards.first()).toContainText("Both tags");
    await filters.getByText("All", { exact: true }).click();
    await expect(filters.getByRole("radio", { name: "All", exact: true })).toBeChecked();
    await expect(cards).toHaveCount(4);
    await expect(filters.getByRole("button", { name: "frontend", exact: true })).toHaveAttribute("aria-pressed", "false");

    await page.locator(".topbar").getByRole("button", { name: "New work", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Create durable work" });
    await dialog.getByLabel("Title", { exact: true }).fill("Created with a tag");
    await dialog.getByLabel("Summary").fill("The selected tag is saved on the objective.");
    await dialog.getByLabel("Initial context checkpoint").fill("Retain the selected project label.");
    await dialog.getByRole("checkbox", { name: /frontend/ }).check();
    await dialog.getByRole("button", { name: "Create work and checkpoint", exact: true }).click();
    const pane = page.locator(".work-detail-pane");
    await expect(pane.locator(".detail-title")).toHaveText("Created with a tag");
    await expect(pane.locator(".detail-fact-tags .work-tag")).toHaveText("frontend");
    await pane.getByRole("button", { name: "Edit work item", exact: true }).click();
    await pane.getByRole("checkbox", { name: /frontend/ }).uncheck();
    await pane.getByRole("checkbox", { name: /needs-design/ }).check();
    await pane.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(pane.locator(".detail-fact-tags .work-tag")).toHaveText("needs-design");
    await page.getByRole("button", { name: "Dismiss notification", exact: true }).click();
    await page.evaluate(() => {
      document.querySelectorAll(".detail-scroll, .main-content, .page-content").forEach((element) => { element.scrollTop = 0; });
      window.scrollTo(0, 0);
      (document.activeElement as HTMLElement)?.blur();
    });
    await page.screenshot({ path: testInfo.outputPath("work-tags.png"), fullPage: true, animations: "disabled" });

    await page.goto("/settings/tags");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("list", { name: "Project tags" }).getByRole("listitem").filter({ hasText: "needs-design" }).getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByRole("list", { name: "Project tags" }).locator(".work-tag", { hasText: "needs-design" })).toHaveCount(0);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(page.getByRole("group", { name: "Filter by tags", exact: true }).getByRole("button", { name: "needs-design", exact: true })).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("tag mutations keep their exact retry intent after a lost response", async ({ page }) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    await page.goto("/settings/tags");
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/tags`, async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      bodies.push(route.request().postData()!);
      const response = await route.fetch();
      if (bodies.length === 1) await route.fulfill({ status: 502, json: { detail: "Lost save response" } });
      else await route.fulfill({ response });
    });
    await page.getByRole("button", { name: "New tag", exact: true }).click();
    const form = page.locator(".tag-edit-form");
    await form.getByRole("textbox", { name: /^Name/ }).fill("Reliable retry");
    await form.getByLabel("Description", { exact: true }).fill("Use to exercise request recovery.");
    await form.getByRole("button", { name: "Save tag", exact: true }).click();
    const retry = page.getByRole("button", { name: "Retry same action", exact: true });
    await expect(retry).toBeVisible();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("navigation", { name: "Workspace navigation" }).getByRole("link", { name: "Work items", exact: true }).click();
    await expect(page).toHaveURL("/settings/tags");
    await retry.click();
    await expect(retry).toHaveCount(0);
    await expect(form).toHaveCount(0);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    await expect(page.getByRole("list", { name: "Project tags" }).locator(".work-tag", { hasText: "reliable-retry" })).toHaveCount(1);
  } finally { await api.dispose(); }
});
