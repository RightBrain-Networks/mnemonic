import { expect, request, test, type APIRequestContext, type Page, type Request } from "@playwright/test";
import { claim, createProject, createWork } from "./task-fixtures";
import { openTab, selectWork } from "./surface";
import { reportForFixture } from "./job-report-fixture";

type AbortedRead = { url: string; body: string | null };
type FetchProbeWindow = typeof window & { abortedCoreReads: AbortedRead[] };

async function apiClient() {
  return request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
}

async function observeCancellation(page: Page) {
  await page.addInitScript(() => {
    const target = window as FetchProbeWindow;
    target.abortedCoreReads = [];
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = input instanceof Request ? input.url : String(input);
      const signal = init?.signal ?? (input instanceof Request ? input.signal : null);
      const body = typeof init?.body === "string" ? init.body : null;
      signal?.addEventListener("abort", () => target.abortedCoreReads.push({ url, body }), { once: true });
      return fetch(input, init);
    };
  });
  // Keep refresh timing under test control while the HTTP reads use the real API.
  await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (socket) => {
    socket.send(JSON.stringify({ type: "ready", revision: 0 }));
  });
}

async function holdRead(page: Page, matches: (request: Request) => boolean) {
  const gate = Promise.withResolvers<void>();
  const state = { held: false, released: false, release: () => gate.resolve() };
  let selected = false;
  await page.route("**/api/mnemonic/**", async (route) => {
    if (selected || !matches(route.request())) {
      await route.continue();
      return;
    }
    selected = true;
    const response = await route.fetch();
    state.held = true;
    await gate.promise;
    // Deliver the old response even when the browser has already cancelled it.
    await route.fulfill({ response }).catch(() => {});
    state.released = true;
  });
  return state;
}

async function abortedReads(page: Page) {
  return page.evaluate(() => (window as FetchProbeWindow).abortedCoreReads);
}

function isSearch(request: Request, projectId: string, query: string) {
  return new URL(request.url()).pathname === `/api/mnemonic/projects/${projectId}/search`
    && request.method() === "POST" && request.postDataJSON().q === query;
}

async function addSidebarItems(api: APIRequestContext, projectId: string, suffix: string) {
  const question = await createWork(api, projectId, `Question ${suffix}`);
  const gate = await api.post(`/api/v1/projects/${projectId}/work-items/${question.id}/gates`, { data: {
    gate_type: "human", question: `Proceed with ${suffix}?`,
    requested_by_client: "playwright-api", requested_by_session_id: "core-query",
    client_operation_id: crypto.randomUUID()
  } });
  expect(gate.ok(), await gate.text()).toBe(true);
  const complete = await createWork(api, projectId, `Summary ${suffix}`);
  const report = await api.post(`/api/v1/projects/${projectId}/work-items/${complete.id}/complete`, { data: {
    expected_version: complete.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
    checkpoint: { prompt: "Verified the sidebar count fixture.", source_client: "playwright-api", source_session_id: "core-query" },
    job_completion_report: await reportForFixture(api, projectId)
  } });
  expect(report.ok(), await report.text()).toBe(true);
}

