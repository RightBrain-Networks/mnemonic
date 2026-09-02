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

const UUID_PATTERN = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;

type WorkItem = {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  status: string;
  version: number;
};

type WorkCreation = {
  work_item: WorkItem;
  initial_checkpoint: { id: string };
  initial_relationships: unknown[];
};

type WorkContext = {
  work_item: WorkItem;
  checkpoint_total: number;
  event_total: number;
  relationship_counts: { total: number };
};

type WorkEventPage = {
  items: Array<{ id: string; event_type: string; body: string | null }>;
  total: number;
};

type MutationRequest = {
  method: string;
  url: string;
  body: string;
};

type MutationResponse = {
  status: number;
  body: string;
};

type LostResponseProbe = {
  requests: MutationRequest[];
  responses: MutationResponse[];
};

type Invalidation = {
  type: "invalidate";
  revision: number;
  scope: "projects" | "work-items";
};

async function createFixtureWork(
  client: APIRequestContext,
  title: string,
  summary: string,
  sessionId: string
): Promise<WorkCreation> {
  const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
    data: {
      title,
      summary,
      status: "pending",
      priority: 29,
      initial_checkpoint: {
        prompt: `Initial Phase 6 context for ${title}.`,
        source_client: "playwright-api",
        source_session_id: sessionId,
        source_model: null,
        source_session_url: null,
        repository_branch: null,
        verified_against: null,
        tags: ["phase-6", "idempotency"],
        source_metadata: {}
      }
    }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkCreation;
}

async function getWork(
  client: APIRequestContext,
  workId: string
): Promise<WorkItem | null> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}`
  );
  if (response.status() === 404) return null;
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkItem;
}

async function hideFixtureWork(
  client: APIRequestContext,
  workId: string
): Promise<void> {
  const work = await getWork(client, workId);
  if (!work) return;
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/delete`,
    { data: { expected_version: work.version } }
  );
  expect(response.ok(), await response.text()).toBe(true);
}

async function getContext(
  client: APIRequestContext,
  workId: string
): Promise<WorkContext> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/context`
      + "?recent_limit=5&recent_event_limit=10"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkContext;
}

async function progressEvents(
  client: APIRequestContext,
  workId: string
): Promise<WorkEventPage> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/events`
      + "?event_type=progress&order=newest&limit=100&offset=0"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkEventPage;
}

async function installCommittedResponseLoss(
  page: Page,
  url: string,
  method: "POST" | "DELETE",
  replacement: "bad-gateway" | "malformed-success"
): Promise<LostResponseProbe> {
  const probe: LostResponseProbe = { requests: [], responses: [] };
  await page.route(url, async (route) => {
    const browserRequest = route.request();
    if (browserRequest.method() !== method) {
      await route.continue();
      return;
    }
    probe.requests.push({
      method: browserRequest.method(),
      url: browserRequest.url(),
      body: browserRequest.postData() ?? ""
    });
    const response = await route.fetch();
    const responseBody = await response.text();
    probe.responses.push({ status: response.status(), body: responseBody });
    if (probe.requests.length === 1) {
      if (replacement === "bad-gateway") {
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              code: "database_unavailable",
              message: "The upstream result could not be delivered.",
              context: {}
            }
          })
        });
      } else {
        await route.fulfill({
          status: response.status(),
          contentType: "application/json",
          body: "{}"
        });
      }
      return;
    }
    await route.fulfill({ response, body: responseBody });
  });
  return probe;
}

function operationId(probe: LostResponseProbe): string {
  const body = JSON.parse(probe.requests[0]!.body) as {
    client_operation_id?: unknown;
  };
  expect(typeof body.client_operation_id).toBe("string");
  expect(body.client_operation_id).toMatch(UUID_PATTERN);
  expect(probe.requests[0]!.body.split(String(body.client_operation_id)).length - 1).toBe(1);
  return String(body.client_operation_id);
}

function expectExactReplay(probe: LostResponseProbe): void {
  expect(probe.requests).toHaveLength(2);
  expect(probe.requests[1]).toEqual(probe.requests[0]);
  expect(probe.responses).toHaveLength(2);
  expect(probe.responses[1]!.status).toBe(probe.responses[0]!.status);
  expect(probe.responses[1]!.body).toBe(probe.responses[0]!.body);
  expect(JSON.parse(probe.responses[1]!.body)).toEqual(
    JSON.parse(probe.responses[0]!.body)
  );
}

