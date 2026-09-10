import { expect, test } from "@playwright/test";

test("the sidebar note keeps only the updated message", async ({ page }) => {
  await page.goto("/");

  const note = page.locator(".sidebar-note");
  await expect(note.locator("h2")).toHaveText("Keeping your agents on the same page.");
  await expect(note.locator("img")).toHaveCount(0);
  await expect(note.locator("p")).toHaveCount(0);
});
