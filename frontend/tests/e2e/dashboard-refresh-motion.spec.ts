import { expect, request, test, type APIRequestContext, type Page, type WebSocketRoute } from "@playwright/test";
import { reportForFixture } from "./job-report-fixture";
import { EASE_IN_OUT_QUINT, WORK_ITEM_FADE_DURATION_MS, WORK_ITEM_SLIDE_DURATION_MS } from "../../lib/work-item-motion";

type Queue = "summaries" | "attention";
type Item = { id: string; workId: string };
type MotionRecord = { id: string; exit: boolean; opacity: string[]; duration: number | string; easing: string };

async function fixture() {
  const api = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  const response = await api.post("/api/v1/projects", { data: { name: `Dashboard motion ${crypto.randomUUID()}` } });
  expect(response.ok(), await response.text()).toBe(true);
  const project = await response.json() as { id: string };
  return { api, projectId: project.id };
}

async function open(page: Page, projectId: string, view: string) {
  let socket: WebSocketRoute | undefined;
  await page.routeWebSocket(/\/api\/mnemonic\/sync$/, (value) => {
    socket = value;
    value.send(JSON.stringify({ type: "ready", revision: 0 }));
  });
  await page.goto(`/${view}`);
  await page.locator("#project-select").selectOption(projectId);
  await expect.poll(() => Boolean(socket)).toBe(true);
  let revision = 0;
  return () => socket!.send(JSON.stringify({ type: "invalidate", scope: "work-items", revision: ++revision }));
}

async function add(api: APIRequestContext, projectId: string, queue: Queue, title: string): Promise<Item> {
  const create = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title, summary: "A disposable dashboard refresh and motion fixture.", priority: 1,
    initial_checkpoint: { prompt: "Check live dashboard updates.", source_client: "playwright-api", source_session_id: "dashboard-motion" }
  } });
  expect(create.ok(), await create.text()).toBe(true);
  const { work_item: work } = await create.json() as { work_item: { id: string; version: number } };
  const result = queue === "summaries"
    ? await api.post(`/api/v1/projects/${projectId}/work-items/${work.id}/complete`, { data: {
        expected_version: work.version, client_operation_id: crypto.randomUUID(),
        checkpoint: { prompt: "Verified the dashboard fixture.", source_client: "playwright-api", source_session_id: "dashboard-motion" },
        job_completion_report: await reportForFixture(api, projectId)
      } })
    : await api.post(`/api/v1/projects/${projectId}/work-items/${work.id}/gates`, { data: {
        gate_type: "human", question: `Should we proceed with ${title}?`,
        requested_by_client: "playwright-api", requested_by_session_id: "dashboard-motion",
        client_operation_id: crypto.randomUUID()
      } });
  expect(result.ok(), await result.text()).toBe(true);
  const body = await result.json();
  return { id: queue === "summaries" ? body.job_completion_report.id : body.id, workId: work.id };
}

async function remove(api: APIRequestContext, projectId: string, queue: Queue, item: Item) {
  let response;
  if (queue === "summaries") {
    response = await api.post(`/api/v1/projects/${projectId}/job-completion-reports/${item.id}/dismiss`, { data: {
      actor: { actor_client: "playwright-api", actor_session_id: "dashboard-motion" }, client_operation_id: crypto.randomUUID()
    } });
  } else {
    const snapshot = await api.get(`/api/v1/projects/${projectId}/human-attention?work_item_id=${item.workId}`);
    expect(snapshot.ok(), await snapshot.text()).toBe(true);
    const { items } = await snapshot.json();
    response = await api.post(`/api/v1/projects/${projectId}/work-items/${item.workId}/gates/${item.id}/resolve`, { data: {
      resolution: "Proceed with the fixture.", reviewed_context_revision: items[0].gate.current_context_revision,
      resolved_by_client: "playwright-api", resolved_by_session_id: "dashboard-motion", client_operation_id: crypto.randomUUID()
    } });
  }
  expect(response.ok(), await response.text()).toBe(true);
}

async function observeMotion(page: Page) {
  await page.evaluate(() => {
    const target = window as typeof window & { queueMotion: MotionRecord[] };
    target.queueMotion = [];
    const animate = Element.prototype.animate;
    Element.prototype.animate = function (...args) {
      const animation = animate.apply(this, args);
      if (!(this instanceof HTMLElement) || !(animation.effect instanceof KeyframeEffect)) return animation;
      const id = this.dataset.workItemId ?? this.dataset.workItemExitId;
      if (!id) return animation;
      const frames = animation.effect.getKeyframes();
      const timing = animation.effect.getTiming();
      target.queueMotion.push({ id, exit: Boolean(this.dataset.workItemExitId), opacity: frames.filter((frame) => frame.opacity !== undefined).map((frame) => String(frame.opacity)), duration: Number(timing.duration), easing: timing.easing ?? "linear" });
      if (frames.some((frame) => frame.opacity !== undefined)) {
        // Hold a real browser animation halfway through for deterministic visual assertions.
        animation.pause();
        animation.currentTime = Number(timing.duration) / 2;
      }
      return animation;
    };
  });
}

