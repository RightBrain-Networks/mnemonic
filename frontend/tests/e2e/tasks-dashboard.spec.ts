import { expect, request, test, type Page } from "@playwright/test";
import { createProject, createWork, createReview, claim, openProject } from "./task-fixtures";

async function capturePage(page: Page, path: string) {
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ ...viewport, height: viewport.width > 800 ? 1100 : 1700 });
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  await page.setViewportSize(viewport);
}

test("task dashboard separates counts, combines active cards and links to exact task details", async ({ page }, testInfo) => {
  test.setTimeout(90000);
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    const settingsPath = `/api/v1/projects/${project.id}/settings`;
    const settings = await (await api.get(settingsPath)).json();
    expect((await api.patch(settingsPath, { data: { expected_revision: settings.revision, code_review_required_min_priority: 0 } })).ok()).toBe(true);
    const active = await createWork(api, project.id, "Continue the shared context implementation");
    await claim(api, project.id, active.id);
    for (let index = 0; index < 21; index += 1) await createWork(api, project.id, `Pending handoff ${index + 1}`);
    const review = await createReview(api, project.id, "Review the completed navigation changes");
    await claim(api, project.id, review.work_item_id, review.id);
    await createReview(api, project.id, "Review waiting in the backlog");
    await openProject(page, project.id);
    await expect(page.locator(".task-widget-work_items dd")).toHaveText(["1", "21"]);
    await expect(page.locator(".task-widget-code_reviews dd")).toHaveText(["1", "1"]);
    await expect(page.locator(".task-card")).toHaveCount(2);
    await expect(page.locator(".task-card-work_item .task-kind")).toHaveText("Work item");
    await expect(page.locator(".task-card-code_review .task-kind")).toHaveText("Code review");
    await expect(page.locator(".sidebar-note")).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const cardWidth = await page.locator(".task-card").first().evaluate((element) => element.getBoundingClientRect().width);
    const listWidth = await page.locator(".task-card-list").evaluate((element) => element.getBoundingClientRect().width);
    expect(Math.abs(cardWidth - listWidth)).toBeLessThan(2);
    await capturePage(page, testInfo.outputPath("tasks-dashboard.png"));

    await page.locator(".task-card-work_item").getByRole("link", { name: "View", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/work-items\\?project=${project.id}&work=${active.id}`));
    await expect(page.locator(".detail-title")).toHaveText("Continue the shared context implementation");
    await page.goBack();
    await expect(page.locator(".task-card")).toHaveCount(2);
    await page.locator(".task-card-code_review").getByRole("link", { name: "View", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/code-reviews\\?project=${project.id}&work=${review.work_item_id}&review=${review.id}`));
    await expect(page.getByRole("region", { name: "Code review detail" })).toBeVisible();
    await expect(page.locator(".review-state")).toHaveText("Active");
    await page.reload();
    await expect(page.locator(".task-detail-heading h2")).toHaveText("Review the completed navigation changes");
    await page.getByRole("link", { name: "← Back to code reviews" }).click();
    await expect(page.locator(".code-review-library .queue-card-title")).toHaveText("Review waiting in the backlog");
    await page.getByRole("group", { name: "Filter code reviews" }).getByRole("button", { name: "Active", exact: true }).click();
    await expect(page.locator(".code-review-library .queue-card-title")).toHaveText("Review the completed navigation changes");
    await capturePage(page, testInfo.outputPath("code-reviews.png"));
    await page.locator(".tasks-nav").getByRole("link", { name: "Work items", exact: true }).click();
    await page.getByRole("group", { name: "Filter work items" }).getByRole("button", { name: "Done", exact: true }).click();
    await expect(page.locator(".queue-card .status-badge")).toHaveText(["Done", "Done"]);
    await expect(page.getByRole("group", { name: "Filter work items" }).getByRole("button", { name: "To review", exact: true })).toHaveCount(0);
    await page.goto(`/?project=${project.id}&work=${active.id}`);
    await expect(page).toHaveURL(new RegExp(`/work-items\\?project=${project.id}&work=${active.id}`));
    await expect(page.locator(".detail-title")).toHaveText("Continue the shared context implementation");
  } finally { await api.dispose(); }
});

test("application settings hide Nemo by default, persist across routes and restore keyboard focus", async ({ page }, testInfo) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Application settings", exact: true });
  await expect(page.locator(".sidebar-note")).toBeHidden();
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "Application settings", exact: true });
  const toggle = drawer.getByRole("switch", { name: "Hide Nemo logo", exact: true });
  await expect(toggle).toBeChecked();
  await capturePage(page, testInfo.outputPath("application-settings.png"));
  await toggle.uncheck();
  await expect(page.locator("html")).toHaveAttribute("data-hide-nemo", "false");
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(trigger).toBeFocused();
  if ((page.viewportSize()?.width ?? 0) > 800) await expect(page.locator(".sidebar-note")).toBeVisible();
  await page.goto("/work-items");
  await trigger.click();
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await drawer.getByRole("button", { name: "Close application settings" }).click();
  await expect(trigger).toBeFocused();
  await page.reload();
  await expect(page.locator(".sidebar-note")).toBeHidden();
  await trigger.click();
  await expect(toggle).toBeChecked();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("tasks menu persists its disclosure state and the dashboard recovers failed reads", async ({ page }) => {
  await page.goto("/");
  const group = page.locator(".tasks-nav");
  const toggle = group.getByRole("button", { name: "Tasks", exact: true });
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(group.getByRole("link", { name: "Work items", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(group.getByRole("link", { name: "Code reviews", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/code-reviews");
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.route("**/tasks?**", (route) => route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Task read unavailable" } } }));
  await page.getByRole("link", { name: "Mnemonic home" }).click();
  await expect(page.getByRole("button", { name: "Retry tasks" })).toBeVisible();
  await page.unroute("**/tasks?**");
  await page.getByRole("button", { name: "Retry tasks" }).click();
  await expect(page.getByRole("button", { name: "Retry tasks" })).toHaveCount(0);
  await expect(page.locator(".task-widget-work_items dd").first()).not.toHaveText("—");
});
