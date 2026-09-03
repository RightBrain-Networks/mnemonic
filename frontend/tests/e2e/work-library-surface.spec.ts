import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
  type TestInfo
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard, workPane } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type WorkItem = {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  status: string;
  priority: number;
  version: number;
  updated_at: string;
};

type MergeRevision = {
  work_version: number;
  context_checkpoint_id: string;
  work_event_count: number;
};

type WorkContext = {
  work_item: WorkItem;
  checkpoint_total: number;
  merge_review_revision: MergeRevision;
  canonical: {
    is_duplicate: boolean;
    canonical_work_item: { id: string; title: string };
  };
  duplicate_member_total: number;
  recent_events: Array<{
    event_type: string;
    body: string;
    metadata: Record<string, unknown>;
  }>;
};

type SeedInput = {
  title: string;
  summary?: string;
  prompt?: string;
  sessionId: string;
  priority?: number;
};

const COPY_ICON_PATH = "M9 5V3h12v14h-3M3 7h12v14H3V7Z";
const CHECK_ICON_PATH = "m5 12 4 4L19 6";
const WORK_PAGE_SIZE = 20;

function narrowProject(testInfo: TestInfo): boolean {
  return testInfo.project.name === "chromium-narrow";
}

function testKey(testInfo: TestInfo): string {
  return [testInfo.project.name, state.runId.slice(0, 8), `r${testInfo.retry}`].join("-");
}

// A single lexical token that only this test's fixtures carry, so a flat search
// returns exactly the seeded records regardless of what earlier specs left behind.
function searchToken(prefix: string, testInfo: TestInfo): string {
  return prefix + testKey(testInfo).replaceAll("-", "");
}

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

async function createWork(client: APIRequestContext, input: SeedInput): Promise<WorkItem> {
  const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
    data: {
      title: input.title,
      summary: input.summary ?? `Work surface fixture for ${input.title}.`,
      status: "pending",
      priority: input.priority ?? 23,
      initial_checkpoint: {
        prompt: input.prompt ?? `Immutable starting context for ${input.title}.`,
        source_client: "playwright-api",
        source_session_id: input.sessionId,
        tags: ["work-surface"],
        source_metadata: {}
      }
    }
  });
  expect(response.status(), `${input.title}: ${await response.text()}`).toBe(201);
  return (await response.json() as { work_item: WorkItem }).work_item;
}

async function getContext(client: APIRequestContext, workItemId: string): Promise<WorkContext> {
  const response = await client.get(
    `/api/v1/projects/${state.projectId}/work-items/${workItemId}/context`
      + "?recent_limit=5&recent_event_limit=10"
  );
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as WorkContext;
}

