import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import type { JobReportEnvelope, JobReportPage, ProjectSettings } from "../../lib/types";
import { reportForFixture } from "./job-report-fixture";
import { openTab, selectWork, workCard } from "./surface";

async function client() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: {
    Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json"
  }});
}
async function createProject(api: APIRequestContext) {
  const response = await api.post("/api/v1/projects", { data: { name: `Phase 12 ${crypto.randomUUID()}` } });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as { id: string; name: string };
}
async function createReport(api: APIRequestContext, projectId: string, status: "done" | "wont-do" | "promoted" = "done") {
  const create = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title: `Dashboard font ${status}`, summary: "Use one readable font across the dashboard.", status: "pending", priority: 1,
    initial_checkpoint: { prompt: "Choose a readable font for the dashboard.", source_client: "playwright-api", source_session_id: "phase12" }
  }});
  expect(create.ok(), await create.text()).toBe(true);
  const { work_item } = await create.json() as { work_item: { id: string; version: number } };
  const report = { ...await reportForFixture(api, projectId), summary: status === "done"
    ? "The dashboard now uses a consistent font, so its pages are easier to scan. The change is ready to review and has not been deployed."
    : status === "wont-do" ? "The font change was deliberately stopped because the current font already meets the project’s needs."
      : "The font decision has moved to the design team for its next review. The font change itself is still unfinished.",
    fyi_items: status === "done" ? ["I chose Arial because it is widely available; create a follow-up if you prefer another font."] : [] };
  const common = { expected_version: work_item.version, job_completion_report: report, client_operation_id: crypto.randomUUID() };
  const closeout = status === "done" ? await api.post(`/api/v1/projects/${projectId}/work-items/${work_item.id}/complete`, { data: {
    ...common, checkpoint: { prompt: "Updated dashboard typography and checked the browser layouts.", source_client: "playwright-api", source_session_id: "phase12" }
  }}) : await api.patch(`/api/v1/projects/${projectId}/work-items/${work_item.id}`, { data: {
    ...common, status, actor: { actor_client: "playwright-api", actor_session_id: "phase12" }
  }});
  expect(closeout.ok(), await closeout.text()).toBe(true);
  const value = await closeout.json() as { job_completion_report: { id: string; completion_checkpoint_id: string | null } };
  expect(value.job_completion_report.completion_checkpoint_id === null).toBe(status !== "done");
  return { reportId: value.job_completion_report.id, workId: work_item.id };
}
async function openSummaries(page: Page, projectId: string) {
  await page.goto("/summaries");
  await page.locator("#project-select").selectOption(projectId);
  await expect(page.getByRole("heading", { name: "Summaries.", exact: true })).toBeVisible();
}
async function reportInbox(api: APIRequestContext, projectId: string) {
  const response = await api.get(`/api/v1/projects/${projectId}/job-completion-reports`);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as JobReportPage;
}

