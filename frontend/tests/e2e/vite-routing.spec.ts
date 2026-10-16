import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { dashboardPaths } from "../../lib/dashboard-routes";
import { statePath, type E2EState } from "./global.setup";

test("static delivery opens and refreshes every dashboard route and rejects retired paths", async ({ page }) => {
  test.setTimeout(90_000);
  for (const path of dashboardPaths) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    await expect(page.locator(".sidebar")).toBeVisible();
    expect((await page.reload())?.status(), `${path} reload`).toBe(200);
    await expect(page.locator(".sidebar")).toBeVisible();
    if (path !== "/") {
      await page.goto(`${path}/?marker=trailing#target`);
      await expect(page).toHaveURL(`${path}?marker=trailing#target`);
      await expect(page.locator(".sidebar")).toBeVisible();
    }
  }
  for (const path of ["/settings", "/missing-route", "/settings/missing", "/api/v1/projects"]) {
    const response = await page.request.get(path);
    expect(response.status(), path).toBe(404);
  }
});

test("root work links preserve project review and repeated query parameters", async ({ page }) => {
  const state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
  const query = `?work=${state.historicalCompletion.workItemId}&project=${state.projectId}&review=1&tag=one&tag=two`;
  await page.goto(`/${query}`);
  await expect(page).toHaveURL(`/work-items${query}`);
  await expect(page.locator(".detail-title")).toHaveText(state.historicalCompletion.title);
  expect(new URL(page.url()).searchParams.getAll("tag")).toEqual(["one", "two"]);
});

test("runtime settings failure blocks forms until a valid retry and retains prepaint preferences", async ({ page }) => {
  const actual = await page.request.get("/api/mnemonic/runtime-config");
  expect(actual.ok()).toBe(true);
  const config = await actual.json();
  await page.addInitScript(() => {
    localStorage.setItem("mnemonic.theme", "dark");
    localStorage.setItem("mnemonic.hide-nemo-logo", "false");
    localStorage.setItem("mnemonic.library-tools", "closed");
  });
  let valid = false;
  await page.route("**/api/mnemonic/runtime-config", (route) => route.fulfill({
    json: valid ? config : { ...config, work_summary_max_chars: "invalid" }
  }));
  await page.goto("/work-items");
  await expect(page.getByRole("alert")).toContainText("Dashboard settings could not be loaded");
  await expect(page.locator("form")).toHaveCount(0);
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-hide-nemo", "false");
  await expect(page.locator("html")).toHaveAttribute("data-library-tools", "closed");
  valid = true;
  await page.getByRole("button", { name: "Retry loading settings" }).click();
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
