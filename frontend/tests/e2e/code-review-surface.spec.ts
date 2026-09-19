import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import { createProject, createReview } from "./task-fixtures";

async function setup() {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  const project = await createProject(api);
  const path = `/api/v1/projects/${project.id}/settings`;
  const settings = await (await api.get(path)).json();
  expect((await api.patch(path, { data: { expected_revision: settings.revision, code_review_required_min_priority: 0 } })).ok()).toBe(true);
  return { api, project };
}

function card(page: Page, title: string) { return page.getByRole("option", { name: title, exact: true }); }
function filter(page: Page, label: string) { return page.getByRole("group", { name: "Filter code reviews" }).getByRole("button", { name: label, exact: true }); }
async function context(api: APIRequestContext, projectId: string, workId: string) {
  return await (await api.get(`/api/v1/projects/${projectId}/work-items/${workId}/context`)).json();
}

test("code review split surface keeps cards mounted, resizes and opens exact details", async ({ page }, testInfo) => {
  const { api, project } = await setup();
  try {
    const first = await createReview(api, project.id, "Review queue navigation");
    await createReview(api, project.id, "Review retained context");
    await page.goto(`/code-reviews?project=${project.id}`);
    const queue = page.getByRole("listbox", { name: "Code reviews", exact: true });
    await expect(queue.getByRole("option")).toHaveCount(2);
    await queue.evaluate((element) => element.setAttribute("data-stays-mounted", "true"));
    const narrow = (page.viewportSize()?.width ?? 0) <= 900;
    if (!narrow) {
      await expect(page.getByRole("heading", { name: "Pick a code review." })).toBeVisible();
      const resizer = page.getByRole("separator", { name: "Resize the code review queue" });
      const before = Number(await resizer.getAttribute("aria-valuenow"));
      await resizer.focus(); await page.keyboard.press("ArrowRight");
      await expect(resizer).toHaveAttribute("aria-valuenow", String(before + 2));
      await expect(filter(page, "Pending")).toHaveAttribute("aria-pressed", "true");
    }
    await card(page, "Review queue navigation").click();
    const pane = page.getByRole("region", { name: "Code review detail" });
    await expect(pane.locator(".detail-title")).toHaveText("Review queue navigation");
    await expect(page).toHaveURL(new RegExp(`review=${first.id}`));
    await expect(queue).toHaveAttribute("data-stays-mounted", "true");
    if (!narrow) {
      const left = (await queue.boundingBox())!, right = (await pane.boundingBox())!;
      expect(left.x + left.width).toBeLessThan(right.x);
      expect(left.height).toBeGreaterThan(300);
    }
    await page.screenshot({ path: testInfo.outputPath("code-review-split.png"), fullPage: true, animations: "disabled" });
    await pane.getByRole("link", { name: "← Back to code reviews" }).click();
    await card(page, "Review queue navigation").getByRole("button", { name: "Choose an action for Review queue navigation" }).click();
    const menu = page.getByRole("menu", { name: "Actions for Review queue navigation" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem").first()).toBeFocused();
    await expect(menu.getByRole("menuitem", { name: /Move/ })).toHaveCount(0);
    const bounds = (await menu.boundingBox())!;
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(page.viewportSize()!.height);
    await page.screenshot({ path: testInfo.outputPath("code-review-actions.png"), fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(page).not.toHaveURL(/review=/);
    await page.reload();
    await card(page, "Review retained context").focus();
    await page.keyboard.press("ArrowDown");
    await expect(pane.locator(".detail-title")).toHaveText("Review retained context");
    await page.keyboard.press("Escape");
    await expect(page).not.toHaveURL(/review=/);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("code review cards copy the exact pointer and defer only their own review episode", async ({ page }) => {
  const { api, project } = await setup();
  try {
    const review = await createReview(api, project.id, "Review card actions");
    const other = await createReview(api, project.id, "Separate review episode");
    await page.goto(`/code-reviews?project=${project.id}`);
    const item = card(page, "Review card actions");
    await item.getByRole("button", { name: "Copy recall pointer for Review card actions" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(review.id);
    const pointer = await page.evaluate(() => navigator.clipboard.readText());
    expect(pointer).toContain(review.work_item_id);
    expect(pointer).toContain("WARM, ADVERSARIAL");
    expect(pointer).not.toContain(other.id);
    await expect(item).toHaveAttribute("aria-selected", "false");
    await expect(page).not.toHaveURL(/review=/);
    await item.getByRole("button", { name: "Defer Review card actions", exact: true }).click();
    await expect(item).toHaveCount(0);
    const deferred = await context(api, project.id, review.work_item_id);
    expect(deferred.work_item.status).toBe("done");
    expect(deferred.code_review_context.current_review.human_decision.status).toBe("deferred");
    expect((await context(api, project.id, other.work_item_id)).code_review_context.current_review.human_decision).toBeUndefined();
    await filter(page, "Deferred").click();
    await expect(item).toBeVisible();
    await expect(item.getByRole("button", { name: "Defer Review card actions", exact: true })).toBeDisabled();
    await item.getByRole("button", { name: "Copy recall pointer for Review card actions" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("does not authorize execution");
    await item.getByRole("button", { name: "Choose an action for Review card actions" }).click();
    await page.getByRole("menuitem", { name: "Pending Review card actions", exact: true }).click();
    await expect(item).toHaveCount(0);
    await filter(page, "Pending").click();
    await expect(item).toBeVisible();
    await item.getByRole("button", { name: "Choose an action for Review card actions" }).click();
    await page.getByRole("menuitem", { name: "Done Review card actions", exact: true }).click();
    await expect(item).toHaveCount(0);
    const closed = await context(api, project.id, review.work_item_id);
    expect(closed.work_item.status).toBe("done");
    expect(closed.code_review_context.current_review.human_decision.job_completion_report.summary).toContain("no agent findings");
    await filter(page, "Done").click();
    await expect(item.getByRole("button", { name: "Choose an action for Review card actions" })).toBeEnabled();
  } finally { await api.dispose(); }
});

test("code review card decisions retry the same request after a lost response", async ({ page }) => {
  const { api, project } = await setup();
  try {
    const review = await createReview(api, project.id, "Review retry preservation");
    await page.goto(`/code-reviews?project=${project.id}`);
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/work-items/${review.work_item_id}`, async (route) => {
      if (route.request().method() !== "PATCH") { await route.continue(); return; }
      bodies.push(route.request().postData()!);
      const response = await route.fetch();
      if (bodies.length === 1) await route.fulfill({ status: 502, contentType: "application/json", body: '{"detail":"Lost review decision response."}' });
      else await route.fulfill({ response });
    });
    await card(page, "Review retry preservation").getByRole("button", { name: "Defer Review retry preservation", exact: true }).click();
    const retry = page.getByRole("button", { name: "Retry exact request", exact: true });
    await expect(retry).toBeVisible();
    await page.locator(".tasks-nav").getByRole("link", { name: "Work items", exact: true }).click();
    await expect(page).toHaveURL(/code-reviews/);
    await retry.click();
    await expect(retry).toHaveCount(0);
    expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
    expect(JSON.parse(bodies[0]).review_decision.resource_id).toBe(review.id);
    const latest = await context(api, project.id, review.work_item_id);
    expect(latest.work_item.status).toBe("done");
    expect(latest.code_review_context.current_review.human_decision.version).toBe(1);
  } finally { await api.dispose(); }
});

test("completed code review cards retain read-only actions and exact historical pointers", async ({ page }) => {
  const { api, project } = await setup();
  try {
    const review = await createReview(api, project.id, "Completed adversarial review");
    const base = `/api/v1/projects/${project.id}/work-items/${review.work_item_id}`;
    const detail = await (await api.get(`${base}/code-reviews/${review.id}`)).json();
    const claim = await api.post(`${base}/claim`, { data: { holder_client: "test-reviewer", holder_session_id: "surface-review", claim_request_id: crypto.randomUUID(), session_transcript: null, purpose: "code_review", code_review_id: review.id, mode: "cold" } });
    expect(claim.ok(), await claim.text()).toBe(true);
    const lease = await claim.json();
    const complete = await api.post(`${base}/code-reviews/${review.id}/complete`, { data: {
      expected_review_version: detail.review.version, scope_sha256: detail.review.scope_sha256,
      lease_token: lease.lease_token, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
      actor: { actor_client: "test-reviewer", actor_session_id: "surface-review" },
      result: { mode: "cold", summary: "Verified the scoped changes; no actionable findings.", coverage: [{ repository_key: "main", base_commit: "a".repeat(40), head_commit: "b".repeat(40) }], limitations: [], findings: [] }
    } });
    expect(complete.ok(), await complete.text()).toBe(true);
    await page.goto(`/code-reviews?project=${project.id}`); await filter(page, "Done").click();
    const item = card(page, "Completed adversarial review");
    await expect(item.getByRole("button", { name: "Defer Completed adversarial review", exact: true })).toBeDisabled();
    await expect(item.getByRole("button", { name: "Choose an action for Completed adversarial review" })).toBeDisabled();
    await item.getByRole("button", { name: "Copy recall pointer for Completed adversarial review" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(review.id);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("does not authorize execution");
    await item.click();
    await expect(page.getByRole("region", { name: "Code review detail" })).toContainText("no actionable findings");
  } finally { await api.dispose(); }
});


test("stale review cards cannot overwrite a newer human decision", async ({ page }) => {
  const { api, project } = await setup();
  try {
    const review = await createReview(api, project.id, "Review concurrent decision");
    const tasks = await (await api.get(`/api/v1/projects/${project.id}/tasks?status=pending&kind=code_review`)).json();
    await page.route(`**/api/mnemonic/projects/${project.id}/tasks?**`, (route) => route.fulfill({ json: tasks }));
    await page.goto(`/code-reviews?project=${project.id}`);
    const item = card(page, "Review concurrent decision");
    await expect(item).toBeVisible();
    const before = await context(api, project.id, review.work_item_id);
    const response = await api.patch(`/api/v1/projects/${project.id}/work-items/${review.work_item_id}`, { data: {
      expected_version: before.work_item.version, client_operation_id: crypto.randomUUID(),
      actor: { actor_client: "dashboard", actor_session_id: "another-person" },
      review_decision: { resource_id: review.id, expected_decision_version: 0, status: "deferred" }
    } });
    expect(response.ok(), await response.text()).toBe(true);
    const writes: string[] = [];
    page.on("request", (request) => { if (request.method() === "PATCH") writes.push(request.url()); });
    await item.getByRole("button", { name: "Defer Review concurrent decision", exact: true }).click();
    await expect(page.getByText("This review changed since the card was loaded. Refresh before making a status decision.", { exact: true })).toBeVisible();
    expect(writes).toHaveLength(0);
    const after = await context(api, project.id, review.work_item_id);
    expect(after.work_item.status).toBe("done");
    expect(after.code_review_context.current_review.human_decision.status).toBe("deferred");
    expect(after.code_review_context.current_review.human_decision.version).toBe(1);
  } finally { await api.dispose(); }
});
