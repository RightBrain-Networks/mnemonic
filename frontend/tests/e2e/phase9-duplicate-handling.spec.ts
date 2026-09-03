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

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type WorkItem = {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  status: string;
  version: number;
  updated_at: string;
};

type MergeRevision = {
  work_version: number;
  context_checkpoint_id: string;
  work_event_count: number;
};

type IdentityPointer = {
  id: string;
  title: string;
  status: string;
};

type WorkContext = {
  work_item: WorkItem;
  merge_review_revision: MergeRevision;
  canonical: {
    is_duplicate: boolean;
    direct_destination: IdentityPointer | null;
    canonical_work_item: IdentityPointer;
    path: IdentityPointer[];
    duplicate_member_count: number;
  };
  duplicate_member_total: number;
  event_total: number;
};

type MergeInput = {
  destination_work_item_id: string;
  reviewed_source_revision: MergeRevision;
  reviewed_destination_revision: MergeRevision;
  rationale: string;
  merged_by_client: string;
  merged_by_session_id: string;
  merged_by_model: string | null;
  client_operation_id: string;
};

type MergeResult = {
  merge: {
    id: string;
    project_id: string;
    source_work_item_id: string;
    destination_work_item_id: string;
    resulting_source_work_version: number;
    resulting_destination_work_version: number;
    created_at: string;
  };
  source_work_item: WorkItem;
  destination_work_item: WorkItem;
  direct_destination: IdentityPointer;
  canonical_work_item: IdentityPointer;
  relationship_events: Array<{ created_at: string; work_item_id: string }>;
  merge_events: Array<{ created_at: string; work_item_id: string; body: string }>;
};

type MergeRequest = {
  method: string;
  url: string;
  body: string;
};

type MergeResponse = {
  status: number;
  body: string;
};

type LostResponseProbe = {
  requests: MergeRequest[];
  responses: MergeResponse[];
};

type Invalidation = {
  type: "invalidate";
  revision: number;
  scope: "projects" | "work-items";
};

function e2eCredentials(): { apiURL: string; apiKey: string } {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) {
    throw new Error("Run this test through the disposable E2E stack.");
  }
  return { apiURL, apiKey };
}

async function apiClient(): Promise<APIRequestContext> {
  const { apiURL, apiKey } = e2eCredentials();
  return await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${apiKey}`,
      Accept: "application/json"
    }
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
      priority: 37,
      initial_checkpoint: {
        prompt,
        source_client: "playwright-api",
        source_session_id: sessionId,
        source_model: null,
        source_session_url: null,
        repository_branch: null,
        verified_against: null,
        tags: ["phase-9", "duplicate-handling"],
        source_metadata: {}
      }
    }
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { work_item: WorkItem }).work_item;
}

async function getContext(
  client: APIRequestContext,
  workItemId: string
): Promise<WorkContext> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workItemId}/context`
      + "?recent_limit=5&recent_event_limit=10"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkContext;
}

function mergeInput(
  source: WorkContext,
  destination: WorkContext,
  rationale: string,
  sessionId: string,
  mergedByClient = "playwright-api"
): MergeInput {
  return {
    destination_work_item_id: destination.work_item.id,
    reviewed_source_revision: source.merge_review_revision,
    reviewed_destination_revision: destination.merge_review_revision,
    rationale,
    merged_by_client: mergedByClient,
    merged_by_session_id: sessionId,
    merged_by_model: null,
    client_operation_id: crypto.randomUUID()
  };
}