test("all closeout outcomes appear in Summaries and a human follow-up retains both exact sources", async ({ page }, testInfo) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const done = await createReport(api, project.id);
    await createReport(api, project.id, "wont-do");
    await createReport(api, project.id, "promoted");
    await openSummaries(page, project.id);
    const nav = page.getByRole("navigation", { name: "Workspace navigation" });
    const labels = await nav.getByRole("link").allTextContents();
    expect(labels.findIndex((value) => value.includes("Needs Attention"))).toBe(labels.findIndex((value) => value.includes("Summaries")) + 1);
    await expect(page.locator("article.job-report-card")).toHaveCount(3);
    await expect(page.locator(".summary-nav-count")).toHaveText("3");
    await expect(page.locator(".attention-nav-count")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("summaries.png"), fullPage: true });
    const card = page.getByRole("article", { name: "Report for Dashboard font done", exact: true });
    await expect(card.locator(".human-report-fyis li")).toHaveCount(1);
    await expect(card).toContainText("Arial");
    await expect(page.getByRole("article", { name: "Report for Dashboard font promoted", exact: true })).toContainText("still unfinished");
    await card.getByRole("button", { name: "Create Follow-up", exact: true }).click();
    const form = page.getByRole("form", { name: "Create Follow-up" });
    await form.getByLabel("Title", { exact: true }).fill("Use Comic Sans on the dashboard");
    await form.getByLabel("Work summary", { exact: true }).fill("Replace Arial with Comic Sans throughout the dashboard.");
    await form.getByLabel("Initial context and requested change", { exact: true }).fill("Change the dashboard font from Arial to Comic Sans across the main pages. Preserve readable text sizes and check narrow screens.");
    await page.screenshot({ path: testInfo.outputPath("follow-up-form.png"), fullPage: true });
    await form.getByRole("button", { name: "Create pending work" }).click();
    await expect(page.getByText("Follow-up created in Pending.", { exact: true })).toBeVisible();
    const inbox = await reportInbox(api, project.id);
    const item = inbox.items.find((entry) => entry.report.id === done.reportId)!;
    expect(item.follow_up_count).toBe("1");
    expect(item.human_dismissed).toBe(false);
    const provenance = await api.get(`/api/v1/projects/${project.id}/job-completion-reports/${done.reportId}/follow-ups`);
    const links = await provenance.json() as { items: Array<{ report_id: string; source_work_item_id: string; follow_up_work_item_id: string }> };
    expect(links.items).toHaveLength(1);
    expect(links.items[0]).toMatchObject({ report_id: done.reportId, source_work_item_id: done.workId });
    const context = await api.get(`/api/v1/projects/${project.id}/work-items/${links.items[0].follow_up_work_item_id}/context`);
    expect(await context.json()).toMatchObject({ work_item: { status: "pending", title: "Use Comic Sans on the dashboard" }, readiness: { has_active_lease: false } });
    await card.getByRole("button", { name: "Dismiss", exact: true }).click();
    await expect(card).toHaveCount(0);
    await page.reload();
    const wontDoCard = page.getByRole("article", {
      name: "Report for Dashboard font wont-do", exact: true
    });
    const promotedCard = page.getByRole("article", {
      name: "Report for Dashboard font promoted", exact: true
    });
    await expect(page.locator(".summary-nav-count")).toHaveText("2");
    await wontDoCard.getByRole("button", { name: "Dismiss", exact: true }).click();
    await expect(wontDoCard).toHaveCount(0);
    await promotedCard.getByRole("button", { name: "Dismiss", exact: true }).click();
    await expect(promotedCard).toHaveCount(0);
    await expect(page.locator(".summary-nav-count")).toHaveCount(0);
    const detail = await api.get(`/api/v1/projects/${project.id}/job-completion-reports/${done.reportId}`);
    expect(await detail.json()).toMatchObject({ human_dismissed: true, report: { id: done.reportId } });
  } finally { await api.dispose(); }
});

test("report prompt and recall content save and reset independently with visible revision conflicts", async ({ page }, testInfo) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const initial = await (await api.get(`/api/v1/projects/${project.id}/settings`)).json() as ProjectSettings;
    expect(initial.job_completion_report_prompt).toContain("only LLM output");
    await page.goto("/settings");
    await page.locator("#project-select").selectOption(project.id);
    const recall = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "Recall pointer content", exact: true }) });
    const reports = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "Job completion report prompt", exact: true }) });
    await expect(reports.getByRole("textbox")).toHaveValue(initial.job_completion_report_prompt);
    await page.screenshot({ path: testInfo.outputPath("report-prompt-settings.png"), fullPage: true });
    await recall.getByRole("textbox").fill("Recall $WORK_ITEM_ID for this project.");
    await recall.getByRole("button", { name: "Save", exact: true }).click();
    await expect(recall.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await reports.getByRole("textbox").fill("Write a concise human summary. Assume no other LLM output was read. Mention optional decisions in FYIs.");
    await reports.getByRole("button", { name: "Save", exact: true }).click();
    await expect(reports.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await recall.getByRole("button", { name: "Clear", exact: true }).click();
    await expect(recall.getByRole("button", { name: "Clear", exact: true })).toBeDisabled();
    let settings = await (await api.get(`/api/v1/projects/${project.id}/settings`)).json() as ProjectSettings;
    expect(settings.recall_pointer_template).toBeNull();
    expect(settings.job_completion_report_prompt).toContain("Mention optional decisions");
    await reports.getByRole("button", { name: "Reset to default" }).click();
    await expect(reports.getByRole("textbox")).toHaveValue(initial.job_completion_report_prompt);
    await reports.getByRole("textbox").fill("A human draft that must survive another settings change.");
    settings = await (await api.get(`/api/v1/projects/${project.id}/settings`)).json() as ProjectSettings;
    const competing = await api.patch(`/api/v1/projects/${project.id}/settings`, { data: { expected_revision: settings.revision, recall_pointer_template: "Another editor’s recall content." } });
    expect(competing.ok()).toBe(true);
    await page.locator(".page-heading").getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByText("Review the latest saved settings before applying your draft.", { exact: false })).toBeVisible();
    await expect(reports.getByRole("textbox")).toHaveValue("A human draft that must survive another settings change.");
    await expect(reports.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  } finally { await api.dispose(); }
});