test("retained attention and summary counts recover failed background reads after activity is consumed", async ({ page }) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    await addSidebarItems(api, project.id, "initial");
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    await page.goto(`/work-items?project=${project.id}`);
    const attention = page.locator('a[href="/attention"] .attention-nav-count');
    const reports = page.locator('a[href="/summaries"] .summary-nav-count');
    await expect(attention).toHaveText("1");
    await expect(reports).toHaveText("1");
    let failCounts = true;
    const failures = { attention: 0, reports: 0 };
    let emptyActivityAfterFailure = 0;
    await page.route(`**/api/mnemonic/projects/${project.id}/human-attention?*`, async (route) => {
      if (failCounts && new URL(route.request().url()).searchParams.get("limit") === "0") {
        await route.fulfill({ status: 503, json: { detail: "Attention count temporarily unavailable." } });
        failures.attention += 1;
      } else await route.continue();
    });
    await page.route(`**/api/mnemonic/projects/${project.id}/job-completion-reports/count`, async (route) => {
      if (failCounts) {
        await route.fulfill({ status: 503, json: { detail: "Summary count temporarily unavailable." } });
        failures.reports += 1;
      } else await route.continue();
    });
    await page.route(`**/api/mnemonic/projects/${project.id}/activity?*`, async (route) => {
      const response = await route.fetch();
      const value = await response.json() as { items: unknown[] };
      if (failures.attention > 0 && failures.reports > 0 && value.items.length === 0) emptyActivityAfterFailure += 1;
      await route.fulfill({ response });
    });
    await addSidebarItems(api, project.id, "added during background failure");
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => failures.attention).toBeGreaterThan(0);
    await expect.poll(() => failures.reports).toBeGreaterThan(0);
    await expect(attention).toHaveText("1");
    await expect(reports).toHaveText("1");
    // Let both error states render before asking the activity coordinator to retry.
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
    failCounts = false;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => emptyActivityAfterFailure).toBeGreaterThan(0);
    await expect(attention).toHaveText("2");
    await expect(reports).toHaveText("2");
  } finally {
    await page.unrouteAll({ behavior: "wait" });
    await api.dispose();
  }
});

