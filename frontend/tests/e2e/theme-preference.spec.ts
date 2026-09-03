import { expect, test } from "@playwright/test";

test("theme choices persist and Auto follows the system", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");

  const auto = page.getByRole("radio", { name: "Auto" });
  const dark = page.getByRole("radio", { name: "Dark" });
  const light = page.getByRole("radio", { name: "Light" });

  await expect(auto).toBeChecked();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByText("Light", { exact: true }).click();
  await expect.poll(() => page.evaluate(() => document.getAnimations()
    .some((animation) => {
      const effect = animation.effect;
      return effect instanceof KeyframeEffect && effect.getKeyframes().some(
        (frame) => frame.easing === "cubic-bezier(0.25, 1, 0.5, 1)"
      );
    }))).toBe(true);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("mnemonic.theme")))
    .toBe("light");

  await page.reload();
  await expect(light).toBeChecked();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await page.getByText("Dark", { exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByText("Auto", { exact: true }).click();
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("mnemonic.theme")))
    .toBe("auto");
});
