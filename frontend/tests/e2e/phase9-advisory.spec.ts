import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Page,
  type Route
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type WorkItem = {
  id: string;
  title: string;
  status: string;
  version: number;
};

type WorkContext = {
  work_item: WorkItem;
  merge_review_revision: {
    work_version: number;
    context_checkpoint_id: string;
    work_event_count: number;
  };
};

async function apiClient(): Promise<APIRequestContext> {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");
  return await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
}

async function createWork(
  client: APIRequestContext,
  title: string,
  summary: string,
  prompt: string,
  sessionId: string
): Promise<WorkItem> {
  const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
    data: {
      title,
      summary,
      status: "pending",
      priority: 13,
      initial_checkpoint: {
        prompt,
        source_client: "playwright-api",
        source_session_id: sessionId,
        source_model: null,
        source_session_url: null,
        repository_branch: null,
        verified_against: null,
        tags: ["phase-9", "advisory"],
        source_metadata: {}
      }
    }
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { work_item: WorkItem }).work_item;
}

async function context(client: APIRequestContext, workItemId: string): Promise<WorkContext> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workItemId}/context`
      + "?recent_limit=5&recent_event_limit=10"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkContext;
}

async function completeWork(client: APIRequestContext, work: WorkItem, suffix: string): Promise<void> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${work.id}/complete`,
    {
      data: {
        expected_version: work.version,
        checkpoint: {
          prompt: `Completion evidence ${suffix}.`,
          source_client: "playwright-api",
          source_session_id: `phase9-advisory-complete-${suffix}`,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-9", "advisory"],
          source_metadata: {}
        },
        client_operation_id: crypto.randomUUID()
      }
    }
  );
  expect(response.ok(), await response.text()).toBe(true);
}