test("unknown dismissal survives a project switch and retries identical bytes once", async ({ page }) => {
  const api = await client();
  try {
    const first = await createProject(api); const second = await createProject(api);
    const report = await createReport(api, first.id);
    const requests: string[] = [];
    await page.route(`**/api/mnemonic/projects/${first.id}/job-completion-reports/${report.reportId}/dismiss`, async (route) => {
      requests.push(route.request().postData()!);
      const response = await route.fetch();
      if (requests.length === 1) await route.fulfill({ status: 502, contentType: "application/json", body: '{"detail":"Lost response."}' });
      else await route.fulfill({ response });
    });
    await openSummaries(page, first.id);
    await page.getByRole("article", { name: "Report for Dashboard font done", exact: true }).getByRole("button", { name: "Dismiss", exact: true }).click();
    await expect(page.getByText("Dismiss summary · outcome unknown", { exact: true })).toBeVisible();
    await page.locator("#project-select").selectOption(second.id);
    await page.getByRole("button", { name: "Retry exact request", exact: true }).click();
    await expect(page.getByText("Dismiss summary · outcome unknown", { exact: true })).toHaveCount(0);
    expect(requests).toHaveLength(2); expect(requests[1]).toBe(requests[0]);
    await page.locator("#project-select").selectOption(first.id);
    await expect(page.locator("article.job-report-card")).toHaveCount(0);
    const detail = await (await api.get(`/api/v1/projects/${first.id}/job-completion-reports/${report.reportId}`)).json() as JobReportEnvelope;
    expect(detail.human_dismissed).toBe(true);
  } finally { await api.dispose(); }
});

test("durable activity catches up with all socket hints dropped and report prose stays inert", async ({ page }) => {
  test.setTimeout(60_000);
  const api = await client();
  try {
    const project = await createProject(api);
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await openSummaries(page, project.id);
    await expect(page.getByText("You’re caught up.", { exact: true })).toBeVisible();
    const created = await createReport(api, project.id);
    const card = page.getByRole("article", { name: "Report for Dashboard font done", exact: true });
    await expect(card).toBeVisible({ timeout: 25_000 });
    const reopened = await api.patch(`/api/v1/projects/${project.id}/work-items/${created.workId}`, {
      data: {expected_version:2,status:"pending",actor:{actor_client:"playwright-api",actor_session_id:"phase12"}}
    });
    expect(reopened.ok(), await reopened.text()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(card.locator(".report-source-state")).toContainText("The work is now Pending.", {timeout:25_000});
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator("article.job-report-card")).toHaveCount(1);
    await expect(page.locator(".human-report-summary script, .human-report-fyis script")).toHaveCount(0);
  } finally { await api.dispose(); }
});


test("recovering another report action preserves an unrelated follow-up draft", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    await createReport(api, project.id, "done");
    const other = await createReport(api, project.id, "promoted");
    let attempts = 0;
    await page.route(`**/api/mnemonic/projects/${project.id}/job-completion-reports/${other.reportId}/dismiss`, async (route) => {
      const response = await route.fetch();
      if (++attempts === 1) await route.fulfill({status:502,contentType:"application/json",body:'{"detail":"Lost response."}'});
      else await route.fulfill({response});
    });
    await openSummaries(page, project.id);
    await page.getByRole("article", {name:"Report for Dashboard font promoted",exact:true}).getByRole("button", {name:"Dismiss",exact:true}).click();
    await expect(page.getByText("Dismiss summary · outcome unknown", {exact:true})).toBeVisible();
    await page.getByRole("article", {name:"Report for Dashboard font done",exact:true}).getByRole("button", {name:"Create Follow-up",exact:true}).click();
    const form=page.getByRole("form", {name:"Create Follow-up"});
    await form.getByLabel("Title", {exact:true}).fill("Keep this separate font decision");
    await form.getByLabel("Work summary", {exact:true}).fill("This draft belongs to the other report.");
    await page.getByRole("button", {name:"Retry exact request",exact:true}).click();
    await expect.poll(() => attempts).toBe(2);
    await expect(page.locator(".mutation-recovery-global")).toHaveCount(0);
    await expect(form.getByLabel("Title", {exact:true})).toHaveValue("Keep this separate font decision");
    await expect(form.getByLabel("Work summary", {exact:true})).toHaveValue("This draft belongs to the other report.");
    expect(attempts).toBe(2);
  } finally { await api.dispose(); }
});


