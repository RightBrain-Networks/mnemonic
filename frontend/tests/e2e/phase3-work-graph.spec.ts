import { reportForFixture } from "./job-report-fixture";
import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { expireLease } from "./database";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard } from "./surface";

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
    collapsedChild: `Initially hidden child ${suffix}`,
    discoveryRoot: `Discovery origin branch ${suffix}`,
    discoveredChild: `Discovered child ${suffix}`
  };
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  const initialCheckpoints = new Map<string, string>();

  async function createWork(
    title: string,
    status: "pending" | "wont-do" | "promoted" = "pending"
  ) {
    const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: `Phase 3 graph fixture for ${title}.`,
        status: "pending",
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
    const created = await response.json() as {
      work_item: { id: string };
      initial_checkpoint: { id: string };
    };
    if (status !== "pending") {
      const retired = await client.patch(`/api/v1/projects/${state.projectId}/work-items/${created.work_item.id}`, {
        data: { expected_version: 1, status, actor: { actor_client: "playwright-api", actor_session_id: `phase3-${suffix}` },
          client_operation_id: crypto.randomUUID(), job_completion_report: await reportForFixture(client, state.projectId) }
      });
      expect(retired.ok(), await retired.text()).toBe(true);
    }
    initialCheckpoints.set(created.work_item.id, created.initial_checkpoint.id);
    return created.work_item.id;
  }

  async function addRelationship(
    relationship_type: "parent-child" | "blocks" | "discovered-from",
    source_work_item_id: string,
    target_work_item_id: string,
    context_checkpoint_id: string | null = null
  ) {
    const response = await client.post(`/api/v1/projects/${state.projectId}/relationships`, {
      data: {
        relationship_type,
        source_work_item_id,
        target_work_item_id,
        created_by_client: "playwright-api",
        created_by_session_id: `phase3-${suffix}`,
        created_by_model: null,
        context_checkpoint_id
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
    const discoveryRootId = await createWork(titles.discoveryRoot);
    const discoveredChildId = await createWork(titles.discoveredChild);
    const completed = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${doneRootId}/complete`,
      {
        data: {
          job_completion_report: await reportForFixture(client, state.projectId),
          client_operation_id: crypto.randomUUID(),
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
    await addRelationship("parent-child", discoveryRootId, discoveredChildId);
    const discoveryCheckpointId = initialCheckpoints.get(discoveryRootId);
    if (!discoveryCheckpointId) throw new Error("Discovery origin checkpoint was not retained.");
    await addRelationship(
      "discovered-from",
      discoveredChildId,
      discoveryRootId,
      discoveryCheckpointId
    );

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${childId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: `active-and-blocked-${suffix}`,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim child fixture (${claimed.status()}): ${await claimed.text()}`);
    const collapsedClaim = await client.post(
      `/api/v1/projects/${state.projectId}/work-items/${collapsedChildId}/claim`,
      {
        data: {
          holder_client: "claude-code",
          holder_session_id: `collapsed-expiry-${suffix}`,
          claim_request_id: crypto.randomUUID()
        }
      }
    );
    if (!collapsedClaim.ok()) {
      throw new Error(
        `Could not claim collapsed child fixture (${collapsedClaim.status()}): ${await collapsedClaim.text()}`
      );
    }

    const childRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/children?")) childRequests.push(request.url());
    });
    await page.clock.install();
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);

    const rootCard = workCard(page, titles.root);
    const childCard = workCard(page, titles.child);
    const grandchildCard = workCard(page, titles.grandchild);
    const promotedRootCard = workCard(page, titles.promotedRoot);
    const promotedChildCard = workCard(page, titles.promotedChild);
    const doneRootCard = workCard(page, titles.doneRoot);
    const doneChildCard = workCard(page, titles.doneChild);
    const collapsedRootCard = workCard(page, titles.collapsedRoot);
    const collapsedChildCard = workCard(page, titles.collapsedChild);
    const discoveryRootCard = workCard(page, titles.discoveryRoot);
    const discoveredChildCard = workCard(page, titles.discoveredChild);
    // The compact card replaces the per-root aggregate strip with one descendant
    // chip whose title carries every branch total the strip used to list.
    const descendantChip = (card: typeof rootCard) => card.locator(".queue-chip", { hasText: /descendant/ });
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
    const collapsedAggregates = descendantChip(collapsedRootCard);
    await expect(collapsedAggregates).toHaveText("1 descendant");
    await expect(collapsedAggregates).toHaveAttribute("title", /1 active descendant/);
    await expireLease(state.projectId, collapsedChildId);
    await page.clock.fastForward(61 * 1000);
    await expect(collapsedAggregates).toHaveAttribute("title", /0 active descendants/);
    await expect(
      page.getByRole("button", { name: `Expand children of ${titles.collapsedRoot}` })
    ).toHaveAttribute("aria-expanded", "false");
    // The Pending filter keeps Active distinct. The Active child is therefore
    // navigation scaffolding and auto-expands to reveal its Pending descendant.
    await expect(grandchildCard).toHaveCount(1);
    await expect(discoveryRootCard).toHaveCount(1);
    const discoveryAggregates = descendantChip(discoveryRootCard);
    await expect(discoveryAggregates).toHaveText("1 descendant");
    await expect(discoveryAggregates).toHaveAttribute("title", /1 discovered descendant/);
    await page.getByRole("button", { name: `Expand children of ${titles.discoveryRoot}` }).click();
    await expect(discoveredChildCard).toHaveCount(1);
    // The discovered child is grouped beneath its discovery origin, and the pane's
    // Graph tab shows the exact discovered-from edge that grouping was derived from.
    // The nearest `hierarchy-node*` ancestor of a card is its row, whose sibling holds the
    // children, so the branch is addressed by the node's own work-item id instead.
    const discoveryBranch = page.locator(`.hierarchy-node[data-work-item-id="${discoveryRootId}"]`);
    await expect(
      discoveryBranch.locator(".hierarchy-children").locator("article.work-item-card").filter({ hasText: titles.discoveredChild })
    ).toHaveCount(1);
    const discoveredPane = await selectWork(page, titles.discoveredChild);
    const discoveredGraph = await openTab(discoveredPane, "Graph");
    await expect(discoveredGraph.getByRole("region", { name: "Discovered from" })).toContainText(titles.discoveryRoot);
    await closeDetail(page);
    await expect(page.locator(".result-count")).toContainText("root branch");

    const rootAggregates = descendantChip(rootCard);
    await expect(rootAggregates).toHaveText("2 descendants");
    await expect(rootAggregates).toHaveAttribute("title", /1 direct child/);
    await expect(rootAggregates).toHaveAttribute("title", /2 descendants/);
    await expect(rootAggregates).toHaveAttribute("title", /1 active descendant/);
    await page.getByRole("button", { name: "Won’t do", exact: true }).click();
    await expect(rootCard).toHaveCount(1);
    await page.getByRole("button", { name: `Expand children of ${titles.root}` }).click();
    const hiddenChildren = page.getByRole("note").filter({
      hasText: "The lifecycle filter hides this branch’s children."
    });
    await expect(hiddenChildren).toBeVisible();
    await hiddenChildren.getByRole("button", { name: "Show all descendants" }).click();
    const focusedChildrenRegion = page.locator(".hierarchy-children:focus");
    await expect(focusedChildrenRegion).toHaveCount(1);
    await expect(focusedChildrenRegion.locator("xpath=ancestor::div[contains(@class,'hierarchy-node')][1]")).toContainText(
      titles.root
    );
    await expect(
      page.getByRole("status").filter({ hasText: "Branch override active" })
    ).toBeVisible();
    await expect(childCard).toHaveCount(1);
    await page.getByRole("button", { name: `Expand children of ${titles.child}` }).click();
    await expect(grandchildCard).toHaveCount(1);
    await expect.poll(() => childRequests.some(
      (url) => url.includes(`/work-items/${rootId}/children?`) && url.includes("status=all")
    )).toBe(true);
    await page.getByRole("button", { name: "Pending", exact: true }).click();
    await expect(grandchildCard).toHaveCount(1);

    const childChildrenPath = `/work-items/${childId}/children?`;
    await expect.poll(() => childRequests.some(
      (url) => url.includes(childChildrenPath) && url.includes("limit=50") && url.includes("offset=0")
    )).toBe(true);
    const initialChildRequestCount = childRequests.filter(
      (url) => url.includes(childChildrenPath)
    ).length;
    // Collapsing and re-expanding a branch refetches only that branch's children;
    // the root queue and its settled total stay untouched.
    const resultCount = page.locator(".result-count");
    await expect(resultCount).toContainText("root branch");
    const rootCount = await resultCount.innerText();
    await page.getByRole("button", { name: `Collapse children of ${titles.child}` }).click();
    await expect(grandchildCard).toHaveCount(0);
    await page.getByRole("button", { name: `Expand children of ${titles.child}` }).click();
    await expect(grandchildCard).toHaveCount(1);
    await expect.poll(() => childRequests.filter(
      (url) => url.includes(childChildrenPath)
    ).length).toBeGreaterThan(initialChildRequestCount);
    await expect.poll(() => resultCount.innerText()).toBe(rootCount);

    await page.getByLabel("Search work items").fill(titles.grandchild);
    const searchResult = page.locator(".search-result").filter({ hasText: titles.grandchild });
    await expect(searchResult).toHaveCount(1);
    await expect(searchResult.getByRole("navigation", { name: `Ancestry for ${titles.grandchild}` })).toContainText(titles.root);
    await expect(searchResult.getByRole("navigation", { name: `Ancestry for ${titles.grandchild}` })).toContainText(titles.child);

    await page.getByRole("button", { name: "Active", exact: true }).click();
    await page.getByLabel("Search work items").fill(titles.child);
    await expect(childCard).toHaveCount(1);
    const detail = await selectWork(page, titles.child);
    await expect(detail.locator(".detail-identity > .status-badge")).toHaveText("Active");
    const graph = await openTab(detail, "Graph");
    await graph.getByText("Add a relationship", { exact: true }).click();
    await graph.getByLabel("Find another work item").fill(titles.blocker);
    await graph.getByRole("option", { name: new RegExp(titles.blocker) }).click();
    await graph.getByLabel("Relationship type").selectOption("blocks");
    await graph.getByLabel("Toward this work item").check();
    await expect(graph.locator(".relationship-preview")).toContainText(`${titles.blocker} blocks ${titles.child}.`);
    await graph.getByRole("button", { name: "Add relationship" }).click();
    await expect(graph.getByRole("heading", { name: "Blocked by", exact: true })).toBeVisible();
    await expect(detail.locator(".detail-identity > .status-badge")).toHaveText("Blocked");
    await expect(detail.locator(".detail-identity .operational-badge.active")).toHaveText("Active");

    const blockedByHeading = graph.getByRole("heading", { name: "Blocked by", exact: true });
    const blockedByGroup = blockedByHeading.locator("xpath=..");
    await blockedByGroup.getByRole("button", { name: "Remove" }).click();
    await expect(graph.getByRole("heading", { name: "Blocked by", exact: true })).toHaveCount(0);
    await expect(detail.locator(".detail-identity > .status-badge")).toHaveText("Active");
    await expect(detail.locator(".detail-identity .operational-badge.blocked")).toHaveCount(0);

    // Relationship reconciliation keeps the editor mounted and its open state intact.
    await expect(graph.getByText("Add a relationship", { exact: true })).toBeVisible();
    await graph.getByLabel("Find another work item").fill(titles.secondParent);
    await graph.getByRole("option", { name: new RegExp(titles.secondParent) }).click();
    await graph.getByLabel("Relationship type").selectOption("parent-child");
    await graph.getByLabel("Toward this work item").check();
    await expect(graph.locator(".relationship-preview")).toContainText(`${titles.secondParent} is the parent of ${titles.child}.`);
    await graph.getByRole("button", { name: "Add relationship" }).click();
    await expect(graph.getByRole("alert")).toContainText("already has a parent");

    const box = await detail.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  } finally {
    await client.dispose();
  }
});
