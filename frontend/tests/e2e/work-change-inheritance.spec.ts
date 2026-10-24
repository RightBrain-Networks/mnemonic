import { expect, request, test } from "@playwright/test";
import { createProject, createWork } from "./task-fixtures";
import { reportForFixture } from "./job-report-fixture";
import { workPane } from "./surface";

test("declared-origin inheritance exclusions remain readable after refresh and completion", async ({ page }, testInfo) => {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("The disposable E2E API is not configured.");
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${apiKey}` } });
  try {
    const project = await createProject(api);
    const origin = await createWork(api, project.id, "Origin investigation");
    const checkpointResponse = await api.post(`/api/v1/projects/${project.id}/work-items/${origin.id}/checkpoints`, { data: {
      client_operation_id: crypto.randomUUID(), kind: "context", prompt: "Selected discovery context.",
      source_client: "codex", source_session_id: "inheritance-browser-check"
    } });
    expect(checkpointResponse.ok(), await checkpointResponse.text()).toBe(true);
    const checkpoint = await checkpointResponse.json();
    const reason = "Independent follow-up <script>window.inheritanceInjected = true</script>\nKeep discovery evidence.";
    const intent = {
      title: "Independent tooling follow-up", summary: "Improve review tooling while retaining its discovery source.",
      discovered_from_work_item_id: origin.id, inherit_decision_change: false,
      decision_change_inheritance_reason: reason, client_operation_id: crypto.randomUUID(),
      initial_checkpoint: { prompt: "Implement independent tooling.", source_client: "codex", source_session_id: "inheritance-browser-check" },
      initial_relationships: [{ type: "discovered-from", direction: "outgoing", other_work_item_id: origin.id, context_checkpoint_id: checkpoint.id }]
    };
    const path = `/api/v1/projects/${project.id}/work-items`;
    const createdResponse = await api.post(path, { data: intent });
    expect(createdResponse.ok(), await createdResponse.text()).toBe(true);
    const created = await createdResponse.json(), child = created.work_item;
    expect(child.decision_change_inheritance_exclusion).toMatchObject({
      origin_work_item_id: origin.id, origin_context_checkpoint_id: checkpoint.id, reason
    });
    expect(created.initial_relationships).toEqual(expect.arrayContaining([expect.objectContaining({
      relationship_type: "discovered-from", target_work_item_id: origin.id, context_checkpoint_id: checkpoint.id
    })]));
    await page.goto(`/work-items?project=${project.id}&work=${child.id}`);
    const panel = workPane(page).locator(".decision-change-panel");
    for (let pass = 0; pass < 2; pass += 1) {
      if (pass) await page.reload();
      await panel.getByText("Decision impact review scope", { exact: true }).click();
      await expect(panel.getByText("Decision changes do not inherit through the declared origin discovery edge.")).toBeVisible();
      await expect(panel.locator("code").filter({ hasText: origin.id })).toBeVisible();
      await expect(panel.locator("code").filter({ hasText: checkpoint.id })).toBeVisible();
      await expect(panel.locator(".preserve-whitespace")).toHaveText(reason);
      await expect(panel.getByText("Other discovery paths and explicit adoption still apply.")).toBeVisible();
      expect(await page.evaluate(() => "inheritanceInjected" in window)).toBe(false);
    }
    const screenshot = testInfo.outputPath(`work-change-inheritance-${testInfo.project.name}.png`);
    await panel.screenshot({ path: screenshot, animations: "disabled" });
    await testInfo.attach("Retained origin-edge inheritance declaration", { path: screenshot, contentType: "image/png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const completed = await api.post(`${path}/${child.id}/complete`, { data: {
      expected_version: child.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
      checkpoint: { prompt: "Completed the independently scoped follow-up.", source_client: "codex", source_session_id: "inheritance-browser-check" },
      job_completion_report: await reportForFixture(api, project.id)
    } });
    expect(completed.ok(), await completed.text()).toBe(true);
    await page.reload();
    await panel.getByText("Decision impact review scope", { exact: true }).click();
    await expect(panel.locator(".preserve-whitespace")).toHaveText(reason);
    await expect(panel.getByText("Adopt existing assessments into this work item", { exact: true })).toHaveCount(0);
    const replay = await api.post(path, { data: intent });
    expect(replay.ok(), await replay.text()).toBe(true);
    expect(await replay.json()).toEqual(created);
  } finally {
    await api.dispose();
  }
});
