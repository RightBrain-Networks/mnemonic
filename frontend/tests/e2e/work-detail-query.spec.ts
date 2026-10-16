import { expect, request, test } from "@playwright/test";
import { claim, createProject, createWork } from "./task-fixtures";
import { openTab, selectWork } from "./surface";

for (const concurrentEdit of [false, true]) {
  test(`a held context refresh preserves the editor${concurrentEdit ? " and its reviewed version" : " while refreshing lease metadata"}`, async ({ page }) => {
    const api = await request.newContext({
      baseURL: process.env.MNEMONIC_E2E_API_URL,
      extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
    });
    const held = Promise.withResolvers<void>();
    const serverChanged = Promise.withResolvers<void>();
    let refresh: () => void = () => { throw new Error("Live socket not connected"); };
    let responseHeld = false;
    let responseDelivered = false;
    try {
      const project = await createProject(api);
      const title = `Held detail ${crypto.randomUUID()}`;
      const work = await createWork(api, project.id, title);
      await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
        socket.send(JSON.stringify({ type: "ready", revision: 0 }));
        refresh = () => socket.send(JSON.stringify({ type: "invalidate", revision: 1, scope: "work-items" }));
      });
      await page.goto(`/work-items?project=${project.id}`);
      const pane = await selectWork(page, title);
      await expect(pane.getByRole("button", { name: "Edit work item" })).toBeEnabled();

      // The context request starts while the pane is in view mode. Its decoded
      // server response is deliberately delivered only after the user edits.
      const base = `/api/v1/projects/${project.id}/work-items/${work.id}`;
      await page.route(`**/api/mnemonic/projects/${project.id}/work-items/${work.id}/context?*`, async (route) => {
        await serverChanged.promise;
        const response = await route.fetch();
        responseHeld = true;
        await held.promise;
        await route.fulfill({ response });
        responseDelivered = true;
      });
      if (concurrentEdit) {
        const changed = await api.patch(base, { data: {
          expected_version: work.version, summary: "Another session changed the summary.",
          actor: { actor_client: "playwright-api", actor_session_id: "held-context" },
          client_operation_id: crypto.randomUUID()
        } });
        expect(changed.ok(), await changed.text()).toBe(true);
      } else await claim(api, project.id, work.id);
      serverChanged.resolve();
      refresh();
      await expect.poll(() => responseHeld).toBe(true);
      await pane.getByRole("button", { name: "Edit work item" }).click();
      const draft = `Keep this unsaved draft ${crypto.randomUUID()}`;
      await pane.getByLabel("Summary").fill(draft);
      held.resolve();
      await expect.poll(() => responseDelivered).toBe(true);
      if (concurrentEdit) await expect(pane.locator(".detail-version")).toHaveText(`v${work.version + 1}`);
      else await expect(pane.locator(".detail-identity .status-badge")).toHaveText(/Active/);
      await expect(pane.getByLabel("Summary")).toHaveValue(draft);
      const patches: string[] = [];
      page.on("request", (req) => {
        if (req.method() === "PATCH" && new URL(req.url()).pathname.endsWith(`/work-items/${work.id}`)) patches.push(req.postData()!);
      });
      await pane.getByRole("button", { name: "Save changes" }).click();
      await expect.poll(() => patches.length).toBe(1);
      expect(JSON.parse(patches[0]!)).toMatchObject({ summary: draft, expected_version: work.version });
      if (concurrentEdit) {
        await expect(pane.getByText("This work item changed after you opened it. Your edits are still here.")).toBeVisible();
        await expect(pane.getByLabel("Summary")).toHaveValue(draft);
        const latest = await api.get(base);
        expect((await latest.json()).work_item.summary).toBe("Another session changed the summary.");
      } else {
        await expect(pane.locator(".detail-summary")).toHaveText(draft);
        await expect(pane.locator(".detail-identity .status-badge")).toHaveText(/Active/);
      }
    } finally {
      serverChanged.resolve();
      held.resolve();
      await page.unrouteAll({ behavior: "wait" });
      await api.dispose();
    }
  });
}

test("exact checkpoint recovery reconciles before a queued live context refresh", async ({ page }) => {
  const api = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  const releaseContext = Promise.withResolvers<void>();
  const checkpointRequests: string[] = [];
  let refresh: () => void = () => { throw new Error("Live socket not connected"); };
  let contextReads = 0;
  let contextHeld = false;
  try {
    const project = await createProject(api);
    const title = `Recovery context ${crypto.randomUUID()}`;
    const work = await createWork(api, project.id, title);
    const base = `/api/mnemonic/projects/${project.id}/work-items/${work.id}`;
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
      socket.send(JSON.stringify({ type: "ready", revision: 0 }));
      refresh = () => socket.send(JSON.stringify({ type: "invalidate", revision: 1, scope: "work-items" }));
    });
    await page.goto(`/work-items?project=${project.id}`);
    const pane = await selectWork(page, title);
    await expect(pane.getByRole("button", { name: "Edit work item" })).toBeEnabled();
    const context = await openTab(pane, "Context");
    await page.route(`**${base}/checkpoints`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      checkpointRequests.push(route.request().postData()!);
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      if (checkpointRequests.length === 1) {
        // The write committed, but strict decoding cannot confirm this response.
        await route.fulfill({ status: response.status(), contentType: "application/json", body: "{}" });
      } else await route.fulfill({ response });
    });
    await context.getByLabel("Checkpoint text").fill("Recover this exact committed checkpoint.");
    await context.getByRole("button", { name: "Add checkpoint", exact: true }).click();
    await expect(page.locator(".mutation-recovery")).toContainText("outcome unknown");

    await page.route(`**${base}/context?*`, async (route) => {
      contextReads += 1;
      const response = await route.fetch();
      contextHeld = true;
      await releaseContext.promise;
      await route.fulfill({ response });
    });
    // A checkpoint read proves the socket's debounced invalidation has flushed.
    // Context is still guarded by the uncertain mutation, leaving its refresh pending.
    const invalidatedCheckpoints = page.waitForResponse((response) =>
      response.request().method() === "GET"
      && new URL(response.url()).pathname === `${base}/checkpoints`
    );
    refresh();
    await invalidatedCheckpoints;
    expect(contextReads).toBe(0);
    await page.locator(".mutation-recovery").getByRole("button", { name: "Retry exact request" }).click();
    await expect.poll(() => contextHeld).toBe(true);
    await expect(page.locator(".mutation-recovery")).toHaveCount(0);
    releaseContext.resolve();

    await expect(page.locator(".toast")).toContainText("resolved from its original request. Current views are reconciling.");
    await expect(page.locator(".toast-error")).toHaveCount(0);
    expect(checkpointRequests).toHaveLength(2);
    expect(checkpointRequests[1]).toBe(checkpointRequests[0]);
    await expect(context.getByLabel("Checkpoint text")).toHaveValue("");
    await page.getByRole("button", { name: "Dismiss notification" }).click();
    await context.getByLabel("Checkpoint text").fill("The reconciled context accepts another checkpoint.");
    await context.getByRole("button", { name: "Add checkpoint", exact: true }).click();
    await expect.poll(() => checkpointRequests.length).toBe(3);
    await expect(context.getByLabel("Checkpoint text")).toHaveValue("");
    await expect(page.locator(".toast-error")).toHaveCount(0);
  } finally {
    releaseContext.resolve();
    await page.unrouteAll({ behavior: "wait" });
    await api.dispose();
  }
});