async function mergeDirect(
  client: APIRequestContext,
  sourceId: string,
  destinationId: string,
  rationale: string,
  sessionId: string
): Promise<MergeResult> {
  const [source, destination] = await Promise.all([
    getContext(client, sourceId),
    getContext(client, destinationId)
  ]);
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${sourceId}/merge`,
    { data: mergeInput(source, destination, rationale, sessionId) }
  );
  expect(response.status(), await response.text()).toBe(201);
  return await response.json() as MergeResult;
}

async function openDashboard(page: Page): Promise<void> {
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.locator(".sync-status")).toHaveText("Live updates");
}

function resultFor(page: Page, workItemId: string): Locator {
  return page.locator(`.search-result[data-work-item-id="${workItemId}"]`);
}

async function searchFor(page: Page, query: string, workItemId: string): Promise<Locator> {
  await page.getByLabel("Search work items").fill(query);
  const result = resultFor(page, workItemId);
  await expect(result).toHaveCount(1);
  return result;
}

async function openSearchResult(
  page: Page,
  query: string,
  workItemId: string,
  dialogName: "Work context" | "Duplicate audit" = "Work context"
): Promise<Locator> {
  const result = await searchFor(page, query, workItemId);
  await result.locator("article.work-item-card button.card-title").click();
  const dialog = page.getByRole("dialog", { name: dialogName });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function openMergeReview(
  page: Page,
  sourceDetail: Locator,
  destinationId: string
): Promise<{ dialog: Locator; destinationButton: Locator }> {
  await sourceDetail.getByRole("button", { name: /Merge as duplicate/ }).click();
  const dialog = page.getByRole("dialog", { name: "Merge duplicate work" });
  await expect(dialog).toBeVisible();
  const search = dialog.getByLabel("Find a canonical destination");
  await search.fill(destinationId);
  const destinationButton = dialog
    .getByRole("group", { name: "Canonical merge destinations" })
    .getByRole("button")
    .filter({ hasText: destinationId });
  await expect(destinationButton).toHaveCount(1);
  await destinationButton.focus();
  await expect(destinationButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog.locator("[data-direction='source']")).toBeVisible();
  await expect(dialog.locator("[data-direction='destination']")).toBeVisible();
  return { dialog, destinationButton };
}

async function confirmMerge(dialog: Locator, rationale: string): Promise<void> {
  await dialog.getByLabel("Merge rationale").fill(rationale);
  await dialog.getByLabel(/I understand this permanently makes the source immutable/).check();
  await expect(dialog.getByRole("button", { name: "Permanently merge source" })).toBeEnabled();
}

async function installCommittedResponseLoss(
  page: Page,
  sourceId: string
): Promise<LostResponseProbe> {
  const probe: LostResponseProbe = { requests: [], responses: [] };
  await page.route(
    `**/api/mnemonic/projects/${state.projectId}/work-items/${sourceId}/merge`,
    async (route) => {
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
      const responseBody = await response.text();
      probe.responses.push({ status: response.status(), body: responseBody });
      if (probe.requests.length === 1) {
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              code: "database_unavailable",
              message: "The committed merge response could not be delivered.",
              context: {}
            }
          })
        });
        return;
      }
      await route.fulfill({ response, body: responseBody });
    }
  );
  return probe;
}

function operationId(probe: LostResponseProbe, index = 0): string {
  const body = JSON.parse(probe.requests[index]!.body) as { client_operation_id?: unknown };
  expect(typeof body.client_operation_id).toBe("string");
  expect(body.client_operation_id).toMatch(UUID_PATTERN);
  expect(
    probe.requests[index]!.body.split(String(body.client_operation_id)).length - 1
  ).toBe(1);
  return String(body.client_operation_id);
}

function expectExactReplay(probe: LostResponseProbe): void {
  expect(probe.requests).toHaveLength(2);
  expect(probe.requests[1]).toEqual(probe.requests[0]);
  expect(probe.responses).toHaveLength(2);
  expect(probe.responses[1]).toEqual(probe.responses[0]);
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

function workInvalidationCount(messages: Invalidation[]): number {
  for (const message of messages) {
    expect(Object.keys(message).sort()).toEqual(["revision", "scope", "type"]);
  }
  return messages.filter((message) => message.scope === "work-items").length;
}

test("a hostile-title merge preserves direction and recovers one exact durable effect", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = [
    testInfo.project.name,
    state.runId.slice(0, 8),
    String(testInfo.retry),
    crypto.randomUUID().slice(0, 8)
  ].join("-");
  const hostileTitle = `Phase 9 \u2067العربية\u2069 \u202Edestination\u202C zero\u200Bwidth ${suffix}`;
  const sourcePrompt = `Exact source-owned audit context ${suffix}.`;
  const destinationPrompt = `Canonical-only destination context ${suffix}.`;
  const rationale = `These visually identical records are one objective — \u2067دليل\u2069 ${suffix}.`;
  const client = await apiClient();
  const source = await createWork(
    client,
    hostileTitle,
    `Source summary that must become immutable ${suffix}.`,
    sourcePrompt,
    `phase9-hostile-source-${suffix}`
  );
  const destination = await createWork(
    client,
    hostileTitle,
    `Destination summary that must remain canonical ${suffix}.`,
    destinationPrompt,
    `phase9-hostile-destination-${suffix}`
  );
  const sourceBefore = await getContext(client, source.id);
  const destinationBefore = await getContext(client, destination.id);
  const invalidations = captureInvalidations(page);

  try {
    await openDashboard(page);
    const detail = await openSearchResult(page, source.id, source.id);
    const { dialog, destinationButton } = await openMergeReview(
      page,
      detail,
      destination.id
    );
    const sourcePanel = dialog.locator("[data-direction='source']");
    const destinationPanel = dialog.locator("[data-direction='destination']");

    expect(await dialog.locator("[data-direction]").evaluateAll((panels) => (
      panels.map((panel) => panel.getAttribute("data-direction"))
    ))).toEqual(["source", "destination"]);
    await expect(sourcePanel).toHaveAccessibleName(/^SOURCE — BECOMES IMMUTABLE/);
    await expect(destinationPanel).toHaveAccessibleName(/^DESTINATION — REMAINS CANONICAL/);
    await expect(sourcePanel.locator(".merge-full-id")).toHaveText(source.id);
    await expect(destinationPanel.locator(".merge-full-id")).toHaveText(destination.id);
    for (const panel of [sourcePanel, destinationPanel]) {
      const title = panel.locator("h3 bdi");
      await expect(title).toHaveText(hostileTitle);
      await expect(title).toHaveAttribute("dir", "auto");
      expect(await title.evaluate((element) => getComputedStyle(element).unicodeBidi)).toBe(
        "isolate"
      );
    }

    await destinationButton.focus();
    await page.keyboard.press("Tab");
    await expect(dialog.getByLabel("Merge rationale")).toBeFocused();
    await confirmMerge(dialog, rationale);

    const probe = await installCommittedResponseLoss(page, source.id);
    await dialog.getByRole("button", { name: "Permanently merge source" }).click();
    await expect.poll(() => probe.requests.length).toBe(1);
    await expect(dialog.locator(".mutation-recovery")).toContainText(
      "The merge outcome is unknown."
    );
    await expect(dialog.getByLabel("Find a canonical destination")).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Close merge dialog" })).toBeDisabled();

    const retainedOperationId = operationId(probe);
    expect(probe.responses[0]!.status).toBe(201);
    expect(probe.responses[0]!.body).not.toContain(retainedOperationId);
    expect(probe.requests[0]!.body).not.toContain("lease_token");
    const firstResult = JSON.parse(probe.responses[0]!.body) as MergeResult;
    expect(firstResult.merge).toMatchObject({
      project_id: state.projectId,
      source_work_item_id: source.id,
      destination_work_item_id: destination.id,
      resulting_source_work_version: sourceBefore.work_item.version + 1,
      resulting_destination_work_version: destinationBefore.work_item.version + 1
    });
    expect(firstResult.source_work_item.updated_at).toBe(firstResult.merge.created_at);
    expect(firstResult.destination_work_item.updated_at).toBe(firstResult.merge.created_at);
    expect(firstResult.direct_destination.id).toBe(destination.id);
    expect(firstResult.canonical_work_item).toEqual(firstResult.direct_destination);
    expect(firstResult.relationship_events).toHaveLength(2);
    expect(firstResult.merge_events).toHaveLength(2);
    for (const event of [...firstResult.relationship_events, ...firstResult.merge_events]) {
      expect(event.created_at).toBe(firstResult.merge.created_at);
    }
    expect(firstResult.merge_events.map((event) => event.body)).toEqual([
      rationale,
      rationale
    ]);

    await expect.poll(() => workInvalidationCount(invalidations)).toBe(1);
    const committed = await getContext(client, source.id);
    expect(committed.canonical.is_duplicate).toBe(true);
    expect(committed.canonical.canonical_work_item.id).toBe(destination.id);
    expect(committed.work_item.version).toBe(sourceBefore.work_item.version + 1);

    await dialog.getByRole("button", { name: "Retry exact pending merge" }).click();
    await expect.poll(() => probe.requests.length).toBe(2);
    await expect.poll(() => probe.responses.length).toBe(2);
    expectExactReplay(probe);
    await page.waitForTimeout(400);
    expect(workInvalidationCount(invalidations)).toBe(1);

    const replayed = await getContext(client, source.id);
    expect(replayed.work_item.version).toBe(committed.work_item.version);
    expect(replayed.work_item.updated_at).toBe(committed.work_item.updated_at);
    expect(replayed.event_total).toBe(committed.event_total);
    const audit = page.getByRole("dialog", { name: "Duplicate audit" });
    await expect(audit).toBeVisible();
    await expect(audit.locator(".detail-title")).toHaveText(hostileTitle);
    await expect(audit.locator(".prompt-body")).toHaveText(sourcePrompt);
    await expect(audit.locator(".prompt-body")).not.toContainText(destinationPrompt);
    await expect(audit.locator(".duplicate-direction-grid > div").nth(0)).toContainText(
      destination.id
    );
    await expect(audit.locator(".duplicate-direction-grid > div").nth(1)).toContainText(
      destination.id
    );
    await expect(audit.locator(".duplicate-merge-fact")).toContainText(rationale);
    await expect(audit.getByRole("button", { name: /Merge as duplicate/ })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(retainedOperationId);

    await audit.getByRole("button", { name: "Close dialog" }).click();
    const canonicalResult = await searchFor(page, source.id, destination.id);
    await expect(resultFor(page, source.id)).toHaveCount(0);
    await expect(page.locator(".result-count")).toHaveText("1 work record");
    await expect(canonicalResult.locator(".matched-member")).toContainText(
      "Matched duplicate member"
    );
    await expect(canonicalResult.locator(".matched-member")).toContainText(source.id);
    await expect(canonicalResult.locator(".matched-member bdi")).toHaveText(hostileTitle);
  } finally {
    await client.dispose();
  }
});

test("a root with an incoming alias can merge again and regroup every audit under the new root", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${testInfo.retry}-${crypto.randomUUID().slice(0, 8)}`;
  const groupPrefix = `Phase 9 regroup ${suffix}`;
  const client = await apiClient();
  const firstAlias = await createWork(
    client,
    `${groupPrefix} A`,
    `First retained audit ${suffix}.`,
    `First alias-owned checkpoint ${suffix}.`,
    `phase9-regroup-a-${suffix}`
  );
  const intermediateRoot = await createWork(
    client,
    `${groupPrefix} B`,
    `Intermediate canonical root ${suffix}.`,
    `Intermediate root-owned checkpoint ${suffix}.`,
    `phase9-regroup-b-${suffix}`
  );
  const finalRoot = await createWork(
    client,
    `${groupPrefix} C`,
    `Final canonical root ${suffix}.`,
    `Final root-owned checkpoint ${suffix}.`,
    `phase9-regroup-c-${suffix}`
  );
  await mergeDirect(
    client,
    firstAlias.id,
    intermediateRoot.id,
    `First grouping decision ${suffix}.`,
    `phase9-regroup-first-${suffix}`
  );

  try {
    await openDashboard(page);
    const detail = await openSearchResult(
      page,
      intermediateRoot.id,
      intermediateRoot.id
    );
    const { dialog } = await openMergeReview(page, detail, finalRoot.id);
    await confirmMerge(dialog, `Move the established duplicate group to its final root ${suffix}.`);
    await dialog.getByRole("button", { name: "Permanently merge source" }).click();

    let audit = page.getByRole("dialog", { name: "Duplicate audit" });
    await expect(audit).toBeVisible();
    await expect(audit.locator(".detail-title")).toHaveText(intermediateRoot.title);
    await expect(audit.locator(".duplicate-direction-grid > div").nth(0)).toContainText(
      finalRoot.id
    );
    await expect(audit.locator(".duplicate-direction-grid > div").nth(1)).toContainText(
      finalRoot.id
    );
    await expect(audit.locator(".duplicate-audit-panel")).toContainText(
      "2 immutable duplicate audit records"
    );

    await audit.getByRole("button", { name: "View duplicate group" }).click();
    const groupStatus = page.locator(".duplicate-group-filter");
    await expect(groupStatus).toContainText("Canonical group");
    await expect(groupStatus).toContainText(finalRoot.id);
    await expect(page.getByRole("radio", { name: "All records" })).toBeChecked();
    await expect(page.locator(".result-count")).toHaveText("3 work records");
    for (const id of [firstAlias.id, intermediateRoot.id, finalRoot.id]) {
      await expect(resultFor(page, id)).toHaveCount(1);
    }
    await expect(resultFor(page, firstAlias.id).locator(".operational-badge.duplicate"))
      .toHaveText("Duplicate");
    await expect(resultFor(page, intermediateRoot.id).locator(".operational-badge.duplicate"))
      .toHaveText("Duplicate");
    await expect(resultFor(page, finalRoot.id).locator(".operational-badge.duplicate"))
      .toHaveCount(0);

    await resultFor(page, firstAlias.id)
      .locator("article.work-item-card button.card-title")
      .click();
    audit = page.getByRole("dialog", { name: "Duplicate audit" });
    await expect(audit.locator(".detail-title")).toHaveText(firstAlias.title);
    await expect(audit.locator(".prompt-body")).toHaveText(
      `First alias-owned checkpoint ${suffix}.`
    );
    await expect(audit.locator(".duplicate-direction-grid > div").nth(0)).toContainText(
      intermediateRoot.id
    );
    await expect(audit.locator(".duplicate-direction-grid > div").nth(1)).toContainText(
      finalRoot.id
    );
    const path = audit.getByRole("list", { name: "Canonical merge path" }).getByRole("listitem");
    await expect(path).toHaveCount(2);
    await expect(path.nth(0)).toContainText(intermediateRoot.id);
    await expect(path.nth(1)).toContainText(finalRoot.id);
    await expect(audit.getByRole("button", { name: "Copy audit ID" })).toBeVisible();
    await expect(audit.getByRole("button", { name: "Copy canonical ID" })).toHaveCount(0);
    await expect(audit.getByRole("button", { name: "Edit work item" })).toHaveCount(0);
    await expect(audit.getByRole("button", { name: "Delete work item" })).toHaveCount(0);
    await expect(audit.getByLabel("Checkpoint text")).toHaveCount(0);

    await audit.getByRole("button", { name: "Close dialog" }).click();
    await page.getByText("Canonical only", { exact: true }).click();
    await expect(page.getByRole("radio", { name: "Canonical only" })).toBeChecked();
    await expect(groupStatus).toHaveCount(0);
    const rootNode = page.locator(
      `.hierarchy-node[data-depth="0"][data-work-item-id="${finalRoot.id}"]`
    );
    await expect(rootNode).toBeVisible();
    await expect(page.locator(`.hierarchy-node[data-work-item-id="${firstAlias.id}"]`))
      .toHaveCount(0);
    await expect(page.locator(`.hierarchy-node[data-work-item-id="${intermediateRoot.id}"]`))
      .toHaveCount(0);
    await expect(rootNode.getByRole("list", { name: "Branch totals" })).toContainText(
      "2 merged duplicate audit records"
    );
  } finally {
    await client.dispose();
  }
});

