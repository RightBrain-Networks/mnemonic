import { readFile } from "node:fs/promises";
import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type CreatedWork = {
  work_item: { id: string; version: number };
  initial_checkpoint: { id: string };
};

async function createFixtureWork(
  client: APIRequestContext,
  projectId: string,
  title: string,
  sessionId: string
): Promise<CreatedWork> {
  const response = await client.post(`/api/v1/projects/${projectId}/work-items`, {
    data: {
      title,
      summary: "Disposable Phase 5 browser acceptance fixture.",
      status: "pending",
      priority: 17,
      initial_checkpoint: {
        prompt: `Initial context for ${title}.`,
        source_client: "playwright-api",
        source_session_id: sessionId,
        tags: ["phase-5", "events"],
        source_metadata: {}
      }
    }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as CreatedWork;
}

async function appendProgress(
  client: APIRequestContext,
  projectId: string,
  workId: string,
  body: string,
  sessionId: string
): Promise<void> {
  const response = await client.post(
    `/api/v1/projects/${projectId}/work-items/${workId}/events`,
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

async function hideFixtureWork(
  client: APIRequestContext,
  projectId: string,
  workId: string
): Promise<void> {
  if (!workId) return;
  const current = await client.get(`/api/v1/projects/${projectId}/work-items/${workId}`);
  if (!current.ok()) return;
  const detail = await current.json() as { work_item: { version: number } };
  await client.post(`/api/v1/projects/${projectId}/work-items/${workId}/delete`, {
    data: { expected_version: detail.work_item.version }
  });
}

test("activity is live, safe text, actor-attributed, and usable at both viewports", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}`;
  const title = `Phase 5 activity ${suffix}`;
  const counterpartTitle = `Phase 5 counterpart ${suffix}`;
  const hostile = `<img src=x onerror="globalThis.phase5Pwned=true"><script>globalThis.phase5Pwned=true</script> ${suffix}`;
  const dashboardProgress = `Dashboard progress ${suffix}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });

  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Disposable work-event dashboard acceptance fixture.",
        status: "pending",
        priority: 42,
        initial_checkpoint: {
          prompt: `Initial checkpoint text must stay out of activity rows for ${suffix}.`,
          source_client: "playwright-api",
          source_session_id: `phase5-${suffix}`,
          tags: ["phase-5", "events"],
          source_metadata: {}
        }
      }
    });
    expect(created.ok(), await created.text()).toBe(true);
    const workId = (await created.json() as { work_item: { id: string } }).work_item.id;
    const counterpartResponse = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title: counterpartTitle,
        summary: "Counterpart used to verify actor-bearing relationship removal.",
        status: "pending",
        priority: 1,
        initial_checkpoint: {
          prompt: `Counterpart context for ${suffix}.`,
          source_client: "playwright-api",
          source_session_id: `phase5-counterpart-${suffix}`,
          tags: ["phase-5"],
          source_metadata: {}
        }
      }
    });
    expect(counterpartResponse.ok(), await counterpartResponse.text()).toBe(true);
    const counterpartId = (
      await counterpartResponse.json() as { work_item: { id: string } }
    ).work_item.id;
    const relationshipResponse = await client.post(`/api/v1/projects/${state.projectId}/relationships`, {
      data: {
        relationship_type: "related",
        source_work_item_id: workId,
        target_work_item_id: counterpartId,
        created_by_client: "playwright-api",
        created_by_session_id: `phase5-${suffix}`,
        created_by_model: null,
        context_checkpoint_id: null
      }
    });
    expect(relationshipResponse.ok(), await relationshipResponse.text()).toBe(true);
    const relationshipId = (
      await relationshipResponse.json() as { relationship: { id: string } }
    ).relationship.id;

    const eventRequests: unknown[] = [];
    const patchRequests: unknown[] = [];
    const relationshipDeleteRequests: unknown[] = [];
    const workDeleteRequests: unknown[] = [];
    page.on("request", (browserRequest) => {
      const url = browserRequest.url();
      if (browserRequest.method() === "POST" && url.endsWith(`/work-items/${workId}/events`)) {
        eventRequests.push(browserRequest.postDataJSON());
      } else if (browserRequest.method() === "PATCH" && url.endsWith(`/work-items/${workId}`)) {
        patchRequests.push(browserRequest.postDataJSON());
      } else if (browserRequest.method() === "DELETE" && url.endsWith(`/relationships/${relationshipId}`)) {
        relationshipDeleteRequests.push(browserRequest.postDataJSON());
      } else if (browserRequest.method() === "POST" && url.endsWith(`/work-items/${workId}/delete`)) {
        workDeleteRequests.push(browserRequest.postDataJSON());
      }
    });

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    const pane = await selectWork(page, title);

    // Activity lives in its own tab of the detail pane; the panel is mounted only while selected.
    const activity = (await openTab(pane, "Activity")).locator(".event-timeline");
    await expect(activity.getByRole("heading", { name: "Activity" })).toBeVisible();
    await expect(activity.locator(".event-list").getByText("Created work", { exact: true })).toBeVisible();
    await expect(activity.locator(".event-list")).not.toContainText("Initial checkpoint text must stay out of activity rows");

    const external = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${workId}/events`,
      {
        data: {
          event_type: "progress",
          body: hostile,
          metadata: {},
          actor: {
            actor_client: "playwright-api",
            actor_session_id: `external-${suffix}`,
            actor_model: null
          }
        }
      }
    );
    expect(external.ok(), await external.text()).toBe(true);

    const hostileRow = activity.locator("article.work-event").filter({ hasText: hostile });
    await expect(hostileRow).toHaveCount(1);
    await expect(hostileRow.locator(".work-event-body")).toHaveText(hostile);
    await expect(hostileRow.locator("img, script")).toHaveCount(0);
    expect(await page.evaluate(() => (globalThis as typeof globalThis & { phase5Pwned?: boolean }).phase5Pwned)).toBeUndefined();

    await activity.getByLabel("Progress text").fill(dashboardProgress);
    await activity.getByRole("button", { name: "Add progress update" }).click();
    await expect(activity.locator("article.work-event").filter({ hasText: dashboardProgress })).toHaveCount(1);
    expect(eventRequests).toHaveLength(1);
    expect(eventRequests[0]).toMatchObject({
      event_type: "progress",
      body: dashboardProgress,
      metadata: {},
      actor: { actor_client: "dashboard" }
    });
    expect((eventRequests[0] as { actor: { actor_session_id?: string } }).actor.actor_session_id).toBeTruthy();
    expect(JSON.stringify(eventRequests[0])).not.toContain("lease_token");

    await activity.getByLabel("Event type").selectOption("progress");
    await expect(activity.locator("article.work-event")).toHaveCount(2);
    await activity.getByLabel("Event type").selectOption("");
    await expect(activity.locator(".event-list").getByText("Created work", { exact: true })).toBeVisible();
    await expect(activity.locator("article.work-event").filter({ hasText: counterpartTitle })).toHaveCount(1);

    // Edit is inline: it replaces the Context tab body until the form is saved or cancelled.
    await pane.getByRole("button", { name: "Edit work item" }).click();
    const editor = pane.locator(".detail-edit");
    await editor.getByLabel("Summary").fill("Updated by the Phase 5 actor-provenance acceptance test.");
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(pane.locator(".detail-summary")).toHaveText(
      "Updated by the Phase 5 actor-provenance acceptance test."
    );
    await expect(editor).toHaveCount(0);
    expect(patchRequests).toHaveLength(1);
    expect(patchRequests[0]).toMatchObject({
      actor: { actor_client: "dashboard" }
    });
    expect((patchRequests[0] as { actor: { actor_session_id?: string } }).actor.actor_session_id).toBeTruthy();

    const graph = await openTab(pane, "Graph");
    const relatedGroup = graph.getByRole("heading", { name: "Related", exact: true }).locator("xpath=..");
    await relatedGroup.getByRole("button", { name: "Remove" }).click();
    await expect(graph.getByRole("heading", { name: "Related", exact: true })).toHaveCount(0);
    expect(relationshipDeleteRequests).toHaveLength(1);
    expect(relationshipDeleteRequests[0]).toMatchObject({
      actor: { actor_client: "dashboard" }
    });
    expect((relationshipDeleteRequests[0] as { actor: { actor_session_id?: string } }).actor.actor_session_id).toBeTruthy();

    const box = await pane.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await openTab(pane, "Activity");
    await activity.getByLabel("Progress text").focus();
    await expect(activity.getByLabel("Progress text")).toBeFocused();

    // Delete moved from the card to the pane; its confirmation stays a modal dialog.
    await pane.getByRole("button", { name: "Delete work item" }).click();
    const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
    await deleteDialog.getByRole("button", { name: "Delete work item" }).click();
    await expect(card).toHaveCount(0);
    expect(workDeleteRequests).toHaveLength(1);
    expect(workDeleteRequests[0]).toMatchObject({
      actor: { actor_client: "dashboard" }
    });
    expect((workDeleteRequests[0] as { actor: { actor_session_id?: string } }).actor.actor_session_id).toBeTruthy();
    const actorSessions = [
      eventRequests[0],
      patchRequests[0],
      relationshipDeleteRequests[0],
      workDeleteRequests[0]
    ].map((payload) => (
      payload as { actor: { actor_session_id: string } }
    ).actor.actor_session_id);
    expect(new Set(actorSessions).size).toBe(1);
  } finally {
    await client.dispose();
  }
});

test("activity pagination, refresh recovery, replay, and proxy denials stay coherent", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  let sendSync: ((message: string) => void) | undefined;
  await page.routeWebSocket("**/api/mnemonic/sync", (socket) => {
    sendSync = (message) => socket.send(message);
  });

  const suffix = "phase5-pages-" + testInfo.project.name + "-" + state.runId.slice(0, 8);
  const title = "Phase 5 paged activity " + suffix;
  const counterpartTitle = "Phase 5 replay counterpart " + suffix;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: "Bearer " + apiKey, Accept: "application/json" }
  });
  let workId = "";
  let counterpartId = "";
  let relationshipId = "";

  try {
    const created = await createFixtureWork(client, state.projectId, title, suffix);
    workId = created.work_item.id;
    const unattributedPatch = await client.patch(
      "/api/v1/projects/" + state.projectId + "/work-items/" + workId,
      {
        data: {
          expected_version: created.work_item.version,
          summary: "This actor-omitting direct REST patch must remain unattributed."
        }
      }
    );
    expect(unattributedPatch.ok(), await unattributedPatch.text()).toBe(true);

    for (let index = 1; index <= 22; index += 1) {
      await appendProgress(
        client,
        state.projectId,
        workId,
        "Seeded progress " + String(index).padStart(2, "0") + " " + suffix,
        suffix
      );
    }

    const counterpart = await createFixtureWork(
      client,
      state.projectId,
      counterpartTitle,
      suffix + "-counterpart"
    );
    counterpartId = counterpart.work_item.id;
    const relationshipPayload = {
      relationship_type: "related",
      source_work_item_id: workId,
      target_work_item_id: counterpartId,
      created_by_client: "playwright-api",
      created_by_session_id: suffix,
      created_by_model: null,
      context_checkpoint_id: null
    };
    const relationship = await client.post(
      "/api/v1/projects/" + state.projectId + "/relationships",
      { data: relationshipPayload }
    );
    expect(relationship.ok(), await relationship.text()).toBe(true);
    const relationshipResult = await relationship.json() as {
      created: boolean;
      relationship: { id: string };
    };
    expect(relationshipResult.created).toBe(true);
    relationshipId = relationshipResult.relationship.id;
    const replay = await client.post(
      "/api/v1/projects/" + state.projectId + "/relationships",
      { data: relationshipPayload }
    );
    expect(replay.ok(), await replay.text()).toBe(true);
    expect((await replay.json() as { created: boolean }).created).toBe(false);

    await page.goto("/");
    await expect.poll(() => Boolean(sendSync)).toBe(true);
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    const pane = await selectWork(page, title);

    const activity = (await openTab(pane, "Activity")).locator(".event-timeline");
    await expect(activity.locator("article.work-event")).toHaveCount(20);
    await expect(activity.locator(".event-pagination")).toContainText("1–20 of 25");
    await expect(activity.locator(".event-list").getByText("Added relationship", { exact: true })).toHaveCount(1);
    await expect(activity.locator("article.work-event").filter({ hasText: counterpartTitle })).toHaveCount(1);

    const deniedAppend = await page.evaluate(async ({ projectId, targetWorkId }) => {
      const response = await fetch(
        "/api/mnemonic/projects/" + projectId + "/work-items/" + targetWorkId + "/events",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event_type: "progress",
            body: "must not be stored",
            metadata: {},
            actor: { actor_client: "dashboard", actor_session_id: "browser-negative" },
            lease_token: "browser-capability"
          })
        }
      );
      return { status: response.status, payload: await response.json() };
    }, { projectId: state.projectId, targetWorkId: workId });
    expect(deniedAppend.status).toBe(400);
    expect(JSON.stringify(deniedAppend.payload)).not.toContain("browser-capability");

    const deniedQuery = await page.evaluate(async ({ projectId, targetWorkId }) => {
      const response = await fetch(
        "/api/mnemonic/projects/" + projectId + "/work-items/" + targetWorkId
          + "/events?order=newest&limit=20&offset=0&unexpected=true"
      );
      return response.status;
    }, { projectId: state.projectId, targetWorkId: workId });
    expect(deniedQuery).toBe(400);
    const progressCountResponse = await client.get(
      "/api/v1/projects/" + state.projectId + "/work-items/" + workId
        + "/events?event_type=progress&limit=1&offset=0"
    );
    expect(progressCountResponse.ok(), await progressCountResponse.text()).toBe(true);
    expect((await progressCountResponse.json() as { total: number }).total).toBe(22);

    await activity.getByRole("button", { name: "Older" }).click();
    await expect(activity.locator(".event-pagination")).toContainText("21–25 of 25");
    await expect(activity.getByText("Unattributed earlier action", { exact: true })).toBeVisible();

    // A manual Refresh must surface the externally appended event and reset the activity page.
    // On the narrow project the sheet has to close before Refresh is reachable; on desktop the
    // pane stays open beside the queue and the refresh signal resets the timeline in place.
    const manualRefreshBody = "Manual refresh progress " + suffix;
    await appendProgress(client, state.projectId, workId, manualRefreshBody, suffix + "-manual");
    await closeDetail(page);
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await selectWork(page, title);
    await openTab(pane, "Activity");
    await expect(activity.locator("article.work-event").filter({ hasText: manualRefreshBody })).toHaveCount(1);
    await expect(activity.locator(".event-pagination")).toContainText("1–20 of 26");

    await activity.getByRole("button", { name: "Older" }).click();
    await expect(activity.locator(".event-pagination")).toContainText("21–26 of 26");
    const liveResetBody = "Live reset progress " + suffix;
    await appendProgress(client, state.projectId, workId, liveResetBody, suffix + "-live");
    sendSync!(JSON.stringify({
      type: "invalidate",
      revision: 1,
      scope: "work-items"
    }));
    await expect(activity.locator("article.work-event").filter({ hasText: liveResetBody })).toHaveCount(1);
    await expect(activity.locator(".event-pagination")).toContainText("1–20 of 27");

    await activity.getByRole("button", { name: "Older" }).click();
    await expect(activity.locator(".event-pagination")).toContainText("21–27 of 27");
    let failNextEventPage = false;
    await page.route(
      "**/api/mnemonic/projects/" + state.projectId + "/work-items/" + workId + "/events?*",
      async (route) => {
        if (!failNextEventPage) {
          await route.continue();
          return;
        }
        failNextEventPage = false;
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Forced event refresh failure." })
        });
      }
    );
    const failedRefreshBody = "Retry recovery progress " + suffix;
    await appendProgress(client, state.projectId, workId, failedRefreshBody, suffix + "-retry");
    failNextEventPage = true;
    sendSync!(JSON.stringify({
      type: "invalidate",
      revision: 2,
      scope: "work-items"
    }));
    await expect(activity.getByRole("alert")).toContainText("Forced event refresh failure.");
    await expect(activity.locator(".event-list")).toHaveCount(0);
    await expect(activity.locator(".event-pagination")).toHaveCount(0);
    await activity.getByRole("button", { name: "Try again" }).click();
    await expect(activity.locator("article.work-event").filter({ hasText: failedRefreshBody })).toHaveCount(1);
    await expect(activity.locator(".event-pagination")).toContainText("1–20 of 28");
  } finally {
    if (relationshipId) {
      await client.delete(
        "/api/v1/projects/" + state.projectId + "/relationships/" + relationshipId
      );
    }
    await hideFixtureWork(client, state.projectId, workId);
    await hideFixtureWork(client, state.projectId, counterpartId);
    await client.dispose();
  }
});

test("reconstructed and discovered-from events retain bounded references", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = "phase5-references-" + testInfo.project.name + "-" + state.runId.slice(0, 8);
  const title = "Phase 5 discovered work " + suffix;
  const originTitle = "Phase 5 originating work " + suffix;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: "Bearer " + apiKey, Accept: "application/json" }
  });
  let workId = "";
  let originId = "";
  let relationshipId = "";

  try {
    const origin = await createFixtureWork(
      client,
      state.projectId,
      originTitle,
      suffix + "-origin"
    );
    originId = origin.work_item.id;
    const created = await createFixtureWork(client, state.projectId, title, suffix);
    workId = created.work_item.id;
    const relationship = await client.post(
      "/api/v1/projects/" + state.projectId + "/relationships",
      {
        data: {
          relationship_type: "discovered-from",
          source_work_item_id: workId,
          target_work_item_id: originId,
          created_by_client: "playwright-api",
          created_by_session_id: suffix,
          created_by_model: null,
          context_checkpoint_id: origin.initial_checkpoint.id
        }
      }
    );
    expect(relationship.ok(), await relationship.text()).toBe(true);
    relationshipId = (
      await relationship.json() as { relationship: { id: string } }
    ).relationship.id;

    await page.route(
      "**/api/mnemonic/projects/" + state.projectId + "/work-items/" + workId + "/events?*",
      async (route) => {
        const response = await route.fetch();
        if (!response.ok()) {
          await route.fulfill({ response });
          return;
        }
        const payload = await response.json() as {
          items: Array<Record<string, unknown>>;
          pre_phase5_history_may_be_incomplete: boolean;
        };
        payload.items = (payload.items ?? []).map((item) => item.event_type === "work_created"
          ? { ...item, actor_kind: "unattributed", actor_client: null, actor_session_id: null,
              actor_model: null, metadata: {}, origin: "backfill" }
          : item);
        payload.pre_phase5_history_may_be_incomplete = true;
        await route.fulfill({ response, json: payload });
      }
    );

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    const pane = await selectWork(page, title);

    const activity = (await openTab(pane, "Activity")).locator(".event-timeline");
    await expect(activity.getByRole("note")).toContainText("Earlier history was reconstructed");
    await expect(activity.getByText("Reconstructed", { exact: true })).toBeVisible();
    await expect(activity.getByText("Unattributed earlier action", { exact: true })).toBeVisible();
    const relationshipRow = activity.locator("article.work-event").filter({ hasText: originTitle });
    await expect(relationshipRow).toContainText(originTitle);
    await expect(relationshipRow).toContainText("Relationship context checkpoint");
    await expect(relationshipRow.locator(".work-event-references")).toContainText(
      origin.initial_checkpoint.id
    );
    await expect(activity).not.toContainText("Initial context for " + originTitle + ".");
  } finally {
    if (relationshipId) {
      await client.delete(
        "/api/v1/projects/" + state.projectId + "/relationships/" + relationshipId
      );
    }
    await hideFixtureWork(client, state.projectId, workId);
    await hideFixtureWork(client, state.projectId, originId);
    await client.dispose();
  }
});
