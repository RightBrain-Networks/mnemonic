import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Locator,
  type Page
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard, workPane } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type WorkCreation = {
  work_item: { id: string; version: number };
};

type HumanGate = {
  question_version: number;
  id: string;
  status: "unresolved" | "resolved";
  resolution: string | null;
  current_context_revision: GateRevision;
};

type LostResponseProbe = {
  requests: Array<{ method: string; url: string; body: string }>;
  responses: Array<{ status: number; body: string }>;
};

type GateRevision = {
  work_version: number;
  context_checkpoint_id: string;
  relationship_event_count: number;
};

async function createWork(
  client: APIRequestContext,
  title: string,
  tag: string,
  sessionId: string
): Promise<WorkCreation> {
  const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
    data: {
      title,
      summary: "Disposable Phase 7–8 human-gate browser fixture.",
      status: "pending",
      priority: 67,
      initial_checkpoint: {
        prompt: `Exact current context for ${title}.`,
        source_client: "playwright-api",
        source_session_id: sessionId,
        source_model: null,
        tags: [tag],
        source_metadata: {}
      }
    }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkCreation;
}

async function createGate(
  client: APIRequestContext,
  workId: string,
  question: string,
  sessionId: string
): Promise<HumanGate> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/gates`,
    {
      data: {
        gate_type: "human",
        question,
        requested_by_client: "playwright-api",
        requested_by_session_id: sessionId,
        requested_by_model: null,
        client_operation_id: crypto.randomUUID()
      }
    }
  );
  expect(response.status(), await response.text()).toBe(201);
  return await response.json() as HumanGate;
}

async function resolveGate(
  client: APIRequestContext,
  workId: string,
  gate: HumanGate,
  resolution: string,
  sessionId: string
): Promise<HumanGate> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/gates/${gate.id}/resolve`,
    {
      data: {
        resolution,
        resolved_by_client: "playwright-api",
        resolved_by_session_id: sessionId,
        resolved_by_model: null,
        expected_question_version: gate.question_version,
        reviewed_context_revision: gate.current_context_revision,
        client_operation_id: crypto.randomUUID()
      }
    }
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as HumanGate;
}

async function appendProgress(
  client: APIRequestContext,
  workId: string,
  body: string,
  sessionId: string
): Promise<void> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/events`,
    {
      data: {
        event_type: "progress",
        body,
        metadata: {},
        actor: {
          actor_client: "playwright-api",
          actor_session_id: sessionId,
          actor_model: null
        }
      }
    }
  );
  expect(response.ok(), await response.text()).toBe(true);
}

async function advanceReviewContext(
  client: APIRequestContext,
  workId: string,
  expectedVersion: number,
  summary: string,
  prompt: string,
  tag: string,
  sessionId: string
): Promise<{ workVersion: number; checkpointId: string }> {
  const patched = await client.patch(
    `/api/v1/projects/${state.projectId}/work-items/${workId}`,
    { data: { expected_version: expectedVersion, summary } }
  );
  expect(patched.ok(), await patched.text()).toBe(true);
  const work = await patched.json() as { version: number };

  const checkpoint = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/checkpoints`,
    {
      data: {
        kind: "context",
        prompt,
        source_client: "playwright-api",
        source_session_id: sessionId,
        source_model: null,
        tags: [tag],
        source_metadata: {}
      }
    }
  );
  expect(checkpoint.status(), await checkpoint.text()).toBe(201);
  return {
    workVersion: work.version,
    checkpointId: (await checkpoint.json() as { id: string }).id
  };
}

