import { expect, request, test } from "@playwright/test";
import { removeRetainedDependencyFixture, seedRetainedDependencyCycle } from "./database";
import { openTab, workPane } from "./surface";

test("dependency cycles explain warnings, refuse closure changes, and display retained deadlocks", async ({ page }, testInfo) => {
  test.slow();
  const client = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  let retainedCycle: string | undefined;
  try {
    const projectResponse = await client.post("/api/v1/projects", { data: { name: `Cycle guard ${crypto.randomUUID()}` } });
    expect(projectResponse.ok()).toBe(true);
    const { id: project } = await projectResponse.json();
    async function create(title: string) {
      const response = await client.post(`/api/v1/projects/${project}/work-items`, { data: {
        title, summary: title, priority: 30, discovered_from_work_item_id: null,
        tags: null,
        initial_checkpoint: { prompt: title, source_client: "playwright", source_session_id: "cycle-guard" }
      } });
      expect(response.ok(), await response.text()).toBe(true);
      return (await response.json()).work_item.id as string;
    }
    const parent = await create("Cycle umbrella");
    const child = await create("Cycle child");
    let parentEdge = "";
    for (const type of ["parent-child", "blocks"]) {
      const response = await client.post(`/api/v1/projects/${project}/relationships`, { data: {
        relationship_type: type, source_work_item_id: parent, target_work_item_id: child,
        created_by_client: "playwright", created_by_session_id: "cycle-guard"
      } });
      expect(response.ok(), await response.text()).toBe(true);
      if (type === "parent-child") parentEdge = (await response.json()).relationship.id;
    }
    await page.goto(`/work-items?project=${project}&work=${child}`);
    const pane = workPane(page);
    await expect(pane.getByRole("region", { name: "Planning cycle", exact: true })).toBeVisible();
    const graph = await openTab(pane, "Graph");
    await graph.getByText("Add a relationship", { exact: true }).click();
    await graph.getByLabel("Find another work item").fill("Cycle umbrella");
    await graph.getByRole("option", { name: /Cycle umbrella/ }).click();
    await graph.getByLabel("Relationship type").selectOption("parent-child");
    await graph.getByLabel("Toward this work item").check();
    await graph.getByLabel("Require the child to close before the parent can close").check();
    await graph.getByLabel(/Replace the .* parent link/).check();
    await graph.getByRole("button", { name: "Replace parent relationship" }).click();
    const error = graph.locator(".relationship-action-message");
    await expect(error).toContainText(parent);
    await expect(error).toContainText(child);
    await expect(error).toContainText("child to parent");
    const retained = await client.get(`/api/v1/projects/${project}/relationships/${parentEdge}`);
    expect(retained.ok()).toBe(true);
    expect((await retained.json()).requires_child_closeout).toBeUndefined();

    retainedCycle = await seedRetainedDependencyCycle(project, child, parent);
    await page.reload();
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Deadlocked");
    const notice = pane.getByRole("region", { name: "Dependency deadlock", exact: true });
    await expect(notice).toContainText("owner repairs the graph");
    await notice.getByText("Show cycle (2 relationships)").click();
    await expect(notice).toContainText(parent);
    await expect(notice).toContainText(child);
    const ready = await client.get(`/api/v1/projects/${project}/ready-work`);
    expect((await ready.json()).deadlocked_count).toBe(2);
    await page.screenshot({ path: testInfo.outputPath("dependency-deadlock.png"), fullPage: true });
    await notice.screenshot({ path: testInfo.outputPath("dependency-cycle-evidence.png") });
  } finally {
    if (retainedCycle) await removeRetainedDependencyFixture(retainedCycle);
    await client.dispose();
  }
});
