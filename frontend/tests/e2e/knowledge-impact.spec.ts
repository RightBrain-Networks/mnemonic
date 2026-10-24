import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { openTab, selectWork } from "./surface";
import type { Decision } from "../../lib/knowledge";

const repository = "github.com/acceptance/transport";
const originalHash = "c".repeat(40), mergedHash = "d".repeat(40);
async function apiClient() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json" } });
}
async function json(response: Awaited<ReturnType<APIRequestContext["post"]>>) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}
async function work(api: APIRequestContext, projectId: string, title: string) {
  return (await json(await api.post(`/api/v1/projects/${projectId}/work-items`, { data: { title, summary: "Trace decision changes through implementation and reassessment.", priority: 10, discovered_from_work_item_id: null,
    initial_checkpoint: { prompt: "Implement the recorded transport decision.", source_client: "playwright-api", source_session_id: "impact-fixture", tags: [], source_metadata: {} } } }))).work_item as { id: string; title: string; version: number };
}
async function fixture(api: APIRequestContext) {
  const project = await json(await api.post("/api/v1/projects", { data: { name: `Decision impact ${crypto.randomUUID().slice(0, 8)}` } }));
  const path = `/api/v1/projects/${project.id}/knowledge`;
  const domain = await json(await api.post(`${path}/domains`, { data: { name: "Architecture", prefix: "ADR", client_operation_id: crypto.randomUUID() } }));
  const base = { domain_id: domain.id, state: "accepted", media_type: "text/markdown", author_kind: "human" };
  async function decision(title: string, content: string, extras: Record<string, unknown> = {}) {
    return (await json(await api.put(`${path}/decisions`, { data: { ...base, title, content, ...extras, client_operation_id: crypto.randomUUID() } }))).decision as Decision;
  }
  const prerequisite = await decision("Keep transport identities stable", "# Transport identities\n\nKeep adapter identities stable across catalog changes.\n");
  const dependencies = [{ decision_id: prerequisite.decision_id, revision: 0, rationale: "Catalog entries use the existing stable adapter identifiers." }];
  const origin = await decision("Use a durable transport catalog", "# Transport catalog\n\nUse local transport adapters.\n", { depends_on: dependencies });
  const dependent = await decision("Build a transport gateway", "# Gateway\n\nThe gateway uses the catalog.\n", { depends_on: [{ decision_id: origin.decision_id, revision: 0, rationale: "The gateway resolves transports through the catalog contract." }] });
  const related = await decision("Record support questions", "# Support\n\nRelated discussion, with no dependency assertion.\n", { related_decision_ids: [origin.decision_id] });
  const remediation = await work(api, project.id, "Update the gateway transport adapter");
  const authorWork = await work(api, project.id, "Implement remote catalog registration");
  await json(await api.post(`/api/v1/projects/${project.id}/work-items/${authorWork.id}/claim`, { data: { holder_client: "codex", holder_session_id: "revision-author-session", claim_request_id: crypto.randomUUID(), session_transcript: null } }));
  const registered = await json(await api.post(`${path}/decisions/${dependent.decision_id}/scope`, { data: { revision: 0, repository, commit_hash: originalHash, work_item_id: remediation.id, agent_session_id: "implementation-session", client_operation_id: crypto.randomUUID() } }));
  const storedRepository = registered.scope.repository;
  expect(storedRepository).toBe("https://github.com/acceptance/transport");
  const mapping = await json(await api.post(`${path}/commits/reconcile`, { data: { repository: storedRepository, original_commit_hash: originalHash, merged_commit_hash: mergedHash, client_operation_id: crypto.randomUUID() } }));
  expect(mapping.repository).toBe(storedRepository);
  const current = await decision(origin.title, "# Transport catalog\n\nUse remotely registered transport adapters.\n", { decision_id: origin.decision_id, expected_revision: 0, depends_on: dependencies, author_kind: "agent", agent_session_id: "revision-author-session", model_id: "acceptance-model", source_work_item_id: authorWork.id });
  const replacement = await decision("Replace local adapter discovery", "# Discovery\n\nReplace only the catalog discovery protocol.\n", { supersession_details: [{ decision_id: origin.decision_id, revision: current.revision, extent: "partial", reason: "Replace discovery; retain the catalog identity rules." }] });
  return { project, prerequisite, origin, dependent, related, current, replacement, remediation, authorWork, path, storedRepository };
}

