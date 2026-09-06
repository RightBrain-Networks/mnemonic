import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("project, filter, and sort selections survive a reload", async ({ page }) => {
  await page.goto("/");
  const projectSelect = page.locator("#project-select");
  await projectSelect.selectOption(state.projectId);

  const activeFilter = page.getByRole("button", { name: "Active", exact: true });
  await activeFilter.click();
  await page.getByText("Priority", { exact: true }).click();

  await expect(activeFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("radio", { name: "Priority" })).toBeChecked();
  await expect.poll(() => page.evaluate(() => ({
    project: localStorage.getItem("mnemonic.project"),
    status: localStorage.getItem("mnemonic.status"),
    sort: localStorage.getItem("mnemonic.sort")
  }))).toEqual({
    project: state.projectId,
    status: "active",
    sort: "priority"
  });

  await page.reload();

  await expect(projectSelect).toHaveValue(state.projectId);
  await expect(activeFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("radio", { name: "Priority" })).toBeChecked();
});

test("the complete library overview collapses with directional easing and persists", async ({ page }) => {
  await page.goto("/");
  const toggle = page.locator(".library-tools-toggle");
  const panel = page.locator("#library-tools-panel");
  const filters = page.getByRole("group", { name: "Filter work items" });
  const heading = page.getByRole("heading", { name: /^Work library[.:]/ });
  const pageHeading = page.locator(".page-heading");
  const refresh = pageHeading.getByRole("button", { name: "Refresh" });
  const newWork = pageHeading.getByRole("button", { name: "New work" });
  const liveUpdates = pageHeading.getByText("Live updates", { exact: true });
  const reviewQueue = page.getByText("Code review queue and unanswered recommendations", {
    exact: true
  });
  const search = page.getByRole("searchbox", { name: "Search work items" });
  const filterTop = async () => {
    const box = await filters.boundingBox();
    if (!box) throw new Error("The lifecycle filter row is not visible.");
    return box.y;
  };

  await expect(panel.locator(".page-heading")).toHaveCount(1);
  await expect(panel.locator(".sync-status")).toHaveCount(1);
  await expect(page.getByText("Search and reviews", { exact: true })).toHaveCount(0);
  await expect(heading).toBeVisible();
  await expect(refresh).toBeVisible();
  await expect(newWork).toBeVisible();
  await expect(liveUpdates).toBeVisible();
  await expect(reviewQueue).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "open");
  await expect(toggle).toHaveAccessibleName("Collapse work library overview");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toHaveText("");
  await expect(panel).toHaveAttribute("aria-hidden", "false");
  await expect(panel).toHaveCSS(
    "transition-timing-function",
    "cubic-bezier(0.5, 0, 0.75, 0)"
  );
  await expect(search).toBeVisible();
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem("mnemonic.library-tools")
  )).toBe("open");
  const expandedTop = await filterTop();
  const [toggleBox, filtersBox] = await Promise.all([toggle.boundingBox(), filters.boundingBox()]);
  if (!toggleBox || !filtersBox) throw new Error("The library controls are not visible.");
  expect(toggleBox.x).toBeLessThan(filtersBox.x);

  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "closed");
  await expect(toggle).toHaveAccessibleName("Expand work library overview");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(panel).toHaveAttribute("aria-hidden", "true");
  await expect(panel).toHaveAttribute("inert", "");
  await expect(panel).toHaveCSS(
    "transition-timing-function",
    "cubic-bezier(0.25, 1, 0.5, 1)"
  );
  await expect(panel).toHaveCSS("height", "0px");
  await expect(heading).toBeHidden();
  await expect(refresh).toBeHidden();
  await expect(newWork).toBeHidden();
  await expect.poll(filterTop).toBeLessThan(expandedTop - 120);
  const collapsedTop = await filterTop();
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem("mnemonic.library-tools")
  )).toBe("closed");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "closed");
  await expect(toggle).toHaveAccessibleName("Expand work library overview");
  await expect(panel).toHaveCSS("height", "0px");
  await expect(filters).toBeVisible();

  await page.keyboard.press("/");
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "open");
  await expect(toggle).toHaveAccessibleName("Collapse work library overview");
  await expect(search).toBeFocused();
  await expect(panel).toHaveCSS(
    "transition-timing-function",
    "cubic-bezier(0.5, 0, 0.75, 0)"
  );
  await expect.poll(filterTop).toBeGreaterThan(collapsedTop + 120);
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem("mnemonic.library-tools")
  )).toBe("open");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "open");
  await expect(toggle).toHaveAccessibleName("Collapse work library overview");
  await expect(heading).toBeVisible();
  await expect(search).toBeVisible();
});
