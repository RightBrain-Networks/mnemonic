import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Page
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

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
  acknowledge_context_change: boolean;
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
  const work = await current.json() as { version: number };
  const deletionPath = `/api/v1/projects/${state.projectId}/work-items/${workId}/delete`;
  let response = await client.post(deletionPath, {
    data: { expected_version: work.version }
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
            acknowledge_context_change: gate.context_changed_since_request,
            reviewed_context_revision: gate.context_changed_since_request
              ? gate.current_context_revision
              : null,
            client_operation_id: crypto.randomUUID()
          }
        }
      );
      expect(resolution.ok(), await resolution.text()).toBe(true);
    }
    response = await client.post(deletionPath, {
      data: { expected_version: work.version }
    });
    body = await response.text();
  }
  expect(response.ok(), body).toBe(true);
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
    await page.getByLabel("Tag").fill(tag);
    await page.getByLabel("Source client").fill("playwright-api");
    await page.getByLabel("Source session").fill(sessionId);

    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(card).toHaveCount(1);
    const branch = card.locator("xpath=ancestor::div[contains(@class,'hierarchy-node')][1]");
    await expect(branch.locator(".hierarchy-aggregate-strip")).toHaveAttribute(
      "aria-label",
      /1 unresolved human question/
    );
    await expect(card.getByText("Needs attention", { exact: true })).toBeVisible();

    await card.getByRole("button", { name: title, exact: true }).click();
    const detail = page.getByRole("dialog", { name: "Work context" });
    await expect(
      detail.getByRole("region", { name: "Questions and answers" })
        .getByText(question, { exact: true })
    ).toBeVisible();
    await expect(
      detail.locator(".work-event-kind-human_attention_requested")
    ).toHaveText("Requested human attention");
    await detail.getByRole("button", { name: "Close dialog" }).click();

    await page.getByRole("link", { name: /Needs Attention/ }).click();
    const attentionCard = page.locator("article.attention-card").filter({ hasText: title });
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
    await expect(page.locator(".attention-nav-count")).toHaveText("0");

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
      acknowledge_context_change: true,
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
      acknowledge_context_change: true,
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
        context_change_acknowledged: boolean | null;
      }>;
    };
    expect(history.items).toHaveLength(1);
    expect(history.items[0]).toMatchObject({
      id: gate.id,
      status: "resolved",
      resolution: answer,
      resolved_context_revision: attempts[1]!.reviewed_context_revision,
      context_changed_at_resolution: true,
      context_change_acknowledged: true
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
