import { expect, request, test, type APIRequestContext } from "@playwright/test";
import type { ProjectSettings, WorkItem } from "../../lib/types";
import type { Decision } from "../../lib/knowledge";
import { selectWork, workCard } from "./surface";
import { claim } from "./task-fixtures";

async function apiClient() {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("The disposable E2E API is not configured.");
  return request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${apiKey}` } });
}
async function json(response: Awaited<ReturnType<APIRequestContext["post"]>>) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}
async function createProject(api: APIRequestContext) {
  return await json(await api.post("/api/v1/projects", {
    data: { name: `Automatic decision impact reviews ${crypto.randomUUID().slice(0, 8)}` }
  })) as { id: string; name: string };
}
async function getSettings(api: APIRequestContext, projectId: string): Promise<ProjectSettings> {
  return await json(await api.get(`/api/v1/projects/${projectId}/settings`));
}
async function configure(api: APIRequestContext, projectId: string, values: Partial<ProjectSettings>) {
  const current = await getSettings(api, projectId);
  return json(await api.patch(`/api/v1/projects/${projectId}/settings`, {
    data: { expected_revision: current.revision, ...values }
  }));
}

test("automatic decision impact reviews disclose priority only when enabled and preserve project settings", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const selected = await createProject(api), other = await createProject(api);
    expect(await getSettings(api, selected.id)).toMatchObject({ automatic_decision_reviews: false, decision_review_priority: 40 });
    await page.goto(`/settings/workspace?project=${selected.id}`);
    await page.locator("#project-select").selectOption(selected.id);
    const card = page.getByRole("region", { name: "Automatic decision impact reviews", exact: true });
    const toggle = card.getByRole("switch");
    const priority = card.getByRole("spinbutton", { name: "Decision impact review priority", exact: true });
    const save = card.getByRole("button", { name: "Save decision impact review settings" });
    await expect(toggle).not.toBeChecked();
    await expect(priority).toHaveCount(0);
    await expect(save).toBeDisabled();
    await toggle.check();
    await expect(priority).toHaveValue("40");
    await card.scrollIntoViewIfNeeded();
    const screenshot = testInfo.outputPath(`automatic-decision-impact-reviews-${testInfo.project.name}.png`);
    await card.screenshot({ path: screenshot });
    await testInfo.attach("Automatic decision impact reviews settings", { path: screenshot, contentType: "image/png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const invalid of ["", "-1", "101", "40.5"]) {
      await priority.fill(invalid);
      await expect(save).toBeDisabled();
      await expect(card.getByRole("alert")).toContainText("whole number from 0 to 100");
    }
    await priority.fill("55");
    await save.click();
    await expect(save).toBeDisabled();
    expect(await getSettings(api, selected.id)).toMatchObject({ automatic_decision_reviews: true, decision_review_priority: 55 });
    await page.reload();
    await expect(toggle).toBeChecked();
    await expect(priority).toHaveValue("55");
    await page.locator("#project-select").selectOption(other.id);
    await expect(toggle).not.toBeChecked();
    await expect(priority).toHaveCount(0);
    await toggle.check();
    await expect(priority).toHaveValue("40");
    await page.locator("#project-select").selectOption(selected.id);
    await expect(priority).toHaveValue("55");
    await priority.fill("");
    await expect(save).toBeDisabled();
    await toggle.uncheck();
    await expect(priority).toHaveCount(0);
    await expect(save).toBeEnabled();
    await save.click();
    await expect(save).toBeDisabled();
    expect(await getSettings(api, selected.id)).toMatchObject({ automatic_decision_reviews: false, decision_review_priority: 55 });
  } finally { await api.dispose(); }
});

test("automatic decision impact review drafts survive concurrent settings changes", async ({ page }) => {
  const api = await apiClient();
  try {
    const selected = await createProject(api);
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.goto(`/settings/workspace?project=${selected.id}`);
    await page.locator("#project-select").selectOption(selected.id);
    const card = page.getByRole("region", { name: "Automatic decision impact reviews", exact: true });
    const toggle = card.getByRole("switch");
    const priority = card.getByRole("spinbutton", { name: "Decision impact review priority", exact: true });
    const save = card.getByRole("button", { name: "Save decision impact review settings" });
    await toggle.check();
    await priority.fill("65");
    await configure(api, selected.id, { automatic_decision_reviews: true, decision_review_priority: 25 });
    await save.click();
    await expect(card.getByText("Saved settings: automatic decision impact reviews on; priority 25.")).toBeVisible();
    await expect(priority).toHaveValue("65");
    await expect(save).toBeDisabled();
    await card.getByRole("button", { name: "I reviewed the saved decision impact review settings" }).click();
    await save.click();
    await expect(save).toBeDisabled();
    expect(await getSettings(api, selected.id)).toMatchObject({ automatic_decision_reviews: true, decision_review_priority: 65 });
  } finally { await api.dispose(); }
});

test("changed decisions create ordinary investigation tasks with editable instructions and decision impact review chips", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const selected = await createProject(api);
    await configure(api, selected.id, { automatic_decision_reviews: true, decision_review_priority: 55 });
    await page.goto(`/settings/prompts?project=${selected.id}`);
    await page.locator("#project-select").selectOption(selected.id);
    await page.getByRole("button", { name: "Decision impact review", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Decision impact review", exact: true });
    const editor = drawer.getByRole("textbox", { name: "Prompt content" });
    await expect(editor).toHaveValue(/create_work/);
    const instructions = "Investigate the changed decision for $PROJECT_NAME. Do not change code. Create follow-up work with create_work for necessary remediation.";
    await editor.fill(instructions);
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    const path = `/api/v1/projects/${selected.id}/knowledge`;
    const domain = await json(await api.post(`${path}/domains`, {
      data: { name: "Architecture", prefix: "ADR", client_operation_id: crypto.randomUUID() }
    }));
    const fields = { domain_id: domain.id, title: "Use durable transport adapters", state: "accepted", media_type: "text/markdown", author_kind: "human" };
    const original = (await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "Use local transport adapters.", client_operation_id: crypto.randomUUID() }
    }))).decision as Decision;
    const changed = (await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "Use remote transport registration.", decision_id: original.decision_id,
        expected_revision: original.revision, client_operation_id: crypto.randomUUID() }
    }))).decision as Decision;
    const tasks = await json(await api.get(`/api/v1/projects/${selected.id}/tasks?status=pending&kind=work_item&limit=20`));
    expect(tasks.items).toHaveLength(1);
    const investigation = tasks.items[0] as { id: string; title: string; kind: string; decision_review_revision_id: string };
    expect(investigation.kind).toBe("work_item");
    expect(investigation.decision_review_revision_id).toMatch(/^[0-9a-f-]{36}$/);
    const detail = await json(await api.get(`/api/v1/projects/${selected.id}/work-items/${investigation.id}`)) as { work_item: WorkItem };
    expect(detail.work_item).toMatchObject({ status: "pending", priority: 55 });
    const history = await json(await api.get(`/api/v1/projects/${selected.id}/work-items/${investigation.id}/checkpoints`));
    const checkpoint = history.items.find((item: { id: string }) => item.id === detail.work_item.initial_checkpoint_id);
    expect(checkpoint.source_metadata.decision_revision_id).toBe(investigation.decision_review_revision_id);
    expect(checkpoint.source_metadata.prompt_id).toBe("decision-impact-review");
    expect(checkpoint.prompt).toContain(instructions.replace("$PROJECT_NAME", selected.name));
    expect(checkpoint.prompt).toContain(original.reference);
    expect(checkpoint.prompt).toContain(changed.reference);
    await page.goto(`/work-items?project=${selected.id}`);
    const card = workCard(page, investigation.title);
    await expect(card.getByText("DECISION IMPACT REVIEW", { exact: true })).toBeVisible();
    await card.screenshot({ path: testInfo.outputPath("decision-impact-review-work-card.png") });
    const pane = await selectWork(page, investigation.title);
    await expect(pane.getByText("DECISION IMPACT REVIEW", { exact: true })).toBeVisible();
    await claim(api, selected.id, investigation.id);
    await page.goto(`/?project=${selected.id}`);
    await expect(page.locator(".task-card").filter({ hasText: investigation.title }).getByText("DECISION IMPACT REVIEW", { exact: true })).toBeVisible();
    await configure(api, selected.id, { automatic_decision_reviews: false });
    await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "Retain remote transport registration with retries.", decision_id: original.decision_id,
        expected_revision: changed.revision, client_operation_id: crypto.randomUUID() }
    }));
    const remaining = await json(await api.get(`/api/v1/projects/${selected.id}/tasks?status=all&kind=work_item&limit=20`));
    expect(remaining.items).toHaveLength(1);
  } finally { await api.dispose(); }
});