async function createPendingWork(api: APIRequestContext, projectId: string, title: string) {
  const response = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title, summary: "A focused browser regression fixture.", status: "pending", priority: 1,
    initial_checkpoint: { prompt: "Review the font decision and report the result.", source_client: "playwright-api", source_session_id: "phase12" }
  }});
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { work_item: { id: string; title: string; version: number } }).work_item;
}
async function openProjectWork(page: Page, projectId: string, title: string) {
  await page.goto("/");
  await page.locator("#project-select").selectOption(projectId);
  await page.getByRole("group", { name: "Filter work items" }).getByRole("button", { name: "Pending", exact: true }).click();
  return selectWork(page, title);
}

test("report-only drafts survive refused close, work selection, project change and activity refresh", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const otherProject = await createProject(api);
    const source = await createPendingWork(api, project.id, "Preserve the human closeout draft");
    const other = await createPendingWork(api, project.id, "Another work item");
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    const pane = await openProjectWork(page, project.id, source.title);
    await expect(pane.getByText("Project report instructions · revision 1", { exact: true })).toBeVisible();
    const dialogs: string[] = [];
    const refuse = async (dialog: import("@playwright/test").Dialog) => {
      dialogs.push(dialog.message());
      await dialog.dismiss();
    };
    page.on("dialog", refuse);
    // Merely loading the required prompt revision must not create a dirty draft.
    await pane.locator(".prompt-body").focus();
    await page.keyboard.press("Escape");
    await expect(pane.locator(".detail-title")).toHaveCount(0);
    expect(dialogs).toHaveLength(0);
    await selectWork(page, source.title);
    await pane.getByLabel(/^Human summary/).fill("The font decision is ready for a human review.");
    await pane.locator(".prompt-body").focus();
    await page.keyboard.press("Escape");
    await expect.poll(() => dialogs.length).toBe(1);
    await expect(pane.getByLabel(/^Human summary/)).toHaveValue("The font decision is ready for a human review.");
    // Dispatching the card event also exercises the selection guard under the narrow pane overlay.
    await workCard(page, other.title).dispatchEvent("click");
    await expect.poll(() => dialogs.length).toBe(2);
    await expect(pane.locator(".detail-title")).toHaveText(source.title);
    await page.locator("#project-select").selectOption(otherProject.id);
    await expect.poll(() => dialogs.length).toBe(3);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    await expect(pane.getByLabel(/^Human summary/)).toHaveValue("The font decision is ready for a human review.");
    await pane.getByLabel(/^Human summary/).fill("");
    await pane.getByRole("button", { name: "Add FYI", exact: true }).click();
    await pane.getByLabel(/^FYI 1/).fill("I chose Arial; the font can be changed in a follow-up.");
    await pane.locator(".prompt-body").focus();
    await page.keyboard.press("Escape");
    await expect.poll(() => dialogs.length).toBe(4);
    await workCard(page, other.title).dispatchEvent("click");
    await expect.poll(() => dialogs.length).toBe(5);
    await page.locator("#project-select").selectOption(otherProject.id);
    await expect.poll(() => dialogs.length).toBe(6);
    expect(dialogs.every((message) => message === "Discard your unsaved job completion report?")).toBe(true);
    const changed = await api.patch(`/api/v1/projects/${project.id}/work-items/${source.id}`, { data: {
      expected_version: source.version, priority: 2, actor: { actor_client: "playwright-api", actor_session_id: "phase12" }
    }});
    expect(changed.ok(), await changed.text()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pane.getByLabel(/^FYI 1/)).toHaveValue("I chose Arial; the font can be changed in a follow-up.");
    page.off("dialog", refuse);
    page.once("dialog", (dialog) => void dialog.accept());
    await pane.locator(".prompt-body").focus();
    await page.keyboard.press("Escape");
    await expect(pane.locator(".detail-title")).toHaveCount(0);
    await selectWork(page, source.title);
    await expect(pane.getByLabel(/^Human summary/)).toHaveValue("");
    await expect(pane.getByLabel(/^FYI 1/)).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("a failed attention list retries after its activity cursor advances and counts succeed", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    let listReads = 0;
    let countReads = 0;
    let failList = false;
    let failedListReads = 0;
    let emptyActivityAfterFailure = 0;
    await page.route(`**/api/mnemonic/projects/${project.id}/human-attention?*`, async (route) => {
      if (new URL(route.request().url()).searchParams.get("limit") === "0") {
        countReads += 1;
        await route.continue();
      } else {
        listReads += 1;
        if (failList) {
          failedListReads += 1;
          await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Attention list temporarily unavailable."}' });
        } else await route.continue();
      }
    });
    await page.route(`**/api/mnemonic/projects/${project.id}/activity?*`, async (route) => {
      const response = await route.fetch();
      const value = await response.json() as { items: unknown[] };
      if (failedListReads && value.items.length === 0) emptyActivityAfterFailure += 1;
      await route.fulfill({ response });
    });
    await page.goto("/attention");
    await page.locator("#project-select").selectOption(project.id);
    const list = page.locator(".attention-list");
    await expect(list.getByText("No explicit human questions are waiting.", { exact: true })).toBeVisible();
    const countsBefore = countReads;
    failList = true;
    await createPendingWork(api, project.id, "Activity wakes the attention view");
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(list.getByRole("alert")).toContainText("Attention list temporarily unavailable.");
    await expect.poll(() => countReads).toBeGreaterThan(countsBefore);
    await expect(page.locator(".attention-nav-count")).toHaveCount(0);
    const readsBeforeRecovery = listReads;
    // Only the dependent read remains dirty. Further activity pages contain no new events.
    failList = false;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => emptyActivityAfterFailure).toBeGreaterThan(0);
    await expect.poll(() => listReads).toBeGreaterThan(readsBeforeRecovery);
    await expect(list.getByRole("alert")).toHaveCount(0);
    await expect(list.getByText("No explicit human questions are waiting.", { exact: true })).toBeVisible();
  } finally { await api.dispose(); }
});

