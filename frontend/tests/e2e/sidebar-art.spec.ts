import { expect, test } from "@playwright/test";

test("the sidebar note renders its artwork from a resolvable asset", async ({ page }) => {
  await page.goto("/");

  const art = page.locator("img.note-art");
  await expect(art).toHaveAttribute("src", "/img/robot.svg");

  // A missing or misnamed asset still yields an <img> element, so assert the
  // browser actually decoded it. This is what breaks when public/ artwork does
  // not reach the deployed image.
  await expect.poll(() => art.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);

  // Decorative: the note's own heading carries the meaning.
  await expect(art).toHaveAttribute("alt", "");
  await expect(page.locator(".sidebar-note h2")).not.toBeEmpty();
});