async function finishMotion(page: Page) {
  await page.evaluate(() => document.getAnimations().forEach((animation) => animation.finish()));
  await expect(page.locator(".work-item-entering, .work-item-exiting")).toHaveCount(0);
}

for (const view of ["summaries", "attention", "artifacts"]) {
  test(`${view} keeps its loaded empty view visible during background invalidation`, async ({ page }) => {
    const { api, projectId } = await fixture();
    let release = () => {};
    try {
      const invalidate = await open(page, projectId, view);
      const selector = view === "summaries" ? ".job-report-list .empty-state" : view === "attention" ? ".attention-empty" : ".artifact-empty";
      const empty = page.locator(selector);
      await expect(empty).toBeVisible();
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      const bounds = await empty.boundingBox();
      await empty.evaluate((element) => element.setAttribute("data-stability-probe", "retained"));
      const held = new Promise<void>((resolve) => { release = resolve; });
      let requests = 0;
      await page.route(/\/api\/(mnemonic|artifacts)\//, async (route) => {
        const url = new URL(route.request().url());
        if (route.request().method() === "GET" && /\/(job-completion-reports|human-attention|code-reviews|artifacts)(\/count)?$/.test(url.pathname)) {
          requests += 1;
          await held;
        }
        await route.continue();
      });
      invalidate();
      await expect.poll(() => requests).toBeGreaterThan(0);
      await expect(page.locator('[aria-busy="true"]')).not.toHaveCount(0);
      await expect(empty).toBeVisible();
      await expect(empty).toHaveAttribute("data-stability-probe", "retained");
      expect(await empty.boundingBox()).toEqual(bounds);
      await expect(page.locator(".loading-state")).toHaveCount(0);
      if (view === "attention") {
        await expect(page.getByText("No requested reviews on this page.")).toBeVisible();
        await expect(page.getByText("Loading review queue…")).toHaveCount(0);
      }
      release();
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    } finally { release(); await api.dispose(); }
  });
}

