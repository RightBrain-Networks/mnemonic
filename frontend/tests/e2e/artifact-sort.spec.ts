import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { Artifact, ArtifactSort } from "../../lib/artifacts";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

test("artifact sorting preserves scroll position while loading and after reordering", async ({ page }, testInfo) => {
  const artifacts: Artifact[] = Array.from({ length: 100 }, (_, index) => ({
    id: randomUUID(), project_id: state.projectId,
    filename: `artifact-${String(index).padStart(2, "0")}.txt`, description: null,
    revision: 100 - index, size_bytes: (100 - index) * 1024, sha256: "a".repeat(64),
    mime_type: "text/plain", created_at: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
    modified_at: new Date(Date.UTC(2026, 0, 100 - index)).toISOString(),
    deleted_at: null, content_available: true, sensitive: false, related_artifact_ids: [],
    created_by_agent_session_id: null, originating_work_item_id: null, related_work_item_ids: [],
    extraction: { status: "pending", metadata: {}, truncated: false, error_code: null, extracted_at: null }
  }));
  let release: (() => void) | undefined;
  let holdResponse = false;
  function releaseResponse() {
    holdResponse = false;
    release?.();
    release = undefined;
  }
  await page.route(`**/api/artifacts/projects/${state.projectId}/artifacts?*`, async (route) => {
    const query = new URL(route.request().url()).searchParams;
    const sort = query.get("sort") as ArtifactSort;
    const offset = Number(query.get("offset"));
    const direction = query.get("order") === "asc" ? 1 : -1;
    const items = [...artifacts].sort((left, right) => {
      const a = left[sort], b = right[sort];
      return (a < b ? -1 : a > b ? 1 : 0) * direction;
    });
    if (holdResponse) await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ json: { items: items.slice(offset, offset + 50), total: items.length, limit: 50, offset } });
  });
  await page.goto(`/artifacts?project=${state.projectId}`);
  const directory = page.getByRole("region", { name: "Sortable artifact directory" });
  const content = page.locator(".page-content");
  // The narrow layout scrolls the document; desktop scrolls the content pane.
  const scroller = await content.evaluate((element) => getComputedStyle(element).overflowY === "auto")
    ? content : page.locator("html");
  const rows = directory.locator("tbody tr");
  await expect(rows).toHaveCount(50);
  await expect(directory).toHaveAttribute("aria-busy", "false");

  const columns = [
    ["Name", "filename"], ["Size", "size_bytes"], ["Revision", "revision"],
    ["Created", "created_at"], ["Modified", "modified_at"]
  ] as const;
  for (const [label, sort] of columns) {
    const header = directory.getByRole("columnheader", { name: label, exact: true });
    const button = header.getByRole("button");
    for (const keyboard of [false, true]) {
      await button.scrollIntoViewIfNeeded();
      if (keyboard) await button.focus();
      await scroller.evaluate((element) => {
        const header = element.querySelector(".artifact-table thead")!;
        const top = element === document.scrollingElement ? 0 : element.getBoundingClientRect().top;
        element.scrollTop += header.getBoundingClientRect().top - top - 24;
      });
      const before = await scroller.evaluate((element) => element.scrollTop);
      const horizontalBefore = await directory.evaluate((element) => element.scrollLeft);
      expect(before).toBeGreaterThan(100);
      const ascending = await header.getAttribute("aria-sort") !== "ascending";
      holdResponse = true;
      const requested = page.waitForRequest((request) => {
        const url = new URL(request.url());
        return url.pathname.endsWith("/artifacts") && url.searchParams.get("sort") === sort;
      });
      if (keyboard) await button.press("Enter"); else await button.click();
      await requested;
      try {
        await expect(directory).toHaveAttribute("aria-busy", "true");
        expect(await scroller.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
        expect(await directory.evaluate((element) => element.scrollLeft)).toBeCloseTo(horizontalBefore, 0);
        await expect(rows).toHaveCount(50);
        await expect(directory.getByRole("columnheader", { name: label, exact: true })).toHaveAttribute("aria-sort", ascending ? "ascending" : "descending");
      } finally {
        releaseResponse();
      }
      await expect(directory).toHaveAttribute("aria-busy", "false");
      const sorted = [...artifacts].sort((left, right) => {
        const a = left[sort], b = right[sort];
        return (a < b ? -1 : a > b ? 1 : 0) * (ascending ? 1 : -1);
      });
      await expect(rows.first().locator(".artifact-name")).toHaveText(sorted[0].filename);
      // Let layout and Firefox's scroll anchoring settle before measuring again.
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect(await scroller.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
      expect(await directory.evaluate((element) => element.scrollLeft)).toBeCloseTo(horizontalBefore, 0);
      if (keyboard) await expect(button).toBeFocused();
    }
  }
  // Pagination keeps the displayed range truthful until the replacement page arrives.
  const pagination = page.locator(".artifact-pagination");
  holdResponse = true;
  await pagination.getByRole("button", { name: "Next", exact: true }).click();
  await expect(directory).toHaveAttribute("aria-busy", "true");
  await expect(rows).toHaveCount(50);
  await expect(pagination).toContainText("1–50 of 100 artifacts");
  await expect.poll(() => release).toBeTruthy();
  releaseResponse();
  await expect(directory).toHaveAttribute("aria-busy", "false");
  await expect(pagination).toContainText("51–100 of 100 artifacts");

  const name = directory.getByRole("button", { name: "Name", exact: true });
  await name.scrollIntoViewIfNeeded();
  const before = await scroller.evaluate((element) => element.scrollTop);
  holdResponse = true;
  await name.click();
  await expect(directory).toHaveAttribute("aria-busy", "true");
  expect(await scroller.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
  await expect(rows).toHaveCount(50);
  await expect(pagination).toContainText("51–100 of 100 artifacts");
  await expect.poll(() => release).toBeTruthy();
  releaseResponse();
  await expect(directory).toHaveAttribute("aria-busy", "false");
  await expect(pagination).toContainText("1–50 of 100 artifacts");

  await page.screenshot({ path: testInfo.outputPath("artifact-sort-scroll.png") });
  await testInfo.attach("Artifact sorting preserves scroll position", { path: testInfo.outputPath("artifact-sort-scroll.png"), contentType: "image/png" });

  // Retaining rows for sort/page requests must not leak them into a different filter.
  holdResponse = true;
  await page.getByLabel("Show deleted", { exact: true }).check();
  await expect(directory).toHaveAttribute("aria-busy", "true");
  await expect(rows).toHaveCount(0);
  await expect(page.getByText("Loading artifacts…", { exact: true })).toBeVisible();
  await expect.poll(() => release).toBeTruthy();
  releaseResponse();
  await expect(directory).toHaveAttribute("aria-busy", "false");
  await expect(rows).toHaveCount(50);
});
