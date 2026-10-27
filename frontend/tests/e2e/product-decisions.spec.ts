import { expect, request, test, type APIRequestContext, type APIResponse } from "@playwright/test";
import type { DecisionContent, KnowledgeDomain } from "../../lib/knowledge";
import type { HumanGateRead } from "../../lib/types";
import { selectWork } from "./surface";

async function json<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as T;
}

async function seed(api: APIRequestContext) {
  const suffix = crypto.randomUUID().slice(0, 8);
  const project = await json<{ id: string }>(await api.post("/api/v1/projects", { data: { name: `Product decisions ${suffix}` } }));
  const path = `/api/v1/projects/${project.id}`;
  const domains = await json<{ items: KnowledgeDomain[] }>(await api.get(`${path}/knowledge/domains`));
  expect(domains.items).toHaveLength(1);
  expect(domains.items[0]).toMatchObject({ prefix: "PDR", name: "Product Decision Records", is_product_decision_domain: true });
  const title = `Choose the product launch policy ${suffix}`;
  const recordTitle = `Product launch enrollment policy ${suffix}`;
  const created = await json<{ work_item: { id: string } }>(await api.post(`${path}/work-items`, { data: {
    title, summary: "Ask the product owner before implementing the launch policy.", discovered_from_work_item_id: null,
    tags: null,
    initial_checkpoint: { prompt: "The launch behavior requires a product decision.", source_client: "playwright-api", source_session_id: `pdr-${suffix}`, source_model: "test-model", tags: [], source_metadata: {} }
  } }));
  const workId = created.work_item.id;
  const gatePath = `${path}/work-items/${workId}/gates`;
  const actor = { requested_by_client: "playwright-api", requested_by_session_id: `pdr-${suffix}`, requested_by_model: "test-model" };
  const original = `Should the launch include automatic enrollment? ${suffix}`;
  const question = `Should customers explicitly opt in before enrollment? ${suffix}`;
  const gate = await json<HumanGateRead>(await api.post(gatePath, { data: {
    gate_type: "human", question: original, ...actor, decision_metadata: { title: recordTitle }, client_operation_id: crypto.randomUUID()
  } }));
  const updated = await json<HumanGateRead>(await api.post(gatePath, { data: {
    gate_type: "human", question, gate_id: gate.id, expected_question_version: 1, ...actor, client_operation_id: crypto.randomUUID()
  } }));
  expect(updated.decision).toMatchObject({ decision_id: gate.decision!.decision_id, revision: 1, state: "proposed" });
  return { project, path, title, recordTitle, workId, original, question, gate: updated };
}