test("history, evidence, event and gate views recover failed reads without new activity", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const source = await createPendingWork(api, project.id, "Retry each dependent detail read");
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    const failures = new Map<string, number>();
    for (const endpoint of ["checkpoints", "report-follow-ups", "completion-evidence", "events", "gates"]) {
      await page.route(`**/api/mnemonic/projects/${project.id}/work-items/${source.id}/${endpoint}?*`, async (route) => {
        // The two provenance directions are distinct failed views.
        const key = endpoint + (new URL(route.request().url()).searchParams.get("direction") ?? "");
        const attempts = (failures.get(key) ?? 0) + 1;
        failures.set(key, attempts);
        if (attempts === 1) await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: `${key} temporarily unavailable.` }) });
        else await route.continue();
      });
    }
    const pane = await openProjectWork(page, project.id, source.title);
    const history = await openTab(pane, "History");
    await expect(history.getByText("No report follow-up links.", { exact: true })).toHaveCount(2);
    await expect(history.getByRole("alert")).toHaveCount(0);
    await expect.poll(() => failures.get("checkpoints") ?? 0).toBeGreaterThan(1);
    await expect.poll(() => failures.get("report-follow-upsorigin") ?? 0).toBeGreaterThan(1);
    await expect.poll(() => failures.get("report-follow-upscreated") ?? 0).toBeGreaterThan(1);
    const evidence = await openTab(pane, "Evidence");
    await expect(evidence.getByText("No completion episodes recorded.", { exact: true })).toBeVisible();
    await expect.poll(() => failures.get("completion-evidence") ?? 0).toBeGreaterThan(1);
    const activity = await openTab(pane, "Activity");
    await expect(activity.locator("article.work-event")).toHaveCount(1);
    await expect(activity.getByRole("alert")).toHaveCount(0);
    await expect.poll(() => failures.get("events") ?? 0).toBeGreaterThan(1);
    const questions = await openTab(pane, "Questions");
    await questions.getByRole("button", { name: "Browse full paired gate history", exact: true }).click();
    await expect(questions.getByText("No human-gate history is retained for this work item.", { exact: true })).toBeVisible();
    await expect.poll(() => failures.get("gates") ?? 0).toBeGreaterThan(1);
  } finally { await api.dispose(); }
});