test("an active source lease disables merge and its capability cannot cross the browser proxy", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${testInfo.retry}-${crypto.randomUUID().slice(0, 8)}`;
  const client = await apiClient();
  const source = await createWork(
    client,
    `Phase 9 leased source ${suffix}`,
    `Leased source summary ${suffix}.`,
    `Leased source context ${suffix}.`,
    `phase9-lease-source-${suffix}`
  );
  const destination = await createWork(
    client,
    `Phase 9 lease destination ${suffix}`,
    `Lease destination summary ${suffix}.`,
    `Lease destination context ${suffix}.`,
    `phase9-lease-destination-${suffix}`
  );
  const holderSession = `phase9-active-holder-${suffix}`;
  const claim = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${source.id}/claim`,
    {
      data: {
        holder_client: "claude-code",
        holder_session_id: holderSession,
        claim_request_id: crypto.randomUUID()
      }
    }
  );
  expect(claim.status(), await claim.text()).toBe(200);
  const leaseToken = (await claim.json() as { lease_token: string }).lease_token;
  expect(leaseToken.length).toBeGreaterThan(20);

  try {
    const [sourceContext, destinationContext] = await Promise.all([
      getContext(client, source.id),
      getContext(client, destination.id)
    ]);
    await openDashboard(page);
    await page.getByRole("button", { name: "Active", exact: true }).click();
    const detail = await openSearchResult(page, source.id, source.id);
    await expect(detail.getByLabel("Active work lease")).toContainText(holderSession);
    const mergeButton = detail.getByRole("button", { name: /Merge as duplicate/ });
    await expect(mergeButton).toBeDisabled();
    await expect(mergeButton).toHaveAccessibleDescription(
      "Release the source’s active lease, or wait for it to expire, before merging in the browser."
    );
    await expect(page.locator("body")).not.toContainText(leaseToken);

    const validBody = mergeInput(
      sourceContext,
      destinationContext,
      `Tokenless browser defense-in-depth merge ${suffix}.`,
      `phase9-browser-lease-${suffix}`,
      "dashboard"
    );
    const nestedDenial = await page.evaluate(async ({ projectId, sourceId, body, token }) => {
      const poisonedBody = {
        ...body,
        reviewed_source_revision: {
          ...body.reviewed_source_revision,
          nested: [{ harmless: true }, { LeAsE_ToKeN: token }]
        }
      };
      const response = await fetch(
        `/api/mnemonic/projects/${projectId}/work-items/${sourceId}/merge`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(poisonedBody)
        }
      );
      return { status: response.status, text: await response.text() };
    }, {
      projectId: state.projectId,
      sourceId: source.id,
      body: validBody,
      token: leaseToken
    });
    expect(nestedDenial.status).toBe(400);
    expect(JSON.parse(nestedDenial.text)).toEqual({
      detail: "The request body contains an unsupported field: LeAsE_ToKeN."
    });
    expect(nestedDenial.text).not.toContain(leaseToken);

    const tokenlessDenial = await page.evaluate(async ({ projectId, sourceId, body }) => {
      const response = await fetch(
        `/api/mnemonic/projects/${projectId}/work-items/${sourceId}/merge`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, client_operation_id: crypto.randomUUID() })
        }
      );
      return { status: response.status, body: await response.json() as unknown };
    }, { projectId: state.projectId, sourceId: source.id, body: validBody });
    expect(tokenlessDenial).toMatchObject({
      status: 409,
      body: {
        detail: {
          code: "lease_token_mismatch",
          message: "A matching lease token is required for this operation."
        }
      }
    });
    expect(JSON.stringify(tokenlessDenial)).not.toContain(leaseToken);

    const unchanged = await getContext(client, source.id);
    expect(unchanged.merge_review_revision).toEqual(sourceContext.merge_review_revision);
    expect(unchanged.canonical.is_duplicate).toBe(false);
  } finally {
    const released = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${source.id}/release-claim`,
      { data: { lease_token: leaseToken } }
    );
    expect(released.ok(), await released.text()).toBe(true);
    await client.dispose();
  }
});

test("a drifted merge review requires explicit refetch and a new operation UUID", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${testInfo.retry}-${crypto.randomUUID().slice(0, 8)}`;
  const rationale = `Fresh review after destination drift ${suffix}.`;
  const client = await apiClient();
  const source = await createWork(
    client,
    `Phase 9 drift source ${suffix}`,
    `Drift source summary ${suffix}.`,
    `Drift source context ${suffix}.`,
    `phase9-drift-source-${suffix}`
  );
  const destination = await createWork(
    client,
    `Phase 9 drift destination ${suffix}`,
    `Original destination summary ${suffix}.`,
    `Drift destination context ${suffix}.`,
    `phase9-drift-destination-${suffix}`
  );
  const capturedBodies: MergeInput[] = [];

  page.on("request", (browserRequest) => {
    const expectedSuffix = `/api/mnemonic/projects/${state.projectId}/work-items/${source.id}/merge`;
    if (browserRequest.method() !== "POST" || !browserRequest.url().endsWith(expectedSuffix)) {
      return;
    }
    capturedBodies.push(browserRequest.postDataJSON() as MergeInput);
  });

  try {
    await openDashboard(page);
    const detail = await openSearchResult(page, source.id, source.id);
    const { dialog } = await openMergeReview(page, detail, destination.id);
    const firstDestinationRevision = (await getContext(
      client,
      destination.id
    )).merge_review_revision;

    const patched = await client.patch(
      `/api/v1/projects/${state.projectId}/work-items/${destination.id}`,
      {
        data: {
          expected_version: destination.version,
          summary: `Authoritative destination summary after review ${suffix}.`,
          actor: {
            actor_client: "playwright-api",
            actor_session_id: `phase9-drift-writer-${suffix}`,
            actor_model: null
          },
          client_operation_id: crypto.randomUUID()
        }
      }
    );
    expect(patched.ok(), await patched.text()).toBe(true);
    const patchedDestination = await patched.json() as WorkItem;
    expect(patchedDestination.version).toBe(destination.version + 1);

    await confirmMerge(dialog, rationale);
    await dialog.getByRole("button", { name: "Permanently merge source" }).click();
    await expect.poll(() => capturedBodies.length).toBe(1);
    const staleNotice = dialog.getByRole("alert").filter({
      hasText: "The reviewed source or destination changed."
    });
    await expect(staleNotice).toBeVisible();
    await expect(dialog.getByLabel(/I understand this permanently makes the source immutable/))
      .toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Permanently merge source" }))
      .toBeDisabled();
    await expect(dialog.getByLabel("Merge rationale")).toHaveValue(rationale);
    expect(capturedBodies[0]!.reviewed_destination_revision).toEqual(
      firstDestinationRevision
    );

    await staleNotice.getByRole("button", { name: "Refetch both contexts" }).click();
    await expect(dialog.locator("[data-direction='destination']")).toBeVisible();
    const acknowledgement = dialog.getByLabel(
      /I understand this permanently makes the source immutable/
    );
    await expect(acknowledgement).toBeEnabled();
    await expect(acknowledgement).not.toBeChecked();
    await expect(dialog.getByLabel("Merge rationale")).toHaveValue(rationale);
    await acknowledgement.check();
    await dialog.getByRole("button", { name: "Permanently merge source" }).click();

    await expect.poll(() => capturedBodies.length).toBe(2);
    const audit = page.getByRole("dialog", { name: "Duplicate audit" });
    await expect(audit).toBeVisible();
    await expect(audit.locator(".duplicate-direction-grid > div").nth(1)).toContainText(
      destination.id
    );
    const [staleRequest, freshRequest] = capturedBodies;
    expect(staleRequest!.client_operation_id).toMatch(UUID_PATTERN);
    expect(freshRequest!.client_operation_id).toMatch(UUID_PATTERN);
    expect(freshRequest!.client_operation_id).not.toBe(staleRequest!.client_operation_id);
    expect(freshRequest!.reviewed_source_revision).toEqual(
      staleRequest!.reviewed_source_revision
    );
    expect(freshRequest!.reviewed_destination_revision.work_version).toBe(
      patchedDestination.version
    );
    expect(freshRequest!.reviewed_destination_revision).not.toEqual(
      staleRequest!.reviewed_destination_revision
    );
    expect(freshRequest!.rationale).toBe(rationale);
    await expect(page.locator("body")).not.toContainText(staleRequest!.client_operation_id);
    await expect(page.locator("body")).not.toContainText(freshRequest!.client_operation_id);
  } finally {
    await client.dispose();
  }
});
