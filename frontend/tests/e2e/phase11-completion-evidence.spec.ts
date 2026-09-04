import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Page
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { openTab, selectWork, workCard } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type WorkItem = {
  id: string;
  project_id: string;
  title: string;
  status: string;
  version: number;
};

type CompletionEpisode = {
  completion_event_id: string;
  completion_checkpoint: { id: string };
  verification_results: Array<{
    name: string;
    command?: string;
    observed_at?: string;
  }>;
  artifact_references: Array<{
    artifact_type: string;
    reference: string;
  }>;
};

type CompletionEvidencePage = {
  work_item_id: string;
  work_version: number;
  is_duplicate: boolean;
  canonical_work_item_id: string;
  current_completion_checkpoint_id: string | null;
  lifecycle_status: string;
  items: CompletionEpisode[];
  total: number;
  structured_completion_total: number;
  next_cursor: string | null;
  as_of_completion_event_id: string | null;
};

type CompletionResult = {
  work_item: WorkItem;
  checkpoint: { id: string };
  completion_evidence?: unknown;
};

type MergeContext = {
  work_item: WorkItem;
  merge_review_revision: {
    work_version: number;
    context_checkpoint_id: string;
    work_event_count: number;
  };
};

type LostResponseProbe = {
  requests: string[];
  responses: Array<{ status: number; body: string }>;
};

const UUID_PATTERN = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;

async function apiClient(): Promise<APIRequestContext> {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) {
    throw new Error("Run this test through the disposable E2E stack.");
  }
  return request.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${apiKey}`,
      Accept: "application/json"
    }
  });
}

async function createFixture(
  client: APIRequestContext,
  title: string,
  suffix: string
): Promise<WorkItem> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items`,
    {
      data: {
        title,
        summary: "Exercise atomic, replayable, inert completion evidence in the browser.",
        status: "pending",
        priority: 31,
        initial_checkpoint: {
          prompt: "Initial context for the Phase 11 browser acceptance flow.",
          source_client: "playwright-api",
          source_session_id: `phase11-create-${suffix}`,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-11", "completion-evidence"],
          source_metadata: {}
        }
      }
    }
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { work_item: WorkItem }).work_item;
}

async function readWork(
  client: APIRequestContext,
  workId: string
): Promise<WorkItem | null> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}`
  );
  if (response.status() === 404) return null;
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { work_item: WorkItem }).work_item;
}

async function deleteFixture(
  client: APIRequestContext,
  workId: string
): Promise<void> {
  const work = await readWork(client, workId);
  if (!work) return;
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/delete`,
    { data: { expected_version: work.version } }
  );
  expect(response.ok(), await response.text()).toBe(true);
}