test("open follow-up and originating-report context stay fresh while their drafts survive", async ({ page }) => {
  test.setTimeout(60_000);
  const api = await client();
  try {
    const project = await createProject(api);
    const source = await createReport(api, project.id);
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await openSummaries(page, project.id);
    const card = page.getByRole("article", { name: "Report for Dashboard font done", exact: true });
    await card.getByRole("button", { name: "Create Follow-up", exact: true }).click();
    const draft = page.getByRole("complementary", { name: "Follow-up draft and original report" });
    const form = draft.getByRole("form", { name: "Create Follow-up" });
    await form.getByLabel("Title", { exact: true }).fill("Preserve my exact font request");
    await form.getByLabel("Work summary", { exact: true }).fill("Replace Arial with Comic Sans.");
    await form.getByLabel("Initial context and requested change", { exact: true }).fill("Change the font consistently and review the narrow layout.");
    const reopened = await api.patch(`/api/v1/projects/${project.id}/work-items/${source.workId}`, { data: {
      expected_version: 2, status: "pending", actor: { actor_client: "playwright-api", actor_session_id: "phase12" }
    }});
    expect(reopened.ok(), await reopened.text()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(draft.locator(".report-source-state")).toContainText("The work is now Pending.");
    await expect(form.getByLabel("Work summary", { exact: true })).toHaveValue("Replace Arial with Comic Sans.");
    await expect(form.getByLabel("Initial context and requested change", { exact: true })).toHaveValue("Change the font consistently and review the narrow layout.");
    await form.getByRole("button", { name: "Create pending work", exact: true }).click();
    await expect(page.getByText("Follow-up created in Pending.", { exact: true })).toBeVisible();
    const links = await (await api.get(`/api/v1/projects/${project.id}/job-completion-reports/${source.reportId}/follow-ups`)).json() as { items: Array<{ follow_up_work_item_id: string }> };
    expect(links.items).toHaveLength(1);
    const pane = await openProjectWork(page, project.id, "Preserve my exact font request");
    const history = await openTab(pane, "History");
    let detailReads = 0;
    await page.route(`**/api/mnemonic/projects/${project.id}/job-completion-reports/${source.reportId}`, async (route) => {
      if (++detailReads === 1) await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Stored report temporarily unavailable."}' });
      else await route.continue();
    });
    await history.getByRole("button", { name: "Read originating report", exact: true }).click();
    const stored = history.locator("aside.job-report-card");
    await expect(stored.locator(".report-source-state")).toContainText("The work is now Pending.");
    expect(detailReads).toBeGreaterThan(1);
    const deleted = await api.post(`/api/v1/projects/${project.id}/work-items/${source.workId}/delete`, { data: {
      expected_version: 3, actor: { actor_client: "playwright-api", actor_session_id: "phase12" }, client_operation_id: crypto.randomUUID()
    }});
    expect(deleted.ok(), await deleted.text()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(stored.locator(".report-source-state")).toContainText("The original work is now deleted.");
    await expect(stored.locator(".human-report-summary")).toContainText("The dashboard now uses a consistent font");
  } finally { await api.dispose(); }
});


test("a source merge refreshes both an open follow-up draft and an already-open originating report", async ({ page, context }) => {
  const api = await client();
  const summaries = await context.newPage();
  try {
    const project = await createProject(api);
    const source = await createReport(api, project.id);
    const destination = await createPendingWork(api, project.id, "Canonical font decision");
    const followUp = await api.post(`/api/v1/projects/${project.id}/job-completion-reports/${source.reportId}/follow-ups`, { data: {
      title: "Read the exact originating report", summary: "Review the original font decision.", priority: 1,
      actor: { actor_client: "playwright-api", actor_session_id: "phase12" },
      initial_checkpoint: { prompt: "Review the original font decision and its source work.", source_client: "playwright-api", source_session_id: "phase12" },
      client_operation_id: crypto.randomUUID()
    }});
    expect(followUp.ok(), await followUp.text()).toBe(true);
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await summaries.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.bringToFront();
    const pane = await openProjectWork(page, project.id, "Read the exact originating report");
    const history = await openTab(pane, "History");
    await history.getByRole("button", { name: "Read originating report", exact: true }).click();
    const stored = history.locator("aside.job-report-card");
    await expect(stored.locator(".human-report-summary")).toBeVisible();
    await summaries.bringToFront();
    await openSummaries(summaries, project.id);
    const card = summaries.getByRole("article", { name: "Report for Dashboard font done", exact: true });
    await card.getByRole("button", { name: "Create Follow-up", exact: true }).click();
    const draft = summaries.getByRole("complementary", { name: "Follow-up draft and original report" });
    await draft.getByLabel("Work summary", { exact: true }).fill("Keep this request through the source merge.");
    const [sourceContext, destinationContext] = await Promise.all([
      api.get(`/api/v1/projects/${project.id}/work-items/${source.workId}/context`).then((response) => response.json()),
      api.get(`/api/v1/projects/${project.id}/work-items/${destination.id}/context`).then((response) => response.json())
    ]);
    const merged = await api.post(`/api/v1/projects/${project.id}/work-items/${source.workId}/merge`, { data: {
      destination_work_item_id: destination.id,
      reviewed_source_revision: sourceContext.merge_review_revision,
      reviewed_destination_revision: destinationContext.merge_review_revision,
      rationale: "Keep one canonical font decision while preserving both report sources.",
      merged_by_client: "playwright-api", merged_by_session_id: "phase12", merged_by_model: null,
      client_operation_id: crypto.randomUUID()
    }});
    expect(merged.ok(), await merged.text()).toBe(true);
    await summaries.bringToFront();
    await summaries.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(card.locator(".report-source-state")).toContainText("The original work has since been merged");
    await expect(draft.locator(".report-source-state")).toContainText("The original work has since been merged");
    await expect(draft.getByLabel("Work summary", { exact: true })).toHaveValue("Keep this request through the source merge.");
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(stored.locator(".report-source-state")).toContainText("The original work has since been merged");
    await expect(stored.locator(".human-report-summary")).toContainText("The dashboard now uses a consistent font");
  } finally { await summaries.close(); await api.dispose(); }
});


test("closeout submissions wait for their project prompt revision without losing authored prose", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const complete = await createPendingWork(api, project.id, "Wait for report instructions before completion");
    const retire = await createPendingWork(api, project.id, "Wait for report instructions before retirement");
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    let releaseSettings!: () => void;
    let settingsReady = new Promise<void>((resolve) => { releaseSettings = resolve; });
    let closeoutWrites = 0;
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.method() === "POST" && url.pathname.endsWith(`/${complete.id}/complete`)
        || request.method() === "PATCH" && url.pathname.endsWith(`/${retire.id}`)) closeoutWrites += 1;
    });
    await page.route(`**/api/mnemonic/projects/${project.id}/settings`, async (route) => {
      await settingsReady;
      await route.continue();
    });
    const pane = await openProjectWork(page, project.id, complete.title);
    await pane.getByLabel(/^Checkpoint text/).fill("The font change is complete and the browser layouts were checked.");
    await pane.getByLabel(/^Human summary/).fill("The dashboard now uses a consistent, readable font.");
    await expect(pane.getByText("Loading project report instructions…", { exact: true })).toBeVisible();
    await expect(pane.getByRole("button", { name: "Complete work", exact: true })).toBeDisabled();
    expect(closeoutWrites).toBe(0);
    releaseSettings();
    await expect(pane.getByRole("button", { name: "Complete work", exact: true })).toBeEnabled();
    await expect(pane.getByLabel(/^Human summary/)).toHaveValue("The dashboard now uses a consistent, readable font.");
    await pane.getByRole("button", { name: "Complete work", exact: true }).click();
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Done");
    expect(closeoutWrites).toBe(1);
    settingsReady = new Promise<void>((resolve) => { releaseSettings = resolve; });
    await selectWork(page, retire.title);
    await pane.getByRole("button", { name: "Edit work item", exact: true }).click();
    await pane.getByLabel("Lifecycle", { exact: false }).selectOption("wont-do");
    await pane.getByLabel(/^Human summary/).fill("The separate font change was stopped because the current choice is suitable.");
    await expect(pane.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
    expect(closeoutWrites).toBe(1);
    releaseSettings();
    await expect(pane.getByRole("button", { name: "Save changes", exact: true })).toBeEnabled();
    await expect(pane.getByLabel(/^Human summary/)).toHaveValue("The separate font change was stopped because the current choice is suitable.");
    await pane.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Won’t do");
    expect(closeoutWrites).toBe(2);
  } finally { await api.dispose(); }
});
