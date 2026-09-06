import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { expireLease } from "./database";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, selectWork, workCard } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("an active lease is visible without exposing its capability and refreshes at expiry", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Leased work ${suffix} ${state.runId.slice(0, 8)}`;
  const holderSession = `lease-e2e-${suffix}-${crypto.randomUUID()}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workItemId = "";
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Verify human-safe active-session visibility and expiry refresh.",
        status: "pending",
        priority: 23,
        initial_checkpoint: {
          prompt: "This checkpoint must remain separate from the temporary lease.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: holderSession,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-2", "lease-visibility"],
          source_metadata: {}
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create lease fixture (${created.status()}): ${await created.text()}`);
    workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${workItemId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: holderSession,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim lease fixture (${claimed.status()}): ${await claimed.text()}`);
  } finally {
    await client.dispose();
  }

  await page.clock.install();
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByRole("button", { name: "Active", exact: true }).click();
  await page.getByLabel("Search work items").fill(title);

  const card = workCard(page, title);
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText(/Active/);

  // The split control remains available for an explicit human override. Its
  // menu excludes Active because that is the exact current operational state.
  const pane = await selectWork(page, title);
  await expect(pane.locator(".prompt-body")).toHaveText("This checkpoint must remain separate from the temporary lease.");
  const defer = pane.getByRole("button", { name: `Defer ${title}` });
  await expect(defer).toBeEnabled();
  await expect(defer).toHaveAttribute(
    "title",
    "Explicitly hold this work item out of the work queue"
  );
  const move = pane.getByRole("button", { name: `Move ${title} to another project` });
  const moveReason = pane.getByText(
    "Release the active lease before moving this work item.",
    { exact: true }
  );
  await expect(move).toBeDisabled();
  await expect(moveReason).toBeVisible();
  await expect(move).toHaveAttribute("aria-describedby", await moveReason.getAttribute("id") ?? "");
  const statusChooser = pane.getByRole("button", { name: `Choose a status for ${title}` });
  await statusChooser.click();
  await expect(pane.getByRole("menuitem")).toHaveText([
    "Pending",
    "Done",
    "Won’t Do",
    "Promote"
  ]);
  await expect(pane.getByRole("menuitem", { name: `Active ${title}` })).toHaveCount(0);
  await pane.screenshot({ path: testInfo.outputPath("manual-status-menu.png") });
  await page.keyboard.press("Escape");
  await expect(pane.getByRole("menu")).toHaveCount(0);
  const lease = pane.getByLabel("Active work lease");
  await expect(lease).toContainText("Active session");
  await expect(lease).toContainText("Claude Code");
  await expect(lease).toContainText(holderSession);
  await expect(lease).toContainText("Lease acquired");
  await expect(lease).toContainText("Renewed");
  await expect(lease).toContainText("Expires");
  const completion = pane.getByRole("button", { name: "Complete work" });
  await expect(completion).toBeDisabled();
  await expect(completion).toHaveAttribute(
    "title",
    "This work is actively leased. Complete it from the owning client, or release the lease before completing it in the browser."
  );

  const browserListPayload = await page.evaluate(async ({ projectId, title }) => {
    const query = new URLSearchParams({ q: title, status: "pending", view: "full", limit: "20", offset: "0" });
    return (await fetch(`/api/mnemonic/projects/${projectId}/work-items?${query}`)).text();
  }, { projectId: state.projectId, title });
  expect(browserListPayload).not.toContain("lease_token");
  // The pane renders the lease from the same-origin context read, which must be as
  // capability-free as the list.
  const browserContextPayload = await page.evaluate(async ({ projectId, workItemId }) => {
    const query = new URLSearchParams({ recent_limit: "5", recent_event_limit: "10" });
    return (await fetch(`/api/mnemonic/projects/${projectId}/work-items/${workItemId}/context?${query}`)).text();
  }, { projectId: state.projectId, workItemId });
  expect(browserContextPayload).not.toContain("lease_token");
  await expect(page.getByRole("button", { name: /^(?:claim(?: work)?|force release(?: work)?)$/i })).toHaveCount(0);

  await expireLease(state.projectId, workItemId);
  await page.clock.fastForward(61 * 1000);
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Dropped", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText(/Dropped/);
  const dropped = await selectWork(page, title);
  await expect(dropped.locator(".detail-identity > .status-badge")).toHaveText(/Dropped/);
  await expect(dropped.getByLabel("Active work lease")).toHaveCount(0);
  await dropped.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await dropped.getByRole("menuitem", { name: `Pending ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Explicit human decision recorded");
  await expect(card).toHaveCount(0);
});

test("a human can move work through every manual status", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Human deferred work ${suffix} ${state.runId.slice(0, 8)}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workItemId = "";
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Verify the dashboard-only deferral workflow.",
        status: "pending",
        priority: 19,
        initial_checkpoint: {
          prompt: "A human will temporarily hold this work out of the queue.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: `defer-e2e-${suffix}`,
          tags: ["deferred", "dashboard"]
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create deferral fixture (${created.status()}): ${await created.text()}`);
    workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;
  } finally {
    await client.dispose();
  }

  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await page.getByLabel("Search work items").fill(title);

  const card = workCard(page, title);
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Pending");
  const pane = await selectWork(page, title);
  await pane.getByRole("button", { name: `Defer ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Deferred and held out of the work queue");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Deferred", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Deferred");
  const deferred = await selectWork(page, title);
  await expect(deferred.locator(".detail-identity > .status-badge")).toHaveText("Deferred");
  await deferred.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await deferred.getByRole("menuitem", { name: `Pending ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Pending and available in the work queue");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Pending");
  const returned = await selectWork(page, title);
  await expect(returned.locator(".detail-identity > .status-badge")).toHaveText("Pending");
  await expect(returned.getByRole("button", { name: `Defer ${title}` })).toBeEnabled();

  const pendingChooser = returned.getByRole("button", {
    name: `Choose a status for ${title}`
  });
  await pendingChooser.click();
  await expect(returned.getByRole("menuitem")).toHaveText([
    "Active",
    "Done",
    "Won’t Do",
    "Promote"
  ]);
  await expect(returned.getByRole("menuitem", { name: `Pending ${title}` })).toHaveCount(0);
  await returned.getByRole("menuitem", { name: `Active ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Explicit human decision recorded");
  await expect(page.locator(".toast")).toContainText("is Active");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Active", exact: true }).click();
  await expect(card).toHaveCount(1);
  const active = await selectWork(page, title);
  await expect(active.locator(".detail-identity > .status-badge")).toHaveText("Active");
  await active.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await expect(active.getByRole("menuitem", { name: `Active ${title}` })).toHaveCount(0);
  await active.getByRole("menuitem", { name: `Pending ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Explicit human decision recorded");
  await expect(page.locator(".toast")).toContainText("is Pending");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await expect(card).toHaveCount(1);

  const pendingAgain = await selectWork(page, title);
  await pendingAgain.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await pendingAgain.getByRole("menuitem", { name: `Won’t Do ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("is Won’t Do");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Won’t do", exact: true }).click();
  await expect(card).toHaveCount(1);
  const wontDo = await selectWork(page, title);
  await expect(wontDo.locator(".detail-identity > .status-badge")).toHaveText("Won’t do");
  await wontDo.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await expect(wontDo.getByRole("menuitem", { name: `Won’t Do ${title}` })).toHaveCount(0);
  await wontDo.getByRole("menuitem", { name: `Promote ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("is Promoted");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Promoted", exact: true }).click();
  await expect(card).toHaveCount(1);
  const promoted = await selectWork(page, title);
  await expect(promoted.locator(".detail-identity > .status-badge")).toHaveText("Promoted");
  await promoted.getByRole("button", { name: `Choose a status for ${title}` }).click();
  await expect(promoted.getByRole("menuitem", { name: `Promote ${title}` })).toHaveCount(0);
  await promoted.getByRole("menuitem", { name: `Done ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("is Done");
  await expect(card).toHaveCount(0);

  await closeDetail(page);
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(card).toHaveCount(1);
  const done = await selectWork(page, title);
  await expect(done.locator(".detail-identity > .status-badge")).toHaveText("Done");
  await done.getByRole("tab", { name: "Activity" }).click();
  await expect(done.getByText(
    "Explicit human decision: completed work with an immutable completion checkpoint."
  )).toBeVisible();

  const verification = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const response = await verification.get(
      `/api/v1/projects/${state.projectId}/work-items/${workItemId}`
    );
    expect(response.ok()).toBeTruthy();
    expect((await response.json() as { work_item: { status: string } }).work_item.status).toBe("done");
    const checkpointsResponse = await verification.get(
      `/api/v1/projects/${state.projectId}/work-items/${workItemId}/checkpoints?order=newest&limit=20&offset=0`
    );
    expect(checkpointsResponse.ok()).toBeTruthy();
    const checkpoints = await checkpointsResponse.json() as {
      items: Array<{
        kind: string;
        source_client: string;
        source_metadata: Record<string, unknown>;
      }>;
    };
    const completionCheckpoint = checkpoints.items.find((item) => item.kind === "completion");
    expect(completionCheckpoint).toMatchObject({
      source_client: "dashboard",
      source_metadata: {
        decision: "explicit-human",
        action: "manual-status-change"
      }
    });

    const eventsResponse = await verification.get(
      `/api/v1/projects/${state.projectId}/work-items/${workItemId}/events?event_type=work_completed&order=newest&limit=20&offset=0`
    );
    expect(eventsResponse.ok()).toBeTruthy();
    const events = await eventsResponse.json() as {
      items: Array<{ actor_client: string; actor_model: string | null }>;
    };
    expect(events.items).toHaveLength(1);
    expect(events.items[0]).toMatchObject({
      actor_client: "dashboard",
      actor_model: null
    });
  } finally {
    await verification.dispose();
  }
});

test("a claim committed before an identity edit is reconciled in the visible detail", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Edit claim race ${suffix} ${state.runId.slice(0, 8)}`;
  const updatedSummary = `Identity edit reconciled the active lease for ${suffix}.`;
  const holderSession = `edit-claim-${suffix}-${crypto.randomUUID()}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Ready work that will be claimed while its identity editor is open.",
        status: "pending",
        priority: 24,
        initial_checkpoint: {
          prompt: "Open this ready work for editing before the direct API claim.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: holderSession,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-2", "edit-claim-race"],
          source_metadata: {}
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create edit-race fixture (${created.status()}): ${await created.text()}`);
    const workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    const pane = await selectWork(page, title);
    await pane.getByRole("button", { name: "Edit work item" }).click();

    // Edit is inline: the header keeps showing the identity while the Context tab
    // holds the editor form.
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Pending");
    await pane.getByLabel("Summary").fill(updatedSummary);

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${workItemId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: holderSession,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim edit-race fixture (${claimed.status()}): ${await claimed.text()}`);

    await pane.getByRole("button", { name: "Save changes" }).click();
    await expect(pane.locator(".detail-summary")).toHaveText(updatedSummary);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Active");
    await expect(pane.getByLabel("Active work lease")).toContainText(holderSession);
  } finally {
    await client.dispose();
  }
});
