import { fillFixtureReport } from "./job-report-fixture";
import { readFile } from "node:fs/promises";
import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Page
} from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard, workPane } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type WorkItem = { id: string; version: number };

async function apiClient(): Promise<APIRequestContext> {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("The disposable E2E API is not configured.");
  return request.newContext({
    baseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
}

async function deleteFixture(client: APIRequestContext, workId: string): Promise<void> {
  const read = await client.get(`/api/v1/projects/${state.projectId}/work-items/${workId}`);
  if (read.status() === 404) return;
  expect(read.ok(), await read.text()).toBe(true);
  const body = await read.json() as { work_item: WorkItem };
  const deleted = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${workId}/delete`,
    { data: { expected_version: body.work_item.version } }
  );
  expect(deleted.ok(), await deleted.text()).toBe(true);
}

async function openDashboard(page: Page): Promise<void> {
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.locator(".sync-status")).toHaveText("Live updates");
}

test("browser creates, displays, appends, completes, and refreshes declared scopes", async ({
  page
}, testInfo) => {
  test.slow();
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 10 scoped work ${suffix}`;
  const baseline = "ABCDEF1234567";
  const branch = "feature/مرحبا\u202Ebranch";
  const initialPaths = ["src/**", "tests/test_*.py", "README.md"];
  const progressPaths = ["frontend/**", `long/${"x".repeat(480)}`];
  const completionPaths = ["docs/**", "frontend/tests/**"];
  const mutationBodies: Array<Record<string, unknown>> = [];
  let workId = "";
  page.on("request", (browserRequest) => {
    if (
      browserRequest.method() === "POST"
      && browserRequest.url().includes(`/api/mnemonic/projects/${state.projectId}/work-items`)
      && browserRequest.postData()
    ) mutationBodies.push(browserRequest.postDataJSON() as Record<string, unknown>);
  });
  const client = await apiClient();

  try {
    await openDashboard(page);
    await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
    const create = page.getByRole("dialog", { name: "Create durable work" });
    await create.getByLabel("Title").fill(title);
    await create.getByLabel("Summary").fill("Exercise browser-only Phase 10 declarations.");
    await create.getByLabel("Initial context checkpoint").fill("Initial scoped context.");
    await create.locator("details.edit-context summary").click();
    await create.getByLabel("Repository branch").fill(branch);
    await create.getByLabel("Caller-asserted baseline commit").fill(baseline);
    await create.getByLabel(/Declared affected paths/).fill(initialPaths.join("\n"));
    await create.getByRole("button", { name: "Create work and checkpoint" }).click();

    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    let pane = workPane(page);
    await expect(pane.locator(".detail-title")).toHaveText(title);
    workId = await pane.locator(".detail-id code").textContent() ?? "";
    expect(workId).toMatch(/^[0-9a-f-]{36}$/);
    const createBody = mutationBodies[0] as {
      initial_checkpoint?: { affected_paths?: unknown; verified_against?: unknown };
    };
    expect(createBody.initial_checkpoint?.affected_paths).toEqual(initialPaths);
    expect(createBody.initial_checkpoint?.verified_against).toBe(baseline.toLowerCase());

    let context = pane.locator("#detail-panel-context");
    await expect(context.getByText("Not assessed by this browser.")).toHaveCount(1);
    const currentDeclaration = context.locator(".checkpoint-repository-declaration");
    const declaredBranch = currentDeclaration.getByText("Caller-declared branch", { exact: true })
      .locator("xpath=following-sibling::dd");
    const declaredBaseline = currentDeclaration.getByText("Caller-asserted baseline", {
      exact: true
    }).locator("xpath=following-sibling::dd");
    await expect(declaredBranch.locator('bdi[dir="auto"]')).toHaveText(branch);
    await expect(declaredBaseline.locator('bdi[dir="auto"]')).toHaveText(baseline.toLowerCase());
    await expect(currentDeclaration.locator(".affected-path-list li")).toHaveText(initialPaths);
    await expect(currentDeclaration.locator("a, button, input, textarea, select")).toHaveCount(0);

    await context.getByLabel("Checkpoint text").fill("Invalid scope must fail before UUID freeze.");
    const repositoryContextSummary = context
      .locator(".checkpoint-compose details.edit-context > summary")
      .filter({ hasText: /^Repository context and tags$/ });
    await repositoryContextSummary.click();
    await context.getByLabel(/Declared affected paths/).fill("src/**\nunsafe path");
    const requestCount = mutationBodies.length;
    await context.getByRole("button", { name: "Add checkpoint" }).click();
    await expect(context.getByText(/Affected path 2:/)).toBeVisible();
    expect(mutationBodies).toHaveLength(requestCount);

    await context.getByLabel(/Declared affected paths/).fill(progressPaths.join("\n"));
    await context.getByLabel("Caller-asserted baseline commit").fill(baseline);
    await context.getByRole("button", { name: "Add checkpoint" }).click();
    let history = await openTab(pane, "History");
    await expect(history.locator("article.checkpoint")).toHaveCount(2);
    const addBody = mutationBodies.at(-1) as { affected_paths?: unknown };
    expect(addBody.affected_paths).toEqual(progressPaths);
    const progressCheckpoint = history.locator("article.checkpoint").filter({
      hasText: "Invalid scope must fail before UUID freeze."
    });
    await expect(progressCheckpoint.locator(".affected-path-list li")).toHaveText(progressPaths);
    const longPath = progressCheckpoint.locator(".affected-path-list li").nth(1);
    expect(await longPath.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

    await closeDetail(page);
    await page.getByRole("button", { name: "Refresh" }).click();
    pane = await selectWork(page, title);
    history = await openTab(pane, "History");
    await expect(history.locator("article.checkpoint")).toHaveCount(2);
    await expect(history.getByText("Not assessed by this browser.")).toHaveCount(2);

    context = await openTab(pane, "Context");
    await context.getByLabel("Checkpoint text").fill("Completion scoped evidence.");
    await context
      .locator(".checkpoint-compose details.edit-context > summary")
      .filter({ hasText: /^Repository context and tags$/ })
      .click();
    await context.getByLabel("Caller-asserted baseline commit").fill(baseline);
    await context.getByLabel(/Declared affected paths/).fill(completionPaths.join("\n"));
    await fillFixtureReport(context);
    await context.getByRole("button", { name: "Complete work" }).click();
    await expect(pane.locator(".detail-identity > .status-badge")).toHaveText(/Done/);
    const completionBody = mutationBodies.at(-1) as {
      checkpoint?: { affected_paths?: unknown };
    };
    expect(completionBody.checkpoint?.affected_paths).toEqual(completionPaths);
    history = await openTab(pane, "History");
    await expect(history.locator("article.checkpoint")).toHaveCount(3);

    await closeDetail(page);
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await page.getByLabel("Search work items").fill(title);
    const doneCard = workCard(page, title);
    await expect(doneCard).toHaveCount(1);
    pane = await selectWork(page, title);
    history = await openTab(pane, "History");
    await expect(history.locator("article.checkpoint")).toHaveCount(3);
    await expect(
      history.locator("article.checkpoint").filter({ hasText: "Completion scoped evidence." })
        .locator(".affected-path-list li")
    ).toHaveText(completionPaths);
  } finally {
    if (workId) await deleteFixture(client, workId);
    await client.dispose();
  }
});

test("historical checkpoint responses omit scope and display unknown declaration", async ({
  page
}, testInfo) => {
  const suffix = `${testInfo.project.name}-${state.runId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Phase 10 historical work ${suffix}`;
  const client = await apiClient();
  let workId = "";
  try {
    const response = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Checkpoint without a declared dependency scope.",
        priority: 0,
        status: "pending",
        initial_checkpoint: {
          prompt: "Historical sparse checkpoint.",
          source_client: "playwright-api",
          source_session_id: `phase10-history-${suffix}`,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: [],
          source_metadata: {}
        }
      }
    });
    const text = await response.text();
    expect(response.ok(), text).toBe(true);
    const created = JSON.parse(text) as {
      work_item: WorkItem;
      initial_checkpoint: Record<string, unknown>;
    };
    workId = created.work_item.id;
    expect(Object.hasOwn(created.initial_checkpoint, "affected_paths")).toBe(false);

    await openDashboard(page);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);
    const pane = await selectWork(page, title);
    const context = pane.locator("#detail-panel-context");
    await expect(context.getByText("No dependency scope declared")).toHaveCount(1);
    await expect(context.getByText("Not assessed by this browser.")).toHaveCount(1);
    const history = await openTab(pane, "History");
    await expect(history.getByText("No dependency scope declared")).toHaveCount(1);
    await expect(history.getByText("Not assessed by this browser.")).toHaveCount(1);
  } finally {
    if (workId) await deleteFixture(client, workId);
    await client.dispose();
  }
});