for (const queue of ["summaries", "attention"] as const) {
  test(`${queue} eases live additions and removals and retains content on refresh`, async ({ page }, testInfo) => {
    const { api, projectId } = await fixture();
    try {
      const invalidate = await open(page, projectId, queue);
      await expect(page.locator(queue === "summaries" ? ".job-report-list .empty-state" : ".attention-empty")).toBeVisible();
      await observeMotion(page);
      const first = await add(api, projectId, queue, "First work result");
      invalidate();
      const firstCard = page.locator(`[data-work-item-id="${first.id}"]`);
      await expect(firstCard).toHaveCSS("opacity", "0.5");
      await expect(firstCard).toHaveAttribute("inert", "");
      await finishMotion(page);
      await expect(firstCard).toHaveCSS("opacity", "1");
      const firstElement = await firstCard.elementHandle();
      const list = page.locator(queue === "summaries" ? ".job-report-items" : ".attention-items");
      await expect(list).toHaveAttribute("aria-busy", "false");
      if (queue === "attention") await firstCard.getByRole("textbox", { name: /^Durable answer/ }).fill("Keep this answer draft through live updates.");
      const bounds = await firstCard.boundingBox();
      const held = Promise.withResolvers<void>();
      const reads = new RegExp(`/api/mnemonic/projects/${projectId}/(job-completion-reports|human-attention|code-reviews)(/count)?(?:\\?|$)`);
      await page.route(reads, async (route) => { await held.promise; await route.continue(); });
      try {
        invalidate();
        await expect(list).toHaveAttribute("aria-busy", "true");
        await expect(list).toHaveCSS("opacity", "1");
        await expect(page.locator(".loading-state")).toHaveCount(0);
        expect(await firstCard.boundingBox()).toEqual(bounds);
        expect(await firstElement!.evaluate((element) => element.isConnected)).toBe(true);
        if (queue === "summaries") await expect(page.locator(".summary-nav-count")).toHaveText("1");
        else await expect(firstCard.getByRole("textbox", { name: /^Durable answer/ })).toHaveValue("Keep this answer draft through live updates.");
      } finally { held.resolve(); }
      await expect(list).toHaveAttribute("aria-busy", "false");
      await page.unroute(reads);
      const second = await add(api, projectId, queue, "New work result");
      invalidate();
      const secondCard = page.locator(`[data-work-item-id="${second.id}"]`);
      await expect(secondCard).toHaveCSS("opacity", "0.5");
      await secondCard.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`${queue}-enter.png`), fullPage: true });
      invalidate();
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      await expect(secondCard).toHaveCSS("opacity", "0.5");
      await expect(firstCard).toHaveCSS("opacity", "1");
      expect(await firstElement!.evaluate((element) => element.isConnected)).toBe(true);
      await finishMotion(page);
      await remove(api, projectId, queue, second);
      invalidate();
      const exiting = page.locator(`[data-work-item-exit-id="${second.id}"]`);
      await expect(exiting).toHaveCSS("opacity", "0.5");
      await expect(exiting).toHaveAttribute("aria-hidden", "true");
      await expect(exiting).toHaveAttribute("inert", "");
      await page.screenshot({ path: testInfo.outputPath(`${queue}-exit.png`), fullPage: true });
      await finishMotion(page);
      await remove(api, projectId, queue, first);
      invalidate();
      await expect(page.locator(`[data-work-item-exit-id="${first.id}"]`)).toHaveCSS("opacity", "0.5");
      await finishMotion(page);
      const records = await page.evaluate(() => (window as typeof window & { queueMotion: MotionRecord[] }).queueMotion);
      for (const id of [first.id, second.id]) {
        expect(records).toContainEqual({ id, exit: false, opacity: ["0", "1"], duration: WORK_ITEM_FADE_DURATION_MS, easing: EASE_IN_OUT_QUINT });
        expect(records).toContainEqual({ id, exit: true, opacity: ["1", "0"], duration: WORK_ITEM_FADE_DURATION_MS, easing: EASE_IN_OUT_QUINT });
      }
      // Reports insert at the head; human questions append in oldest-first order.
      if (queue === "summaries") expect(records).toContainEqual({ id: first.id, exit: false, opacity: [], duration: WORK_ITEM_SLIDE_DURATION_MS, easing: "linear" });
    } finally { await api.dispose(); }
  });

  test(`${queue} respects reduced motion and project changes`, async ({ page }) => {
    const { api, projectId } = await fixture();
    try {
      await page.emulateMedia({ reducedMotion: "reduce" });
      const invalidate = await open(page, projectId, queue);
      await expect(page.locator(queue === "summaries" ? ".job-report-list .empty-state" : ".attention-empty")).toBeVisible();
      await observeMotion(page);
      const item = await add(api, projectId, queue, "Reduced motion result");
      invalidate();
      await expect(page.locator(`[data-work-item-id="${item.id}"]`)).toHaveCSS("opacity", "1");
      await remove(api, projectId, queue, item);
      invalidate();
      await expect(page.locator(`[data-work-item-id="${item.id}"]`)).toHaveCount(0);
      await expect(page.locator(".work-item-entering, .work-item-exiting")).toHaveCount(0);
      expect(await page.evaluate(() => (window as typeof window & { queueMotion: MotionRecord[] }).queueMotion)).toEqual([]);
      const other = await fixture();
      try {
        const next = await add(other.api, other.projectId, queue, "Other project result");
        // Refresh the project catalog and restore normal motion before switching scope.
        await page.reload();
        await page.emulateMedia({ reducedMotion: "no-preference" });
        await observeMotion(page);
        await page.locator("#project-select").selectOption(other.projectId);
        await expect(page.locator(`[data-work-item-id="${next.id}"]`)).toHaveCSS("opacity", "1");
        expect(await page.evaluate(() => (window as typeof window & { queueMotion: MotionRecord[] }).queueMotion)).toEqual([]);
      } finally { await other.api.dispose(); }
    } finally { await api.dispose(); }
  });
}

test("summaries animate full-page replacements while Load more remains immediate", async ({ page }) => {
  const { api, projectId } = await fixture();
  try {
    const seeded: Item[] = [];
    for (let index = 0; index < 20; index += 1) seeded.push(await add(api, projectId, "summaries", `Report ${index + 1}`));
    const invalidate = await open(page, projectId, "summaries");
    await expect(page.locator(".job-report-items > article")).toHaveCount(20);
    await observeMotion(page);
    const latest = await add(api, projectId, "summaries", "Newest report on a full page");
    invalidate();
    await expect(page.locator(`[data-work-item-id="${latest.id}"]`)).toHaveCSS("opacity", "0.5");
    await expect(page.locator(`[data-work-item-exit-id="${seeded[0].id}"]`)).toHaveCSS("opacity", "0.5");
    await expect(page.locator(".job-report-items > article")).toHaveCount(20);
    await finishMotion(page);
    const count = await page.evaluate(() => (window as typeof window & { queueMotion: MotionRecord[] }).queueMotion.length);
    await page.getByRole("button", { name: "Load more summaries" }).click();
    await expect(page.locator(".job-report-items > article")).toHaveCount(21);
    await expect(page.locator(`[data-work-item-id="${seeded[0].id}"]`)).toHaveCSS("opacity", "1");
    expect(await page.evaluate(() => (window as typeof window & { queueMotion: MotionRecord[] }).queueMotion.length)).toBe(count);
  } finally { await api.dispose(); }
});