async function hideWork(client: APIRequestContext, workId: string): Promise<void> {
  if (!workId) return;
  const current = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}`
  );
  if (!current.ok()) return;
  const detail = await current.json() as { work_item: { version: number } };
  const deletionPath = `/api/v1/projects/${state.projectId}/work-items/${workId}/delete`;
  let response = await client.post(deletionPath, {
    data: { expected_version: detail.work_item.version }
  });
  let body = await response.text();
  if (response.status() === 409 && body.includes("\"code\":\"work_gated\"")) {
    const gatesResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/gates?status=unresolved&limit=100`
    );
    expect(gatesResponse.ok(), await gatesResponse.text()).toBe(true);
    const gates = await gatesResponse.json() as {
      items: Array<{
        id: string;
        question_version: number;
        context_changed_since_request: boolean;
        current_context_revision: {
          work_version: number;
          context_checkpoint_id: string;
          relationship_event_count: number;
        };
      }>;
    };
    for (const gate of gates.items) {
      const resolution = await client.post(
        `/api/v1/projects/${state.projectId}/work-items/${workId}/gates/${gate.id}/resolve`,
        {
          data: {
            resolution: "Playwright cleanup after an incomplete acceptance flow.",
            resolved_by_client: "playwright-cleanup",
            resolved_by_session_id: "phase78-cleanup",
            resolved_by_model: null,
            expected_question_version: gate.question_version,
            reviewed_context_revision: gate.current_context_revision,
            client_operation_id: crypto.randomUUID()
          }
        }
      );
      expect(resolution.ok(), await resolution.text()).toBe(true);
    }
    response = await client.post(deletionPath, {
      data: { expected_version: detail.work_item.version }
    });
    body = await response.text();
  }
  expect(response.ok(), body).toBe(true);
}

async function openMoreFilters(page: Page): Promise<Locator> {
  const toggle = page.getByRole("button", { name: "More filters" });
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  const panel = page.locator("#more-filters-panel");
  await expect(panel).toBeVisible();
  return panel;
}

async function installCommittedResponseLoss(page: Page, gatePath: string): Promise<LostResponseProbe> {
  const probe: LostResponseProbe = { requests: [], responses: [] };
  await page.route(`**/api/mnemonic${gatePath}`, async (route) => {
    const browserRequest = route.request();
    if (browserRequest.method() !== "POST") {
      await route.continue();
      return;
    }
    probe.requests.push({
      method: browserRequest.method(),
      url: browserRequest.url(),
      body: browserRequest.postData() ?? ""
    });
    const response = await route.fetch();
    const body = await response.text();
    probe.responses.push({ status: response.status(), body });
    if (probe.requests.length === 1) {
      await route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "database_unavailable",
            message: "The committed answer response could not be delivered.",
            context: {}
          }
        })
      });
      return;
    }
    await route.fulfill({ response, body });
  });
  return probe;
}

test("human questions stay visible and recover one exact durable resolution", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Human gate ${suffix}`;
  const tag = `gate-${crypto.randomUUID().slice(0, 8)}`;
  const sessionId = `phase78-${suffix}`;
  const question = `<img src=x onerror="globalThis.phase78Pwned=true"> Which durable path should continue? ${suffix}`;
  const answer = `Continue only after the reviewed checks pass. ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workId = "";

  try {
    const created = await createWork(client, title, tag, sessionId);
    workId = created.work_item.id;
    const gate = await createGate(client, workId, question, sessionId);
    const resolutionPath = `/projects/${state.projectId}/work-items/${workId}/gates/${gate.id}/resolve`;
    const probe = await installCommittedResponseLoss(page, resolutionPath);

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    const moreFilters = await openMoreFilters(page);
    await moreFilters.getByLabel("Tag").fill(tag);
    await moreFilters.getByLabel("Source client").fill("playwright-api");
    await moreFilters.getByLabel("Source session").fill(sessionId);

    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    await expect(card.locator(".queue-chip-attention")).toHaveText("1 needs attention");
    await expect(card.getByText("Needs attention", { exact: true })).toBeVisible();

    const pane = await selectWork(page, title);
    const questionsTab = await openTab(pane, "Questions");
    await expect(
      questionsTab.getByRole("region", { name: "Questions and answers" })
        .getByText(question, { exact: true })
    ).toBeVisible();
    const activityTab = await openTab(pane, "Activity");
    await expect(
      activityTab.locator(".work-event-kind-human_attention_requested")
    ).toHaveText("Requested human attention");
    await closeDetail(page);

    await page.getByRole("link", { name: /Needs Attention/ }).click();
    const attentionCard = page.locator("article.attention-card").filter({ hasText: title });
    await expect(page.locator(".attention-nav-count")).toHaveText("1");
    await expect(attentionCard.getByText(question, { exact: true })).toBeVisible();
    expect(await page.evaluate(() => (globalThis as typeof globalThis & {
      phase78Pwned?: boolean;
    }).phase78Pwned)).not.toBe(true);

    await attentionCard.getByLabel("Your answer").fill(answer);
    await attentionCard.getByRole("button", { name: "Record answer" }).click();
    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Resolve human question · outcome unknown"
    );
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL(/\/attention$/);
    await expect(page.locator(".toast")).toContainText(
      "Resolve pending mutations before leaving this dashboard document."
    );
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Resolve human question · outcome unknown"
    );

    const firstBody = JSON.parse(probe.requests[0]!.body) as {
      client_operation_id: string;
    };
    expect(firstBody.client_operation_id).toMatch(
      /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/
    );
    const browserStorage = await page.evaluate(() => JSON.stringify({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
      cookie: document.cookie
    }));
    expect(browserStorage).not.toContain(question);
    expect(browserStorage).not.toContain(answer);
    expect(browserStorage).not.toContain(firstBody.client_operation_id);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect.poll(() => probe.responses.length).toBe(2);
    expect(probe.requests[1]).toEqual(probe.requests[0]);
    expect(probe.responses[1]).toEqual(probe.responses[0]);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    await expect(page.getByText(question, { exact: true })).toHaveCount(0);
    await expect(page.locator(".attention-nav-count")).toHaveCount(0);

    const historyResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/gates?status=all&limit=30`
    );
    expect(historyResponse.ok(), await historyResponse.text()).toBe(true);
    const history = await historyResponse.json() as { items: HumanGate[]; total: number };
    expect(history.total).toBe(1);
    expect(history.items[0]).toMatchObject({
      id: gate.id,
      status: "resolved",
      resolution: answer
    });

    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.getByRole("heading", {
      name: "No explicit human questions are waiting."
    })).toBeVisible();

    const eventsResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/events?order=newest&limit=100&offset=0`
    );
    expect(eventsResponse.ok(), await eventsResponse.text()).toBe(true);
    const events = await eventsResponse.json() as {
      items: Array<{ event_type: string; body: string | null }>;
    };
    const resolutionEvents = events.items.filter(
      (event) => event.event_type === "human_attention_resolved"
    );
    expect(resolutionEvents).toHaveLength(1);
    expect(resolutionEvents[0]).toMatchObject({
      event_type: "human_attention_resolved",
      body: answer
    });
  } finally {
    await hideWork(client, workId);
    await client.dispose();
  }
});