async function reopenFixture(
  client: APIRequestContext,
  workId: string
): Promise<WorkItem> {
  const current = await readWork(client, workId);
  expect(current).not.toBeNull();
  const response = await client.patch(
    `/api/v1/projects/${state.projectId}/work-items/${workId}`,
    { data: { expected_version: current!.version, status: "pending" } }
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkItem;
}

async function completeFixtureWithoutEvidence(
  client: APIRequestContext,
  work: WorkItem,
  sessionId: string
): Promise<CompletionResult> {
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${work.id}/complete`,
    {
      data: {
        expected_version: work.version,
        checkpoint: {
          prompt: `Evidence-free completion ${sessionId}.`,
          source_client: "playwright-api",
          source_session_id: sessionId,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-11", "evidence-free"],
          source_metadata: {}
        }
      }
    }
  );
  expect(response.ok(), await response.text()).toBe(true);
  const result = await response.json() as CompletionResult;
  expect(result).not.toHaveProperty("completion_evidence");
  return result;
}

async function readMergeContext(
  client: APIRequestContext,
  workId: string
): Promise<MergeContext> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/context`
      + "?recent_limit=5&recent_event_limit=10"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as MergeContext;
}

async function mergeFixture(
  client: APIRequestContext,
  sourceId: string,
  destinationId: string,
  suffix: string
): Promise<void> {
  const [source, destination] = await Promise.all([
    readMergeContext(client, sourceId),
    readMergeContext(client, destinationId)
  ]);
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${sourceId}/merge`,
    {
      data: {
        destination_work_item_id: destinationId,
        reviewed_source_revision: source.merge_review_revision,
        reviewed_destination_revision: destination.merge_review_revision,
        rationale: `Preserve source-owned Phase 11 evidence ${suffix}.`,
        merged_by_client: "playwright-api",
        merged_by_session_id: `phase11-merge-${suffix}`,
        merged_by_model: null,
        client_operation_id: crypto.randomUUID()
      }
    }
  );
  expect(response.status(), await response.text()).toBe(201);
}

async function installCommittedResponseLoss(
  page: Page,
  workId: string
): Promise<LostResponseProbe> {
  const probe: LostResponseProbe = { requests: [], responses: [] };
  await page.route(
    `**/api/mnemonic/projects/${state.projectId}/work-items/${workId}/complete`,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      probe.requests.push(route.request().postData() ?? "");
      const response = await route.fetch();
      const body = await response.text();
      probe.responses.push({ status: response.status(), body });
      if (probe.requests.length === 1) {
        await route.fulfill({
          status: response.status(),
          contentType: "application/json",
          body: "{}"
        });
        return;
      }
      await route.fulfill({ response, body });
    }
  );
  return probe;
}

async function openFixture(page: Page, title: string, status: "Pending" | "Done" = "Pending") {
  const response = await page.goto("/");
  expect(response).not.toBeNull();
  expect(await response!.headerValue("x-dns-prefetch-control")).toBe("off");
  await expect(
    page.locator('link[rel="dns-prefetch"], link[rel="preconnect"]')
  ).toHaveCount(0);
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByRole("group", { name: "Filter work items" }).getByRole("button", {
    name: status,
    exact: true
  }).click();
  await page.getByLabel("Search work items").fill(title);
  await expect(workCard(page, title)).toHaveCount(1);
  return selectWork(page, title);
}

test("a true 0018-migrated completion replays exactly and renders as an honest empty episode", async ({
  page
}) => {
  const fixture = state.historicalCompletion;
  expect(fixture.completionGeneration).toBe(`-${fixture.completionEventId}`);
  expect(fixture.requestBody).not.toHaveProperty("completion_evidence");

  const client = await apiClient();
  try {
    const replayResponse = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${fixture.workItemId}/complete`,
      { data: fixture.requestBody }
    );
    const replayBody = await replayResponse.text();
    expect(replayResponse.status(), replayBody).toBe(200);
    expect(replayBody).toBe(fixture.responseBody);
    const replay = JSON.parse(replayBody) as CompletionResult;
    expect(replay.work_item.id).toBe(fixture.workItemId);
    expect(replay.checkpoint.id).toBe(fixture.completionCheckpointId);
    expect(replay).not.toHaveProperty("completion_evidence");

    const historyResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${fixture.workItemId}`
        + "/completion-evidence?limit=10"
    );
    expect(historyResponse.ok(), await historyResponse.text()).toBe(true);
    const history = await historyResponse.json() as CompletionEvidencePage;
    expect(history.work_item_id).toBe(fixture.workItemId);
    expect(history.as_of_completion_event_id).toBe(fixture.completionEventId);
    expect(history.current_completion_checkpoint_id).toBe(fixture.completionCheckpointId);
    expect(history.total).toBe(1);
    expect(history.structured_completion_total).toBe(0);
    expect(history.items).toHaveLength(1);
    expect(history.items[0]!.completion_event_id).toBe(fixture.completionEventId);
    expect(history.items[0]!.verification_results).toEqual([]);
    expect(history.items[0]!.artifact_references).toEqual([]);

    const pane = await openFixture(page, fixture.title, "Done");
    const evidence = await openTab(pane, "Evidence");
    await expect(evidence.locator("article.completion-episode")).toHaveCount(1);
    await expect(evidence.locator("article.completion-episode.is-current")).toHaveCount(1);
    await expect(evidence.locator("article.completion-episode .section-label")).toHaveText(
      "CURRENT COMPLETION"
    );
    await expect(evidence.getByText(
      "No structured completion evidence recorded",
      { exact: true }
    )).toBeVisible();
    await expect(evidence.locator(".evidence-history-heading")).toContainText(
      "Structured evidence recorded for 0 of 1 completion episode"
    );
  } finally {
    await client.dispose();
  }
});

test("completion evidence is accessible, replayable, lazy, paginated, and source-owned", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = [
    testInfo.project.name,
    state.runId.slice(0, 8),
    String(testInfo.retry),
    crypto.randomUUID().slice(0, 8)
  ].join("-");
  const title = `Phase 11 completion evidence ${suffix}`;
  const command = `printf '<img src=https://evidence.invalid/command>' # ${suffix}`;
  const resultName = `Browser checks ‮ ${suffix}`;
  const resultSummary = "**Reported** browser success; \u001b[31m remains inert text.";
  const limitationName = `Accepted browser limitation ${suffix}`;
  const limitationSummary = "The owner accepted that this browser observation is inconclusive.";
  const artifactUrl = "https://evidence.invalid/build/42";
  const client = await apiClient();
  const work = await createFixture(client, title, suffix);
  const probe = await installCommittedResponseLoss(page, work.id);
  const externalRequests: string[] = [];
  const addCheckpointRequests: string[] = [];
  const evidenceRequests: string[] = [];
  let destination: WorkItem | null = null;
  let merged = false;
  page.on("request", (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (url.hostname === "evidence.invalid") {
      externalRequests.push(browserRequest.url());
    }
    if (
      browserRequest.method() === "POST"
      && url.pathname.endsWith(`/work-items/${work.id}/checkpoints`)
    ) addCheckpointRequests.push(browserRequest.url());
    if (
      browserRequest.method() === "GET"
      && url.pathname.endsWith(`/work-items/${work.id}/completion-evidence`)
    ) evidenceRequests.push(browserRequest.url());
  });

  try {
    let pane = await openFixture(page, title);
    await expect(page.locator(".sync-status")).toHaveText("Live updates");
    const context = await openTab(pane, "Context");
    await context.getByLabel("Checkpoint text").fill(
      "Completion whose structured evidence is committed and replayed atomically. "
      + "The owner explicitly accepted the documented inconclusive browser limitation."
    );
    await context.locator("details.completion-evidence-disclosure > summary").click();
    const addResult = context.getByRole("button", { name: "Add verification result" });
    await addResult.focus();
    await addResult.press("Enter");
    await addResult.press(" ");
    let resultRows = context.getByRole("group", { name: /^Verification result \d+$/ });
    await expect(resultRows).toHaveCount(2);
    await resultRows.nth(0).getByLabel("Name").fill("First stable result");
    await resultRows.nth(1).getByLabel("Name").fill("Second movable result");
    const moveSecondUp = context.getByRole("button", {
      name: "Move verification result 2 up"
    });
    await moveSecondUp.focus();
    await moveSecondUp.press("Enter");
    await expect(resultRows.nth(0).getByLabel("Name")).toHaveValue(
      "Second movable result"
    );
    const removeFirst = context.getByRole("button", {
      name: "Remove verification result 1"
    });
    await removeFirst.focus();
    await removeFirst.press(" ");
    await expect(resultRows).toHaveCount(1);
    await expect(resultRows.nth(0).getByLabel("Name")).toHaveValue(
      "First stable result"
    );

    const addArtifact = context.getByRole("button", { name: "Add artifact reference" });
    await addArtifact.focus();
    await addArtifact.press("Enter");
    await expect(context.locator(".evidence-budget")).toContainText("2/20 entries");

    for (let index = 0; index < 18; index += 1) await addResult.click();
    await expect(context.locator(".evidence-budget")).toContainText("20/20 entries");
    await expect(addResult).toBeDisabled();
    await expect(addArtifact).toBeDisabled();
    for (let index = 0; index < 18; index += 1) {
      await context.getByRole("button", {
        name: /Remove verification result/
      }).last().click();
    }
    await expect(context.locator(".evidence-budget")).toContainText("2/20 entries");

    resultRows = context.getByRole("group", { name: /^Verification result \d+$/ });
    const result = resultRows.nth(0);
    await result.getByLabel("Result type").selectOption("command");
    await result.getByLabel("Name").fill("n".repeat(201));
    await result.getByLabel("Result summary").fill(resultSummary);
    const commandInput = result.getByRole("textbox", { name: /^Command/ });
    await commandInput.fill(command);
    await context.getByRole("button", { name: "Complete with summary" }).click();
    const nameInput = result.getByLabel("Name");
    await expect(nameInput).toHaveAttribute("aria-invalid", "true");
    const nameErrorId = await nameInput.getAttribute("aria-describedby");
    expect(nameErrorId).toMatch(/^completion-evidence-verification_results-.+-name-error$/);
    await expect(page.locator(`#${nameErrorId}`)).toContainText("too long");
    expect(probe.requests).toHaveLength(0);

    await nameInput.fill(resultName);
    await result.getByLabel("Name").press("Enter");
    await expect.poll(() => addCheckpointRequests.length).toBe(0);
    expect(probe.requests).toHaveLength(0);
    await expect(nameInput).toHaveAttribute("aria-invalid", "false");
    await expect(nameInput).not.toHaveAttribute("aria-describedby", /.+/);
    await expect(result.locator(".field-hint").nth(0)).toContainText(
      `${Array.from(resultName).length}/200 characters`
    );

    await context.getByRole("button", { name: "Complete with summary" }).click();
    const exitCode = result.getByLabel("Exit code");
    await expect(exitCode).toHaveAttribute("aria-invalid", "true");
    const exitErrorId = await exitCode.getAttribute("aria-describedby");
    expect(exitErrorId).toMatch(
      /^completion-evidence-verification_results-.+-exit_code-error$/
    );
    await expect(page.locator(`#${exitErrorId}`)).toHaveText(
      "A passed command requires exit code 0."
    );
    expect(probe.requests).toHaveLength(0);

    await exitCode.fill("-0");
    await context.getByRole("button", { name: "Complete with summary" }).click();
    await expect(exitCode).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator(`#${exitErrorId}`)).toHaveText(
      "Use 0 instead of -0; negative zero cannot be preserved on the wire."
    );
    expect(probe.requests).toHaveLength(0);

    await exitCode.fill("0");
    await result.getByLabel("Observed at").fill("2026-09-04T12:30:00-04:00");
    await result.getByLabel("Observed commit").fill("abcdef1");
    const artifact = context.getByRole("group", { name: "Artifact reference 1" });
    await artifact.getByLabel("Artifact type").selectOption("pull_request");
    await artifact.getByLabel("Label").fill("Synthetic review artifact");
    await artifact.getByRole("textbox", { name: /^Reference / }).fill(artifactUrl);
    await addResult.click();
    resultRows = context.getByRole("group", { name: /^Verification result \d+$/ });
    const limitation = resultRows.nth(1);
    await limitation.getByLabel("Name").fill(limitationName);
    await limitation.getByLabel("Reported outcome").selectOption("inconclusive");
    await limitation.getByLabel("Result summary").fill(limitationSummary);
    await expect(context.locator(".evidence-budget")).toContainText("3/20 entries");
    await expect(context.locator(".evidence-budget")).toContainText("/32,768 bytes");

    await context.getByRole("button", { name: "Complete with summary" }).click();
    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(page.locator(".mutation-recovery")).toContainText(
      "Complete work · outcome unknown"
    );

    const firstRequest = JSON.parse(probe.requests[0]!) as {
      client_operation_id: string;
      completion_evidence: {
        verification_results: Array<{
          verification_type: string;
          name: string;
          outcome: string;
          summary: string;
          command: string;
          observed_at: string;
        }>;
        artifact_references: Array<{ reference: string }>;
      };
    };
    expect(firstRequest.client_operation_id).toMatch(UUID_PATTERN);
    expect(firstRequest.completion_evidence.verification_results).toHaveLength(2);
    expect(firstRequest.completion_evidence.verification_results[0]).toMatchObject({
      verification_type: "command",
      name: resultName,
      outcome: "passed",
      command,
      summary: resultSummary,
      observed_at: "2026-09-04T16:30:00Z"
    });
    expect(firstRequest.completion_evidence.verification_results[1]).toEqual({
      verification_type: "observation",
      name: limitationName,
      outcome: "inconclusive",
      summary: limitationSummary
    });
    expect(firstRequest.completion_evidence.artifact_references).toEqual([
      expect.objectContaining({ reference: artifactUrl })
    ]);
    expect(probe.responses[0]!.status).toBe(200);
    expect(probe.responses[0]!.body).not.toContain(firstRequest.client_operation_id);
    expect(JSON.parse(probe.responses[0]!.body)).toHaveProperty(
      "completion_evidence.verification_results.0.id"
    );

    await expect(commandInput).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await expect(page.locator("body")).not.toContainText(firstRequest.client_operation_id);
    const browserState = await page.evaluate(async () => ({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
      cookies: document.cookie,
      url: location.href,
      cacheNames: "caches" in globalThis ? await caches.keys() : []
    }));
    const serializedState = JSON.stringify(browserState);
    expect(serializedState).not.toContain(firstRequest.client_operation_id);
    expect(serializedState).not.toContain(command);
    expect(serializedState).not.toContain(artifactUrl);

    await page.locator(".mutation-recovery").getByRole("button", {
      name: "Retry exact request"
    }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    expect(probe.requests[1]).toBe(probe.requests[0]);
    expect(probe.responses[1]).toEqual(probe.responses[0]);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Done");
    expect(evidenceRequests).toHaveLength(0);

    await reopenFixture(client, work.id);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Pending");
    const emptyContext = await openTab(pane, "Context");
    await emptyContext.getByLabel("Checkpoint text").fill(
      "A browser completion with an untouched evidence editor."
    );
    await emptyContext.locator("details.completion-evidence-disclosure > summary").click();
    await expect(emptyContext.locator("fieldset.evidence-edit-row")).toHaveCount(0);
    await emptyContext.getByRole("button", { name: "Complete with summary" }).click();
    await expect.poll(() => probe.requests.length).toBe(3);
    expect(JSON.parse(probe.requests[2]!)).not.toHaveProperty("completion_evidence");
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Done");
    expect(evidenceRequests).toHaveLength(0);

    let failHistoryOnce = true;
    await page.route("**/api/mnemonic/**", async (route) => {
      const requestUrl = new URL(route.request().url());
      if (
        failHistoryOnce
        && route.request().method() === "GET"
        && requestUrl.pathname.endsWith(`/work-items/${work.id}/completion-evidence`)
      ) {
        failHistoryOnce = false;
        await route.fulfill({
          status: 503,
          headers: {
            "Cache-Control": "no-store, max-age=0, no-transform",
            "Content-Encoding": "identity",
            "Content-Type": "application/json",
            "X-DNS-Prefetch-Control": "off"
          },
          body: JSON.stringify({ detail: "Synthetic evidence history failure." })
        });
        return;
      }
      await route.continue();
    });

    let evidence = await openTab(pane, "Evidence");
    await expect(evidence.getByText("Synthetic evidence history failure.")).toBeVisible();
    await expect(pane.getByRole("tab", { name: /^Evidence/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(evidenceRequests).toHaveLength(1);
    const successfulHead = page.waitForResponse((response) => {
      const responseUrl = new URL(response.url());
      return response.request().method() === "GET"
        && response.status() === 200
        && responseUrl.pathname.endsWith(`/work-items/${work.id}/completion-evidence`);
    });
    await evidence.getByRole("button", { name: "Try again" }).click();
    const headResponse = await successfulHead;
    expect(await headResponse.headerValue("content-encoding")).toBe("identity");
    expect(await headResponse.headerValue("x-dns-prefetch-control")).toBe("off");
    await expect(evidence.locator("article.completion-episode")).toHaveCount(2);
    await expect(evidence.locator("article.completion-episode").nth(0)).toHaveClass(
      /is-current/
    );
    await expect(evidence.locator("article.completion-episode").nth(0)).toContainText(
      "No structured completion evidence recorded"
    );

    const activePendingBaseline = evidenceRequests.length;
    const activePending = await reopenFixture(client, work.id);
    await expect.poll(() => evidenceRequests.length).toBeGreaterThan(activePendingBaseline);
    await expect(evidence.locator(".evidence-reopened")).toContainText(
      "Work currently reopened"
    );
    await expect(evidence.locator("article.completion-episode.is-current")).toHaveCount(0);

    const activeCompletionBaseline = evidenceRequests.length;
    await completeFixtureWithoutEvidence(
      client,
      activePending,
      `phase11-active-recomplete-${suffix}`
    );
    await expect.poll(() => evidenceRequests.length).toBeGreaterThan(
      activeCompletionBaseline
    );
    await expect(evidence.locator("article.completion-episode").nth(0)).toHaveClass(
      /is-current/
    );

    await openTab(pane, "Context");
    await page.waitForTimeout(250);
    const inactiveBaseline = evidenceRequests.length;
    for (let index = 0; index < 9; index += 1) {
      const pending = await reopenFixture(client, work.id);
      await completeFixtureWithoutEvidence(
        client,
        pending,
        `phase11-page-${index}-${suffix}`
      );
    }
    await page.waitForTimeout(500);
    expect(evidenceRequests).toHaveLength(inactiveBaseline);

    evidence = await openTab(pane, "Evidence");
    await expect(evidence.locator("article.completion-episode")).toHaveCount(10);
    await expect(evidence.locator(".evidence-history-heading")).toContainText(
      "Structured evidence recorded for 1 of 12 completion episodes"
    );
    const loadOlder = evidence.getByRole("button", { name: "Load older completions" });
    await expect(loadOlder).toBeVisible();
    let injectedLiveDrift = false;
    await page.route("**/api/mnemonic/**", async (route) => {
      const requestUrl = new URL(route.request().url());
      if (
        !injectedLiveDrift
        && route.request().method() === "GET"
        && requestUrl.pathname.endsWith(`/work-items/${work.id}/completion-evidence`)
        && requestUrl.searchParams.has("cursor")
      ) {
        injectedLiveDrift = true;
        const response = await route.fetch();
        const continuation = await response.json() as CompletionEvidencePage;
        await route.fulfill({
          response,
          body: JSON.stringify({
            ...continuation,
            work_version: continuation.work_version + 2,
            current_completion_checkpoint_id: crypto.randomUUID()
          })
        });
        return;
      }
      await route.fallback();
    });
    await loadOlder.focus();
    await loadOlder.press("Enter");
    await expect(evidence.getByRole("alert")).toContainText(
      "Completion evidence changed while older history was loading."
    );
    await expect(evidence.locator("article.completion-episode")).toHaveCount(10);
    await expect(evidence.locator("article.completion-episode.is-current")).toHaveCount(1);
    await evidence.getByRole("button", { name: "Reload current history" }).click();
    await expect(evidence.getByRole("alert")).toHaveCount(0);
    await expect(loadOlder).toBeVisible();
    await loadOlder.press("Enter");
    await expect(evidence.locator("article.completion-episode")).toHaveCount(12);
    await expect(loadOlder).toHaveCount(0);

    const episodes = evidence.locator("article.completion-episode");
    await expect(episodes.nth(0)).toHaveClass(/is-current/);
    await expect(episodes.nth(0).locator(".section-label")).toHaveText(
      "CURRENT COMPLETION"
    );
    await expect(episodes.nth(1).locator(".section-label")).toHaveText(
      "PRIOR COMPLETION"
    );
    const structuredEpisode = episodes.filter({ hasText: resultName });
    await expect(structuredEpisode).toHaveCount(1);
    await expect(structuredEpisode.getByText(resultName, { exact: true })).toBeVisible();
    await expect(structuredEpisode.getByText(resultSummary, { exact: true })).toBeVisible();
    await expect(structuredEpisode.getByText(command, { exact: true })).toBeVisible();
    await expect(structuredEpisode.getByText(limitationName, { exact: true })).toBeVisible();
    await expect(structuredEpisode.getByText(limitationSummary, { exact: true })).toBeVisible();
    await expect(structuredEpisode.getByText("Reported outcome: Passed", {
      exact: true
    })).toBeVisible();
    await expect(structuredEpisode.getByText("Reported outcome: Inconclusive", {
      exact: true
    })).toBeVisible();
    await expect(evidence.locator(".completion-evidence-history > .authority-note")).toContainText(
      "untrusted, caller-reported historical assertions"
    );
    await expect(
      structuredEpisode.locator("img, script, iframe, object, embed")
    ).toHaveCount(0);
    const link = structuredEpisode.getByRole("link", {
      name: "Pull request on evidence.invalid (opens in a new tab)"
    });
    await expect(link).toHaveAttribute("href", artifactUrl);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(externalRequests).toEqual([]);

    const historyResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${work.id}/completion-evidence?limit=10`
    );
    expect(historyResponse.ok(), await historyResponse.text()).toBe(true);
    const history = await historyResponse.json() as CompletionEvidencePage;
    expect(history.total).toBe(12);
    expect(history.structured_completion_total).toBe(1);
    expect(history.next_cursor).not.toBeNull();
    expect(history.items).toHaveLength(10);
    expect(history.items[0]!.verification_results).toEqual([]);
    expect(history.items[0]!.artifact_references).toEqual([]);

    destination = await createFixture(
      client,
      `Phase 11 canonical destination ${suffix}`,
      `destination-${suffix}`
    );
    await mergeFixture(client, work.id, destination.id, suffix);
    merged = true;
    await expect(pane.locator(".detail-identity .operational-badge.duplicate")).toHaveText(
      "Duplicate"
    );
    await expect(evidence.locator(".migration-warning")).toContainText(
      destination.id
    );
    await expect(evidence.locator("article.completion-episode.is-current")).toHaveCount(0);
    await expect(episodes.nth(0).locator(".section-label")).toHaveText(
      "PRIOR COMPLETION"
    );

    const aliasHistoryResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${work.id}/completion-evidence?limit=10`
    );
    expect(aliasHistoryResponse.ok(), await aliasHistoryResponse.text()).toBe(true);
    const aliasHistory = await aliasHistoryResponse.json() as CompletionEvidencePage;
    expect(aliasHistory.is_duplicate).toBe(true);
    expect(aliasHistory.canonical_work_item_id).toBe(destination.id);
    expect(aliasHistory.current_completion_checkpoint_id).toBeNull();
    expect(aliasHistory.total).toBe(12);

    const destinationHistoryResponse = await client.get(
      `/api/v1/projects/${state.projectId}/work-items/${destination.id}/completion-evidence?limit=10`
    );
    expect(destinationHistoryResponse.ok(), await destinationHistoryResponse.text()).toBe(true);
    const destinationHistory = await destinationHistoryResponse.json() as CompletionEvidencePage;
    expect(destinationHistory.is_duplicate).toBe(false);
    expect(destinationHistory.total).toBe(0);

    await pane.getByRole("button", { name: "Open canonical work" }).click();
    await expect(pane.locator(".detail-title")).toHaveText(destination.title);
    evidence = pane.locator("#detail-panel-evidence");
    await expect(evidence.getByText("No completion episodes recorded.")).toBeVisible();
    await expect(evidence.locator(".migration-warning")).toHaveCount(0);
    expect(externalRequests).toEqual([]);
  } finally {
    if (!merged) {
      await deleteFixture(client, work.id);
      if (destination) await deleteFixture(client, destination.id);
    }
    await client.dispose();
  }
});