test("decision impact traces exact revisions and appends auditable dispositions with safe retries", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, prerequisite, origin, dependent, related, current, replacement, remediation, path } = await fixture(api);
    await page.goto(`/knowledge?project=${project.id}&decision=${origin.decision_id}&revision=0`);
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Impact", exact: true }).click();
    await expect(drawer.getByRole("complementary", { name: "Impact coverage" })).toContainText("Provenance is incomplete");
    await expect(drawer.getByRole("complementary", { name: "Impact coverage" })).toContainText(`${origin.reference} has no linked commits`);
    await expect(drawer).toContainText(dependent.title);
    await expect(drawer).toContainText(related.title);
    await expect(drawer).toContainText("Incoming related link · causality unknown · disposition required");
    await expect(drawer).not.toContainText(prerequisite.title);
    await drawer.getByRole("button", { name: "Related context (1)", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "Related context (1)", exact: true })).toHaveClass(/selected/);
    await expect(drawer.getByRole("button", { name: "Code impact (3)", exact: true })).not.toHaveClass(/selected/);
    await expect(drawer).not.toContainText(related.title);
    await expect(drawer).toContainText(prerequisite.title);
    await expect(drawer).not.toContainText(dependent.title);
    await expect(drawer).toContainText("Prerequisite used by this decision · context only");
    await expect(drawer).toContainText("Individual verdicts are optional");
    await drawer.getByRole("button", { name: "Code impact (3)", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "Code impact (3)", exact: true })).toHaveClass(/selected/);
    await expect(drawer.getByRole("list", { name: `Evidence path to ${dependent.reference}` })).toContainText(origin.reference);
    await drawer.getByRole("list", { name: `Evidence path to ${dependent.reference}` }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("knowledge-impact-paths.png"), animations: "disabled" });
    await drawer.getByText("Incoming related link · causality unknown · disposition required", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("incoming-related-impact.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "Assessments", exact: true }).click();
    await drawer.getByRole("button", { name: `${origin.reference} → ${current.reference}`, exact: true }).click();
    await expect(drawer.getByLabel("Decision revision diff")).toContainText("-Use local transport adapters.");
    await expect(drawer.getByLabel("Decision revision diff")).toContainText("+Use remotely registered transport adapters.");
    await page.screenshot({ path: testInfo.outputPath("knowledge-impact-diff.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: `Record disposition for ${dependent.reference}`, exact: true }).click();
    const form = drawer.getByRole("form", { name: `Disposition for ${dependent.reference}` });
    await form.getByRole("combobox", { name: "Disposition", exact: true }).selectOption("change-required");
    await form.getByRole("textbox", { name: "Reason", exact: true }).fill("The gateway must switch to remote registration before the old adapter is removed.");
    await form.getByRole("textbox", { name: "Remediation work item ID (optional)", exact: true }).fill(remediation.id);
    await form.getByRole("textbox", { name: "Finding ID (optional)", exact: true }).fill("remote-registration");
    await form.getByRole("combobox", { name: "Remediation target (optional)", exact: true }).selectOption("both");
    await page.screenshot({ path: testInfo.outputPath("impact-finding-form.png"), animations: "disabled" });
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/knowledge/assessments/*/dispositions`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      bodies.push(route.request().postData()!);
      if (bodies.length === 1) {
        const response = await route.fetch(); expect(response.ok(), await response.text()).toBe(true);
        return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Response interrupted after saving." }) });
      }
      return route.continue();
    });
    await form.getByRole("button", { name: "Save disposition", exact: true }).click();
    await expect(form.getByRole("alert")).toContainText("outcome is unconfirmed");
    await expect(form.getByRole("textbox", { name: "Reason", exact: true })).toBeDisabled();
    await expect(form.getByRole("textbox", { name: "Finding ID (optional)", exact: true })).toBeDisabled();
    await expect(form.getByRole("combobox", { name: "Remediation target (optional)", exact: true })).toBeDisabled();
    await expect(drawer.getByRole("button", { name: "Close preview" })).toBeDisabled();
    await expect(drawer.getByRole("button", { name: "Document", exact: true })).toBeDisabled();
    await expect(drawer.getByRole("button", { name: "Related context (1)", exact: true })).toBeDisabled();
    await expect(page.getByRole("combobox", { name: "Select project" })).toBeDisabled();
    await page.keyboard.press("Escape"); await expect(drawer).toBeVisible();
    await form.getByRole("button", { name: "Retry pending disposition" }).click();
    await expect(form).toHaveCount(0); expect(bodies).toHaveLength(2); expect(bodies[1]).toBe(bodies[0]);
    expect(JSON.parse(bodies[0])).toMatchObject({ finding_id: "remote-registration", remediation_target: "both" });
    await expect(drawer.getByRole("link", { name: `Remediation work ${remediation.id}` })).toBeVisible();
    await expect(drawer.locator(".impact-assessment-status")).toContainText("Changes required");
    await expect(drawer.locator(".impact-assessment-status")).toContainText("1 named finding");
    await expect(drawer).toContainText("Remediation target: both");
    await page.screenshot({ path: testInfo.outputPath("impact-finding-summary.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: `Record disposition for ${dependent.reference}`, exact: true }).click();
    await form.getByRole("combobox", { name: "Disposition", exact: true }).selectOption("resolved");
    await form.getByRole("textbox", { name: "Reason", exact: true }).fill("The linked remediation updated the gateway and passed its transport tests.");
    await form.getByRole("textbox", { name: "Remediation work item ID (optional)", exact: true }).fill(remediation.id);
    await form.getByRole("button", { name: "Save disposition", exact: true }).click();
    await expect(form).toHaveCount(0);
    await expect(drawer.locator(".impact-assessment-status")).toContainText("Assessment pending");
    await drawer.getByText("Disposition history · 2 records", { exact: true }).click();
    await expect(drawer).toContainText("The gateway must switch to remote registration");
    await expect(drawer).toContainText("The linked remediation updated the gateway");
    await drawer.getByText("The gateway must switch to remote registration before the old adapter is removed.", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("knowledge-impact-assessment.png"), animations: "disabled" });
    const assessments = await json(await api.get(`${path}/assessments?decision_id=${origin.decision_id}`));
    const assessment = assessments.items.find((item: { before_reference: string }) => item.before_reference === origin.reference);
    const history = await json(await api.get(`${path}/assessments/${assessment.id}/dispositions`));
    expect(history.total).toBe(2);
    await drawer.getByRole("button", { name: "Close preview" }).click();
    await page.goto(`/knowledge?project=${project.id}&decision=${replacement.decision_id}&revision=0`);
    await drawer.getByRole("button", { name: "Details & links" }).click();
    await expect(drawer).toContainText("Partial supersession");
    await expect(drawer).toContainText("Replace discovery; retain the catalog identity rules.");
    await drawer.getByRole("button", { name: "Close preview" }).click();
    await page.getByText("Find decisions cited by a commit", { exact: true }).click();
    await page.getByRole("textbox", { name: "Repository identity", exact: true }).fill(repository);
    await page.getByRole("textbox", { name: "Commit hash", exact: true }).fill(mergedHash);
    await page.getByRole("button", { name: "Find citations", exact: true }).click();
    const lookup = page.locator(".knowledge-commit-lookup");
    await expect(lookup).toContainText(originalHash);
    await expect(lookup).toContainText(mergedHash);
    await lookup.getByRole("button", { name: dependent.reference, exact: true }).click();
    await drawer.getByRole("button", { name: "Details & links" }).click();
    await expect(drawer).toContainText("The gateway resolves transports through the catalog contract.");
    await drawer.getByRole("button", { name: "Assessments", exact: true }).click();
    await expect(drawer.getByRole("button", { name: `${origin.reference} → ${current.reference}`, exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("Done requires a decision declaration while checkpoints remain independent", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, dependent, path, storedRepository } = await fixture(api);
    const item = await work(api, project.id, "Ship the transport gateway");
    await json(await api.post(`${path}/decisions/${dependent.decision_id}/scope`, { data: {
      revision: 0, repository, commit_hash: "e".repeat(40), agent_session_id: "earlier-merge-session",
      client_operation_id: crypto.randomUUID(),
    } }));
    await page.goto(`/work-items?project=${project.id}`);
    const pane = await selectWork(page, item.title), context = await openTab(pane, "Context");
    await context.getByLabel("Checkpoint text").fill("Confirmed the catalog contract and prepared the gateway implementation.");
    await expect(context.getByRole("combobox", { name: "Decision use", exact: true })).toBeVisible();
    await context.getByRole("button", { name: "Add checkpoint", exact: true }).click();
    await expect(context.getByLabel("Checkpoint text")).toHaveValue("");
    await context.getByLabel("Checkpoint text").fill("Gateway implementation and transport tests are complete.");
    await context.getByRole("textbox", { name: /^Human summary/ }).fill("The gateway now uses the transport catalog. Its tests pass and the changes are ready to review.");
    await context.getByRole("button", { name: "Complete work", exact: true }).click();
    await expect(context.getByRole("alert")).toContainText("Declare whether this work cites decisions");
    await context.getByRole("combobox", { name: "Decision use", exact: true }).selectOption("cited");
    await context.getByRole("button", { name: "Add decision citation" }).click();
    await context.getByRole("textbox", { name: "Decision ID 1", exact: true }).fill(dependent.decision_id);
    await context.getByRole("spinbutton", { name: "Revision", exact: true }).fill("0");
    await context.getByRole("button", { name: "Add implementation commit" }).click();
    await context.getByRole("textbox", { name: "Repository 1", exact: true }).fill(repository);
    await context.getByRole("textbox", { name: "Commit hash 1" }).fill("e".repeat(40));
    if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1280, height: 1100 });
    await context.getByRole("heading", { name: "Decision provenance", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("knowledge-completion-provenance.png"), animations: "disabled" });
    await context.getByRole("button", { name: "Complete work", exact: true }).click();
    await expect.poll(async () => (await json(await api.get(`/api/v1/projects/${project.id}/work-items/${item.id}`))).work_item.status).toBe("done");
    const scopes = await json(await api.get(`${path}/commits?repository=${repository}&commit_hash=${"e".repeat(40)}`));
    expect(scopes.items).toHaveLength(1);
    expect(scopes.items[0]).toMatchObject({ decision_id: dependent.decision_id, revision: 0, repository: storedRepository, work_item_id: null, agent_session_id: "earlier-merge-session" });
    expect(scopes.items[0].completion_report_id).toBeUndefined();
    expect(scopes.items[0].completion_citations).toHaveLength(1);
    expect(scopes.items[0].completion_citations[0].work_item_id).toBe(item.id);
    await page.goto(`/summaries?project=${project.id}`);
    const report = page.locator("article.job-report-card").filter({ hasText: item.title });
    await expect(report.getByRole("link", { name: dependent.reference, exact: true })).toBeVisible();
    await expect(report).toContainText("Author-declared commits");
    await expect(report).toContainText("e".repeat(40));
    await page.screenshot({ path: testInfo.outputPath("knowledge-completion-report.png"), animations: "disabled" });
    await page.goto(`/knowledge?project=${project.id}&decision=${dependent.decision_id}&revision=0`);
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Code scope", exact: true }).click();
    const evidence = drawer.locator(".decision-scope-evidence").filter({ hasText: "e".repeat(40) });
    await expect(evidence).toContainText("Session: earlier-merge-session");
    await expect(evidence).toContainText("Closeout citations");
    await expect(evidence.getByRole("link", { name: `Completed work ${item.id}`, exact: true })).toBeVisible();
    await evidence.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("knowledge-closeout-citation.png"), animations: "disabled" });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("assessment drafts require review after refetch and conflict instead of silently advancing their version", async ({ page }) => {
  const api = await apiClient();
  try {
    const { project, origin, current, dependent, related, path } = await fixture(api);
    const episodes = await json(await api.get(`${path}/assessments?decision_id=${origin.decision_id}`));
    const episode = episodes.items.find((item: { before_reference: string }) => item.before_reference === origin.reference);
    const endpoint = `${path}/assessments/${episode.id}/dispositions`;
    await page.goto(`/knowledge?project=${project.id}&decision=${origin.decision_id}&revision=0`);
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Assessments", exact: true }).click();
    await drawer.getByRole("button", { name: `${origin.reference} → ${current.reference}`, exact: true }).click();
    await drawer.getByRole("button", { name: `Record disposition for ${dependent.reference}`, exact: true }).click();
    const form = drawer.getByRole("form", { name: `Disposition for ${dependent.reference}` });
    await form.getByRole("combobox", { name: "Disposition", exact: true }).selectOption("still-valid");
    const reason = "The gateway contract remains valid after reviewing the latest assessment.";
    await form.getByRole("textbox", { name: "Reason", exact: true }).fill(reason);
    async function anotherAssessor(reference: string, version: number) {
      await json(await api.post(endpoint, { data: { candidate_reference: reference, expected_version: version,
        disposition: "not-affected", reason: "Independent review recorded before this draft was submitted.",
        actor: { actor_client: "playwright-api", actor_session_id: "other-assessor" }, client_operation_id: crypto.randomUUID() } }));
    }
    await anotherAssessor(origin.reference, 0);
    await expect(page.locator(".sync-status")).toHaveText("Live Updates");
    await work(api, project.id, "Refresh the assessment after an independent review");
    await expect(form.locator(".impact-review-notice")).toContainText("draft still refers to version 0");
    await expect(form.getByRole("button", { name: "Save disposition", exact: true })).toBeDisabled();
    await expect(form.getByRole("textbox", { name: "Reason", exact: true })).toHaveValue(reason);
    await form.getByRole("button", { name: "Use reviewed assessment version 1", exact: true }).click();
    // The next concurrent write races dispatch, so the server's version conflict
    // must release navigation while preserving the same authored draft.
    await anotherAssessor(related.reference, 1);
    const requests: Array<{ expected_version: number; client_operation_id: string }> = [];
    page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith(`/assessments/${episode.id}/dispositions`)) requests.push(request.postDataJSON()); });
    await form.getByRole("button", { name: "Save disposition", exact: true }).click();
    await expect(form.getByRole("button", { name: "Use reviewed assessment version 2", exact: true })).toBeEnabled();
    await expect(form.getByRole("textbox", { name: "Reason", exact: true })).toHaveValue(reason);
    await expect(form.getByRole("textbox", { name: "Reason", exact: true })).toBeEnabled();
    await expect(drawer.getByRole("button", { name: "Close preview" })).toBeEnabled();
    await expect(page.getByRole("combobox", { name: "Select project" })).toBeEnabled();
    await expect(form.getByRole("button", { name: "Save disposition", exact: true })).toBeDisabled();
    await form.getByRole("button", { name: "Use reviewed assessment version 2", exact: true }).click();
    await form.getByRole("button", { name: "Save disposition", exact: true }).click();
    await expect(form).toHaveCount(0);
    expect(requests.map((item) => item.expected_version)).toEqual([1, 2]);
    expect(requests[0].client_operation_id).not.toBe(requests[1].client_operation_id);
    await expect(drawer.locator(".impact-assessment-status")).toContainText("All candidates assessed");
  } finally { await api.dispose(); }
});

test("decision impact separates optional context and identifies the revision author and current work", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, origin, current, dependent, prerequisite, related, authorWork, path } = await fixture(api);
    await page.goto(`/knowledge?project=${project.id}&decision=${origin.decision_id}&revision=0`);
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Assessments", exact: true }).click();
    await drawer.getByRole("button", { name: `${origin.reference} → ${current.reference}`, exact: true }).click();
    const author = drawer.locator("details.revision-author").filter({ hasText: "Changed revision author" });
    await author.locator("summary").click();
    await expect(author).toContainText("acceptance-model");
    await expect(author).toContainText("revision-author-session");
    const heldWork = author.getByRole("link", { name: authorWork.title, exact: true });
    await expect(heldWork).toHaveAttribute("href", `/work-items?project=${project.id}&work=${authorWork.id}`);
    await expect(author).toContainText("Implementation · codex");
    await expect(author).toContainText("Commit provenance establishes which code used the decision");
    await expect(drawer.locator(".impact-assessment-status")).toContainText("3 unassessed");
    await expect(drawer.getByRole("complementary", { name: "Impact coverage" })).toContainText(`${origin.reference} has no linked commits`);
    await drawer.locator(".impact-diff > summary").click();
    await author.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`decision-impact-feedback-author-${testInfo.project.name}.png`), animations: "disabled" });
    await drawer.getByRole("button", { name: "Related context (1)", exact: true }).click();
    await expect(drawer).toContainText(prerequisite.title);
    await expect(drawer).not.toContainText(related.title);
    await expect(drawer).not.toContainText(dependent.title);
    await expect(drawer.getByText("Optional contradiction note; no disposition required.", { exact: true })).toHaveCount(1);
    await drawer.getByRole("button", { name: "Related context (1)", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`decision-impact-feedback-context-${testInfo.project.name}.png`), animations: "disabled" });
    await drawer.getByRole("button", { name: "Code impact (3)", exact: true }).click();
    for (const candidate of [origin, dependent, related]) {
      await drawer.getByRole("button", { name: `Record disposition for ${candidate.reference}`, exact: true }).click();
      const form = drawer.getByRole("form", { name: `Disposition for ${candidate.reference}` });
      await form.getByRole("combobox", { name: "Disposition", exact: true }).selectOption("still-valid");
      await form.getByRole("textbox", { name: "Reason", exact: true }).fill("Reviewed the scope change against the recorded implementation; the implementation remains valid.");
      await form.getByRole("button", { name: "Save disposition", exact: true }).click();
      await expect(form).toHaveCount(0);
    }
    await expect(drawer.locator(".impact-assessment-status")).toContainText("All candidates assessed");
    await expect(drawer.locator(".impact-assessment-status")).toContainText("0 unassessed");
    await drawer.getByRole("button", { name: "Related context (1)", exact: true }).click();
    await expect(drawer.getByText("Optional contradiction note; no disposition required.", { exact: true })).toHaveCount(1);
    const episodes = await json(await api.get(`${path}/assessments?decision_id=${origin.decision_id}`));
    const episode = episodes.items.find((item: { before_reference: string }) => item.before_reference === origin.reference);
    const history = await json(await api.get(`${path}/assessments/${episode.id}/dispositions`));
    expect(history.items.map((item: { candidate_reference: string }) => item.candidate_reference).sort()).toEqual([origin.reference, dependent.reference, related.reference].sort());
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});
