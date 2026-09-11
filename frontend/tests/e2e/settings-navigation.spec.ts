import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

const preferenceKey = "mnemonic.settings-menu";
const sections = [
  { label: "Workspace", path: "/settings/workspace", cards: ["Project details"] },
  { label: "Prompts", path: "/settings/prompts", cards: ["Recall pointer content", "Job completion report prompt"] },
  { label: "Code reviews", path: "/settings/code-reviews", cards: ["Code reviews"] },
  { label: "Backups", path: "/settings/backups", cards: ["Project backups"] }
];

let state: E2EState;
test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("settings menu links show only their dedicated cards in the requested order", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  const toggle = navigation.getByRole("button", { name: "Project settings" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(page.locator(".settings-nav-chevron")).toBeVisible();
  await expect(page.locator(".settings-nav-leaves a")).toHaveText(sections.map(({ label }) => label));
  for (const section of sections) {
    await navigation.getByRole("link", { name: section.label, exact: true }).click();
    await expect(page).toHaveURL(section.path);
    await expect(page.locator("h1")).toHaveText(`${section.label}.`);
    await expect(page.locator(".settings-card h2")).toHaveText(section.cards);
    await expect(navigation.locator('[aria-current="page"]')).toHaveText(section.label);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
  }
  await navigation.getByRole("link", { name: "Workspace", exact: true }).click();
  await expect(page.getByLabel("Project name", { exact: true })).toHaveValue(state.projectName);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const screenshot = testInfo.outputPath("project-settings-menu.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach("Project settings menu", { path: screenshot, contentType: "image/png" });
});

test("settings menu preserves both disclosure states and supports keyboard navigation", async ({ page }) => {
  await page.goto("/settings/workspace");
  const toggle = page.getByRole("button", { name: "Project settings" });
  const group = page.locator(".settings-nav");
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), preferenceKey)).toBe("closed");
  await expect(page.locator(".settings-nav-collapse")).toHaveAttribute("inert", "");
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.closest(".settings-nav-leaves") !== null)).toBe(false);
  await page.reload();
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Workspace", exact: true })).toBeFocused();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), preferenceKey)).toBe("open");
  await page.reload();
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("link", { name: "Work library", exact: true }).click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
});

test("settings menu uses Expo easing in each direction and respects reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Project settings" });
  const collapse = page.locator(".settings-nav-collapse");
  await expect(page.locator(".settings-nav")).toHaveAttribute("data-ready", "true");
  await toggle.click();
  await expect(collapse).toHaveCSS("transition-timing-function", "cubic-bezier(0.16, 1, 0.3, 1), ease");
  await expect.poll(() => collapse.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(100);
  await toggle.click();
  await expect(collapse).toHaveCSS("transition-timing-function", "cubic-bezier(0.7, 0, 0.84, 0), ease");
  await expect.poll(() => collapse.evaluate((element) => element.getBoundingClientRect().height)).toBe(0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await toggle.click();
  await expect(collapse).toHaveCSS("transition-duration", "0s");
  await expect(page.locator(".settings-nav-chevron")).toHaveCSS("transition-duration", "0s");
  await expect(page.getByRole("link", { name: "Workspace", exact: true })).toBeVisible();
  await toggle.click();
  await expect(collapse).toBeHidden();
});

test("settings menu remains usable when localStorage is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException("Storage unavailable", "SecurityError"); };
    Storage.prototype.setItem = () => { throw new DOMException("Storage unavailable", "SecurityError"); };
  });
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Project settings" });
  await expect(page.locator(".settings-nav")).toHaveAttribute("data-ready", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("link", { name: "Prompts", exact: true }).click();
  await expect(page).toHaveURL("/settings/prompts");
  await expect(page.locator("h1")).toHaveText("Prompts.");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("settings menu has no legacy settings landing page or fallback redirect", async ({ page }) => {
  for (const path of ["/settings", "/settings/unknown"]) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(404);
    expect(response?.request().redirectedFrom()).toBeNull();
    await expect(page).toHaveURL(path);
  }
});