function captureInvalidations(page: Page): Invalidation[] {
  const messages: Invalidation[] = [];
  page.on("websocket", (socket) => {
    if (!socket.url().endsWith("/api/mnemonic/sync")) return;
    socket.on("framereceived", ({ payload }) => {
      if (typeof payload !== "string") return;
      try {
        const parsed = JSON.parse(payload) as Partial<Invalidation>;
        if (parsed.type === "invalidate") messages.push(parsed as Invalidation);
      } catch {
        // Non-JSON frames are irrelevant to this data-free invalidation assertion.
      }
    });
  });
  return messages;
}

function invalidationCount(messages: Invalidation[]): number {
  for (const message of messages) {
    expect(Object.keys(message).sort()).toEqual(["revision", "scope", "type"]);
  }
  return messages.filter((message) => message.scope === "work-items").length;
}

async function openWork(page: Page, title: string): Promise<void> {
  await page.getByLabel("Search work items").fill(title);
  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: title, exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Work context" })).toBeVisible();
}

async function proxyMutation(
  page: Page,
  path: string,
  method: "POST" | "DELETE",
  originalBody: string
): Promise<{ status: number; body: Record<string, unknown> }> {
  const payload = JSON.parse(originalBody) as Record<string, unknown>;
  payload.client_operation_id = crypto.randomUUID();
  return await page.evaluate(async ({ target, requestMethod, body }) => {
    const response = await fetch(`/api/mnemonic${target}`, {
      method: requestMethod,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return {
      status: response.status,
      body: await response.json() as Record<string, unknown>
    };
  }, { target: path, requestMethod: method, body: payload });
}

test("a committed work creation recovers its exact result without a duplicate", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = [
    testInfo.project.name,
    state.runId.slice(0, 8),
    String(testInfo.retry),
    crypto.randomUUID().slice(0, 8)
  ].join("-");
  const title = `Phase 6 recovered creation ${suffix}`;
  const prompt = `Sensitive frozen creation context ${suffix}.`;
  const invalidations = captureInvalidations(page);
  const probe = await installCommittedResponseLoss(
    page,
    `**/api/mnemonic/projects/${state.projectId}/work-items`,
    "POST",
    "bad-gateway"
  );
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workId = "";

  try {
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.locator(".sync-status")).toHaveText("Live updates");

    await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
    const dialog = page.getByRole("dialog", { name: "Create durable work" });
    await dialog.getByLabel("Title").fill(title);
    await dialog.getByLabel("Summary").fill(`One durable creation for ${suffix}.`);
    await dialog.getByLabel("Priority").fill("41");
    await dialog.getByLabel("Initial context checkpoint").fill(prompt);
    await dialog.getByRole("button", { name: "Create work and checkpoint" }).click();

    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText("Create work · outcome unknown");
    const retainedOperationId = operationId(probe);
    const firstResult = JSON.parse(probe.responses[0]!.body) as WorkCreation;
    workId = firstResult.work_item.id;
    expect(probe.responses[0]!.status).toBe(201);
    expect(probe.responses[0]!.body).not.toContain(retainedOperationId);

    await expect(dialog.getByLabel("Title")).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Close dialog" })).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await expect(page.locator("body")).not.toContainText(retainedOperationId);

    await expect(page.getByRole("link", { name: "Mnemonic home" })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Create work · outcome unknown"
    );
    await expect(dialog).toBeVisible();

    const stored = await page.evaluate(async () => ({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
      cookies: document.cookie,
      url: location.href,
      indexedDatabases: typeof indexedDB.databases === "function"
        ? (await indexedDB.databases()).map((database) => database.name ?? "")
        : [],
      cacheNames: "caches" in globalThis ? await caches.keys() : []
    }));
    const serializedStorage = JSON.stringify(stored);
    expect(serializedStorage).not.toContain(retainedOperationId);
    expect(serializedStorage).not.toContain(prompt);
    await expect.poll(() => invalidationCount(invalidations)).toBe(1);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    await expect(dialog).toHaveCount(0);
    expectExactReplay(probe);
    await expect.poll(() => invalidationCount(invalidations)).toBe(2);

    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(card).toHaveCount(1);
    const search = await client.get(
      `/api/v1/projects/${state.projectId}/work-items?status=all&view=minimal`
        + `&q=${encodeURIComponent(title)}&limit=100&offset=0`
    );
    expect(search.ok(), await search.text()).toBe(true);
    const searchPage = await search.json() as {
      items: Array<{ work_item: { id: string; title: string } }>;
    };
    expect(searchPage.items.filter((item) => item.work_item.title === title)).toEqual([
      expect.objectContaining({
        work_item: expect.objectContaining({ id: workId, title })
      })
    ]);
    const context = await getContext(client, workId);
    expect(context.checkpoint_total).toBe(1);
    expect(context.event_total).toBe(1);
  } finally {
    if (workId) await hideFixtureWork(client, workId);
    await client.dispose();
  }
});