test("question versions replace graph review and preserve drafts across a concurrent rewrite", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");
  const sessionId = `versions-${crypto.randomUUID()}`;
  const client = await request.newContext({
    baseURL: apiURL, extraHTTPHeaders: { Authorization: `Bearer ${apiKey}` }
  });
  let workId = "";
  let counterpartId = "";
  let relationshipId = "";
  try {
    const created = await createWork(client, "Choose a deployment window", sessionId, sessionId);
    workId = created.work_item.id;
    const original = "## Deployment window\n\nShould we deploy on Monday or Tuesday?";
    const updated = "## Deployment window\n\nThe infrastructure team is unavailable on Monday. Tuesday’s maintenance window is open.\n\n**Recommendation:** deploy on Tuesday at 10:00. Does that work for you?";
    const newest = "## Deployment window\n\nTuesday’s maintenance window has moved to 14:00. The infrastructure team will be available.\n\n**Recommendation:** deploy on Tuesday at 14:00. Does that work for you?";
    const gate = await createGate(client, workId, original, sessionId);
    const related = await createWork(client, "Confirm infrastructure availability", sessionId, sessionId);
    counterpartId = related.work_item.id;
    const relationship = await client.post(`/api/v1/projects/${state.projectId}/relationships`, { data: {
      relationship_type: "related", source_work_item_id: workId, target_work_item_id: counterpartId,
      created_by_client: "playwright-api", created_by_session_id: sessionId
    }});
    expect(relationship.ok(), await relationship.text()).toBe(true);
    relationshipId = (await relationship.json()).relationship.id;
    await advanceReviewContext(client, counterpartId, related.work_item.version,
      "Monday is unavailable; Tuesday is open.", "Infrastructure availability confirmed for Tuesday.", sessionId, sessionId);
    async function rewrite(question: string, version: number): Promise<void> {
      const response = await client.post(`/api/v1/projects/${state.projectId}/work-items/${workId}/gates`, { data: {
        question, gate_id: gate.id, expected_question_version: version,
        requested_by_client: "playwright-api", requested_by_session_id: sessionId,
        client_operation_id: crypto.randomUUID()
      }});
      expect(response.status(), await response.text()).toBe(201);
    }
    await rewrite(updated, 1);
    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    const card = page.locator("article.attention-card");
    await expect(card).toHaveCount(1);
    const currentTab = card.getByRole("tab", { name: "Version 2 · Current" });
    await expect(currentTab).toHaveAttribute("aria-selected", "true");
    await expect(card.getByRole("tabpanel")).toContainText("Tuesday at 10:00");
    await expect(card.locator(".gate-drift")).toHaveCount(0);
    const answer = card.getByLabel("Your answer");
    await answer.fill("Tuesday works for us.");
    await currentTab.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(card.getByRole("tab", { name: "Version 1", exact: true })).toBeFocused();
    await expect(card.getByRole("tabpanel")).toContainText("Monday or Tuesday?");
    await expect(answer).toBeDisabled();
    await expect(answer).toHaveValue("Tuesday works for us.");
    await expect(card.getByRole("button", { name: "Record answer" })).toBeDisabled();
    await page.keyboard.press("End");
    await expect(currentTab).toBeFocused();
    await expect(answer).toBeEnabled();
    if (process.env.MNEMONIC_CAPTURE_ATTENTION) {
      await card.screenshot({ path: `../docs/images/attention-versions-${testInfo.project.name}.png` });
    }
    const attempts: Array<{ expected_question_version: number; client_operation_id: string }> = [];
    const statuses: number[] = [];
    await page.route(`**/work-items/${workId}/gates/${gate.id}/resolve`, async (route) => {
      attempts.push(route.request().postDataJSON());
      if (attempts.length === 1) await rewrite(newest, 2);
      const response = await route.fetch();
      statuses.push(response.status());
      await route.fulfill({ response });
    });
    await card.getByRole("button", { name: "Record answer" }).click();
    await expect.poll(() => statuses[0]).toBe(409);
    await expect(card.getByRole("tab", { name: "Version 3 · Current" })).toHaveAttribute("aria-selected", "true");
    // IntersectionObserver rounds the scroll edge to fractional CSS pixels.
    await expect(card.getByRole("tab", { name: "Version 3 · Current" })).toBeInViewport({ ratio: 0.99 });
    await expect(card.getByRole("tabpanel")).toContainText("Tuesday at 14:00");
    await expect(answer).toHaveValue("Tuesday works for us.");
    await card.getByRole("button", { name: "Record answer" }).click();
    await expect.poll(() => statuses[1]).toBe(200);
    expect(attempts.map((attempt) => attempt.expected_question_version)).toEqual([2, 3]);
    expect(attempts[0].client_operation_id).not.toBe(attempts[1].client_operation_id);
    await expect(card).toHaveCount(0);
    const history = await client.get(`/api/v1/projects/${state.projectId}/work-items/${workId}/gates?status=all&limit=30`);
    const retained = (await history.json()).items[0];
    expect(retained.question).toBe(newest);
    expect(retained.previous_questions.map((version: { question: string }) => version.question)).toEqual([original, updated]);
  } finally {
    if (relationshipId) await client.delete(`/api/v1/projects/${state.projectId}/relationships/${relationshipId}`);
    await hideWork(client, workId);
    await hideWork(client, counterpartId);
    await client.dispose();
  }
});

