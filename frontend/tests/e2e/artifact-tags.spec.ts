import { expect, request, test } from "@playwright/test";
import { createProject } from "./task-fixtures";

test("artifact tags can be assigned, filtered, edited and removed with exact retries", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    await page.goto(`/artifacts?project=${project.id}`);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    const uploadOptions = page.locator(".artifact-upload-options");
    await uploadOptions.locator("summary").click();
    const uploadTags = uploadOptions.getByRole("group", { name: "Tags", exact: true });
    for (const [suffix, tags] of [["tests", ["tests"]], ["infra", ["infra"]], ["both", ["tests", "infra"]], ["plain", []]] as const) {
      for (const tag of ["tests", "infra"]) await uploadTags.getByRole("checkbox", { name: new RegExp(`^${tag}\\b`) }).setChecked((tags as readonly string[]).includes(tag));
      const name = `artifact-tag-report-${suffix}.txt`;
      await page.getByLabel("Upload artifact files").setInputFiles({ name, mimeType: "text/plain", buffer: Buffer.from(`Tag acceptance content for ${suffix}.`) });
      const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
      await expect(row).toBeVisible();
      await expect.poll(async () => (await row.locator(".work-tag").allTextContents()).sort()).toEqual([...tags].sort());
    }
    await uploadOptions.locator("summary").click();
    const filter = page.getByRole("group", { name: "Filter by tags", exact: true });
    const rows = page.locator(".artifact-table tbody tr");
    await expect(rows).toHaveCount(4);
    await filter.getByRole("button", { name: "tests", exact: true }).click();
    await expect(rows).toHaveCount(2);
    await filter.getByRole("button", { name: "infra", exact: true }).click();
    await expect(rows).toHaveCount(3);
    await filter.getByText("AND", { exact: true }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("artifact-tag-report-both.txt");
    await page.getByLabel("Include contents").uncheck();
    await page.getByLabel("Search artifact metadata and content").fill("artifact-tag-report");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(rows).toHaveCount(1);
    await expect(page.locator(".artifact-search-status")).toBeVisible();
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await filter.getByText("All", { exact: true }).click();
    await expect(rows).toHaveCount(4);
    await page.getByRole("columnheader", { name: /^Name/ }).getByRole("button").click();
    await expect(rows.first()).toContainText("artifact-tag-report-both.txt");
    const viewport = page.viewportSize()!;
    if (viewport.width > 650) await page.setViewportSize({ ...viewport, height: 1200 });
    await page.evaluate(() => { document.querySelectorAll(".main-content, .page-content").forEach((element) => { element.scrollTop = 0; }); (document.activeElement as HTMLElement)?.blur(); });
    await page.screenshot({ path: testInfo.outputPath("artifact-tags-library.png"), fullPage: true, animations: "disabled" });
    await page.setViewportSize(viewport);

    const filename = "artifact-tag-report-both.txt";
    await page.getByRole("button", { name: filename, exact: true }).click();
    const details = page.getByRole("region", { name: `Metadata for ${filename}` });
    const tags = details.getByRole("region", { name: "Artifact tags", exact: true });
    await expect.poll(async () => (await tags.locator(".work-tag").allTextContents()).sort()).toEqual(["infra", "tests"]);
    await tags.getByRole("button", { name: "Edit tags", exact: true }).click();
    await tags.getByRole("checkbox", { name: /^infra\b/ }).uncheck();
    await tags.getByRole("checkbox", { name: /^security\b/ }).check();
    await tags.getByRole("button", { name: "Save tags", exact: true }).click();
    await expect(tags.getByRole("button", { name: "Edit tags", exact: true })).toBeEnabled();
    await expect.poll(async () => (await tags.locator(".work-tag").allTextContents()).sort()).toEqual(["security", "tests"]);
    await expect(details.locator(".artifact-revision")).toHaveText("r2");
    await details.locator(".artifact-details-body").evaluate((element) => { element.scrollTop = 0; });
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    await details.screenshot({ path: testInfo.outputPath("artifact-tags-details.png"), animations: "disabled" });

    const attempts: { body: string | null; operation: string | undefined }[] = [];
    await page.route(`**/api/artifacts/projects/${project.id}/artifacts/*`, async (route) => {
      const request = route.request();
      if (request.method() !== "PATCH" || !("tags" in request.postDataJSON())) { await route.continue(); return; }
      attempts.push({ body: request.postData(), operation: request.headers()["x-client-operation-id"] });
      const response = await route.fetch();
      if (attempts.length === 1) await route.abort("failed");
      else await route.fulfill({ response });
    });
    await tags.getByRole("button", { name: "Edit tags", exact: true }).click();
    await tags.getByRole("checkbox", { name: /^tests\b/ }).uncheck();
    await tags.getByRole("checkbox", { name: /^security\b/ }).uncheck();
    await tags.getByRole("button", { name: "Save tags", exact: true }).click();
    await expect(details.getByRole("button", { name: "Close details" })).toBeDisabled();
    await details.getByRole("button", { name: "Retry pending action", exact: true }).click();
    await expect(details.getByRole("button", { name: "Close details" })).toBeEnabled();
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toEqual(attempts[1]);
    expect(JSON.parse(attempts[0].body!).tags).toBeNull();
    await tags.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(tags).toContainText("No tags selected.");
    await expect(details.locator(".artifact-revision")).toHaveText("r3");
    await details.getByRole("button", { name: "Close details" }).click();
    const cleared = rows.filter({ has: page.getByRole("button", { name: filename, exact: true }) });
    await expect(cleared.locator(".work-tag")).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("starter tags are editable inline and deletable without changing system tags", async ({ page }, testInfo) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await createProject(api);
    await page.goto(`/work-items?project=${project.id}`);
    await expect(page.locator("#project-select")).toHaveValue(project.id);
    await page.goto("/settings/tags");
    const list = page.getByRole("list", { name: "Project tags" });
    await expect(list.getByRole("listitem")).toHaveCount(9);
    const starter = list.getByRole("listitem").filter({ has: page.locator(".work-tag", { hasText: /^security$/ }) });
    const rowIndex = await starter.evaluate((row) => Array.from(row.parentElement!.children).indexOf(row));
    await starter.getByRole("button", { name: "Edit", exact: true }).click();
    const editingRow = list.getByRole("listitem").nth(rowIndex);
    const form = editingRow.getByRole("form", { name: "Edit tag", exact: true });
    await expect(form).toBeVisible();
    await expect(page.locator(".tag-settings > .tag-edit-form")).toHaveCount(0);
    const bounds = await form.boundingBox();
    expect(bounds!.height).toBeLessThan(testInfo.project.name === "chromium-narrow" ? 260 : 150);
    await form.getByLabel("Description", { exact: true }).fill("Security checks and privacy findings.");
    await form.getByLabel("Tag color hex", { exact: true }).fill("#a8c8e8");
    await form.scrollIntoViewIfNeeded();
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    await page.screenshot({ path: testInfo.outputPath("tags-inline-edit.png"), fullPage: true, animations: "disabled" });
    await form.getByRole("button", { name: "Save tag", exact: true }).click();
    await expect(starter).toContainText("Security checks and privacy findings.");
    page.once("dialog", (dialog) => dialog.accept());
    await starter.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(starter).toHaveCount(0);
    await page.reload();
    await expect(list.getByRole("listitem")).toHaveCount(8);
    await expect(list).toContainText("Design Impact Review: investigate the impact a revised decision document");
    for (const name of ["code-review", "remediation", "dir"]) {
      const row = list.getByRole("listitem").filter({ has: page.locator(".work-tag", { hasText: new RegExp(`^${name}$`) }) });
      await expect(row.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
      await expect(row.getByRole("button", { name: "Delete", exact: true })).toHaveCount(0);
    }
  } finally { await api.dispose(); }
});
