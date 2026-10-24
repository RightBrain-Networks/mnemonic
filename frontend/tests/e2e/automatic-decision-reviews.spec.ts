import { expect, request, test, type APIRequestContext } from "@playwright/test";
import type { ProjectSettings, WorkItem } from "../../lib/types";
import type { Decision } from "../../lib/knowledge";
import { selectWork, workCard } from "./surface";
import { claim, createWork } from "./task-fixtures";
import { reportForFixture } from "./job-report-fixture";

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

test("automatic decision impact reviews keep a shared priority and preserve project settings", async ({ page }, testInfo) => {
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
    await expect(priority).toHaveValue("40");
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
    await expect(priority).toHaveValue("40");
    await toggle.check();
    await expect(priority).toHaveValue("40");
    await page.locator("#project-select").selectOption(selected.id);
    await expect(priority).toHaveValue("55");
    await priority.fill("");
    await expect(save).toBeDisabled();
    await toggle.uncheck();
    await expect(priority).toHaveValue("55");
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

test("knowledge domains override and inherit the project review default", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const selected = await createProject(api);
    const path = `/api/v1/projects/${selected.id}/knowledge/domains`;
    const adr = await json(await api.post(path, { data: {
      name: "Architecture decisions", prefix: "ADR", client_operation_id: crypto.randomUUID()
    } }));
    expect(adr.automatic_decision_reviews).toBe("inherit");
    await page.goto(`/settings/workspace?project=${selected.id}`);
    await page.locator("#project-select").selectOption(selected.id);
    const card = page.getByRole("region", { name: "Automatic decision impact reviews", exact: true });
    const architecture = card.getByRole("form", { name: "ADR automatic decision impact reviews", exact: true });
    const product = card.getByRole("form", { name: "PDR automatic decision impact reviews", exact: true });
    await expect(architecture.getByRole("combobox")).toHaveValue("inherit");
    await expect(product.getByRole("combobox")).toHaveValue("inherit");
    await card.getByRole("switch").check();
    await card.getByRole("button", { name: "Save decision impact review settings", exact: true }).click();
    await expect(architecture.getByText("Saved effective setting: On (inherits project default).")).toBeVisible();
    await product.getByRole("combobox").selectOption("off");
    await product.getByRole("button", { name: "Save PDR setting" }).click();
    await expect(product.getByText("Saved effective setting: Off (domain override).")).toBeVisible();
    await card.getByRole("switch").uncheck();
    await card.getByRole("button", { name: "Save decision impact review settings", exact: true }).click();
    await expect(architecture.getByText("Saved effective setting: Off (inherits project default).")).toBeVisible();
    await architecture.getByRole("combobox").selectOption("on");
    await architecture.getByRole("button", { name: "Save ADR setting" }).click();
    await expect(architecture.getByText("Saved effective setting: On (domain override).")).toBeVisible();
    await expect(card.getByRole("spinbutton", { name: "Decision impact review priority", exact: true })).toBeVisible();
    await page.reload();
    await expect(card.getByRole("switch")).not.toBeChecked();
    await expect(architecture.getByRole("combobox")).toHaveValue("on");
    await expect(product.getByRole("combobox")).toHaveValue("off");
    const retained = await json(await api.get(path));
    expect(retained.items.find((item: { id: string }) => item.id === adr.id).automatic_decision_reviews).toBe("on");
    expect(retained.items.find((item: { prefix: string }) => item.prefix === "PDR").automatic_decision_reviews).toBe("off");
    const screenshot = testInfo.outputPath(`domain-decision-impact-reviews-${testInfo.project.name}.png`);
    const domainSettings = card.locator(".domain-decision-review-settings");
    await domainSettings.scrollIntoViewIfNeeded();
    await domainSettings.screenshot({ path: screenshot, animations: "disabled" });
    await testInfo.attach("Per-domain automatic decision impact review settings", { path: screenshot, contentType: "image/png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("domain review settings preserve drafts on conflicts and retry the exact uncertain save", async ({ page }) => {
  await page.clock.install();
  const api = await apiClient();
  try {
    const selected = await createProject(api);
    const path = `/api/v1/projects/${selected.id}/knowledge/domains`;
    const adr = await json(await api.post(path, { data: {
      name: "Architecture", prefix: "ADR", client_operation_id: crypto.randomUUID()
    } }));
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.goto(`/settings/workspace?project=${selected.id}`);
    await page.locator("#project-select").selectOption(selected.id);
    const row = page.getByRole("form", { name: "ADR automatic decision impact reviews", exact: true });
    await row.getByRole("combobox").selectOption("on");
    await json(await api.patch(`${path}/${adr.id}`, { data: {
      expected_version: adr.version, automatic_decision_reviews: "off", client_operation_id: crypto.randomUUID()
    } }));
    await row.getByRole("button", { name: "Save ADR setting" }).click();
    await expect(row.getByText("This domain changed. Your draft has been kept. Saved setting: off.")).toBeVisible();
    await expect(row.getByRole("combobox")).toHaveValue("on");
    await expect(row.getByRole("button", { name: "Save ADR setting" })).toBeDisabled();
    await row.getByRole("button", { name: "I reviewed the saved ADR setting" }).click();
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${selected.id}/knowledge/domains/${adr.id}`, async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      bodies.push(route.request().postData()!);
      if (bodies.length === 1) {
        const response = await route.fetch();
        const saved = await response.json();
        await route.fulfill({ response, json: { ...saved, automatic_decision_reviews: "off" } });
      }
      else await route.continue();
    });
    await row.getByRole("button", { name: "Save ADR setting" }).click();
    await expect(row.getByText("Mnemonic returned an inconsistent domain setting. Retry this exact action.")).toBeVisible();
    await expect(row.getByText("The outcome is unconfirmed. Keep this page open and retry the saved action.")).toBeVisible();
    await expect(row.getByRole("combobox")).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    // A concurrent rename can move the pending row off the loaded pages. The
    // exact retry must survive that list refresh while navigation stays blocked.
    let refreshes = 0;
    const domainPages = new RegExp(`/api/mnemonic/projects/${selected.id}/knowledge/domains\\?`);
    await page.route(domainPages, async (route) => {
      const response = await route.fetch();
      const listed = await response.json();
      const items = listed.items.filter((item: { id: string }) => item.id !== adr.id);
      refreshes += 1;
      await route.fulfill({ response, json: { ...listed, items, total: items.length } });
    });
    await page.clock.fastForward(30_001);
    await expect.poll(() => refreshes).toBeGreaterThan(0);
    await expect(row.getByRole("button", { name: "Retry pending ADR setting" })).toBeVisible();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.unroute(domainPages);
    await row.getByRole("button", { name: "Retry pending ADR setting" }).click();
    await expect(row.getByText("Saved effective setting: On (domain override).")).toBeVisible();
    await expect(row.getByRole("combobox")).toBeEnabled();
    await expect(page.locator("#project-select")).toBeEnabled();
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    const retained = await json(await api.get(path));
    expect(retained.items.find((item: { id: string }) => item.id === adr.id)).toMatchObject({
      automatic_decision_reviews: "on", version: adr.version + 2
    });
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
    await expect(pane.getByText(/Scope version 1 · 1 revision · 1 assessment/)).toBeVisible();
    await pane.getByRole("checkbox", { name: "I have inspected all assessments in scope version 1." }).check();
    await pane.getByRole("button", { name: "Acknowledge scope version 1" }).click();
    await expect(pane.getByText("Scope version 1 acknowledged.", { exact: true })).toBeVisible();
    await pane.locator(".decision-change-panel").screenshot({ path: testInfo.outputPath("decision-impact-review-current-scope.png"), animations: "disabled" });
    await pane.getByRole("button", { name: `${original.reference} → ${changed.reference}`, exact: true }).click();
    await pane.getByRole("button", { name: `Record disposition for ${original.reference}`, exact: true }).click();
    const draft = "Keep this investigative disposition draft while another revision extends the review.";
    await pane.getByRole("textbox", { name: "Reason", exact: true }).fill(draft);
    const extended = (await json(await api.put(`${path}/decisions`, { data: {
      ...fields, content: "Use remote transport registration with durable retries.", decision_id: original.decision_id,
      expected_revision: changed.revision, source_work_item_id: investigation.id, client_operation_id: crypto.randomUUID()
    } }))).decision as Decision;
    // This must arrive through live invalidation, before the 30-second fallback poll.
    await expect(pane.getByText(/Scope version 2 · 2 revisions · 2 assessments/)).toBeVisible({ timeout: 10_000 });
    await expect(pane.getByRole("textbox", { name: "Reason", exact: true })).toHaveValue(draft);
    await expect(pane.getByRole("checkbox", { name: "I have inspected all assessments in scope version 2." })).not.toBeChecked();
    await expect(pane.getByRole("button", { name: "Acknowledge scope version 2" })).toBeDisabled();
    const refreshed = await json(await api.get(`/api/v1/projects/${selected.id}/work-items/${investigation.id}`));
    expect(refreshed.work_item.version).toBe(detail.work_item.version);
    await pane.getByRole("button", { name: "Cancel disposition", exact: true }).click();
    await claim(api, selected.id, investigation.id);
    await page.goto(`/?project=${selected.id}`);
    await expect(page.locator(".task-card").filter({ hasText: investigation.title }).getByText("DECISION IMPACT REVIEW", { exact: true })).toBeVisible();
    await configure(api, selected.id, { automatic_decision_reviews: false });
    await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "Retain remote transport registration with retries.", decision_id: original.decision_id,
        expected_revision: extended.revision, client_operation_id: crypto.randomUUID() }
    }));
    const remaining = await json(await api.get(`/api/v1/projects/${selected.id}/tasks?status=all&kind=work_item&limit=20`));
    expect(remaining.items).toHaveLength(1);
  } finally { await api.dispose(); }
});

test("ordinary work adopts assessments and closed reviews require explicit owner admission", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    const path = `/api/v1/projects/${project.id}/knowledge`;
    const domain = await json(await api.post(`${path}/domains`, {
      data: { name: "Architecture", prefix: "ADR", client_operation_id: crypto.randomUUID() }
    }));
    const fields = { domain_id: domain.id, title: "Bounded review adoption", state: "accepted", media_type: "text/markdown", author_kind: "human" };
    const original = (await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "First ruling.", client_operation_id: crypto.randomUUID() }
    }))).decision as Decision;
    await json(await api.put(`${path}/decisions`, {
      data: { ...fields, content: "Replacement ruling.", decision_id: original.decision_id,
        expected_revision: original.revision, client_operation_id: crypto.randomUUID() }
    }));
    const episodes = await json(await api.get(`${path}/assessments?decision_id=${original.decision_id}`));
    const episode = episodes.items[0];
    const work = await createWork(api, project.id, "Combined impact investigation");
    await page.goto(`/work-items?project=${project.id}`);
    const pane = await selectWork(page, "Combined impact investigation");
    await pane.getByText("Decision impact review scope", { exact: true }).click();
    await pane.getByText("Adopt existing assessments into this work item", { exact: true }).click();
    await pane.getByRole("textbox", { name: "Assessment IDs" }).fill(episode.id);
    await pane.getByRole("button", { name: "Adopt assessments", exact: true }).click();
    await expect(pane.getByText("DECISION IMPACT REVIEW", { exact: true })).toBeVisible();
    await expect(pane.getByText(/Scope version 1 ·/)).toBeVisible();
    await pane.getByRole("checkbox", { name: "I have inspected all assessments in scope version 1." }).check();
    await pane.getByRole("button", { name: "Acknowledge scope version 1" }).click();
    await expect(pane.getByText("Scope version 1 acknowledged.", { exact: true })).toBeVisible();
    await pane.locator(".decision-change-panel").screenshot({ path: testInfo.outputPath("decision-impact-review-adopted-scope.png"), animations: "disabled" });
    await json(await api.post(`${path}/assessments/${episode.id}/dispositions`, { data: {
      candidate_reference: original.reference, expected_version: episode.version, disposition: "not-affected",
      reason: "Disposable browser fixture has no implementation to remediate.",
      actor: { actor_client: "dashboard", actor_session_id: "adoption-browser-test" }, client_operation_id: crypto.randomUUID()
    } }));
    const context = await json(await api.get(`/api/v1/projects/${project.id}/work-items/${work.id}`));
    const child = (await json(await api.post(`/api/v1/projects/${project.id}/work-items`, { data: {
      title: "Moved decision follow-up", summary: "Retain the original review boundary after moving projects.",
      discovered_from_work_item_id: work.id,
      initial_checkpoint: { prompt: "Ask the owner about a new scope exception.", source_client: "codex", source_session_id: "adoption-browser-test" }
    } }))).work_item;
    await json(await api.post(`/api/v1/projects/${project.id}/work-items/${work.id}/complete`, { data: {
      expected_version: context.work_item.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
      checkpoint: { prompt: "Reviewed the disposable assessment and acknowledged current scope.", source_client: "dashboard", source_session_id: "adoption-browser-test" },
      job_completion_report: await reportForFixture(api, project.id)
    } }));
    const destination = await createProject(api);
    await configure(api, destination.id, { automatic_decision_reviews: true });
    await json(await api.post(`/api/v1/projects/${project.id}/work-items/${child.id}/move`, { data: {
      target_project_id: destination.id, expected_version: child.version, client_operation_id: crypto.randomUUID(),
      actor: { actor_client: "dashboard", actor_session_id: "adoption-browser-test", actor_model: null }
    } }));
    const gates = `/api/v1/projects/${destination.id}/work-items/${child.id}/gates`;
    const gate = await json(await api.post(gates, { data: {
      question: "Should the moved follow-up apply the new scope exception?", requested_by_client: "codex",
      requested_by_session_id: "adoption-browser-test", requested_by_model: "test-model", client_operation_id: crypto.randomUUID()
    } }));
    const answered = await json(await api.post(`${gates}/${gate.id}/resolve`, { data: {
      resolution: "Apply the reviewed exception in this destination project.", resolved_by_client: "dashboard",
      resolved_by_session_id: "adoption-browser-test", resolved_by_model: "human-ui",
      reviewed_context_revision: gate.current_context_revision, client_operation_id: crypto.randomUUID()
    } }));
    const answer = await json(await api.get(`/api/v1/projects/${destination.id}/knowledge/decisions/${answered.decision.reference}`));
    expect(answer.change.requires_admission).toBe(true);
    await page.goto(`/work-items?project=${destination.id}&work=${child.id}`);
    const movedPane = page.locator(".work-detail-pane");
    await movedPane.getByText(/Inherited change lineage \(/).click();
    const sourceScope = movedPane.getByRole("region", { name: `Decision change ${context.work_item.decision_review_change_id}`, exact: true });
    await expect(sourceScope.getByRole("link", { name: "Open the review work item" })).toHaveAttribute("href", `/work-items?project=${project.id}&work=${work.id}`);
    const heldScope = movedPane.getByRole("region", { name: `Decision change ${answer.change.change_id}`, exact: true });
    await expect(heldScope.getByText("These retained decision revisions have no review driver.", { exact: false })).toBeVisible();
    await heldScope.getByRole("checkbox", { name: "Authorize a new review cycle for this change." }).check();
    await heldScope.getByRole("button", { name: "Admit a new review cycle", exact: true }).click();
    await expect(heldScope.getByText(/New cycle admitted:/)).toBeVisible();
    const destinationTasks = await json(await api.get(`/api/v1/projects/${destination.id}/tasks?status=pending&kind=work_item&limit=20`));
    expect(destinationTasks.items.filter((item: WorkItem) => item.decision_review_change_id)).toHaveLength(1);
    await heldScope.screenshot({ path: testInfo.outputPath("decision-impact-review-moved-lineage.png"), animations: "disabled" });
    await page.reload();
    await movedPane.getByText(/Inherited change lineage \(/).click();
    await expect(heldScope.getByText(/New cycle admitted:/)).toBeVisible();
    await expect(heldScope.getByRole("button", { name: "Admit a new review cycle", exact: true })).toHaveCount(0);
    await page.goto(`/work-items?project=${project.id}&work=${work.id}`);
    const closedPane = page.locator(".work-detail-pane");
    await expect(closedPane.getByRole("button", { name: "Admit a new review cycle", exact: true })).toBeDisabled();
    await closedPane.getByRole("checkbox", { name: "Authorize a new review cycle for this change." }).check();
    await closedPane.getByRole("button", { name: "Admit a new review cycle", exact: true }).click();
    await expect(closedPane.getByText(/New cycle admitted:/)).toBeVisible();
    await closedPane.locator(".decision-change-panel").screenshot({ path: testInfo.outputPath("decision-impact-review-owner-admission.png"), animations: "disabled" });
    await page.reload();
    await expect(closedPane.getByText(/New cycle admitted:/)).toBeVisible();
    await expect(closedPane.getByRole("button", { name: "Admit a new review cycle", exact: true })).toHaveCount(0);
    const retained = await json(await api.get(`/api/v1/projects/${project.id}/work-items/${work.id}`));
    expect(retained.work_item.status).toBe("done");
  } finally { await api.dispose(); }
});
