import { expect, test } from "@playwright/test";

const preferenceKey = "mnemonic.resources-menu";

test("resources menu groups its leaves below Needs Attention and opens the Transcripts library", async ({ page }, testInfo) => {
  if ((page.viewportSize()?.width ?? 0) > 800) await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  const resources = navigation.locator(".resources-nav");
  const toggle = resources.getByRole("button", { name: "Resources", exact: true });
  await expect(resources).toHaveAttribute("data-ready", "true");
  await expect(navigation.locator(":scope > a, :scope > div > button")).toHaveText([
    "Work library", /^Summaries/, /^Needs Attention/, "Resources", "Project settings"
  ]);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(resources.locator("a")).toHaveText(["Artifacts", "Transcripts"]);
  await resources.getByRole("link", { name: "Transcripts", exact: true }).click();
  await expect(page).toHaveURL(/\/transcripts(?:\?project=[a-f0-9-]+)?$/);
  await expect(page.locator("h1")).toHaveText("Transcripts.");
  await expect(navigation.locator('[aria-current="page"]')).toHaveText("Transcripts");
  await expect(page.locator(".breadcrumb")).toContainText("Transcripts");
  await expect(page.locator(".skip-link")).toHaveText("Skip to transcripts");
  await expect(page.getByRole("region", { name: "Transcript library", exact: true })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search transcript metadata and content" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Transcript directory", exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.locator("h1")).toHaveText("Transcripts.");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.evaluate(() => document.fonts.ready);
  const screenshot = testInfo.outputPath("resources-transcripts.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach("Resources and Transcripts library", { path: screenshot, contentType: "image/png" });
});

test("resources menu restores its own disclosure state and supports keyboard navigation", async ({ page }) => {
  await page.goto("/transcripts");
  const group = page.locator(".resources-nav");
  const toggle = group.getByRole("button", { name: "Resources", exact: true });
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const settings = page.getByRole("button", { name: "Project settings" });
  await settings.click();
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(settings).toHaveAttribute("aria-expanded", "true");
  await expect(group.locator(".nav-group-collapse")).toHaveAttribute("inert", "");
  await page.keyboard.press("Tab");
  await expect(settings).toBeFocused();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), preferenceKey)).toBe("closed");
  await page.reload();
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(settings).toHaveAttribute("aria-expanded", "true");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(group.getByRole("link", { name: "Artifacts", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(group.getByRole("link", { name: "Transcripts", exact: true })).toBeFocused();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), preferenceKey)).toBe("open");
  await page.reload();
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
});

test("resources menu supports reduced motion and unavailable storage", async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException("Storage unavailable", "SecurityError"); };
    Storage.prototype.setItem = () => { throw new DOMException("Storage unavailable", "SecurityError"); };
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/artifacts");
  const group = page.locator(".resources-nav");
  const toggle = group.getByRole("button", { name: "Resources", exact: true });
  await expect(group).toHaveAttribute("data-ready", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(group.locator(".nav-group-collapse")).toHaveCSS("transition-property", "none");
  await expect(group.locator(".nav-group-chevron")).toHaveCSS("transition-property", "none");
  await toggle.click();
  await expect(group.locator(".nav-group-collapse")).toBeHidden();
  await toggle.click();
  await group.getByRole("link", { name: "Transcripts", exact: true }).click();
  await expect(page).toHaveURL(/\/transcripts(?:\?project=[a-f0-9-]+)?$/);
  await expect(page.locator("h1")).toHaveText("Transcripts.");
});