test("a deep attention cursor and sibling drafts survive refresh and resolution", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Paged human gates ${suffix}`;
  const sessionId = `phase78-paging-${suffix}`;
  const questions = Array.from(
    { length: 53 },
    (_, index) => `Paged question ${String(index + 1).padStart(2, "0")} ${suffix}`
  );
  const firstDraft = `First page-two draft ${suffix}`;
  const siblingDraft = `Sibling page-two draft ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workId = "";
  let unrelatedWorkId = "";

  try {
    const created = await createWork(client, title, `paging-${suffix}`, sessionId);
    workId = created.work_item.id;
    const gates: HumanGate[] = [];
    for (const question of questions) {
      gates.push(await createGate(client, workId, question, sessionId));
    }
    for (const [index, gate] of gates.slice(32).entries()) {
      await resolveGate(
        client,
        workId,
        gate,
        `Bounded-history answer ${index + 1} ${suffix}`,
        `${sessionId}-history`
      );
    }
    const unrelated = await createWork(
      client,
      `Unrelated attention invalidation ${suffix}`,
      `paging-unrelated-${crypto.randomUUID().slice(0, 8)}`,
      `${sessionId}-unrelated`
    );
    unrelatedWorkId = unrelated.work_item.id;

    const cursorRequests: string[] = [];
    page.on("request", (browserRequest) => {
      const url = new URL(browserRequest.url());
      if (
        url.pathname.endsWith(`/projects/${state.projectId}/human-attention`)
        && url.searchParams.has("cursor")
      ) cursorRequests.push(url.toString());
    });

    let failNextAttentionPage = true;
    await page.route("**/api/mnemonic/**", async (route) => {
      const url = new URL(route.request().url());
      if (
        failNextAttentionPage
        && url.pathname.endsWith(`/projects/${state.projectId}/human-attention`)
        && url.searchParams.get("limit") === "30"
        && url.searchParams.get("work_item_id") === workId
      ) {
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Injected attention page failure." })
        });
        return;
      }
      await route.continue();
    });

    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.getByText("Live Updates", { exact: true })).toBeVisible();
    const attentionList = page.locator(".attention-list");
    await expect(attentionList.getByRole("alert")).toContainText(
      "Injected attention page failure."
    );
    const attentionTitle = page.locator("#attention-list-title");
    failNextAttentionPage = false;
    await attentionList.getByRole("button", { name: "Try again" })
      .click({ timeout: 1_000 })
      .catch(() => undefined);
    await expect(attentionTitle).toHaveText("32 waiting");
    await page.unroute("**/api/mnemonic/**");

    await attentionList.getByRole("link", { name: "Show every question" }).click();
    await expect(page).toHaveURL(/\/attention$/);
    await expect(page.locator(".attention-filter")).toHaveCount(0);
    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(attentionTitle).toHaveText("32 waiting");

    await page.locator("article.attention-card").filter({
      hasText: questions[0]
    }).getByRole("button", { name: "Open work context" }).click();
    await expect(page).toHaveURL(/\?work=/);
    const detail = workPane(page);
    await expect(detail.locator(".detail-title")).toHaveText(title);
    const gatePanel = await openTab(detail, "Questions");
    await expect(gatePanel.getByText(
      "12 additional unresolved questions are omitted from bounded recall. Use the filtered attention queue.",
      { exact: true }
    )).toBeVisible();
    await expect(gatePanel.getByText(
      "1 older resolved decision is omitted from bounded recall.",
      { exact: true }
    )).toBeVisible();
    await gatePanel.getByRole("button", {
      name: "Browse full paired gate history"
    }).click();
    const historyContent = gatePanel.locator(".gate-history-content");
    const historyPager = historyContent.getByRole("navigation", {
      name: "Human-gate history pages"
    });
    await expect(historyPager).toContainText("Page 1 · 53 retained");
    await expect(historyContent.locator("article.gate-fact")).toHaveCount(30);
    await historyPager.getByRole("button", { name: "Older" }).click();
    await expect(historyPager).toContainText("Page 2 · 53 retained");
    await expect(historyContent.locator("article.gate-fact")).toHaveCount(23);
    await historyPager.getByRole("button", { name: "Newer" }).click();
    await expect(historyPager).toContainText("Page 1 · 53 retained");
    await expect(historyContent.locator("article.gate-fact")).toHaveCount(30);
    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(attentionTitle).toHaveText("32 waiting");

    const pager = page.getByRole("navigation", { name: "Human attention pages" });
    await pager.getByRole("button", { name: "Next" }).click();
    await expect(pager).toContainText("Page 2 · 2 shown · 32 currently unresolved");

    const firstCard = page.locator("article.attention-card").filter({
      hasText: questions[30]
    });
    const siblingCard = page.locator("article.attention-card").filter({
      hasText: questions[31]
    });
    const firstAnswer = firstCard.getByLabel("Your answer");
    const siblingAnswer = siblingCard.getByLabel("Your answer");
    await firstAnswer.fill(firstDraft);
    await siblingAnswer.fill(siblingDraft);

    const requestsBeforeInvalidation = cursorRequests.length;
    await appendProgress(
      client,
      unrelatedWorkId,
      `Unrelated progress invalidation ${suffix}`,
      `${sessionId}-unrelated-progress`
    );
    await expect.poll(() => cursorRequests.length).toBeGreaterThan(requestsBeforeInvalidation);
    await expect(pager).toContainText("Page 2 · 2 shown · 32 currently unresolved");
    await expect(firstAnswer).toHaveValue(firstDraft);
    await expect(siblingAnswer).toHaveValue(siblingDraft);

    await firstCard.getByRole("button", { name: "Record answer" }).click();
    await expect(firstCard).toHaveCount(0);
    await expect(pager).toContainText("Page 2 · 1 shown · 31 currently unresolved");
    await expect(siblingAnswer).toHaveValue(siblingDraft);
    await expect(attentionTitle).toBeFocused();
    await expect(page.getByRole("status").filter({
      hasText: "Answer recorded. 31 unresolved questions remain."
    })).toBeVisible();

    await pager.getByRole("button", { name: "Previous" }).click();
    await expect(pager).toContainText("Page 1 · 30 shown · 31 currently unresolved");
    await pager.getByRole("button", { name: "Next" }).click();
    await expect(pager).toContainText("Page 2 · 1 shown · 31 currently unresolved");
    await expect(siblingCard).toBeVisible();
    await page.getByRole("button", { name: "Refresh queue" }).click();
    await expect(pager).toContainText("Page 1 · 30 shown · 31 currently unresolved");
  } finally {
    await hideWork(client, workId);
    await hideWork(client, unrelatedWorkId);
    await client.dispose();
  }
});

