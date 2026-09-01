import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("hierarchy navigation and the relationship editor preserve graph semantics", async ({ page }, testInfo) => {
  test.slow();
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}`;
  const titles = {
    root: `Terminal ancestor ${suffix}`,
    child: `Active child ${suffix}`,
    grandchild: `Lazy grandchild ${suffix}`,
    blocker: `Blocking prerequisite ${suffix}`,
    secondParent: `Second parent ${suffix}`,
    promotedRoot: `Promoted ancestor ${suffix}`,
    promotedChild: `Pending promoted descendant ${suffix}`,
    doneRoot: `Done ancestor ${suffix}`,
    doneChild: `Pending done descendant ${suffix}`,
    collapsedRoot: `Collapsed pending root ${suffix}`,
    collapsedChild: `Initially hidden child ${suffix}`
  };
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });

  async function createWork(
    title: string,
    status: "pending" | "wont-do" | "promoted" = "pending"
  ) {
    const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: `Phase 3 graph fixture for ${title}.`,
        status,
        priority: 31,
        initial_checkpoint: {
          prompt: `Immutable starting context for ${title}.`,
          source_client: "playwright-api",
          source_session_id: `phase3-${suffix}`,
          tags: ["phase-3", "graph"],
          source_metadata: {}
        }
      }
    });
    if (!response.ok()) throw new Error(`Could not create ${title} (${response.status()}): ${await response.text()}`);
    return (await response.json() as { work_item: { id: string } }).work_item.id;
  }

  async function addRelationship(
    relationship_type: "parent-child" | "blocks",
    source_work_item_id: string,
    target_work_item_id: string
  ) {
    const response = await client.post(`/api/v1/projects/${state.projectId}/relationships`, {
      data: {
        relationship_type,
        source_work_item_id,
        target_work_item_id,
        created_by_client: "playwright-api",
        created_by_session_id: `phase3-${suffix}`,
        created_by_model: null,
        context_checkpoint_id: null
      }
    });
    if (!response.ok()) throw new Error(`Could not add ${relationship_type} (${response.status()}): ${await response.text()}`);
  }

  let rootId = "";
  let childId = "";
  try {
    rootId = await createWork(titles.root, "wont-do");
    childId = await createWork(titles.child);
    const grandchildId = await createWork(titles.grandchild);
    await createWork(titles.blocker);
    await createWork(titles.secondParent);
    const promotedRootId = await createWork(titles.promotedRoot, "promoted");
    const promotedChildId = await createWork(titles.promotedChild);
    const doneRootId = await createWork(titles.doneRoot);
    const doneChildId = await createWork(titles.doneChild);
    const collapsedRootId = await createWork(titles.collapsedRoot);
    const collapsedChildId = await createWork(titles.collapsedChild);
    const completed = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${doneRootId}/complete`,
      {
        data: {
          expected_version: 1,
          checkpoint: {
            prompt: `Completed ancestor fixture for ${suffix}.`,
            source_client: "playwright-api",
            source_session_id: `phase3-${suffix}`
          }
        }
      }
    );
    if (!completed.ok()) {
      throw new Error(`Could not complete done ancestor (${completed.status()}): ${await completed.text()}`);
    }
    await addRelationship("parent-child", rootId, childId);
    await addRelationship("parent-child", childId, grandchildId);
    await addRelationship("parent-child", promotedRootId, promotedChildId);
    await addRelationship("parent-child", doneRootId, doneChildId);
    await addRelationship("parent-child", collapsedRootId, collapsedChildId);

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${childId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: `active-and-blocked-${suffix}`,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim child fixture (${claimed.status()}): ${await claimed.text()}`);

    const childRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/children?")) childRequests.push(request.url());
    });

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);

    const rootCard = page.locator("article.work-item-card").filter({ hasText: titles.root });
    const childCard = page.locator("article.work-item-card").filter({ hasText: titles.child });
    const grandchildCard = page.locator("article.work-item-card").filter({ hasText: titles.grandchild });
    const promotedRootCard = page.locator("article.work-item-card").filter({ hasText: titles.promotedRoot });
    const promotedChildCard = page.locator("article.work-item-card").filter({ hasText: titles.promotedChild });
    const doneRootCard = page.locator("article.work-item-card").filter({ hasText: titles.doneRoot });
    const doneChildCard = page.locator("article.work-item-card").filter({ hasText: titles.doneChild });
    const collapsedRootCard = page.locator("article.work-item-card").filter({ hasText: titles.collapsedRoot });
    const collapsedChildCard = page.locator("article.work-item-card").filter({ hasText: titles.collapsedChild });
    await expect(rootCard).toHaveCount(1);
    await expect(rootCard.locator("xpath=ancestor::div[contains(@class,'hierarchy-scaffold')]")).toHaveCount(1);
    await expect(childCard).toHaveCount(1);
    await expect(promotedRootCard).toHaveCount(1);
    await expect(promotedRootCard.locator("xpath=ancestor::div[contains(@class,'hierarchy-scaffold')]")).toHaveCount(1);
    await expect(promotedChildCard).toHaveCount(1);
    await expect(doneRootCard).toHaveCount(1);
    await expect(doneRootCard.locator("xpath=ancestor::div[contains(@class,'hierarchy-scaffold')]")).toHaveCount(1);
    await expect(doneChildCard).toHaveCount(1);
    await expect(collapsedRootCard).toHaveCount(1);
    await expect(collapsedChildCard).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: `Expand children of ${titles.collapsedRoot}` })
    ).toHaveAttribute("aria-expanded", "false");
    // The Pending filter keeps Active distinct. The Active child is therefore
    // navigation scaffolding and auto-expands to reveal its Pending descendant.
    await expect(grandchildCard).toHaveCount(1);
    await expect(page.locator(".result-count")).toContainText("root branch");

    const childChildrenPath = `/work-items/${childId}/children?`;
    await expect.poll(() => childRequests.some(
      (url) => url.includes(childChildrenPath) && url.includes("limit=50") && url.includes("offset=0")
    )).toBe(true);
    const initialChildRequestCount = childRequests.filter(
      (url) => url.includes(childChildrenPath)
    ).length;
    const rootPagination = await page.locator(".pagination").innerText();
    await page.getByRole("button", { name: `Collapse children of ${titles.child}` }).click();
    await expect(grandchildCard).toHaveCount(0);
    await page.getByRole("button", { name: `Expand children of ${titles.child}` }).click();
    await expect(grandchildCard).toHaveCount(1);
    await expect.poll(() => childRequests.filter(
      (url) => url.includes(childChildrenPath)
    ).length).toBeGreaterThan(initialChildRequestCount);
    await expect.poll(() => page.locator(".pagination").innerText()).toBe(rootPagination);

    await page.getByLabel("Search work items").fill(titles.grandchild);
    const searchResult = page.locator(".search-result").filter({ hasText: titles.grandchild });
    await expect(searchResult).toHaveCount(1);
    await expect(searchResult.getByRole("navigation", { name: `Ancestry for ${titles.grandchild}` })).toContainText(titles.root);
    await expect(searchResult.getByRole("navigation", { name: `Ancestry for ${titles.grandchild}` })).toContainText(titles.child);

    await page.getByRole("button", { name: "Active", exact: true }).click();
    await page.getByLabel("Search work items").fill(titles.child);
    await expect(childCard).toHaveCount(1);
    await childCard.getByRole("button", { name: titles.child, exact: true }).click();
    const detail = page.getByRole("dialog", { name: "Work context" });
    await expect(detail.locator(".detail-topline > .status-badge")).toHaveText("Active");
    await detail.getByText("Add a relationship", { exact: true }).click();
    await detail.getByLabel("Find another work item").fill(titles.blocker);
    await detail.getByRole("option", { name: new RegExp(titles.blocker) }).click();
    await detail.getByLabel("Relationship type").selectOption("blocks");
    await detail.getByLabel("Toward this work item").check();
    await expect(detail.locator(".relationship-preview")).toContainText(`${titles.blocker} blocks ${titles.child}.`);
    await detail.getByRole("button", { name: "Add relationship" }).click();
    await expect(detail.getByRole("heading", { name: "Blocked by", exact: true })).toBeVisible();
    await expect(detail.locator(".detail-topline > .status-badge")).toHaveText("Active");
    await expect(detail.locator(".operational-badge.blocked")).toHaveText("Blocked");

    const blockedByHeading = detail.getByRole("heading", { name: "Blocked by", exact: true });
    const blockedByGroup = blockedByHeading.locator("xpath=..");
    await blockedByGroup.getByRole("button", { name: "Remove" }).click();
    await expect(detail.getByRole("heading", { name: "Blocked by", exact: true })).toHaveCount(0);
    await expect(detail.locator(".detail-topline > .status-badge")).toHaveText("Active");
    await expect(detail.locator(".operational-badge.blocked")).toHaveCount(0);

    await detail.getByLabel("Find another work item").fill(titles.secondParent);
    await detail.getByRole("option", { name: new RegExp(titles.secondParent) }).click();
    await detail.getByLabel("Relationship type").selectOption("parent-child");
    await detail.getByLabel("Toward this work item").check();
    await expect(detail.locator(".relationship-preview")).toContainText(`${titles.secondParent} is the parent of ${titles.child}.`);
    await detail.getByRole("button", { name: "Add relationship" }).click();
    await expect(detail.getByRole("alert")).toContainText("already has a parent");

    const box = await detail.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  } finally {
    await client.dispose();
  }
});
