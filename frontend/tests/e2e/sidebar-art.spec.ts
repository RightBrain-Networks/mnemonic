import { expect, test } from "@playwright/test";

test("the sidebar note displays the original robot and updated message", async ({ page }) => {
  await page.goto("/");
  const note = page.locator(".sidebar-note");
  const art = note.locator("img.note-art");
  await expect(note.locator("h2")).toHaveText("Keeping your agents on the same page.");
  await expect(art).toHaveAttribute("src", "/img/robot.svg");
  await expect(art).toHaveAttribute("alt", "");
  await expect.poll(() => art.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
  if ((page.viewportSize()?.width ?? 0) > 800) {
    // Cover both normal and short desktop windows; the artwork must stay available.
    for (const height of [1000, 720]) {
      await page.setViewportSize({ width: 1280, height });
      await art.scrollIntoViewIfNeeded();
      await expect(art).toBeInViewport();
      await expect(art).toBeVisible();
    }
  } else {
    await expect(note).toBeHidden();
  }
  await expect(note.locator("p")).toHaveCount(0);
});