test("detail reconciliation preserves sibling gate drafts and restores focus", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Detail gate drafts ${suffix}`;
  const sessionId = `phase78-detail-${suffix}`;
  const questions = [
    `First detail question ${suffix}`,
    `Second detail question ${suffix}`
  ];
  const firstDraft = `First detail answer ${suffix}`;
  const siblingDraft = `Second detail answer ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workId = "";
  let unrelatedWorkId = "";

  try {
    const created = await createWork(client, title, `detail-${suffix}`, sessionId);
    workId = created.work_item.id;
    for (const question of questions) {
      await createGate(client, workId, question, sessionId);
    }
    await advanceReviewContext(
      client,
      workId,
      created.work_item.version,
      `Drifted detail summary ${suffix}`,
      `Drifted detail context ${suffix}`,
      `detail-${suffix}`,
      `${sessionId}-drift`
    );
    const unrelated = await createWork(
      client,
      `Unrelated detail invalidation ${suffix}`,
      `detail-unrelated-${crypto.randomUUID().slice(0, 8)}`,
      `${sessionId}-unrelated`
    );
    unrelatedWorkId = unrelated.work_item.id;

    let contextLoads = 0;
    page.on("request", (browserRequest) => {
      if (browserRequest.url().includes(`/work-items/${workId}/context?`)) contextLoads += 1;
    });
    await page.goto(`/attention?work_item_id=${workId}`);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.getByText("Live Updates", { exact: true })).toBeVisible();
    await page.locator("article.attention-card").first()
      .getByRole("button", { name: "Open work context" }).click();
    await expect(page).toHaveURL(/\?work=/);

    const detail = workPane(page);
    await expect(detail.locator(".detail-title")).toHaveText(title);
    const questionsTab = await openTab(detail, "Questions");
    const panel = questionsTab.getByRole("region", { name: "Questions and answers" });
    const firstGate = panel.locator(".gate-with-resolution").filter({ hasText: questions[0] });
    const siblingGate = panel.locator(".gate-with-resolution").filter({ hasText: questions[1] });
    const firstAnswer = firstGate.getByLabel("Your answer");
    const siblingAnswer = siblingGate.getByLabel("Your answer");
    const deleteButton = detail.getByRole("button", { name: "Delete work item" });
    await expect(deleteButton).toBeDisabled();
    await expect(detail.getByText(
      "2 unresolved human questions block deletion.",
      { exact: true }
    )).toBeVisible();
    await firstAnswer.fill(firstDraft);
    await siblingAnswer.fill(siblingDraft);

    const loadsBeforeInvalidation = contextLoads;
    await appendProgress(
      client,
      unrelatedWorkId,
      `Same-revision detail invalidation ${suffix}`,
      `${sessionId}-unrelated-progress`
    );
    await expect.poll(() => contextLoads).toBeGreaterThan(loadsBeforeInvalidation);
    await expect(firstAnswer).toHaveValue(firstDraft);
    await expect(siblingAnswer).toHaveValue(siblingDraft);

    await firstGate.getByRole("button", { name: "Record answer" }).click();
    await expect(firstGate).toHaveCount(0);
    await expect(siblingAnswer).toHaveValue(siblingDraft);
    await expect(panel.getByRole("heading", { name: "Questions and answers" })).toBeFocused();
    await expect(panel.getByRole("status").filter({
      hasText: "Answer recorded. 1 unresolved question remains."
    })).toBeVisible();
  } finally {
    await hideWork(client, workId);
    await hideWork(client, unrelatedWorkId);
    await client.dispose();
  }
});