test("a committed deferral recovers its exact result without a second transition", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 6 recovered deferral ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  const created = await createFixtureWork(
    client,
    title,
    `One durable deferral transition for ${suffix}.`,
    `phase6-defer-${suffix}`
  );
  const workId = created.work_item.id;
  const initialVersion = created.work_item.version;
  const probe = await installCommittedResponseLoss(
    page,
    `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/defer`,
    "POST",
    "bad-gateway"
  );

  try {
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(card).toHaveCount(1);
    await card.getByRole("button", { name: `Defer ${title}` }).click();

    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Defer work · outcome unknown"
    );
    const retainedOperationId = operationId(probe);
    expect(probe.responses[0]!.status).toBe(200);
    expect(probe.responses[0]!.body).not.toContain(retainedOperationId);
    await expect(page.locator("#project-select")).toBeDisabled();

    const committed = await getWork(client, workId);
    expect(committed).toMatchObject({
      status: "deferred",
      version: initialVersion + 1
    });
    await expect(page.locator(".toast")).toContainText("mutation outcome is unknown");

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    expectExactReplay(probe);

    const replayed = await getWork(client, workId);
    expect(replayed).toMatchObject({
      status: "deferred",
      version: initialVersion + 1
    });
    await page.getByRole("button", { name: "Deferred", exact: true }).click();
    await expect(card).toHaveCount(1);
    await expect(card.locator(".status-badge")).toHaveText("Deferred");
  } finally {
    await hideFixtureWork(client, workId);
    await client.dispose();
  }
});

test("a malformed committed append retains its editor intent and reconciles newer state", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 6 recovered append ${suffix}`;
  const originalBody = `Committed progress with a lost result ${suffix}.`;
  const newerBody = `Newer progress written before recovery ${suffix}.`;
  const newerSummary = `Authoritative newer summary retained after replay ${suffix}.`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  const created = await createFixtureWork(
    client,
    title,
    `Original summary for ${suffix}.`,
    `phase6-append-${suffix}`
  );
  const workId = created.work_item.id;
  const invalidations = captureInvalidations(page);
  const probe = await installCommittedResponseLoss(
    page,
    `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/events`,
    "POST",
    "malformed-success"
  );

  try {
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.locator(".sync-status")).toHaveText("Live updates");
    await openWork(page, title);
    const detail = page.getByRole("dialog", { name: "Work context" });
    const activity = detail.locator(".event-timeline");
    await activity.getByLabel("Progress text").fill(originalBody);
    await activity.getByRole("button", { name: "Add progress update" }).click();

    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Append progress · outcome unknown"
    );
    const retainedOperationId = operationId(probe);
    expect(probe.responses[0]!.status).toBe(201);
    expect(probe.responses[0]!.body).not.toContain(retainedOperationId);
    await expect(activity.getByLabel("Progress text")).toHaveValue(originalBody);
    await expect(activity.getByLabel("Progress text")).toBeDisabled();
    await expect(detail.getByRole("button", { name: "Close dialog" })).toBeDisabled();
    await expect(detail.getByRole("button", { name: "Edit work item" })).toBeDisabled();
    await expect(detail.getByRole("button", { name: "Delete work item" })).toBeDisabled();
    await expect(detail.getByLabel("Checkpoint text")).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await expect.poll(() => invalidationCount(invalidations)).toBe(1);

    const current = await getWork(client, workId);
    expect(current).not.toBeNull();
    const patched = await client.patch(
      `/api/v1/projects/${state.projectId}/work-items/${workId}`,
      { data: { expected_version: current!.version, summary: newerSummary } }
    );
    expect(patched.ok(), await patched.text()).toBe(true);
    const newer = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/events`,
      {
        data: {
          event_type: "progress",
          body: newerBody,
          metadata: {},
          actor: {
            actor_client: "playwright-api",
            actor_session_id: `phase6-newer-${suffix}`,
            actor_model: null
          }
        }
      }
    );
    expect(newer.ok(), await newer.text()).toBe(true);
    await expect.poll(() => invalidationCount(invalidations)).toBe(3);

    let rejectContextReload = true;
    await page.route(
      `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/context?*`,
      async (route) => {
        if (rejectContextReload) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({
              detail: {
                code: "database_unavailable",
                message: "Synthetic reconciliation failure.",
                context: {}
              }
            })
          });
          return;
        }
        await route.continue();
      }
    );

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    expectExactReplay(probe);
    await expect.poll(() => invalidationCount(invalidations)).toBe(4);

    await expect(page.locator(".toast")).toContainText(
      "current state could not be reloaded"
    );
    await expect(detail).toContainText("Synthetic reconciliation failure");
    await expect(detail.getByRole("button", { name: "Edit work item" })).toHaveCount(0);
    await expect(detail.getByLabel("Checkpoint text")).toHaveCount(0);
    const retryContext = detail.getByRole("button", { name: "Try again" });
    await expect(retryContext).toBeVisible();
    rejectContextReload = false;
    await expect(async () => {
      if (await retryContext.isVisible()) await retryContext.click({ timeout: 500 });
      await expect(detail.locator(".detail-summary")).toHaveText(newerSummary);
    }).toPass();

    await expect(detail.locator(".detail-summary")).toHaveText(newerSummary);
    await expect(activity.getByLabel("Progress text")).toHaveValue("");
    await expect(activity.locator("article.work-event").filter({ hasText: originalBody })).toHaveCount(1);
    await expect(activity.locator("article.work-event").filter({ hasText: newerBody })).toHaveCount(1);
    const events = await progressEvents(client, workId);
    expect(events.total).toBe(2);
    expect(events.items.filter((event) => event.body === originalBody)).toHaveLength(1);
    expect(events.items.filter((event) => event.body === newerBody)).toHaveLength(1);
  } finally {
    await hideFixtureWork(client, workId);
    await client.dispose();
  }
});

