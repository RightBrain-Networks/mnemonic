import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import { createProject, createWork } from "./task-fixtures";
import { reportForFixture } from "./job-report-fixture";

async function setup() {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  return { api, project: await createProject(api) };
}

async function completedWork(api: APIRequestContext, projectId: string, title: string) {
  const work = await createWork(api, projectId, title);
  const response = await api.post(`/api/v1/projects/${projectId}/work-items/${work.id}/complete`, { data: {
    expected_version: work.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
    checkpoint: { prompt: "The implementation is complete.", source_client: "codex", source_session_id: "implementation" },
    job_completion_report: await reportForFixture(api, projectId)
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return work;
}

async function tasksMenu(page: Page) {
  const group = page.locator(".tasks-nav");
  const toggle = group.getByRole("button", { name: "Tasks", exact: true });
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  return group;
}

test("work card pins survive sorting and reload without selecting the card", async ({ page }, testInfo) => {
  const { api, project } = await setup();
  try {
    const first = await createWork(api, project.id, "Pinned dashboard work");
    await createWork(api, project.id, "Recent dashboard work");
    await page.goto(`/work-items?project=${project.id}`);
    const queue = page.getByRole("listbox", { name: "Durable work items" });
    const pinned = queue.getByRole("option", { name: "Pinned dashboard work", exact: true });
    await pinned.getByRole("button", { name: "Pin Pinned dashboard work", exact: true }).click();
    await expect(queue.getByRole("option").first()).toHaveAttribute("data-queue-option", first.id);
    await expect(pinned).toHaveAttribute("aria-selected", "false");
    await expect(pinned.getByRole("button", { name: "Unpin Pinned dashboard work" })).toHaveAttribute("aria-pressed", "true");
    for (const sort of ["Created", "Priority", "Updated"]) {
      await page.getByRole("radio", { name: sort, exact: true }).focus();
      await page.keyboard.press("Space");
      await expect(page.getByRole("radio", { name: sort, exact: true })).toBeChecked();
      await expect(queue.getByRole("option").first()).toHaveAttribute("data-queue-option", first.id);
    }
    await page.getByRole("searchbox", { name: "Search work items" }).fill("dashboard work");
    await expect(page.locator(".search-results").getByRole("option").first()).toHaveAttribute("data-queue-option", first.id);
    await page.getByRole("searchbox", { name: "Search work items" }).fill("");
    await page.reload();
    await expect(queue.getByRole("option").first()).toHaveAttribute("data-queue-option", first.id);
    await page.screenshot({ path: testInfo.outputPath("work-card-pin.png"), fullPage: true, animations: "disabled" });
    const other = await createProject(api);
    await page.goto(`/work-items?project=${other.id}`);
    await expect(queue.getByRole("option")).toHaveCount(0);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(queue.getByRole("option").first()).toHaveAttribute("data-queue-option", first.id);
    await pinned.getByRole("button", { name: "Unpin Pinned dashboard work" }).focus();
    await page.keyboard.press("Enter");
    await expect(queue.getByRole("option").first()).toHaveAccessibleName("Recent dashboard work");
    await expect(page).not.toHaveURL(/work=/);
  } finally { await api.dispose(); }
});

test("summary review split button queues warm and cold reviews and updates sidebar pips", async ({ page }, testInfo) => {
  const { api, project } = await setup();
  try {
    const warm = await completedWork(api, project.id, "Summary warm review");
    const cold = await completedWork(api, project.id, "Summary cold review");
    const pending = await createWork(api, project.id, "Pending sidebar work");
    await page.goto("/summaries");
    await page.locator("#project-select").selectOption(project.id);
    const group = await tasksMenu(page);
    await expect(group.getByLabel("1 pending work items", { exact: true })).toBeVisible();
    await expect(group.getByRole("link", { name: "Code reviews", exact: true }).locator(".attention-nav-count")).toHaveCount(0);
    const warmCard = page.getByRole("article", { name: "Report for Summary warm review", exact: true });
    await warmCard.getByRole("button", { name: "Warm review for Summary warm review" }).click();
    await expect(warmCard.getByRole("status")).toContainText("Warm code review queued");
    await expect(group.getByLabel("1 pending code reviews", { exact: true })).toBeVisible();
    const coldCard = page.getByRole("article", { name: "Report for Summary cold review", exact: true });
    const split = coldCard.getByRole("button", { name: "Choose review mode for Summary cold review" });
    await split.focus(); await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "Cold review", exact: true })).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath("summary-review-menu.png"), fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(split).toBeFocused();
    await split.click(); await page.getByRole("menuitem", { name: "Cold review", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Request cold review", exact: true });
    await expect(dialog.getByRole("button", { name: "Create cold review", exact: true })).toBeDisabled();
    await dialog.getByRole("textbox", { name: "Repository URL", exact: true }).fill("https://github.com/example/repository");
    await dialog.getByRole("textbox", { name: "Base commit", exact: true }).fill("a".repeat(40));
    await dialog.getByRole("textbox", { name: "Head commit", exact: true }).fill("b".repeat(40));
    await dialog.locator(".dialog-content").evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: testInfo.outputPath("cold-review-scope.png"), animations: "disabled" });
    await dialog.getByRole("button", { name: "Create cold review", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(group.getByLabel("2 pending code reviews", { exact: true })).toBeVisible();
    for (const [work, mode] of [[warm, "warm"], [cold, "cold"]] as const) {
      const context = await (await api.get(`/api/v1/projects/${project.id}/work-items/${work.id}/context`)).json();
      expect(context.work_item.status).toBe("done");
      expect(context.code_review_context.current_review.manual_request.mode).toBe(mode);
    }
    const deferred = await api.post(`/api/v1/projects/${project.id}/work-items/${pending.id}/defer`, { data: {
      expected_version: pending.version, client_operation_id: crypto.randomUUID(),
      actor: { actor_client: "dashboard", actor_session_id: "pip-test" }
    } });
    expect(deferred.ok(), await deferred.text()).toBe(true);
    await expect(group.getByRole("link", { name: "Work items", exact: true }).locator(".attention-nav-count")).toHaveCount(0);
    await group.getByRole("link", { name: "Code reviews", exact: true }).click();
    const reviewCard = page.getByRole("option", { name: "Summary cold review", exact: true });
    await reviewCard.getByRole("button", { name: "Copy recall pointer for Summary cold review" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("COLD");
    const pointer = await page.evaluate(() => navigator.clipboard.readText());
    expect(pointer).toContain("COLD");
    expect(pointer).not.toContain("This browser acceptance fixture");
    expect(pointer).toContain("a".repeat(40));
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("summary warm review retries the same mutation after a lost response", async ({ page }) => {
  const { api, project } = await setup();
  try {
    const work = await completedWork(api, project.id, "Retry summary review");
    await page.goto("/summaries");
    await page.locator("#project-select").selectOption(project.id);
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/work-items/${work.id}`, async (route) => {
      if (route.request().method() !== "PATCH") { await route.continue(); return; }
      bodies.push(route.request().postData()!);
      const response = await route.fetch();
      if (bodies.length === 1) await route.fulfill({ status: 502, json: { detail: "Lost response" } });
      else await route.fulfill({ response });
    });
    await page.getByRole("button", { name: "Warm review for Retry summary review" }).click();
    const retry = page.getByRole("button", { name: "Retry exact request", exact: true });
    await expect(retry).toBeVisible();
    await retry.click(); await expect(retry).toHaveCount(0);
    expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
    const tasks = await (await api.get(`/api/v1/projects/${project.id}/tasks?status=pending&kind=code_review`)).json();
    expect(tasks.total).toBe(1);
  } finally { await api.dispose(); }
});
