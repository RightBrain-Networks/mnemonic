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

type ResolutionAttempt = {
  client_operation_id: string;
  resolution: string;
  reviewed_context_revision: GateRevision;
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

    await attentionCard.getByLabel("Durable answer").fill(answer);
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


test("a B review rejected at C preserves the answer and requires a fresh intent", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Stale human review ${suffix}`;
  const counterpartTitle = `Relationship review counterpart ${suffix}`;
  const tag = `stale-${crypto.randomUUID().slice(0, 8)}`;
  const sessionId = `phase78-stale-${suffix}`;
  const question = `Which reviewed context should govern this decision? ${suffix}`;
  const answer = `Use the newly reviewed current context. ${suffix}`;
  const bSummary = `Review snapshot B for ${suffix}.`;
  const bPrompt = `Exact checkpoint B for ${suffix}.`;
  const cSummary = `Review snapshot C for ${suffix}.`;
  const cPrompt = `Exact checkpoint C for ${suffix}.`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workId = "";
  let counterpartId = "";
  let relationshipId = "";

  try {
    const created = await createWork(client, title, tag, sessionId);
    workId = created.work_item.id;
    const counterpart = await createWork(
      client,
      counterpartTitle,
      `${tag}-peer`,
      `${sessionId}-peer`
    );
    counterpartId = counterpart.work_item.id;
    const gate = await createGate(client, workId, question, sessionId);
    const relationshipResponse = await client.post(
      `/api/v1/projects/${state.projectId}/relationships`,
      {
        data: {
          relationship_type: "related",
          source_work_item_id: workId,
          target_work_item_id: counterpartId,
          created_by_client: "playwright-api",
          created_by_session_id: `${sessionId}-relationship-B`,
          created_by_model: null,
          context_checkpoint_id: null
        }
      }
    );
    expect(relationshipResponse.status(), await relationshipResponse.text()).toBe(200);
    const relationshipResult = await relationshipResponse.json() as {
      created: boolean;
      relationship: { id: string };
    };
    expect(relationshipResult.created).toBe(true);
    relationshipId = relationshipResult.relationship.id;
    const reviewB = await advanceReviewContext(
      client,
      workId,
      created.work_item.version,
      bSummary,
      bPrompt,
      tag,
      `${sessionId}-B`
    );
    expect(reviewB.workVersion).toBe(2);

    let freezeOuterAttentionProjection = true;
    let frozenOuterAttentionProjection: Record<string, unknown> | undefined;
    await page.route(
      "**/api/mnemonic/projects/" + state.projectId + "/human-attention?*",
      async (route) => {
        const url = new URL(route.request().url());
        if (
          route.request().method() !== "GET"
          || url.searchParams.get("limit") === "0"
          || !freezeOuterAttentionProjection
        ) {
          await route.continue();
          return;
        }
        if (!frozenOuterAttentionProjection) {
          const response = await route.fetch();
          frozenOuterAttentionProjection = await response.json() as Record<string, unknown>;
          await route.fulfill({ response, json: frozenOuterAttentionProjection });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(frozenOuterAttentionProjection)
        });
      }
    );

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByRole("link", { name: /Needs Attention/ }).click();
    const attentionCard = page.locator("article.attention-card").filter({ hasText: title });
    await expect(attentionCard.getByText(question, { exact: true })).toBeVisible();
    await attentionCard.getByRole("button", { name: "Review current context" }).click();

    const reviewBundle = attentionCard.locator(".gate-context-review");
    await expect(reviewBundle.locator("pre")).toHaveText(bPrompt);
    await expect(reviewBundle).toContainText(bSummary);
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(0)).toHaveText("2");
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(1)).toHaveText(
      reviewB.checkpointId
    );
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(2)).toHaveText("1");
    const relationshipReview = reviewBundle.locator(".gate-review-relationships");
    await relationshipReview.locator("summary").click();
    await expect(
      relationshipReview.locator("li").filter({ hasText: counterpartTitle })
    ).toBeVisible();
    const answerField = attentionCard.getByLabel("Durable answer");
    const acknowledgement = attentionCard.getByLabel(
      "I reviewed this exact current work, context checkpoint, and relationship state."
    );
    const submit = attentionCard.getByRole("button", { name: "Record answer" });
    await answerField.fill(answer);
    await acknowledgement.check();
    await expect(submit).toBeEnabled();

    const resolutionPath = `/projects/${state.projectId}/work-items/${workId}/gates/${gate.id}/resolve`;
    const attempts: ResolutionAttempt[] = [];
    const responses: Array<{ status: number; body: string }> = [];
    let reviewC: { workVersion: number; checkpointId: string } | undefined;
    await page.route(`**/api/mnemonic${resolutionPath}`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      attempts.push(JSON.parse(route.request().postData() ?? "") as ResolutionAttempt);
      if (attempts.length === 1) {
        const removed = await client.delete(
          `/api/v1/projects/${state.projectId}/relationships/${relationshipId}`
        );
        expect(removed.ok(), await removed.text()).toBe(true);
        relationshipId = "";
        reviewC = await advanceReviewContext(
          client,
          workId,
          reviewB.workVersion,
          cSummary,
          cPrompt,
          tag,
          `${sessionId}-C`
        );
      }
      const response = await route.fetch();
      const body = await response.text();
      responses.push({ status: response.status(), body });
      if (response.status() === 200) freezeOuterAttentionProjection = false;
      await route.fulfill({ response, body });
    });

    await submit.click();
    await expect.poll(() => responses.length).toBe(1);
    expect(responses[0]!.status).toBe(409);
    expect(responses[0]!.body).toContain("gate_context_changed");
    expect(attempts[0]).toMatchObject({
      resolution: answer,
      reviewed_context_revision: {
        work_version: reviewB.workVersion,
        context_checkpoint_id: reviewB.checkpointId,
        relationship_event_count: 1
      }
    });
    expect(attempts[0]!.client_operation_id).toMatch(
      /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/
    );

    await expect(attentionCard.getByRole("alert")).toContainText(
      "Your answer is still here; review and acknowledge the newly loaded context"
    );
    await expect(answerField).toHaveValue(answer);
    expect(reviewC).toBeDefined();
    const currentC = reviewC!;
    expect(currentC.workVersion).toBe(3);
    await expect(reviewBundle.locator("pre")).toHaveText(cPrompt);
    await expect(reviewBundle).toContainText(cSummary);
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(0)).toHaveText("3");
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(1)).toHaveText(
      currentC.checkpointId
    );
    await expect(reviewBundle.locator(".gate-review-revision dd").nth(2)).toHaveText("2");
    await expect(relationshipReview.locator("summary")).toHaveText(
      "Review current relationships (0)"
    );
    await relationshipReview.locator("summary").click();
    await expect(relationshipReview.getByText("No current relationships.", { exact: true }))
      .toBeVisible();
    await expect(acknowledgement).not.toBeChecked();
    await expect(submit).toBeDisabled();

    await acknowledgement.check();
    await submit.click();
    await expect.poll(() => responses.length).toBe(2);
    expect(responses[1]!.status).toBe(200);
    expect(attempts[1]).toMatchObject({
      resolution: answer,
      reviewed_context_revision: {
        work_version: currentC.workVersion,
        context_checkpoint_id: currentC.checkpointId,
        relationship_event_count: 2
      }
    });
    expect(attempts[1]!.client_operation_id).not.toBe(attempts[0]!.client_operation_id);
    await expect(attentionCard).toHaveCount(0);

    const historyResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/gates?status=all&limit=30`
    );
    expect(historyResponse.ok(), await historyResponse.text()).toBe(true);
    const history = await historyResponse.json() as {
      items: Array<HumanGate & {
        resolved_context_revision: GateRevision | null;
        context_changed_at_resolution: boolean | null;
      }>;
    };
    expect(history.items).toHaveLength(1);
    expect(history.items[0]).toMatchObject({
      id: gate.id,
      status: "resolved",
      resolution: answer,
      resolved_context_revision: attempts[1]!.reviewed_context_revision,
      context_changed_at_resolution: true
    });

    const eventsResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/events?order=newest&limit=100&offset=0`
    );
    expect(eventsResponse.ok(), await eventsResponse.text()).toBe(true);
    const events = await eventsResponse.json() as {
      items: Array<{ event_type: string; body: string | null }>;
    };
    expect(events.items.filter(
      (event) => event.event_type === "human_attention_resolved" && event.body === answer
    )).toHaveLength(1);
  } finally {
    if (relationshipId) {
      await client.delete(
        `/api/v1/projects/${state.projectId}/relationships/${relationshipId}`
      );
    }
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
    await expect(page.getByText("Live updates", { exact: true })).toBeVisible();
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
    const firstAnswer = firstCard.getByLabel("Durable answer");
    const siblingAnswer = siblingCard.getByLabel("Durable answer");
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
    await expect(page.getByText("Live updates", { exact: true })).toBeVisible();
    await page.locator("article.attention-card").first()
      .getByRole("button", { name: "Open work context" }).click();
    await expect(page).toHaveURL(/\?work=/);

    const detail = workPane(page);
    await expect(detail.locator(".detail-title")).toHaveText(title);
    const questionsTab = await openTab(detail, "Questions");
    const panel = questionsTab.getByRole("region", { name: "Questions and answers" });
    const firstGate = panel.locator(".gate-with-resolution").filter({ hasText: questions[0] });
    const siblingGate = panel.locator(".gate-with-resolution").filter({ hasText: questions[1] });
    const firstAnswer = firstGate.getByLabel("Durable answer");
    const siblingAnswer = siblingGate.getByLabel("Durable answer");
    const acknowledgementLabel =
      "I reviewed this exact current work, context checkpoint, and relationship state.";
    const firstAcknowledgement = firstGate.getByLabel(acknowledgementLabel);
    const siblingAcknowledgement = siblingGate.getByLabel(acknowledgementLabel);
    const deleteButton = detail.getByRole("button", { name: "Delete work item" });
    await expect(deleteButton).toBeDisabled();
    await expect(detail.getByText(
      "2 unresolved human questions block deletion.",
      { exact: true }
    )).toBeVisible();
    await expect(firstAcknowledgement).toBeEnabled();
    await expect(siblingAcknowledgement).toBeEnabled();
    await firstAnswer.fill(firstDraft);
    await siblingAnswer.fill(siblingDraft);
    await firstAcknowledgement.check();
    await siblingAcknowledgement.check();

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
    await expect(firstAcknowledgement).toBeChecked();
    await expect(firstAcknowledgement).toBeEnabled();
    await expect(siblingAcknowledgement).toBeChecked();
    await expect(siblingAcknowledgement).toBeEnabled();

    await firstGate.getByRole("button", { name: "Record answer" }).click();
    await expect(firstGate).toHaveCount(0);
    await expect(siblingAnswer).toHaveValue(siblingDraft);
    await expect(siblingAcknowledgement).toBeChecked();
    await expect(siblingAcknowledgement).toBeEnabled();
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