test("a committed append gates stale controls when direct reconciliation fails", async ({
  page
}, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 6 append reconciliation ${suffix}`;
  const progress = `Committed before reconciliation failed ${suffix}.`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  const created = await createFixtureWork(
    client,
    title,
    `Append reconciliation summary ${suffix}.`,
    `phase6-append-reconcile-${suffix}`
  );
  const workId = created.work_item.id;
  let rejectContextReload = false;
  await page.route(
    `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/context?*`,
    async (route) => {
      if (!rejectContextReload) {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "database_unavailable",
            message: "Synthetic append reconciliation failure.",
            context: {}
          }
        })
      });
    }
  );

  try {
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await openWork(page, title);
    const detail = page.getByRole("dialog", { name: "Work context" });
    const activity = detail.locator(".event-timeline");
    rejectContextReload = true;
    await activity.getByLabel("Progress text").fill(progress);
    await activity.getByRole("button", { name: "Add progress update" }).click();

    await expect(page.locator(".toast")).toContainText(
      "Progress was saved, but current work context could not be reloaded"
    );
    await expect(detail).toContainText("Synthetic append reconciliation failure");
    await expect(detail.getByRole("button", { name: "Edit work item" })).toHaveCount(0);
    await expect(detail.getByLabel("Checkpoint text")).toHaveCount(0);

    const retryContext = detail.getByRole("button", { name: "Try again" });
    await expect(retryContext).toBeVisible();
    rejectContextReload = false;
    await expect(async () => {
      if (await retryContext.isVisible()) await retryContext.click({ timeout: 500 });
      await expect(detail.getByRole("button", { name: "Edit work item" })).toBeVisible();
    }).toPass();
    await expect(detail.getByRole("button", { name: "Edit work item" })).toBeVisible();
    await expect(detail.locator("article.work-event").filter({ hasText: progress })).toHaveCount(1);
    const events = await progressEvents(client, workId);
    expect(events.items.filter((event) => event.body === progress)).toHaveLength(1);
  } finally {
    await hideFixtureWork(client, workId);
    await client.dispose();
  }
});

test("relationship and deletion recovery preserve true receipts and natural no-ops", async ({
  page
}, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 6 relationship source ${suffix}`;
  const counterpartTitle = `Phase 6 relationship target ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  const created = await createFixtureWork(
    client,
    title,
    `Relationship recovery source ${suffix}.`,
    `phase6-relationship-source-${suffix}`
  );
  const counterpart = await createFixtureWork(
    client,
    counterpartTitle,
    `Relationship recovery target ${suffix}.`,
    `phase6-relationship-target-${suffix}`
  );
  const workId = created.work_item.id;
  const counterpartId = counterpart.work_item.id;
  const invalidations = captureInvalidations(page);
  const addProbe = await installCommittedResponseLoss(
    page,
    `**/api/mnemonic/projects/${state.projectId}/relationships`,
    "POST",
    "bad-gateway"
  );
  let relationshipId = "";

  try {
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.locator(".sync-status")).toHaveText("Live updates");
    await openWork(page, title);
    let detail = page.getByRole("dialog", { name: "Work context" });
    await detail.getByText("Add a relationship", { exact: true }).click();
    await detail.getByLabel("Find another work item").fill(counterpartTitle);
    await detail.getByRole("option").filter({ hasText: counterpartTitle }).click();
    const addButton = detail.getByRole("button", { name: "Add relationship" });
    await expect(addButton).toBeEnabled();
    await addButton.click();

    await expect.poll(() => addProbe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Add relationship · outcome unknown"
    );
    await expect(detail.getByLabel("Find another work item")).toBeDisabled();
    await expect(detail.getByRole("button", { name: "Close dialog" })).toBeDisabled();
    const firstAdd = JSON.parse(addProbe.responses[0]!.body) as {
      created: boolean;
      relationship: { id: string };
    };
    expect(firstAdd.created).toBe(true);
    relationshipId = firstAdd.relationship.id;
    operationId(addProbe);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => addProbe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    expectExactReplay(addProbe);
    detail = page.getByRole("dialog", { name: "Work context" });
    const relatedGroup = detail.getByRole("heading", { name: "Related", exact: true });
    await expect(relatedGroup).toBeVisible();
    expect((await getContext(client, workId)).relationship_counts.total).toBe(1);

    const naturalAdd = await proxyMutation(
      page,
      `/projects/${state.projectId}/relationships`,
      "POST",
      addProbe.requests[0]!.body
    );
    expect(naturalAdd.status).toBe(200);
    expect(naturalAdd.body.created).toBe(false);
    expect((naturalAdd.body.relationship as { id: string }).id).toBe(relationshipId);

    const removeProbe = await installCommittedResponseLoss(
      page,
      `**/api/mnemonic/projects/${state.projectId}/relationships/${relationshipId}`,
      "DELETE",
      "malformed-success"
    );
    await relatedGroup.locator("xpath=..").getByRole("button", { name: "Remove" }).click();
    await expect.poll(() => removeProbe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Remove relationship · outcome unknown"
    );
    const firstRemoval = JSON.parse(removeProbe.responses[0]!.body) as {
      removed: boolean;
      relationship_id: string;
    };
    expect(firstRemoval).toMatchObject({ removed: true, relationship_id: relationshipId });
    operationId(removeProbe);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => removeProbe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    expectExactReplay(removeProbe);
    await expect(detail.getByRole("heading", { name: "Related", exact: true })).toHaveCount(0);
    expect((await getContext(client, workId)).relationship_counts.total).toBe(0);

    const naturalRemoval = await proxyMutation(
      page,
      `/projects/${state.projectId}/relationships/${relationshipId}`,
      "DELETE",
      removeProbe.requests[0]!.body
    );
    expect(naturalRemoval.status).toBe(200);
    expect(naturalRemoval.body).toMatchObject({
      removed: false,
      relationship_id: relationshipId
    });
    await expect.poll(() => invalidationCount(invalidations)).toBe(4);

    await detail.getByRole("button", { name: "Close dialog" }).click();
    const deleteProbe = await installCommittedResponseLoss(
      page,
      `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/delete`,
      "POST",
      "bad-gateway"
    );
    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await card.getByRole("button", { name: `Delete ${title}` }).click();
    const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
    await deleteDialog.getByRole("button", { name: "Delete work item" }).click();

    await expect.poll(() => deleteProbe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Delete work · outcome unknown"
    );
    await expect(deleteDialog.getByRole("button", { name: "Keep work item" })).toBeDisabled();
    await expect(deleteDialog.getByRole("button", { name: "Close dialog" })).toBeDisabled();
    expect(await getWork(client, workId)).toBeNull();
    operationId(deleteProbe);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => deleteProbe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    await expect(deleteDialog).toHaveCount(0);
    expectExactReplay(deleteProbe);
    await expect(card).toHaveCount(0);
    await expect.poll(() => invalidationCount(invalidations)).toBe(6);
  } finally {
    if (relationshipId) {
      await client.delete(
        `/api/v1/projects/${state.projectId}/relationships/${relationshipId}`
      );
    }
    await hideFixtureWork(client, workId);
    await hideFixtureWork(client, counterpartId);
    await client.dispose();
  }
});