async function mergeAlias(
  client: APIRequestContext,
  sourceId: string,
  destinationId: string,
  suffix: string
): Promise<void> {
  const [source, destination] = await Promise.all([
    context(client, sourceId),
    context(client, destinationId)
  ]);
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${sourceId}/merge`,
    {
      data: {
        destination_work_item_id: destinationId,
        reviewed_source_revision: source.merge_review_revision,
        reviewed_destination_revision: destination.merge_review_revision,
        rationale: `Same durable objective ${suffix}.`,
        merged_by_client: "playwright-api",
        merged_by_session_id: `phase9-advisory-merge-${suffix}`,
        merged_by_model: null,
        client_operation_id: crypto.randomUUID()
      }
    }
  );
  expect(response.status(), await response.text()).toBe(201);
}

async function openDashboard(page: Page): Promise<void> {
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.locator(".sync-status")).toHaveText("Live updates");
}

async function fillCreateDraft(
  page: Page,
  draft: { title: string; summary: string; prompt: string; tags?: string }
) {
  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const dialog = page.getByRole("dialog", { name: "Create durable work" });
  await dialog.getByLabel("Title").fill(draft.title);
  await dialog.getByLabel("Summary").fill(draft.summary);
  await dialog.getByLabel("Initial context checkpoint").fill(draft.prompt);
  if (draft.tags) {
    await dialog.locator("details.edit-context summary").click();
    await dialog.getByLabel(/Tags/).fill(draft.tags);
  }
  return dialog;
}

test("Advisory reveals a completed canonical group through its exact alias, then permits distinct creation", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const aliasTitle = `Phase 9 advisory exact alias ${suffix}`;
  const canonicalTitle = `Phase 9 completed canonical ${suffix}`;
  const distinctTitle = `Phase 9 distinct creation ${suffix}`;
  const client = await apiClient();
  const suggestionBodies: Array<Record<string, unknown>> = [];
  page.on("request", (browserRequest) => {
    if (
      browserRequest.method() === "POST"
      && browserRequest.url().endsWith(
        `/api/mnemonic/projects/${state.projectId}/duplicate-suggestions`
      )
    ) suggestionBodies.push(browserRequest.postDataJSON() as Record<string, unknown>);
  });

  try {
    const canonical = await createWork(
      client,
      canonicalTitle,
      `The completed canonical objective ${suffix}.`,
      `Canonical context ${suffix}.`,
      `phase9-advisory-canonical-${suffix}`
    );
    await completeWork(client, canonical, suffix);
    const alias = await createWork(
      client,
      aliasTitle,
      `The retained alias that supplies exact-title evidence ${suffix}.`,
      `Alias audit context ${suffix}.`,
      `phase9-advisory-alias-${suffix}`
    );
    await mergeAlias(client, alias.id, canonical.id, suffix);

    await openDashboard(page);
    const createDialog = await fillCreateDraft(page, {
      title: aliasTitle,
      summary: `A new draft to compare against existing work ${suffix}.`,
      prompt: `Draft-only context ${suffix}.`,
      tags: "Phase-9, Advisory, phase-9"
    });
    const createButton = createDialog.getByRole("button", {
      name: "Create work and checkpoint"
    });
    await expect(createButton).toBeEnabled();
    await createDialog.getByRole("button", { name: "Check existing work" }).click();

    const panel = createDialog.locator(".duplicate-suggestions");
    await expect(panel).toContainText("Possible existing work — compare manually");
    const candidate = panel.locator(".duplicate-suggestion-card").filter({
      hasText: canonical.id
    });
    await expect(candidate).toHaveCount(1);
    await expect(candidate.locator("h4 bdi")).toHaveText(canonicalTitle);
    await expect(candidate).toContainText("Done");
    await expect(candidate).toContainText("Duplicate members");
    await expect(candidate).toContainText("Matched duplicate member");
    await expect(candidate).toContainText(alias.id);
    await expect(candidate.locator(".duplicate-suggestion-match bdi")).toHaveText(aliasTitle);
    await expect(candidate.locator(".duplicate-suggestion-signals")).toContainText("Exact title");
    await expect(panel.locator(".duplicate-suggestion-scope")).toContainText(/scope|Lexical/);
    await expect(createButton).toBeEnabled();

    expect(suggestionBodies).toHaveLength(1);
    expect(Object.keys(suggestionBodies[0]!).sort()).toEqual([
      "exclude_work_item_id", "initial_prompt", "limit", "summary", "tags", "title"
    ]);
    expect(suggestionBodies[0]).toEqual({
      title: aliasTitle,
      summary: `A new draft to compare against existing work ${suffix}.`,
      initial_prompt: `Draft-only context ${suffix}.`,
      tags: ["phase-9", "advisory"],
      exclude_work_item_id: null,
      limit: 5
    });

    await candidate.getByRole("button", { name: "Inspect existing work" }).click();
    const detail = page.getByRole("dialog", { name: "Work context" });
    await expect(detail.locator(".detail-title")).toHaveText(canonicalTitle);
    await detail.getByRole("button", { name: "Close dialog" }).click();
    await expect(createDialog).toBeVisible();

    await createDialog.getByLabel("Title").fill(distinctTitle);
    await expect(panel).toContainText("Draft changed. Check existing work again");
    await expect(createButton).toBeEnabled();
    expect(suggestionBodies).toHaveLength(1);
    const persisted = await page.evaluate(() => ({
      local: Object.values(localStorage),
      session: Object.values(sessionStorage),
      url: location.href
    }));
    expect(JSON.stringify(persisted)).not.toContain(aliasTitle);
    expect(JSON.stringify(persisted)).not.toContain(distinctTitle);

    await createButton.click();
    await expect(createDialog).not.toBeVisible();
    await expect(page.locator("article.work-item-card").filter({ hasText: distinctTitle }))
      .toHaveCount(1);
  } finally {
    await client.dispose();
  }
});

type MockBehavior = "busy" | "empty" | "offline" | "unavailable" | "ready" | "delayed";

test("Advisory states stay optional, accessible, stale-safe, and bidi-isolated", async ({
  page
}, testInfo) => {
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const hostileTitle = `Existing \u2067العربية\u2069 \u202Edraft\u202C zero\u200Bwidth ${suffix}`;
  const candidateId = "11111111-1111-4111-8111-111111111111";
  const behaviors: MockBehavior[] = [
    "busy", "empty", "offline", "unavailable", "ready", "delayed"
  ];
  let requests = 0;
  const suggestionResponse = {
    items: [{
      canonical_work: {
        work_item_id: candidateId,
        title: hostileTitle,
        summary: `Literal candidate summary ${suffix}.`,
        status: "wont-do",
        updated_at: "2026-09-02T15:04:05Z",
        duplicate_member_count: 0
      },
      matched_member: { id: candidateId, title: hostileTitle, status: "wont-do" },
      rank: 1,
      signals: ["lexical"]
    }],
    limit: 5,
    mode: "lexical",
    semantic_available: false,
    semantic_scope: "unavailable",
    composition_version: "duplicate-suggestion-v1",
    exact_title_group_total: 0,
    omitted_exact_title_group_count: 0
  };
  await page.route("**/api/mnemonic/projects/*/duplicate-suggestions", async (route: Route) => {
    const behavior = behaviors[requests++];
    if (behavior === "offline") {
      await route.abort("failed");
      return;
    }
    if (behavior === "delayed") {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(suggestionResponse)
      }).catch(() => undefined);
      return;
    }
    if (behavior === "busy" || behavior === "unavailable") {
      await route.fulfill({
        status: behavior === "busy" ? 429 : 503,
        headers: behavior === "busy" ? { "Retry-After": "1" } : {},
        contentType: "application/json",
        body: JSON.stringify({ detail: {
          code: behavior === "busy"
            ? "duplicate_suggestion_busy"
            : "duplicate_suggestion_unavailable",
          message: "Synthetic category-only Advisory failure.",
          context: {}
        } })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(behavior === "empty"
        ? { ...suggestionResponse, items: [] }
        : suggestionResponse)
    });
  });

  await openDashboard(page);
  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const dialog = page.getByRole("dialog", { name: "Create durable work" });
  const check = dialog.getByRole("button", { name: "Check existing work" });
  const create = dialog.getByRole("button", { name: "Create work and checkpoint" });
  await check.click();
  await expect(dialog.getByRole("alert")).toContainText("Complete the required");
  expect(requests).toBe(0);

  await dialog.getByLabel("Title").fill(`Advisory optional states ${suffix}`);
  await dialog.getByLabel("Summary").fill(`Valid summary ${suffix}.`);
  await dialog.getByLabel("Initial context checkpoint").fill(`Valid prompt ${suffix}.`);
  expect(requests).toBe(0);
  await expect(create).toBeEnabled();

  await check.click();
  await expect(dialog.getByRole("alert")).toContainText("comparison is busy");
  await expect(create).toBeEnabled();

  await check.click();
  await expect(dialog.getByRole("status")).toContainText("No possible existing work");
  await expect(dialog.locator(".duplicate-suggestion-scope")).toContainText("Lexical comparison");
  await expect(create).toBeEnabled();

  await check.click();
  await expect(dialog.getByRole("alert")).toContainText("comparison is offline");
  await expect(create).toBeEnabled();

  await check.click();
  await expect(dialog.getByRole("alert")).toContainText("comparison is unavailable");
  await expect(create).toBeEnabled();

  await check.click();
  const candidate = dialog.locator(".duplicate-suggestion-card");
  await expect(candidate.locator("h4 bdi")).toHaveText(hostileTitle);
  await expect(candidate.locator("h4 bdi")).toHaveAttribute("dir", "auto");
  expect(await candidate.locator("h4 bdi").evaluate((element) => (
    getComputedStyle(element).unicodeBidi
  ))).toBe("isolate");
  await expect(candidate.locator(".duplicate-suggestion-signals")).toHaveText("Related text");
  await expect(create).toBeEnabled();

  await dialog.getByLabel("Summary").fill(`Changed summary ${suffix}.`);
  await expect(dialog).toContainText("Draft changed. Check existing work again");
  expect(requests).toBe(5);
  await expect(create).toBeEnabled();

  await check.click();
  await expect(dialog.getByRole("status")).toContainText("Checking existing work");
  await dialog.getByLabel("Initial context checkpoint").fill(`Changed prompt ${suffix}.`);
  await expect(dialog).toContainText("Draft changed. Check existing work again");
  await page.waitForTimeout(650);
  await expect(dialog).toContainText("Draft changed. Check existing work again");
  expect(requests).toBe(6);
  await expect(create).toBeEnabled();

  const persisted = await page.evaluate(() => ({
    local: Object.values(localStorage),
    session: Object.values(sessionStorage),
    url: location.href
  }));
  expect(JSON.stringify(persisted)).not.toContain(hostileTitle);
  expect(JSON.stringify(persisted)).not.toContain(`Changed summary ${suffix}.`);
});