test("Needs Attention records product questions and human answers as searchable PDR revisions", async ({ page }, testInfo) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json" } });
  try {
    const { project, path, title, recordTitle, workId, original, question, gate } = await seed(api);
    const decisionId = gate.decision!.decision_id;
    await page.goto(`/attention?project=${project.id}&work_item_id=${workId}`);
    const card = page.locator("article.attention-card").filter({ hasText: title });
    await expect(card.getByText(question, { exact: true })).toBeVisible();
    await expect(card.getByRole("heading", { level: 3 })).toHaveText(recordTitle);
    await expect(card.getByRole("link", { name: `View product decision record ${decisionId}-1` })).toBeVisible();
    await expect(card.locator(".decision-state")).toHaveText("Proposed");
    await card.getByRole("tab", { name: "Version 1", exact: true }).click();
    await expect(card.getByText(original, { exact: true })).toBeVisible();
    await expect(card.getByRole("link", { name: `View product decision record ${decisionId}-0` })).toBeVisible();
    await expect(card.getByLabel("Your answer")).toBeDisabled();
    await card.getByRole("link", { name: `View question revision ${decisionId}-0` }).click();
    const drawer = page.getByRole("dialog", { name: recordTitle, exact: true });
    await expect(drawer.getByRole("region", { name: "Markdown contents" })).toContainText(original);
    await expect(drawer.getByRole("link", { name: "View originating work", exact: true })).toHaveAttribute("href", `/work-items?project=${project.id}&work=${workId}`);
    await drawer.getByRole("link", { name: "Answer in Needs Attention", exact: true }).click();
    await expect(card.getByText(question, { exact: true })).toBeVisible();
    await expect(card.getByLabel("Your answer")).toBeEnabled();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1280, height: 1100 });
    await page.locator(".page-content").evaluate((element) => { element.scrollTop = 0; });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath("product-decision-attention.png"), fullPage: true, animations: "disabled" });
    const answer = `Do not enroll customers automatically. Require an explicit opt-in for launch ${workId.slice(0, 8)}.`;
    await card.getByLabel("Your answer").fill(answer);
    await card.getByRole("button", { name: "Record answer", exact: true }).click();
    await expect(card).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "No explicit human questions are waiting." })).toBeVisible();
    const decision = await json<DecisionContent>(await api.get(`${path}/knowledge/decisions/${decisionId}`));
    expect(decision).toMatchObject({ revision: 2, state: "accepted", author_kind: "human", related_work_item_ids: [workId], human_gate: { gate_id: gate.id, status: "resolved", question_version: 2 } });
    expect(decision.content).toContain(question);
    expect(decision.content).toContain(answer);
    const results = await json<{ items: DecisionContent[] }>(await api.get(`${path}/knowledge/decisions?q=${encodeURIComponent(answer)}`));
    expect(results.items.map((item) => item.decision_id)).toContain(decisionId);
    await page.goto(`/knowledge?project=${project.id}&decision=${decisionId}&revision=2`);
    await expect(drawer.getByRole("region", { name: "Markdown contents" })).toContainText(answer);
    await expect(drawer.locator(".decision-state").first()).toHaveText("Accepted");
    await expect(drawer.getByRole("link", { name: "Answer in Needs Attention", exact: true })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("product-decision-document.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "Revisions", exact: true }).click();
    for (const revision of [0, 1, 2]) await expect(drawer.getByRole("button", { name: `${decisionId}-${revision}`, exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("product-decision-history.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "Details & links", exact: true }).click();
    await expect(drawer.getByRole("link", { name: workId, exact: true })).toBeVisible();
    await expect(drawer).toContainText(decision.sha256);
  } finally { await api.dispose(); }
});

test("manual Done requires a deliberate decision provenance declaration", async ({ page }) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json" } });
  try {
    const { project, path, title, workId, gate } = await seed(api);
    await json(await api.post(`${path}/work-items/${workId}/gates/${gate.id}/resolve`, { data: {
      expected_question_version: gate.question_version, reviewed_context_revision: gate.current_context_revision,
      resolution: "Require explicit opt-in.", resolved_by_client: "playwright-api", resolved_by_session_id: "manual-closeout-fixture", client_operation_id: crypto.randomUUID()
    } }));
    await page.goto(`/work-items?project=${project.id}`);
    const pane = await selectWork(page, title);
    const submissions: Array<{ job_completion_report: { decision_provenance: unknown } }> = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith(`/work-items/${workId}/complete`)) submissions.push(request.postDataJSON());
    });
    await pane.getByRole("button", { name: `Choose an action for ${title}` }).click();
    await pane.getByRole("menuitem", { name: `Done ${title}`, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Record completion decision", exact: true });
    const complete = dialog.getByRole("button", { name: "Record completion", exact: true });
    await expect(dialog.getByRole("combobox", { name: "Decision use", exact: true })).toHaveValue("");
    await expect(complete).toBeDisabled();
    expect(submissions).toHaveLength(0);
    await dialog.getByRole("combobox", { name: "Decision use", exact: true }).selectOption("not-code");
    await expect(complete).toBeDisabled();
    const reason = "This disposable acceptance fixture records a product decision without implementing application code.";
    await dialog.locator(".decision-provenance-editor").getByRole("textbox", { name: "Reason", exact: true }).fill(reason);
    await complete.click();
    await expect(dialog).toHaveCount(0);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Done");
    expect(submissions).toHaveLength(1);
    expect(submissions[0].job_completion_report.decision_provenance).toEqual({ disposition: "not-code", reason, citations: [], commits: [] });
  } finally { await api.dispose(); }
});