// An authoritative merge written outside the browser, for fixtures that need an
// existing duplicate audit before the page loads.
async function mergeDirect(
  client: APIRequestContext,
  sourceId: string,
  destinationId: string,
  rationale: string,
  sessionId: string
): Promise<void> {
  const [source, destination] = await Promise.all([
    getContext(client, sourceId),
    getContext(client, destinationId)
  ]);
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${sourceId}/merge`,
    {
      data: {
        destination_work_item_id: destination.work_item.id,
        reviewed_source_revision: source.merge_review_revision,
        reviewed_destination_revision: destination.merge_review_revision,
        rationale,
        merged_by_client: "playwright-api",
        merged_by_session_id: sessionId,
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

function resultFor(page: Page, workItemId: string): Locator {
  return page.locator(`.search-result[data-work-item-id="${workItemId}"]`);
}

async function searchFor(page: Page, query: string, expectedTotal: number): Promise<void> {
  await page.getByRole("searchbox", { name: "Search work items" }).fill(query);
  await expect(page.locator(".result-count")).toHaveText(
    `${expectedTotal} work record${expectedTotal === 1 ? "" : "s"}`
  );
}

// Desktop scrolls the queue list itself; below 900px the list is not a scroller and
// the document scrolls instead. Driving both keeps the sentinel observed either way.
async function scrollQueueToEnd(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const list = document.querySelector<HTMLElement>(".work-queue-list");
    if (!list) throw new Error("The work queue list is not rendered.");
    list.scrollTop = list.scrollHeight;
    const content = document.querySelector<HTMLElement>(".page-content");
    if (content) content.scrollTop = content.scrollHeight;
    window.scrollTo(0, document.documentElement.scrollHeight);
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
  });
}

type ScrollProbe = {
  windowY: number;
  contentTop: number;
  listTop: number;
  listScrollable: boolean;
  selectedWithinList: boolean;
};

async function queueScrollProbe(page: Page): Promise<ScrollProbe> {
  return page.evaluate(() => {
    const list = document.querySelector<HTMLElement>(".work-queue-list");
    const content = document.querySelector<HTMLElement>(".page-content");
    if (!list || !content) throw new Error("The library surface is not rendered.");
    const selected = list.querySelector<HTMLElement>("[data-queue-option][aria-selected='true']");
    const listRect = list.getBoundingClientRect();
    const selectedRect = selected?.getBoundingClientRect() ?? null;
    return {
      windowY: window.scrollY,
      contentTop: content.scrollTop,
      listTop: list.scrollTop,
      listScrollable: list.scrollHeight > list.clientHeight,
      selectedWithinList: selectedRect !== null
        && selectedRect.top >= listRect.top - 1
        && selectedRect.bottom <= listRect.bottom + 1
    };
  });
}

test("arrow keys move the selection and scroll only the queue list", async ({ page }, testInfo) => {
  const narrow = narrowProject(testInfo);
  const token = searchToken("surfacearrows", testInfo);
  const letters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"];
  const client = await apiClient();
  try {
    for (const letter of letters) {
      await createWork(client, {
        title: `Arrow item ${letter} ${token}`,
        sessionId: `surface-arrows-${testKey(testInfo)}-${letter}`
      });
    }

    await openDashboard(page);
    await searchFor(page, token, letters.length);
    const cards = page.locator("article.work-item-card");
    const selectedCards = page.locator("article.work-item-card[aria-selected='true']");
    await expect(cards).toHaveCount(letters.length);
    const titles = await cards.locator(".queue-card-title").allTextContents();
    expect(new Set(titles).size).toBe(letters.length);
    for (const title of titles) expect(title).toContain(token);

    // Arrow keys keep their typing meaning inside the search field.
    const searchbox = page.getByRole("searchbox", { name: "Search work items" });
    await searchbox.focus();
    await page.keyboard.press("ArrowDown");
    await expect(selectedCards).toHaveCount(0);
    await searchbox.blur();

    const pane = workPane(page);
    const before = await queueScrollProbe(page);
    expect(before.listTop).toBe(0);
    if (!narrow) expect(before.listScrollable).toBe(true);

    // With nothing selected the first press selects the first card; each further press
    // moves one card down and the pane follows.
    for (const [index, title] of titles.entries()) {
      await page.keyboard.press("ArrowDown");
      const card = cards.nth(index);
      await expect(card).toHaveAttribute("aria-selected", "true");
      await expect(card).toHaveClass(/is-selected/);
      await expect(selectedCards).toHaveCount(1);
      await expect(pane).toHaveClass(/is-open/);
      await expect(pane.locator(".detail-title")).toHaveText(title);
    }

    // The selection clamps at the last card.
    await page.keyboard.press("ArrowDown");
    await expect(cards.last()).toHaveAttribute("aria-selected", "true");
    await expect(pane.locator(".detail-title")).toHaveText(titles[titles.length - 1]);

    const atEnd = await queueScrollProbe(page);
    expect(atEnd.windowY).toBe(before.windowY);
    expect(atEnd.contentTop).toBe(before.contentTop);
    if (narrow) {
      // Below 900px the list is not an internal scroller; nothing moved the page either.
      expect(atEnd.listTop).toBe(0);
    } else {
      expect(atEnd.listTop).toBeGreaterThan(0);
      expect(atEnd.selectedWithinList).toBe(true);
    }

    await page.keyboard.press("ArrowUp");
    await expect(cards.nth(titles.length - 2)).toHaveAttribute("aria-selected", "true");
    await expect(selectedCards).toHaveCount(1);
    await expect(pane.locator(".detail-title")).toHaveText(titles[titles.length - 2]);

    for (let remaining = titles.length - 2; remaining > 0; remaining -= 1) {
      await page.keyboard.press("ArrowUp");
    }
    await expect(cards.first()).toHaveAttribute("aria-selected", "true");
    await expect(pane.locator(".detail-title")).toHaveText(titles[0]);
    await page.keyboard.press("ArrowUp");
    await expect(cards.first()).toHaveAttribute("aria-selected", "true");

    const atStart = await queueScrollProbe(page);
    expect(atStart.windowY).toBe(before.windowY);
    expect(atStart.contentTop).toBe(before.contentTop);
    if (narrow) {
      expect(atStart.listTop).toBe(0);
    } else {
      expect(atStart.listTop).toBeLessThan(atEnd.listTop);
      expect(atStart.selectedWithinList).toBe(true);
    }
  } finally {
    await client.dispose();
  }
});

test("tabs mount one panel at a time and the selected tab persists across items", async ({ page }, testInfo) => {
  const token = searchToken("surfacetabs", testInfo);
  const key = testKey(testInfo);
  const titles = { a: `Tab item alpha ${token}`, b: `Tab item beta ${token}` };
  const prompts = {
    a: `Alpha context saved for the tab regression ${key}.`,
    b: `Beta context saved for the tab regression ${key}.`
  };
  const client = await apiClient();
  try {
    const a = await createWork(client, { title: titles.a, prompt: prompts.a, sessionId: `surface-tabs-${key}-a` });
    const b = await createWork(client, { title: titles.b, prompt: prompts.b, sessionId: `surface-tabs-${key}-b` });

    await openDashboard(page);
    await searchFor(page, token, 2);

    const pane = await selectWork(page, titles.a);
    const tabs = pane.getByRole("tablist", { name: "Work context sections" }).getByRole("tab");
    await expect(tabs).toHaveText([/^Context$/, /^History/, /^Graph/, /^Questions/, /^Activity/]);
    await expect(pane.getByRole("tab", { name: "Context" })).toHaveAttribute("aria-selected", "true");
    await expect(pane.getByRole("tab", { name: "Context" }).locator(".detail-tab-count")).toHaveCount(0);
    await expect(pane.getByRole("tabpanel")).toHaveCount(1);
    await expect(pane.locator("#detail-panel-context")).toHaveAttribute("aria-labelledby", "detail-tab-context");
    await expect(pane.locator("#detail-panel-context .prompt-body")).toHaveText(prompts.a);
    await expect(pane.locator("#detail-panel-context").getByLabel("Checkpoint text")).toBeVisible();
    await expect(pane.getByRole("tab", { name: "History" }).locator(".detail-tab-count")).toHaveText("1");
    await expect(pane.getByRole("tab", { name: "Graph" }).locator(".detail-tab-count")).toHaveText("0");
    await expect(pane.getByRole("tab", { name: "Questions" }).locator(".detail-tab-count")).toHaveText("0");
    await expect(pane.getByRole("tab", { name: "Questions" }).locator(".detail-tab-count")).not.toHaveClass(/is-alert/);
    await expect(pane.getByRole("tab", { name: "Activity" }).locator(".detail-tab-count")).toHaveText(/^\d+$/);

    const history = await openTab(pane, "History");
    await expect(pane.getByRole("tabpanel")).toHaveCount(1);
    await expect(pane.locator("#detail-panel-context")).toHaveCount(0);
    await expect(history).toHaveAttribute("aria-labelledby", "detail-tab-history");
    await expect(history.getByRole("heading", { name: "Session checkpoints" })).toBeVisible();
    await expect(history.locator("article.checkpoint")).toHaveCount(1);
    await expect(history.locator("article.checkpoint")).toContainText(prompts.a);
    await expect(history.locator(".metadata-grid")).toContainText(a.id);

    // Switching items keeps History selected and swaps in the new item's panel.
    const paneB = await selectWork(page, titles.b);
    await expect(paneB.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    await expect(paneB.getByRole("tab", { name: "Context" })).toHaveAttribute("aria-selected", "false");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    const historyB = paneB.locator("#detail-panel-history");
    await expect(historyB.locator("article.checkpoint")).toHaveCount(1);
    await expect(historyB.locator("article.checkpoint")).toContainText(prompts.b);
    await expect(historyB.locator("article.checkpoint")).not.toContainText(prompts.a);
    await expect(historyB.locator(".metadata-grid")).toContainText(b.id);
    await expect(historyB.locator(".metadata-grid")).not.toContainText(a.id);

    const graph = await openTab(paneB, "Graph");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    await expect(graph.getByRole("heading", { name: "Relationships" })).toBeVisible();
    await expect(graph.getByText("Add a relationship", { exact: true })).toBeVisible();

    const questions = await openTab(paneB, "Questions");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    await expect(questions.getByRole("heading", { name: "Questions and answers" })).toBeVisible();

    const activity = await openTab(paneB, "Activity");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    await expect(activity.locator(".event-timeline")).toBeVisible();
    await expect(activity.getByRole("heading", { name: "Activity", exact: true })).toBeVisible();

    const context = await openTab(paneB, "Context");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    await expect(context.locator(".prompt-body")).toHaveText(prompts.b);

    // Returning to the first item keeps the tab chosen on the second one.
    const paneA = await selectWork(page, titles.a);
    await expect(paneA.getByRole("tab", { name: "Context" })).toHaveAttribute("aria-selected", "true");
    await expect(paneA.locator("#detail-panel-context .prompt-body")).toHaveText(prompts.a);
  } finally {
    await client.dispose();
  }
});

test("editing happens inline in the Context tab and cancel discards the draft", async ({ page }, testInfo) => {
  const token = searchToken("surfaceedit", testInfo);
  const key = testKey(testInfo);
  const title = `Inline edit item ${token}`;
  const summary = `Original summary for the inline edit regression ${key}.`;
  const prompt = `Immutable context that editing must not touch ${key}.`;
  const updatedSummary = `Updated summary saved inline; checkpoint history is unchanged ${key}.`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, summary, prompt, sessionId: `surface-edit-${key}` });

    await openDashboard(page);
    await searchFor(page, token, 1);
    const pane = await selectWork(page, title);
    await expect(pane.locator(".detail-summary")).toHaveText(summary);
    await expect(pane.locator(".detail-version")).toHaveText("v1");
    const editButton = pane.getByRole("button", { name: "Edit work item" });
    await expect(editButton).toBeEnabled();

    // Edit always lands on the Context tab, replacing its body instead of opening a dialog.
    await openTab(pane, "History");
    await editButton.click();
    await expect(pane.getByRole("tab", { name: "Context" })).toHaveAttribute("aria-selected", "true");
    const editor = pane.locator("#detail-panel-context .detail-edit");
    await expect(editor).toBeVisible();
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expect(pane.locator(".prompt-body")).toHaveCount(0);
    await expect(editor).toContainText("Editing version 1");
    await expect(editor.getByLabel("Title")).toHaveValue(title);
    await expect(editor.getByLabel("Summary")).toHaveValue(summary);

    await editor.getByLabel("Summary").fill(updatedSummary);
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator(".toast")).toContainText("Work item saved. Checkpoint history was not changed.");
    await expect(editor).toHaveCount(0);
    await expect(pane.locator(".detail-summary")).toHaveText(updatedSummary);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-version")).toHaveText("v2");
    await expect(pane.locator("#detail-panel-context .prompt-body")).toHaveText(prompt);
    await expect(pane.getByRole("tab", { name: "History" }).locator(".detail-tab-count")).toHaveText("1");
    await expect(workCard(page, title).locator(".queue-card-summary")).toHaveText(updatedSummary);

    const saved = await getContext(client, work.id);
    expect(saved.work_item.summary).toBe(updatedSummary);
    expect(saved.work_item.title).toBe(title);
    expect(saved.work_item.version).toBe(2);
    expect(saved.checkpoint_total).toBe(1);

    // Cancel discards the draft without a mutation.
    await editButton.click();
    await expect(editor).toBeVisible();
    await expect(editor).toContainText("Editing version 2");
    await editor.getByLabel("Title").fill(`${title} discarded`);
    await editor.getByRole("button", { name: "Cancel" }).click();
    await expect(editor).toHaveCount(0);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-version")).toHaveText("v2");
    await expect(pane.locator("#detail-panel-context .prompt-body")).toHaveText(prompt);

    // Reopening starts from the saved record, not the discarded draft.
    await editButton.click();
    await expect(editor.getByLabel("Title")).toHaveValue(title);
    await expect(editor.getByLabel("Summary")).toHaveValue(updatedSummary);
    await editor.getByRole("button", { name: "Cancel" }).click();
    await expect(editor).toHaveCount(0);

    const unchanged = await getContext(client, work.id);
    expect(unchanged.work_item.title).toBe(title);
    expect(unchanged.work_item.version).toBe(2);
    expect(unchanged.checkpoint_total).toBe(1);
  } finally {
    await client.dispose();
  }
});

test("merge as duplicate runs inside the Graph tab and lands on the source audit", async ({ page }, testInfo) => {
  test.slow();
  const token = searchToken("surfacemerge", testInfo);
  const key = testKey(testInfo);
  const sourceTitle = `Merge source ${token}`;
  const destinationTitle = `Merge destination ${token}`;
  const sourcePrompt = `Source context retained verbatim under the audit ${key}.`;
  const destinationPrompt = `Destination context that stays canonical ${key}.`;
  const rationale = `Same objective recorded twice during ${key}; keeping the destination canonical.`;
  const client = await apiClient();
  try {
    const source = await createWork(client, {
      title: sourceTitle,
      prompt: sourcePrompt,
      sessionId: `surface-merge-${key}-source`
    });
    const destination = await createWork(client, {
      title: destinationTitle,
      prompt: destinationPrompt,
      sessionId: `surface-merge-${key}-destination`
    });
    const sourceBefore = await getContext(client, source.id);
    expect(sourceBefore.canonical.is_duplicate).toBe(false);

    await openDashboard(page);
    await searchFor(page, token, 2);
    const pane = await selectWork(page, sourceTitle);
    const mergeButton = pane.getByRole("button", { name: /Merge as duplicate/ });
    await expect(mergeButton).toBeEnabled();
    await mergeButton.click();

    // The panel opens inside the Graph tab above the relationship editor; no dialog.
    await expect(pane.getByRole("tab", { name: "Graph" })).toHaveAttribute("aria-selected", "true");
    const graph = pane.locator("#detail-panel-graph");
    const merge = graph.getByRole("region", { name: "Merge as duplicate" });
    await expect(merge).toBeVisible();
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expect(merge.getByRole("heading", { name: "Merge as duplicate" })).toBeVisible();
    await expect(merge.getByRole("button", { name: "Close merge" })).toBeEnabled();
    await expect(merge.locator("[data-direction='source']")).toContainText(sourceTitle);
    await expect(merge.locator("[data-direction='source']")).toContainText(source.id);
    await expect(graph.getByRole("heading", { name: "Relationships" })).toBeVisible();
    await expect(merge.getByRole("button", { name: "Merge permanently" })).toHaveCount(0);

    await merge.getByLabel("Find a canonical destination").fill(destination.id);
    const option = merge
      .getByRole("listbox", { name: "Canonical merge destinations" })
      .getByRole("option")
      .filter({ hasText: destination.id });
    await expect(option).toHaveCount(1);
    await expect(option).toContainText(destinationTitle);
    await option.click();
    await expect(option).toHaveAttribute("aria-selected", "true");

    const sourceReview = merge.locator("[data-review-direction='source']");
    const destinationReview = merge.locator("[data-review-direction='destination']");
    await expect(sourceReview).toBeVisible();
    await expect(destinationReview).toBeVisible();
    await expect(sourceReview).toContainText(source.id);
    await expect(sourceReview).toContainText(sourcePrompt);
    await expect(destinationReview).toContainText(destination.id);
    await expect(destinationReview).toContainText(destinationPrompt);

    const submit = merge.getByRole("button", { name: "Merge permanently" });
    await expect(submit).toBeDisabled();
    await merge.getByLabel("Merge rationale").fill(rationale);
    await expect(submit).toBeDisabled();
    await merge.getByLabel(/I have read both exact work contexts/).check();
    await expect(submit).toBeEnabled();
    await submit.click();

    // The exact source audit opens in the same pane; the merge panel is gone.
    await expect(page.locator(".toast")).toContainText(
      "Merge recorded. The exact source audit is open; canonical lists are refreshing."
    );
    await expect(pane.locator(".detail-title")).toHaveText(sourceTitle);
    await expect(pane.getByRole("region", { name: "Merge as duplicate" })).toHaveCount(0);
    await expect(pane.locator(".detail-identity .operational-badge.duplicate")).toHaveText("Duplicate");
    await expect(pane.locator(".duplicate-audit-panel")).toBeVisible();
    await expect(pane.locator(".duplicate-direction-grid > div").nth(0)).toContainText(destination.id);
    await expect(pane.locator(".duplicate-direction-grid > div").nth(1)).toContainText(destination.id);
    await expect(pane.locator(".duplicate-merge-fact")).toContainText(rationale);
    await expect(pane.getByRole("button", { name: /Merge as duplicate/ })).toHaveCount(0);
    await expect(pane.getByRole("button", { name: "Edit work item" })).toHaveCount(0);
    await expect(pane.getByRole("button", { name: "Delete work item" })).toHaveCount(0);
    await expect(pane.getByRole("button", { name: "Open canonical work" })).toBeVisible();
    await expect(pane.getByRole("tab", { name: "Graph" })).toHaveAttribute("aria-selected", "true");
    await expect(pane.locator("#detail-panel-graph").getByRole("heading", { name: "Relationships" })).toBeVisible();

    // Canonical lists drop the alias while the pane keeps the audit open.
    await expect(page.locator(".result-count")).toHaveText("1 work record");
    await expect(resultFor(page, source.id)).toHaveCount(0);
    await expect(resultFor(page, destination.id)).toHaveCount(1);

    const merged = await getContext(client, source.id);
    expect(merged.canonical.is_duplicate).toBe(true);
    expect(merged.canonical.canonical_work_item.id).toBe(destination.id);
    expect(merged.work_item.version).toBe(sourceBefore.work_item.version + 1);
    expect(merged.checkpoint_total).toBe(sourceBefore.checkpoint_total);
    expect(merged.recent_events.some((event) =>
      event.event_type === "work_merged" && event.body === rationale
    )).toBe(true);
    const canonical = await getContext(client, destination.id);
    expect(canonical.canonical.is_duplicate).toBe(false);
    expect(canonical.duplicate_member_total).toBe(1);

    await pane.getByRole("button", { name: "Open canonical work" }).click();
    await expect(pane.locator(".detail-title")).toHaveText(destinationTitle);
    await expect(pane.locator(".duplicate-audit-panel")).toHaveCount(0);
    await expect(pane.getByRole("button", { name: /Merge as duplicate/ })).toBeVisible();
  } finally {
    await client.dispose();
  }
});

test("the queue appends pages on scroll while the result count shows the total", async ({ page }, testInfo) => {
  test.slow();
  const token = searchToken("surfacescroll", testInfo);
  const key = testKey(testInfo);
  const total = 45;
  const client = await apiClient();
  try {
    const numbers = Array.from({ length: total }, (_, index) => index + 1);
    for (let start = 0; start < numbers.length; start += 5) {
      await Promise.all(numbers.slice(start, start + 5).map((number) => createWork(client, {
        title: `Scroll item ${String(number).padStart(2, "0")} ${token}`,
        sessionId: `surface-scroll-${key}-${number}`
      })));
    }

    const pageRequests: string[] = [];
    page.on("request", (sent) => {
      const url = sent.url();
      if (url.includes("/work-items?") && url.includes(`q=${token}`)) pageRequests.push(url);
    });

    await openDashboard(page);
    await page.getByRole("searchbox", { name: "Search work items" }).fill(token);
    const count = page.locator(".result-count");
    const cards = page.locator("article.work-item-card");
    await expect(count).toHaveText(`${total} work records`);
    await expect(cards).toHaveCount(WORK_PAGE_SIZE);
    await expect(page.locator(".work-queue").getByRole("button", { name: /^(Previous|Next)$/ })).toHaveCount(0);
    await expect(page.locator(".work-queue .pagination")).toHaveCount(0);
    await expect(page.locator(".work-queue-sentinel")).toHaveCount(1);

    await scrollQueueToEnd(page);
    await expect(cards).toHaveCount(WORK_PAGE_SIZE * 2, { timeout: 15_000 });
    await expect(count).toHaveText(`${total} work records`);
    await expect(page.locator(".work-queue-append-error")).toHaveCount(0);

    await scrollQueueToEnd(page);
    await expect(cards).toHaveCount(total, { timeout: 15_000 });
    await expect(count).toHaveText(`${total} work records`);
    await expect(page.locator(".work-queue-append-error")).toHaveCount(0);

    // Every card is a distinct record and nothing beyond the total was requested.
    const ids = await cards.evaluateAll((elements) => elements.map((element) => element.getAttribute("data-queue-option")));
    expect(new Set(ids).size).toBe(total);
    await expect.poll(() => pageRequests.some((url) => url.includes("offset=40"))).toBe(true);
    await scrollQueueToEnd(page);
    await expect(cards).toHaveCount(total);
    await expect(count).toHaveText(`${total} work records`);
    await expect(page.getByRole("status", { name: "Loading more work items" })).toHaveCount(0);
    expect(pageRequests.some((url) => url.includes("offset=60"))).toBe(false);
    for (const url of pageRequests) expect(url).toContain(`limit=${WORK_PAGE_SIZE}`);
  } finally {
    await client.dispose();
  }
});

test("More filters collapses on demand and auto-opens when a canonical group forces it", async ({ page }, testInfo) => {
  const token = searchToken("surfacefilters", testInfo);
  const key = testKey(testInfo);
  const sourceTitle = `Filter alias ${token}`;
  const destinationTitle = `Filter canonical ${token}`;
  const client = await apiClient();
  try {
    const source = await createWork(client, { title: sourceTitle, sessionId: `surface-filters-${key}-alias` });
    const destination = await createWork(client, { title: destinationTitle, sessionId: `surface-filters-${key}-root` });
    await mergeDirect(client, source.id, destination.id, `Merged outside the browser for ${key}.`, `surface-filters-${key}`);

    await openDashboard(page);
    const toggle = page.getByRole("button", { name: "More filters" });
    const panel = page.locator("#more-filters-panel");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toHaveAttribute("aria-controls", "more-filters-panel");
    await expect(toggle).not.toHaveClass(/is-open/);
    await expect(panel).toHaveCount(0);
    // The result count moved out of the filter row into the queue header.
    await expect(page.locator(".filter-row .result-count")).toHaveCount(0);
    await expect(page.locator(".work-queue-header .result-count")).toHaveText(/root branch/);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(toggle).toHaveClass(/is-open/);
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("group", { name: "Duplicate records" })).toBeVisible();
    await expect(panel.getByRole("radio", { name: "Canonical only" })).toBeChecked();
    const provenance = panel.getByRole("group", { name: "Filter hierarchy by checkpoint provenance" });
    await expect(provenance.getByLabel("Tag")).toBeVisible();
    await expect(provenance.getByLabel("Source client")).toBeVisible();
    await expect(provenance.getByLabel("Source session")).toBeVisible();
    await expect(panel.locator(".duplicate-group-filter")).toHaveCount(0);

    // A filled provenance field keeps the panel open but never locks it open.
    await provenance.getByLabel("Source client").fill("playwright-api");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toHaveCount(0);
    await toggle.click();
    await expect(panel).toBeVisible();
    await expect(provenance.getByLabel("Source client")).toHaveValue("playwright-api");
    await provenance.getByLabel("Source client").fill("");
    await toggle.click();
    await expect(panel).toHaveCount(0);

    // With the panel closed, viewing a duplicate group forces it open again.
    await page.goto(`/?work=${source.id}`);
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    const pane = workPane(page);
    await expect(pane.locator(".detail-title")).toHaveText(sourceTitle);
    await expect(pane.locator(".duplicate-audit-panel")).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toHaveCount(0);

    await pane.getByRole("button", { name: "View duplicate group" }).click();
    await expect(page.locator(".work-detail-pane.is-open")).toHaveCount(0);
    await expect(page).not.toHaveURL(/[?&]work=/);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(toggle).toHaveClass(/is-open/);
    await expect(panel).toBeVisible();
    const chip = panel.locator(".duplicate-group-filter");
    await expect(chip).toContainText("Canonical group");
    await expect(chip).toContainText(destination.id);
    await expect(panel.getByRole("radio", { name: "All records" })).toBeChecked();
    await expect(page.locator(".result-count")).toHaveText("2 work records");
    await expect(resultFor(page, source.id)).toHaveCount(1);
    await expect(resultFor(page, destination.id)).toHaveCount(1);

    // The forced state is an effect, not a lock: the user can still collapse it.
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toHaveCount(0);
    await expect(page.locator(".result-count")).toHaveText("2 work records");
    await toggle.click();
    await expect(chip).toContainText(destination.id);
  } finally {
    await client.dispose();
  }
});

test("the work item ID copies from the pane header", async ({ page }, testInfo) => {
  const token = searchToken("surfacecopy", testInfo);
  const key = testKey(testInfo);
  const title = `Copy id item ${token}`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, sessionId: `surface-copy-${key}` });

    await openDashboard(page);
    await searchFor(page, token, 1);
    const pane = await selectWork(page, title);
    await expect(pane.locator(".detail-id code")).toHaveText(work.id);
    const copyId = pane.getByRole("button", { name: "Copy work item ID" });
    await expect(copyId).not.toHaveClass(/is-copied/);
    await expect(copyId.locator("svg path")).toHaveAttribute("d", COPY_ICON_PATH);

    await copyId.click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(work.id);
    await expect(copyId).toHaveClass(/is-copied/);
    await expect(copyId).toHaveAttribute("title", "Copied");
    await expect(copyId.locator("svg path")).toHaveAttribute("d", CHECK_ICON_PATH);
    await expect(page.locator(".toast")).toContainText(`Work item ID copied: ${work.id}`);
    // Other copy targets keep their own state.
    await expect(pane.getByRole("button", { name: "Copy recall pointer", exact: true })).toBeVisible();
    await expect(workCard(page, title).getByRole("button", { name: /Copy recall pointer/ })).not.toHaveClass(/is-copied/);

    // The check icon reverts after the copied window elapses.
    await expect(copyId).not.toHaveClass(/is-copied/, { timeout: 10_000 });
    await expect(copyId).toHaveAttribute("title", "Copy work item ID");
    await expect(copyId.locator("svg path")).toHaveAttribute("d", COPY_ICON_PATH);

    // The pane's primary pointer copy still produces a recall pointer.
    await pane.getByRole("button", { name: "Copy recall pointer", exact: true }).click();
    const pointer = await page.evaluate(() => navigator.clipboard.readText());
    expect(pointer).toContain(work.id);
    expect(pointer).toContain("recall_work");
    await expect(pane.getByRole("button", { name: "Copied", exact: true })).toBeVisible();
    await expect(copyId).not.toHaveClass(/is-copied/);
  } finally {
    await client.dispose();
  }
});

test("the ?work= query restores the selection on reload and clears with it", async ({ page }, testInfo) => {
  const narrow = narrowProject(testInfo);
  const token = searchToken("surfaceurl", testInfo);
  const key = testKey(testInfo);
  const title = `Restored item ${token}`;
  const prompt = `Context restored from the address bar ${key}.`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, prompt, sessionId: `surface-url-${key}` });
    const selectedURL = new RegExp(`[?&]work=${work.id}(?:&|$)`);

    await openDashboard(page);
    await expect(page).not.toHaveURL(/[?&]work=/);
    await searchFor(page, token, 1);
    await selectWork(page, title);
    await expect(page).toHaveURL(selectedURL);

    await page.reload();
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(page).toHaveURL(selectedURL);
    const pane = workPane(page);
    await expect(pane).toHaveClass(/is-open/);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-id code")).toHaveText(work.id);
    await expect(pane.locator("#detail-panel-context .prompt-body")).toHaveText(prompt);
    await expect(pane.getByRole("button", { name: "Edit work item" })).toBeEnabled();

    if (narrow) {
      // The narrow Back button clears the selection and the address with it.
      await closeDetail(page);
      await expect(pane).toBeHidden();
    } else {
      // Deleting the open item clears the selection and the address with it.
      await pane.getByRole("button", { name: "Delete work item" }).click();
      const dialog = page.getByRole("dialog", { name: "Delete this work item?" });
      await dialog.getByRole("button", { name: "Delete work item" }).click();
      await expect(page.locator(".toast")).toContainText("Work item removed from ordinary project views.");
      await expect(pane).not.toHaveClass(/is-open/);
      await expect(pane.getByRole("heading", { name: "Pick a work item." })).toBeVisible();
    }
    await expect(page).not.toHaveURL(/[?&]work=/);
    await expect(pane.locator(".detail-title")).toHaveCount(0);

    await page.reload();
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(page).not.toHaveURL(/[?&]work=/);
    await expect(workPane(page).locator(".detail-title")).toHaveCount(0);
    if (!narrow) {
      await expect(workPane(page).getByRole("heading", { name: "Pick a work item." })).toBeVisible();
    }
  } finally {
    await client.dispose();
  }
});

test("the selection opens as a full-screen sheet with a Back button below 900px", async ({ page }, testInfo) => {
  const narrow = narrowProject(testInfo);
  const token = searchToken("surfacesheet", testInfo);
  const key = testKey(testInfo);
  const title = `Sheet item ${token}`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, sessionId: `surface-sheet-${key}` });

    await openDashboard(page);
    await searchFor(page, token, 1);
    const pane = workPane(page);
    const back = pane.getByRole("button", { name: "Back to work queue" });
    const card = workCard(page, title);
    const viewport = page.viewportSize();
    expect(viewport).not.toBeNull();

    if (narrow) {
      // Nothing selected: the pane is not rendered over the stacked queue.
      await expect(pane).toBeHidden();
      await expect(card).toBeVisible();

      await card.click();
      await expect(pane).toHaveClass(/is-open/);
      await expect(pane.locator(".detail-title")).toHaveText(title);
      await expect(pane).toHaveCSS("position", "fixed");
      // The sheet slides in from the right; measure it once that animation has finished.
      await pane.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
      const box = await pane.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeCloseTo(0, 0);
      expect(box!.y).toBeCloseTo(0, 0);
      expect(box!.width).toBeCloseTo(viewport!.width, 0);
      expect(box!.height).toBeCloseTo(viewport!.height, 0);
      await expect(back).toBeVisible();
      await expect(back).toBeEnabled();
      await expect(pane.getByRole("tablist", { name: "Work context sections" })).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`[?&]work=${work.id}(?:&|$)`));
      await expect(card).toHaveAttribute("aria-selected", "true");

      await back.click();
      await expect(pane).toBeHidden();
      // Hidden regions leave the accessibility tree, so the class check uses the element itself.
      await expect(page.locator(".work-detail-pane")).not.toHaveClass(/is-open/);
      await expect(card).toHaveAttribute("aria-selected", "false");
      await expect(page).not.toHaveURL(/[?&]work=/);

      // Keyboard activation on a focused card opens the sheet again.
      await card.focus();
      await page.keyboard.press("Enter");
      await expect(pane).toHaveClass(/is-open/);
      await expect(pane.locator(".detail-title")).toHaveText(title);
      await expect(back).toBeVisible();
      await closeDetail(page);
      await expect(pane).toBeHidden();
    } else {
      // Desktop keeps the pane in the grid beside the queue with no Back button.
      await expect(pane).toBeVisible();
      await expect(pane.getByRole("heading", { name: "Pick a work item." })).toBeVisible();
      await expect(back).toBeHidden();

      await card.click();
      await expect(pane).toHaveClass(/is-open/);
      await expect(pane.locator(".detail-title")).toHaveText(title);
      await expect(back).toBeHidden();
      await expect(pane).toHaveCSS("position", "relative");
      const cardBox = await card.boundingBox();
      const paneBox = await pane.boundingBox();
      expect(cardBox).not.toBeNull();
      expect(paneBox).not.toBeNull();
      expect(cardBox!.x + cardBox!.width).toBeLessThanOrEqual(paneBox!.x + 1);
      expect(paneBox!.width).toBeLessThan(viewport!.width);
      await expect(card).toBeInViewport();
      await expect(pane).toBeInViewport();
      await expect(card).toHaveAttribute("aria-selected", "true");
    }
  } finally {
    await client.dispose();
  }
});
