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

type Project = { id: string; name: string; slug: string };

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

// The key that selects the project at this position in the picker: 1 through 9, then 0.
function digitKey(index: number): string {
  return String((index + 1) % 10);
}

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

async function createProject(
  client: APIRequestContext,
  name: string,
  slug?: string
): Promise<Project> {
  const response = await client.post("/api/v1/projects", {
    data: { name, ...(slug ? { slug } : {}) }
  });
  expect(response.status(), `${name}: ${await response.text()}`).toBe(201);
  return await response.json() as Project;
}

async function createWork(
  client: APIRequestContext,
  input: SeedInput,
  projectId = state.projectId
): Promise<WorkItem> {
  const response = await client.post(`/api/v1/projects/${projectId}/work-items`, {
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

async function getContext(
  client: APIRequestContext,
  workItemId: string,
  projectId = state.projectId
): Promise<WorkContext> {
  const response = await client.get(
    `/api/v1/projects/${projectId}/work-items/${workItemId}/context`
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

async function openDashboard(page: Page, projectId = state.projectId): Promise<void> {
  await page.goto("/");
  await page.locator("#project-select").selectOption(projectId);
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
    const tabNames = [
      /^Context$/,
      /^History \d+$/,
      /^Evidence$/,
      /^Graph \d+$/,
      /^Questions \d+$/,
      /^Code review$/,
      /^Activity \d+$/
    ];
    await expect(tabs).toHaveCount(tabNames.length);
    for (const [index, name] of tabNames.entries()) {
      await expect(tabs.nth(index)).toHaveAccessibleName(name);
    }
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

    const reviews = await openTab(paneB, "Code review");
    await expect(paneB.getByRole("tabpanel")).toHaveCount(1);
    await expect(reviews.getByText("No code review has been requested for this work item.", { exact: false })).toBeVisible();

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

test("the Defer menu moves deferred work to another project without changing identity", async ({ page }, testInfo) => {
  const token = searchToken("surfacemove", testInfo);
  const key = testKey(testInfo);
  const projectName = `Move project ${key}`;
  const title = `Movable item ${token}`;
  const client = await apiClient();
  try {
    const sourceProject = await createProject(client, projectName, `move-source-${token}`);
    const targetProject = await createProject(client, projectName, `move-target-${token}`);
    await createProject(client, `Alternate ${projectName}`, `move-alternate-${token}`);
    const work = await createWork(client, {
      title,
      sessionId: `surface-move-${key}`
    }, sourceProject.id);

    await openDashboard(page, sourceProject.id);
    await searchFor(page, token, 1);
    const pane = await selectWork(page, title);
    await pane.getByRole("button", { name: `Defer ${title}` }).click();
    await expect(page.locator(".toast")).toContainText("Deferred and held out of the work queue");
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Deferred");

    const deleteAction = pane.getByRole("button", { name: "Delete work item", exact: true });
    await expect(pane.locator(".delete-move-split")).toHaveCount(0);
    await expect(deleteAction).not.toHaveClass(/status-split-primary/);
    await expect(deleteAction).toBeEnabled();

    const statusChooser = pane.getByRole("button", { name: `Choose an action for ${title}` });
    const statusMenu = pane.getByRole("menu", { name: `Actions for ${title}` });
    await statusChooser.click();
    const parentItems = statusMenu.locator('[data-status-menu-item="true"]');
    await expect(parentItems).toHaveText([
      "Pending",
      "Active",
      "Done",
      "Won’t Do",
      "Promote",
      /^Move›$/
    ]);
    const moveAction = statusMenu.getByRole("menuitem", {
      name: `Move ${title} to another project`,
      exact: true
    });
    await expect(moveAction).toBeEnabled();
    await expect(moveAction).toHaveAttribute("aria-haspopup", "menu");
    await expect(parentItems.last()).toHaveAttribute(
      "aria-label",
      `Move ${title} to another project`
    );
    const menu = page.getByRole("menu", { name: `Move ${title} to project` });
    await moveAction.hover();
    await expect(menu).toBeVisible();
    await expect(menu).toHaveAttribute("id", /.+/);
    await expect(statusMenu).toHaveAttribute(
      "aria-owns",
      await menu.getAttribute("id") ?? ""
    );
    const target = menu.getByRole("menuitem", {
      name: `${projectName} (${targetProject.slug})`,
      exact: true
    });
    await target.hover();
    await expect(menu).toBeVisible();
    await expect(menu.getByText(sourceProject.slug, { exact: true })).toHaveCount(0);

    const detailScroll = pane.locator(".detail-scroll");
    await detailScroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await expect(moveAction).not.toBeInViewport();
    await expect(menu).toBeHidden();
    await detailScroll.evaluate((element) => { element.scrollTop = 0; });
    await expect(moveAction).toBeInViewport();
    await moveAction.hover();
    await expect(menu).toBeVisible();

    await deleteAction.click();
    await expect(statusMenu).toBeHidden();
    await expect(menu).toBeHidden();
    const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
    await expect(deleteDialog).toBeVisible();
    await deleteDialog.getByRole("button", { name: "Keep work item" }).click();

    await statusChooser.focus();
    await page.keyboard.press("ArrowDown");
    await expect(parentItems.first()).toBeFocused();
    await page.keyboard.press("End");
    await expect(moveAction).toBeFocused();
    await expect(menu).toBeVisible();
    await page.keyboard.press("ArrowRight");
    const targets = menu.locator(":scope > [role=menuitem]:not(:disabled)");
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(targets.last()).toBeFocused();
    await page.keyboard.press("Home");
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("End");
    await expect(targets.last()).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(menu).toBeHidden();
    await expect(moveAction).toBeFocused();
    await expect(statusMenu).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(menu).toBeVisible();
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(moveAction).toBeFocused();
    await expect(statusMenu).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(statusMenu).toBeHidden();
    await expect(statusChooser).toBeFocused();

    await statusChooser.click();
    await moveAction.focus();
    await expect(menu).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(statusMenu).toBeHidden();
    await expect(menu).toBeHidden();
    await expect(deleteAction).toBeFocused();

    const previousAction = pane.getByRole("button", { name: /Merge as duplicate/ });
    await statusChooser.click();
    await moveAction.focus();
    await expect(menu).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(statusMenu).toBeHidden();
    await expect(menu).toBeHidden();
    await expect(previousAction).toBeFocused();

    await statusChooser.click();
    await moveAction.focus();
    await expect(menu).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(statusMenu).toBeHidden();
    await expect(menu).toBeHidden();
    await expect(deleteAction).toBeFocused();

    await statusChooser.click();
    await moveAction.focus();
    await expect(menu).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(targets.first()).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(statusMenu).toBeHidden();
    await expect(menu).toBeHidden();
    await expect(previousAction).toBeFocused();

    await statusChooser.click();
    await moveAction.focus();
    await expect(menu).toBeVisible();
    await testInfo.attach("Defer menu Move project submenu", {
      body: await page.screenshot(),
      contentType: "image/png"
    });
    await expect(target).toBeEnabled();
    await expect(target.locator("small")).toHaveText(targetProject.slug);
    await target.click();

    await expect(page.locator("#project-select")).toHaveValue(targetProject.id);
    await expect(page).toHaveURL(new RegExp(`[?&]work=${work.id}(?:&|$)`));
    await expect(pane).toHaveClass(/is-open/);
    await expect(pane.locator(".detail-id code")).toHaveText(work.id);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Deferred");
    await expect(page.getByRole("button", { name: "Deferred", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    const moved = await getContext(client, work.id, targetProject.id);
    expect(moved.work_item).toMatchObject({
      id: work.id,
      project_id: targetProject.id,
      status: "deferred"
    });
    const formerSource = await client.get(
      `/api/v1/projects/${sourceProject.id}/work-items/${work.id}/context`
    );
    expect(formerSource.status()).toBe(404);
  } finally {
    await client.dispose();
  }
});

test("an externally moved open item follows its verified project without losing drafts", async ({ page }, testInfo) => {
  test.slow();
  const token = searchToken("surfaceexternalmove", testInfo);
  const key = testKey(testInfo);
  const sourceProjectName = `External move source ${key}`;
  const targetProjectName = `External move target ${key}`;
  const title = `Externally moved item ${token}`;
  const checkpointDraft = `Unsaved checkpoint retained across external move ${key}.`;
  const reportDraft = `Unsaved human report retained across external move ${key}.`;
  const fyiDraft = `Unsaved FYI retained across external move ${key}.`;
  const evidenceDraft = `Unsaved verification retained across external move ${key}`;
  const client = await apiClient();
  try {
    const sourceProject = await createProject(
      client,
      sourceProjectName,
      `external-move-source-${token}`
    );
    const targetProject = await createProject(
      client,
      targetProjectName,
      `external-move-target-${token}`
    );
    const settingsResponse = await client.get(
      `/api/v1/projects/${targetProject.id}/settings`
    );
    expect(settingsResponse.ok(), await settingsResponse.text()).toBe(true);
    const targetSettings = await settingsResponse.json() as { revision: string };
    const changedSettings = await client.patch(
      `/api/v1/projects/${targetProject.id}/settings`,
      {
        data: {
          expected_revision: targetSettings.revision,
          job_completion_report_prompt:
            `Target-project instructions for external move recovery ${key}.`
        }
      }
    );
    expect(changedSettings.ok(), await changedSettings.text()).toBe(true);
    const revisedTargetSettings = await changedSettings.json() as { revision: string };
    const work = await createWork(client, {
      title,
      sessionId: `surface-external-move-${key}`
    }, sourceProject.id);

    // Keep recovery deterministic by exercising the activity catch-up path rather than
    // depending on websocket delivery timing.
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.goto("/");
    await page.locator("#project-select").selectOption(sourceProject.id);
    await searchFor(page, token, 1);
    const pane = await selectWork(page, title);
    const contextPanel = await openTab(pane, "Context");
    await contextPanel.getByLabel("Checkpoint text").fill(checkpointDraft);
    await contextPanel.getByLabel(/^Human summary/).fill(reportDraft);
    await contextPanel.getByRole("button", { name: "Add FYI", exact: true }).click();
    await contextPanel.getByLabel(/^FYI 1/).fill(fyiDraft);
    await contextPanel.locator("details.completion-evidence-disclosure > summary").click();
    await contextPanel.getByRole("button", { name: "Add verification result" }).click();
    await contextPanel
      .getByRole("group", { name: "Verification result 1" })
      .getByLabel("Name")
      .fill(evidenceDraft);

    const moved = await client.post(
      `/api/v1/projects/${sourceProject.id}/work-items/${work.id}/move`,
      {
        data: {
          target_project_id: targetProject.id,
          expected_version: work.version,
          client_operation_id: crypto.randomUUID(),
          actor: {
            actor_client: "playwright-api",
            actor_session_id: `surface-external-move-${key}`,
            actor_model: null
          }
        }
      }
    );
    expect(moved.ok(), await moved.text()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));

    await expect(page.locator(".toast")).toContainText(
      `moved to “${targetProjectName}” in another session`
    );
    await expect(page.locator("#project-select")).toHaveValue(targetProject.id);
    await expect(page).toHaveURL(new RegExp(`[?&]work=${work.id}(?:&|$)`));
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Pending");
    await expect(page.getByRole("button", { name: "Pending", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await expect(contextPanel.getByLabel("Checkpoint text")).toHaveValue(checkpointDraft);
    await expect(contextPanel.getByLabel(/^Human summary/)).toHaveValue(reportDraft);
    await expect(contextPanel.getByLabel(/^FYI 1/)).toHaveValue(fyiDraft);
    await contextPanel.locator("details.completion-evidence-disclosure > summary").click();
    await expect(
      contextPanel
        .getByRole("group", { name: "Verification result 1" })
        .getByLabel("Name")
    ).toHaveValue(evidenceDraft);
    await expect(contextPanel.getByText(
      `Project report instructions · revision ${revisedTargetSettings.revision}`,
      { exact: true }
    )).toBeVisible();
    await expect(contextPanel.getByText(
      "Project instructions changed. Review the latest instructions and your report before accepting this revision.",
      { exact: true }
    )).toHaveCount(0);
    const targetContext = await getContext(client, work.id, targetProject.id);
    expect(targetContext.work_item).toMatchObject({
      id: work.id,
      project_id: targetProject.id,
      status: "pending"
    });
  } finally {
    await client.dispose();
  }
});

test("Move retries keep every draft and follow an item that moves again", async ({ page }, testInfo) => {
  test.slow();
  const token = searchToken("surfacemoverace", testInfo);
  const key = testKey(testInfo);
  const sourceProject = `Receipt race source ${key}`;
  const targetProject = `Receipt race target ${key}`;
  const finalProject = `Receipt race final ${key}`;
  const title = `Receipt-raced item ${token}`;
  const editTitle = `Unsaved edit for ${title}`;
  const checkpointDraft = `Checkpoint kept through receipt target race ${key}.`;
  const reportDraft = `Report kept through receipt target race ${key}.`;
  const fyiDraft = `FYI kept through receipt target race ${key}.`;
  const evidenceDraft = `Evidence kept through receipt target race ${key}`;
  const client = await apiClient();
  try {
    const source = await createProject(
      client,
      sourceProject,
      `receipt-race-source-${token}`
    );
    const target = await createProject(
      client,
      targetProject,
      `receipt-race-target-${token}`
    );
    const final = await createProject(
      client,
      finalProject,
      `receipt-race-final-${token}`
    );
    const work = await createWork(client, {
      title,
      sessionId: `surface-move-receipt-race-${key}`
    }, source.id);

    await openDashboard(page, source.id);
    await searchFor(page, token, 1);
    const pane = await selectWork(page, title);
    const contextPanel = await openTab(pane, "Context");
    await contextPanel.getByLabel("Checkpoint text").fill(checkpointDraft);
    await contextPanel.getByLabel(/^Human summary/).fill(reportDraft);
    await contextPanel.getByRole("button", { name: "Add FYI", exact: true }).click();
    await contextPanel.getByLabel(/^FYI 1/).fill(fyiDraft);
    await contextPanel.locator("details.completion-evidence-disclosure > summary").click();
    await contextPanel.getByRole("button", { name: "Add verification result" }).click();
    await contextPanel
      .getByRole("group", { name: "Verification result 1" })
      .getByLabel("Name")
      .fill(evidenceDraft);
    await pane.getByRole("button", { name: "Edit work item" }).click();
    await contextPanel.getByLabel("Title").fill(editTitle);

    let intercepted = false;
    const capturedMove: { work: WorkItem | null } = { work: null };
    let targetContextAttempts = 0;
    await page.route(
      `**/api/mnemonic/projects/${target.id}/work-items/${work.id}/context?*`,
      async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        targetContextAttempts += 1;
        if (targetContextAttempts === 1) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Temporary target-context failure." })
          });
          return;
        }
        if (targetContextAttempts === 2) {
          await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
          return;
        }
        await route.continue();
      }
    );
    await page.route(
      `**/api/mnemonic/projects/${source.id}/work-items/${work.id}/move`,
      async (route) => {
        if (intercepted || route.request().method() !== "POST") {
          await route.continue();
          return;
        }
        intercepted = true;
        const response = await route.fetch();
        expect(response.ok(), await response.text()).toBe(true);
        const responseBody = await response.body();
        capturedMove.work = (JSON.parse(responseBody.toString("utf8")) as {
          work_item: WorkItem;
        }).work_item;
        await route.fulfill({ response, body: responseBody });
      }
    );

    const moveConfirmed = new Promise<void>((resolve, reject) => {
      page.once("dialog", async (checkpointDialog) => {
        try {
          expect(checkpointDialog.message()).toBe(
            "Discard your unsaved checkpoint and job completion report?"
          );
          page.once("dialog", async (editDialog) => {
            try {
              expect(editDialog.message()).toBe("Discard your unsaved work-item edits?");
              await editDialog.accept();
              resolve();
            } catch (error) {
              reject(error);
            }
          });
          await checkpointDialog.accept();
        } catch (error) {
          reject(error);
        }
      });
    });
    const actionChooser = pane.getByRole("button", { name: `Choose an action for ${title}` });
    await actionChooser.click();
    const actionMenu = pane.getByRole("menu", { name: `Actions for ${title}` });
    const moveAction = actionMenu.getByRole("menuitem", {
      name: `Move ${title} to another project`,
      exact: true
    });
    await moveAction.focus();
    const projectMenu = page.getByRole("menu", { name: `Move ${title} to project` });
    await expect(projectMenu).toBeVisible();
    await Promise.all([
      moveConfirmed,
      projectMenu.getByRole("menuitem", {
        name: `${targetProject} (${target.slug})`,
        exact: true
      }).click()
    ]);

    await expect(pane.getByRole("button", { name: "Try again" })).toBeVisible();
    await expect.poll(() => targetContextAttempts).toBe(1);

    await pane.getByRole("button", { name: "Try again" }).click();
    await expect.poll(() => targetContextAttempts).toBe(2);
    await expect(pane.getByRole("button", { name: "Try again" })).toBeVisible();

    const movedVersion = capturedMove.work?.version;
    if (movedVersion === undefined) {
      throw new Error("The browser Move response was not captured.");
    }
    const movedAgain = await client.post(
      `/api/v1/projects/${target.id}/work-items/${work.id}/move`,
      {
        data: {
          target_project_id: final.id,
          expected_version: movedVersion,
          client_operation_id: crypto.randomUUID(),
          actor: {
            actor_client: "playwright-api",
            actor_session_id: `surface-move-receipt-race-${key}`,
            actor_model: null
          }
        }
      }
    );
    expect(movedAgain.ok(), await movedAgain.text()).toBe(true);

    await pane.getByRole("button", { name: "Try again" }).click();
    await expect(page.locator(".toast")).toContainText(
      `already moved to “${finalProject}” before this move finished`
    );
    expect(intercepted).toBe(true);
    await expect(page.locator("#project-select")).toHaveValue(final.id);
    await expect(page).toHaveURL(new RegExp(`[?&]work=${work.id}(?:&|$)`));
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText("Pending");
    await expect(contextPanel.getByLabel("Title")).toHaveValue(editTitle);
    await contextPanel.getByRole("button", { name: "Cancel" }).click();
    await expect(contextPanel.getByLabel("Checkpoint text")).toHaveValue(checkpointDraft);
    await expect(contextPanel.getByLabel(/^Human summary/)).toHaveValue(reportDraft);
    await expect(contextPanel.getByLabel(/^FYI 1/)).toHaveValue(fyiDraft);
    await contextPanel.locator("details.completion-evidence-disclosure > summary").click();
    await expect(
      contextPanel
        .getByRole("group", { name: "Verification result 1" })
        .getByLabel("Name")
    ).toHaveValue(evidenceDraft);
    const finalContext = await getContext(client, work.id, final.id);
    expect(finalContext.work_item).toMatchObject({
      id: work.id,
      project_id: final.id,
      status: "pending"
    });
    const staleTarget = await client.get(
      `/api/v1/projects/${target.id}/work-items/${work.id}/context`
    );
    expect(staleTarget.status()).toBe(404);
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
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("recall_work");
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

test("changing the lifecycle filter deselects the open work item", async ({ page }, testInfo) => {
  test.skip(
    narrowProject(testInfo),
    "Below 900px the open sheet covers the filter row, so no filter is reachable with a selection."
  );
  const token = searchToken("surfacefilter", testInfo);
  const key = testKey(testInfo);
  const title = `Filtered item ${token}`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, sessionId: `surface-filter-${key}` });
    const selectedURL = new RegExp(`[?&]work=${work.id}(?:&|$)`);
    const pane = workPane(page);
    const pending = page.getByRole("button", { name: "Pending", exact: true });

    await openDashboard(page);
    await searchFor(page, token, 1);
    await selectWork(page, title);
    await expect(pane).toHaveClass(/is-open/);
    await expect(page).toHaveURL(selectedURL);

    // The pending record is absent from the deferred queue, so the pane cannot outlive it.
    await page.getByRole("button", { name: "Deferred", exact: true }).click();
    await expect(pane).not.toHaveClass(/is-open/);
    await expect(pane.getByRole("heading", { name: "Pick a work item." })).toBeVisible();
    await expect(page).not.toHaveURL(/[?&]work=/);

    // Reselecting the filter already in force is not a change and keeps the selection.
    await pending.click();
    await searchFor(page, token, 1);
    await selectWork(page, title);
    await expect(page).toHaveURL(selectedURL);
    await pending.click();
    await expect(pane).toHaveClass(/is-open/);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    await expect(page).toHaveURL(selectedURL);

    // Clearing filters returns the queue to Pending and drops the selection on the same rule.
    await page.getByRole("button", { name: "All", exact: true }).click();
    await expect(pane).not.toHaveClass(/is-open/);
    await searchFor(page, token, 1);
    await selectWork(page, title);
    await expect(page).toHaveURL(selectedURL);
    await searchFor(page, `${token}nomatch`, 0);
    await expect(pane).toHaveClass(/is-open/);
    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(pending).toHaveAttribute("aria-pressed", "true");
    await expect(pane).not.toHaveClass(/is-open/);
    await expect(page).not.toHaveURL(/[?&]work=/);
  } finally {
    await client.dispose();
  }
});

test("Escape deselects the open work item", async ({ page }, testInfo) => {
  const token = searchToken("surfaceescape", testInfo);
  const key = testKey(testInfo);
  const title = `Escapable item ${token}`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, sessionId: `surface-escape-${key}` });
    const selectedURL = new RegExp(`[?&]work=${work.id}(?:&|$)`);
    // Below 900px a closed pane is display:none, where a role locator stops resolving,
    // so the open/closed assertions below go through the class instead.
    const pane = page.locator(".work-detail-pane");

    await openDashboard(page);
    await searchFor(page, token, 1);
    await selectWork(page, title);
    await expect(pane).toHaveClass(/is-open/);
    await expect(page).toHaveURL(selectedURL);

    await page.keyboard.press("Escape");
    await expect(pane).not.toHaveClass(/is-open/);
    await expect(page).not.toHaveURL(/[?&]work=/);
    await expect(workCard(page, title)).toHaveAttribute("aria-selected", "false");
    // Below 900px a closed pane is display:none, so only the desktop column shows the
    // placeholder that replaces the record, and with it the queue's whole keyboard map.
    if (!narrowProject(testInfo)) {
      const empty = workPane(page).locator(".detail-empty");
      await expect(empty.getByRole("heading", { name: "Pick a work item." })).toBeVisible();
      await expect(empty.locator(".detail-empty-hint")).toHaveText([
        "select work item (up/down)",
        "cycle states (left/right)",
        "select a project"
      ]);
      await expect(empty.locator(".key-digits")).toHaveText("1–0");
      // The caps hold the shapes a keyboard gives them: up alone on the row above,
      // centred over down, with left and right beside it.
      const box = async (selector: string) => {
        const rect = await empty.locator(selector).boundingBox();
        expect(rect, `${selector} is not rendered`).not.toBeNull();
        return rect!;
      };
      const up = await box(".key-cluster .key-up");
      const left = await box(".key-cluster .key-left");
      const down = await box(".key-cluster .key-down");
      const right = await box(".key-cluster .key-right");
      expect(up.y + up.height).toBeLessThanOrEqual(left.y);
      expect(Math.abs(up.x - down.x)).toBeLessThan(1);
      expect(left.x + left.width).toBeLessThanOrEqual(down.x);
      expect(down.x + down.width).toBeLessThanOrEqual(right.x);
      expect(Math.abs(left.y - down.y)).toBeLessThan(1);
      expect(Math.abs(right.y - down.y)).toBeLessThan(1);
      // The digit pair centres on the cluster's own axis below it, and all three labels
      // start on one edge clear of the caps: two key groups, one column of text.
      const cluster = await box(".key-cluster");
      const digits = await box(".key-digits");
      expect(Math.abs((cluster.x + cluster.width / 2) - (digits.x + digits.width / 2)))
        .toBeLessThan(1);
      expect(digits.y).toBeGreaterThanOrEqual(left.y + left.height);
      const edges: number[] = [];
      for (const label of await empty.locator(".detail-empty-hint").all()) {
        edges.push((await label.boundingBox())!.x);
      }
      expect(Math.max(...edges) - Math.min(...edges)).toBeLessThan(1);
      expect(Math.min(...edges)).toBeGreaterThanOrEqual(cluster.x + cluster.width);
    }

    // A second press has nothing to close and leaves the queue exactly as it is.
    await page.keyboard.press("Escape");
    await expect(pane).not.toHaveClass(/is-open/);
    await expect(page.locator(".result-count")).toHaveText("1 work record");

    // The keys stay together: a selection made with the arrows drops with Escape too.
    await page.getByRole("searchbox", { name: "Search work items" }).blur();
    await page.keyboard.press("ArrowDown");
    await expect(workCard(page, title)).toHaveAttribute("aria-selected", "true");
    await expect(page).toHaveURL(selectedURL);
    await page.keyboard.press("Escape");
    await expect(pane).not.toHaveClass(/is-open/);
    await expect(page).not.toHaveURL(/[?&]work=/);
  } finally {
    await client.dispose();
  }
});

test("c copies the open record's recall pointer, but not from inside the pane", async ({ page }, testInfo) => {
  const token = searchToken("surfacecopykey", testInfo);
  const key = testKey(testInfo);
  const title = `Pointer key item ${token}`;
  const client = await apiClient();
  try {
    const work = await createWork(client, { title, sessionId: `surface-copy-key-${key}` });
    // Read rather than seed: writing the clipboard from an evaluate has no user
    // activation behind it, so every "nothing happened" check compares before to after.
    const clipboard = async () => await page.evaluate(() => navigator.clipboard.readText());
    const unchangedThrough = async (press: () => Promise<void>) => {
      const before = await clipboard();
      await press();
      expect(await clipboard()).toBe(before);
    };

    await openDashboard(page);
    await searchFor(page, token, 1);
    const card = workCard(page, title);
    const cardCopy = card.getByRole("button", { name: /Copy recall pointer/ });
    const searchbox = page.getByRole("searchbox", { name: "Search work items" });

    // Nothing is open yet, so the key has no record to copy.
    await searchbox.blur();
    await unchangedThrough(() => page.keyboard.press("c"));
    await expect(cardCopy).not.toHaveClass(/is-copied/);

    // The arrows open a record; c copies that one, with the button's own copied state.
    await page.keyboard.press("ArrowDown");
    await expect(card).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("c");
    await expect(page.locator(".toast")).toContainText("Recall pointer copied");
    const pointer = await clipboard();
    expect(pointer).toContain(work.id);
    expect(pointer).toContain("recall_work");
    await expect(cardCopy).toHaveClass(/is-copied/);

    // Caps Lock reports an uppercase letter with no Shift held, so it copies too. The
    // copied window has to elapse first, or the second copy has no visible signal.
    await expect(cardCopy).not.toHaveClass(/is-copied/, { timeout: 10_000 });
    await page.keyboard.press("C");
    await expect(cardCopy).toHaveClass(/is-copied/);
    expect(await clipboard()).toBe(pointer);

    // A real Shift is a different press and is refused.
    await unchangedThrough(() => page.keyboard.press("Shift+c"));

    // Inside the open record the letter belongs to the pane, which carries its own
    // copy button. Below 900px the pane is a full-screen sheet; either way it is the
    // record's own region that holds focus here.
    await closeDetail(page);
    const pane = await selectWork(page, title);
    await pane.getByRole("button", { name: "Copy work item ID" }).focus();
    await unchangedThrough(() => page.keyboard.press("c"));

    // And the search field keeps the letter it is typed into.
    await closeDetail(page);
    await searchbox.fill("");
    await unchangedThrough(() => searchbox.press("c"));
    await expect(searchbox).toHaveValue("c");
  } finally {
    await client.dispose();
  }
});

test("the horizontal arrows walk the lifecycle filters", async ({ page }, testInfo) => {
  test.skip(
    narrowProject(testInfo),
    "The stacked layout below 900px has no divider for the same keys to yield to."
  );
  // The rendered order of the filter row, which the arrows follow.
  const order = ["Pending", "Active", "Dropped", "Deferred", "Done", "Won’t do", "Promoted", "All"];
  const filter = (name: string) => page.getByRole("button", { name, exact: true });
  const pressed = async (name: string) => {
    for (const label of order) {
      await expect(filter(label)).toHaveAttribute("aria-pressed", String(label === name));
    }
  };

  await openDashboard(page);
  // The picker steers itself with the arrow keys while focused, so leave it first.
  await page.locator("#project-select").blur();
  await pressed("Pending");

  for (const label of order.slice(1)) {
    await page.keyboard.press("ArrowRight");
    await pressed(label);
  }

  // The row is a ring in both directions rather than dead-ending on All or Pending.
  await page.keyboard.press("ArrowRight");
  await pressed("Pending");
  await page.keyboard.press("ArrowLeft");
  await pressed("All");
  await page.keyboard.press("ArrowLeft");
  await pressed("Promoted");

  // The filter that the arrows landed on is the one that persists.
  await expect.poll(() => page.evaluate(() => localStorage.getItem("mnemonic.status")))
    .toBe("promoted");

  // Inside the search field the arrows keep their typing meaning.
  const searchbox = page.getByRole("searchbox", { name: "Search work items" });
  await searchbox.focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowRight");
  await pressed("Promoted");
  await searchbox.blur();

  // The surface resizer steps its own split with the same keys while it holds focus.
  const separator = page.getByRole("separator", { name: "Resize the work queue" });
  await separator.focus();
  const before = await separator.getAttribute("aria-valuenow");
  await page.keyboard.press("ArrowLeft");
  await expect(separator).not.toHaveAttribute("aria-valuenow", String(before));
  await pressed("Promoted");
});

test("the digit keys select a project", async ({ page }, testInfo) => {
  const key = testKey(testInfo);
  // Sorts near the front of the picker, which orders by name, so this fixture stays
  // inside the bound range however many projects earlier specs left behind.
  const name = `AAA shortcut ${key}`;
  const client = await apiClient();
  try {
    const response = await client.post("/api/v1/projects", {
      data: { name, description: "Disposable fixture for the workspace digit keys." }
    });
    expect(response.status(), await response.text()).toBe(201);
    const shortcutProject = await response.json() as { id: string };

    await openDashboard(page);
    const select = page.locator("#project-select");
    const breadcrumb = page.locator(".breadcrumb");
    // The keys are named in the quiet placeholder now, not in the option text.
    await expect(select.locator("option").first()).not.toHaveText(/^\d+ · /);

    // Earlier specs seed projects of their own, so this fixture's position is read
    // rather than assumed, and the project it switches back to is any other bound one.
    const options = select.locator("option");
    const labels = await options.allTextContents();
    const values = await options.evaluateAll((items) =>
      items.map((item) => (item as HTMLOptionElement).value));
    const shortcutIndex = values.indexOf(shortcutProject.id);
    expect(shortcutIndex).toBeGreaterThanOrEqual(0);
    expect(shortcutIndex).toBeLessThan(10);
    expect(labels[shortcutIndex]).toBe(name);
    const returnIndex = values.findIndex((id, index) => index < 10 && id !== shortcutProject.id);
    expect(returnIndex).toBeGreaterThanOrEqual(0);
    await expect(select).toHaveValue(state.projectId);

    // The picker steers itself with the arrow keys while focused, but a digit is not
    // one of its own, so it still switches the workspace.
    await page.keyboard.press(digitKey(shortcutIndex));
    await expect(select).toHaveValue(shortcutProject.id);
    await expect(breadcrumb).toContainText(name);

    // A second key comes back, from a picker that no longer holds focus.
    await select.blur();
    await page.keyboard.press(digitKey(returnIndex));
    await expect(select).toHaveValue(values[returnIndex]);
    await expect(breadcrumb).toContainText(labels[returnIndex]);

    // A digit is something a person types, so the search field keeps every one of them.
    const searchbox = page.getByRole("searchbox", { name: "Search work items" });
    await searchbox.fill(digitKey(shortcutIndex));
    await expect(searchbox).toHaveValue(digitKey(shortcutIndex));
    await expect(select).toHaveValue(values[returnIndex]);
    await searchbox.fill("");
    await searchbox.blur();

    // A digit past the end of the workspace is unbound and changes nothing.
    if (labels.length < 10) {
      const current = await select.inputValue();
      await page.keyboard.press(digitKey(labels.length));
      await expect(select).toHaveValue(current);
    }
  } finally {
    await client.dispose();
  }
});

type PaneCrossfade = {
  outgoing: string[];
  incoming: string[];
  paneTitle: string | null;
};

// Changing the filter and reading the transition inside one task removes every race with a
// cross-dissolve that retires itself half a second later. The read waits a frame first, so
// the browser has built the pseudo-element tree and started its animations.
async function crossfadeOnFilter(
  page: Page,
  trigger: { filter: string } | { key: "ArrowLeft" | "ArrowRight" }
): Promise<PaneCrossfade> {
  return await page.evaluate(async (chosen) => {
    if ("filter" in chosen) {
      const button = [...document.querySelectorAll<HTMLButtonElement>(".filter-button")]
        .find((candidate) => candidate.textContent?.trim() === chosen.filter);
      if (!button) throw new Error(`The ${chosen.filter} lifecycle filter is not rendered.`);
      button.click();
    } else {
      // The queue's own shortcut walks the same row through the same handler.
      window.dispatchEvent(new KeyboardEvent("keydown", { key: chosen.key, bubbles: true }));
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const fades = document.getAnimations().flatMap((animation) => {
      const pseudo = (animation.effect as KeyframeEffect | null)?.pseudoElement ?? "";
      const name = (animation as Animation & { animationName?: string }).animationName ?? "";
      // Each captured half also runs the browser's blend-mode companion animation, which
      // carries the same timing; the fade is the one that states the crossfade.
      if (!name.startsWith("-ua-view-transition-fade")) return [];
      const style = getComputedStyle(document.documentElement, pseudo);
      return [`${pseudo} ${style.animationDuration} ${style.animationTimingFunction}`];
    }).sort();
    const half = (kind: string) => fades
      .filter((description) => description.startsWith(`::view-transition-${kind}(`));
    return {
      outgoing: half("old"),
      incoming: half("new"),
      paneTitle: document.querySelector(".work-detail-pane .detail-title")?.textContent ?? null
    };
  }, trigger);
}

// Nothing of a transition may outlive it: a pane still holding a view-transition-name
// would be captured on its own by every later transition, the theme's included.
async function crossfadeSettled(page: Page): Promise<void> {
  await expect
    .poll(async () => await page.evaluate(() => [
      document.documentElement.hasAttribute("data-pane-crossfade"),
      ...[".work-queue", ".work-detail-pane"].map((selector) => Boolean(
        document.querySelector<HTMLElement>(selector)?.style.viewTransitionName
      ))
    ]))
    .toEqual([false, false, false]);
}

test("changing the lifecycle filter cross-dissolves the queue and the pane it retires",
  async ({ page }, testInfo) => {
    test.skip(
      narrowProject(testInfo),
      "Below 900px the open sheet covers the filter row, so no filter is reachable with a selection."
    );
    const token = searchToken("surfacecrossfade", testInfo);
    const key = testKey(testInfo);
    const title = `Dissolved item ${token}`;
    const easeIn = "cubic-bezier(0.55, 0, 1, 0.45)";
    const easeOut = "cubic-bezier(0, 0.55, 0.45, 1)";
    // Both halves of both panes run for the stylesheet's one --pane-crossfade-duration.
    const span = "0.4s";
    const client = await apiClient();
    try {
      await createWork(client, { title, sessionId: `surface-crossfade-${key}` });
      const pane = workPane(page);

      await openDashboard(page);
      await searchFor(page, token, 1);
      await selectWork(page, title);

      // The pending record is absent from the deferred queue, so both panes change at once,
      // and each is captured on its own for the same span on the two circ curves. No root
      // half appears: everything the filter did not rename swaps at once, so the button it
      // was clicked on answers immediately.
      const dissolving = await crossfadeOnFilter(page, { filter: "Deferred" });
      expect(dissolving.outgoing).toEqual([
        `::view-transition-old(work-detail) ${span} ${easeOut}`,
        `::view-transition-old(work-queue) ${span} ${easeOut}`
      ]);
      expect(dissolving.incoming).toEqual([
        `::view-transition-new(work-detail) ${span} ${easeIn}`,
        `::view-transition-new(work-queue) ${span} ${easeIn}`
      ]);
      // The outgoing record lives in the capture, not in a second copy of the pane.
      expect(dissolving.paneTitle).toBeNull();

      await expect(pane.getByRole("heading", { name: "Pick a work item." })).toBeVisible();
      await crossfadeSettled(page);

      // With no record open the detail pane shows the same empty state either way, so only
      // the queue is captured. The horizontal-arrow shortcut reaches the same filter change
      // as the buttons, so it dissolves the queue the same way.
      const queueOnly = await crossfadeOnFilter(page, { key: "ArrowRight" });
      expect(queueOnly.outgoing).toEqual([`::view-transition-old(work-queue) ${span} ${easeOut}`]);
      expect(queueOnly.incoming).toEqual([`::view-transition-new(work-queue) ${span} ${easeIn}`]);
      await crossfadeSettled(page);

      // A reader who asked for less motion gets the plain swap.
      await page.emulateMedia({ reducedMotion: "reduce" });
      const reduced = await crossfadeOnFilter(page, { filter: "Pending" });
      expect(reduced.outgoing).toEqual([]);
      expect(reduced.incoming).toEqual([]);
    } finally {
      await page.emulateMedia({ reducedMotion: null });
      await client.dispose();
    }
  });

test("the queue and pane split is draggable, keyboard-adjustable, and remembered", async ({ page }, testInfo) => {
  test.skip(narrowProject(testInfo), "The stacked layout below 900px has no divider.");
  const token = searchToken("surfacesplit", testInfo);
  const key = testKey(testInfo);
  const title = `Split item ${token}`;
  const client = await apiClient();
  try {
    await createWork(client, { title, sessionId: `surface-split-${key}` });

    await openDashboard(page);
    await searchFor(page, token, 1);
    const surface = page.locator(".work-surface");
    const queue = page.locator(".work-queue");
    const pane = workPane(page);
    const separator = page.getByRole("separator", { name: "Resize the work queue" });
    const width = async (target: Locator) => (await target.boundingBox())!.width;
    const storedSplit = () => page.evaluate(() => localStorage.getItem("mnemonic.work-split"));

    await expect(separator).toBeVisible();
    await expect(separator).toHaveAttribute("aria-valuenow", "35");
    expect(await storedSplit()).toBeNull();
    const surfaceWidth = await width(surface);
    const initialQueue = await width(queue);
    expect(initialQueue / surfaceWidth).toBeCloseTo(0.35, 1);

    // Dragging the divider widens the queue and stores the new share.
    const handle = (await separator.boundingBox())!;
    const handleX = handle.x + handle.width / 2;
    const handleY = handle.y + handle.height / 2;
    await page.mouse.move(handleX, handleY);
    await page.mouse.down();
    await page.mouse.move(handleX + 160, handleY, { steps: 8 });
    await expect(surface).toHaveClass(/is-resizing/);
    await page.mouse.up();
    await expect(surface).not.toHaveClass(/is-resizing/);
    const widenedQueue = await width(queue);
    expect(widenedQueue).toBeGreaterThan(initialQueue + 120);
    const stored = Number(await storedSplit());
    expect(stored).toBeGreaterThan(40);
    await expect(separator).toHaveAttribute("aria-valuenow", String(Math.round(stored)));

    // The pane reflows to its narrower column: nothing overflows the page.
    await selectWork(page, title);
    await expect(pane.locator(".detail-facts")).toBeVisible();
    const paneBox = (await pane.boundingBox())!;
    expect(paneBox.x).toBeGreaterThan(handleX + 100);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    // A reload restores the stored share.
    await page.reload();
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(separator).toHaveAttribute("aria-valuenow", String(Math.round(stored)));
    expect(Math.abs(await width(queue) - widenedQueue)).toBeLessThan(4);

    // Keyboard steps move the split without a pointer.
    await separator.focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await expect(separator).toHaveAttribute("aria-valuenow", String(Math.round(stored - 4)));
    expect(await width(queue)).toBeLessThan(widenedQueue - 10);
    await page.keyboard.press("End");
    await expect(separator).toHaveAttribute("aria-valuenow", "70");
    expect(Number(await storedSplit())).toBe(70);
    // The pane never collapses below its minimum even at the widest queue.
    expect(await width(pane)).toBeGreaterThanOrEqual(440);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    // Double-click forgets the preference and returns to the stylesheet default.
    await separator.dblclick();
    expect(await storedSplit()).toBeNull();
    await expect(separator).toHaveAttribute("aria-valuenow", "35");
    expect(Math.abs(await width(queue) - initialQueue)).toBeLessThan(4);
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