test("an unavailable project link recovers through project selection and valid navigation", async ({ page }) => {
  const api = await apiClient();
  try {
    const project = await createProject(api);
    await createWork(api, project.id, "Available project work");
    await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
    const unavailable = page.getByText("The resource link's project is unavailable. Select a project to continue.", { exact: true });
    await page.goto(`/work-items?project=${crypto.randomUUID()}`);
    await expect(unavailable).toBeVisible();
    await page.locator("#project-select").selectOption(project.id);
    await expect(unavailable).toHaveCount(0);
    await expect(page.locator("article.work-item-card")).toContainText("Available project work");

    await page.goto(`/work-items?project=${crypto.randomUUID()}`);
    await expect(unavailable).toBeVisible();
    await page.getByRole("link", { name: "Mnemonic home" }).click();
    await expect(page).toHaveURL("/");
    await expect(unavailable).toHaveCount(0);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    await expect(page.locator(".task-widget-work_items dd")).toHaveText(["0", "1"]);
    await expect(page.getByRole("heading", { name: "Create your first project." })).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("switching projects cancels a pending task read and isolates its late response", async ({ page }) => {
  const api = await apiClient();
  let held: Awaited<ReturnType<typeof holdRead>> | undefined;
  try {
    const first = await createProject(api);
    const second = await createProject(api);
    const firstTask = await createWork(api, first.id, "First project active task");
    const secondTask = await createWork(api, second.id, "Second project active task");
    await claim(api, first.id, firstTask.id);
    await claim(api, second.id, secondTask.id);
    await createWork(api, second.id, "Second project pending task");
    await observeCancellation(page);
    held = await holdRead(page, (request) => (
      new URL(request.url()).pathname === `/api/mnemonic/projects/${first.id}/tasks`
    ));
    await page.goto("/");
    await page.locator("#project-select").selectOption(first.id);
    await expect(page.locator("#project-select")).toHaveValue(first.id);
    await expect.poll(() => held!.held).toBe(true);
    await page.locator("#project-select").selectOption(second.id);
    await expect(page.locator(".task-widget-work_items dd")).toHaveText(["1", "1"]);
    await expect(page.locator(".task-card")).toHaveCount(1);
    await expect(page.locator(".task-card")).toContainText("Second project active task");
    await expect.poll(async () => (await abortedReads(page)).some(({ url }) => (
      url.includes(`/projects/${first.id}/tasks?`)
    ))).toBe(true);
    held.release();
    await expect.poll(() => held!.released).toBe(true);
    await expect(page.locator(".task-card")).not.toContainText("First project active task");
    await expect(page.locator(".task-widget-work_items dd")).toHaveText(["1", "1"]);
    await expect(page.locator(".task-dashboard [role=alert]")).toHaveCount(0);

    // Returning to the original key must still fetch and render that project's data.
    await page.locator("#project-select").selectOption(first.id);
    await expect(page.locator(".task-card")).toHaveCount(1);
    await expect(page.locator(".task-card")).toContainText("First project active task");
    await expect(page.locator(".task-widget-work_items dd")).toHaveText(["1", "0"]);
  } finally {
    held?.release();
    await page.unrouteAll({ behavior: "wait" });
    await api.dispose();
  }
});

test("changing and clearing work search cancels the old query without replacing restored roots", async ({ page }) => {
  const api = await apiClient();
  let held: Awaited<ReturnType<typeof holdRead>> | undefined;
  try {
    const project = await createProject(api);
    const first = await createWork(api, project.id, "First query result");
    const second = await createWork(api, project.id, "Second query result");
    await observeCancellation(page);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(page.locator(".hierarchy-node[data-depth='0']")).toHaveCount(2);
    held = await holdRead(page, (request) => isSearch(request, project.id, first.id));
    const search = page.getByRole("searchbox", { name: "Search work items" });
    await search.fill(first.id);
    await expect.poll(() => held!.held).toBe(true);
    await search.fill(second.id);
    await expect(page.locator("article.work-item-card")).toHaveCount(1);
    await expect(page.locator("article.work-item-card")).toContainText("Second query result");
    await expect.poll(async () => (await abortedReads(page)).some(({ body }) => (
      body !== null && JSON.parse(body).q === first.id
    ))).toBe(true);
    await search.fill("");
    await expect(page.locator(".hierarchy-node[data-depth='0']")).toHaveCount(2);
    held.release();
    await expect.poll(() => held!.released).toBe(true);
    await expect(search).toHaveValue("");
    await expect(page.locator(".hierarchy-node[data-depth='0']")).toHaveCount(2);
    await expect(page.locator(".result-count")).toHaveText("2 root branches");
    await expect(page.locator(".work-queue [role=alert]")).toHaveCount(0);
  } finally {
    held?.release();
    await page.unrouteAll({ behavior: "wait" });
    await api.dispose();
  }
});

test("canonical relationship search cancels an old project and isolates the same query in another project", async ({ page }) => {
  const api = await apiClient();
  let held: Awaited<ReturnType<typeof holdRead>> | undefined;
  try {
    const first = await createProject(api);
    const second = await createProject(api);
    const marker = `queryscope${crypto.randomUUID().replaceAll("-", "")}`;
    await createWork(api, first.id, `Source ${marker}`);
    const counterpart = await createWork(api, first.id, `Local ${marker}`);
    await createWork(api, second.id, `Remote ${marker}`);
    await observeCancellation(page);
    await page.goto(`/work-items?project=${first.id}`);
    const pane = await selectWork(page, `Source ${marker}`);
    const graph = await openTab(pane, "Graph");
    await graph.locator("summary").filter({ hasText: "Add a relationship" }).click();
    const search = graph.getByLabel("Find another work item");
    const options = graph.getByRole("listbox", { name: "Matching work items" }).getByRole("option");
    await search.fill(marker);
    await expect(options).toHaveCount(1);
    await expect(options).toContainText(`Local ${marker}`);
    held = await holdRead(page, (request) => isSearch(request, first.id, counterpart.id));
    await search.fill(counterpart.id);
    await expect.poll(() => held!.held).toBe(true);
    await graph.getByLabel("Project to search", { exact: false }).selectOption(second.id);
    await expect(search).toHaveValue("");
    await expect(graph.getByRole("listbox")).toHaveCount(0);
    await expect(graph.getByText("Searching…", { exact: true })).toHaveCount(0);
    await expect.poll(async () => (await abortedReads(page)).some(({ body }) => (
      body !== null && JSON.parse(body).q === counterpart.id
    ))).toBe(true);
    await search.fill(marker);
    await expect(options).toHaveCount(1);
    await expect(options).toContainText(`Remote ${marker}`);
    held.release();
    await expect.poll(() => held!.released).toBe(true);
    await expect(options).toHaveCount(1);
    await expect(options).toContainText(`Remote ${marker}`);
    await expect(options).not.toContainText(`Local ${marker}`);
    await expect(graph.getByRole("alert")).toHaveCount(0);
  } finally {
    held?.release();
    await page.unrouteAll({ behavior: "wait" });
    await api.dispose();
  }
});
