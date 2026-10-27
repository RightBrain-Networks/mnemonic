import { expect, request, test } from "@playwright/test";
import { createProject, createReview } from "./task-fixtures";

test("review pagination retains loaded cards and shows failed appends at the queue end", async ({ page }) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    const path = `/api/v1/projects/${project.id}/settings`;
    const settings = await (await api.get(path)).json();
    expect((await api.patch(path, { data: {
      expected_revision: settings.revision, code_review_required_min_priority: 0
    } })).ok()).toBe(true);
    for (let index = 0; index < 21; index += 1) {
      await createReview(api, project.id, `Paginated review ${index}`);
    }
    let failAppend = true;
    let appends = 0;
    await page.route(`**/api/mnemonic/projects/${project.id}/tasks?**`, async (route) => {
      const params = new URL(route.request().url()).searchParams;
      if (params.get("kind") === "code_review" && Number(params.get("offset")) > 0) {
        appends += 1;
        if (failAppend) {
          await route.fulfill({ status: 502, contentType: "application/json",
            body: '{"detail":"Review page temporarily unavailable."}' });
          return;
        }
      }
      await route.continue();
    });
    await page.goto(`/work-items?project=${project.id}`);
    const cards = page.locator("[data-review-id]");
    await expect(cards).toHaveCount(20);
    await page.locator(".work-queue-sentinel").scrollIntoViewIfNeeded();
    const appendError = page.locator(".work-queue-append-error");
    await expect(appendError).toContainText("Review page temporarily unavailable.");
    await expect(cards).toHaveCount(20);
    expect(appends).toBeLessThan(5);
    failAppend = false;
    await appendError.getByRole("button", { name: "Try again", exact: true }).click();
    await expect(cards).toHaveCount(21);
    await expect(appendError).toHaveCount(0);
  } finally {
    await api.dispose();
  }
});
