import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

const contentsKey = "mnemonic.artifact-contents";

test("artifact search remembers both contents preferences and supports the slash shortcut", async ({ page }) => {
  await page.goto(`/artifacts?project=${state.projectId}`);
  const search = page.getByRole("searchbox", { name: "Search artifact metadata and content" });
  const contents = page.getByRole("switch", { name: "Include contents" });
  await expect(contents).toBeChecked();
  await expect(page.locator(".artifact-search-field kbd")).toHaveText("/");
  await page.keyboard.press("/");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("");
  await page.keyboard.type("folder/report");
  await expect(search).toHaveValue("folder/report");

  const submitted = page.waitForRequest((request) => request.url().endsWith("/search-content"));
  await search.press("Enter");
  expect((await submitted).postDataJSON()).toMatchObject({ q: "folder/report", fulltext: true });
  const metadataOnly = page.waitForRequest((request) => request.url().endsWith("/search-content") && request.postDataJSON().fulltext === false);
  await contents.uncheck();
  await metadataOnly;
  expect(await page.evaluate((key) => localStorage.getItem(key), contentsKey)).toBe("false");
  await page.reload();
  await expect(contents).not.toBeChecked();
  await contents.check();
  expect(await page.evaluate((key) => localStorage.getItem(key), contentsKey)).toBe("true");
  await page.reload();
  await expect(contents).toBeChecked();

  await page.getByText("Upload description, links and sensitivity", { exact: true }).click();
  const description = page.getByRole("textbox", { name: "Description", exact: true });
  await description.focus();
  await page.keyboard.type("notes/reference");
  await expect(description).toHaveValue("notes/reference");
  await expect(description).toBeFocused();
  await page.getByRole("button", { name: "Search", exact: true }).focus();
  await page.keyboard.press("Control+/");
  await expect(search).not.toBeFocused();
  await page.keyboard.press("/");
  await expect(search).toBeFocused();
  await search.fill("draft query");
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(search).toHaveValue("");
  await expect(search).toBeFocused();
  await expect(contents).toBeChecked();
});

test("artifact search works when browser preference storage is unavailable", async ({ page }) => {
  await page.addInitScript((key) => {
    const getItem = Storage.prototype.getItem;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.getItem = function (name) {
      if (name === key) throw new DOMException("Storage unavailable", "SecurityError");
      return getItem.call(this, name);
    };
    Storage.prototype.setItem = function (name, value) {
      if (name === key) throw new DOMException("Storage unavailable", "QuotaExceededError");
      return setItem.call(this, name, value);
    };
  }, contentsKey);
  await page.goto(`/artifacts?project=${state.projectId}`);
  const contents = page.getByRole("switch", { name: "Include contents" });
  await expect(contents).toBeChecked();
  await contents.uncheck();
  await expect(contents).not.toBeChecked();
  await page.getByRole("searchbox").fill("storage unavailable");
  const submitted = page.waitForRequest((request) => request.url().endsWith("/search-content"));
  await page.getByRole("button", { name: "Search", exact: true }).click();
  expect((await submitted).postDataJSON()).toMatchObject({ fulltext: false });
  await expect(page.locator(".artifact-search-status")).toBeVisible();
});

test("the upload target opens a picker and filename details use an accessible right drawer", async ({ page }, testInfo) => {
  if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1280, height: 1000 });
  const filename = `Project brief-${state.runId.slice(0, 8)}-${testInfo.project.name}.txt`;
  await page.goto(`/artifacts?project=${state.projectId}`);
  await expect(page.getByText("FILES THAT STAY WITH YOUR WORK — BUT OUT OF YOUR CODEBASE", { exact: true })).toBeVisible();
  await expect(page.getByText(`Store documents, binaries and other files in the “${state.projectName}” project.`, { exact: true })).toBeVisible();
  const target = page.getByRole("button", { name: "Drop, paste, or upload files here.", exact: true });
  await expect(target).toHaveCSS("border-top-style", "dashed");
  await target.focus();
  const picker = page.waitForEvent("filechooser");
  await target.click();
  await (await picker).setFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("A project brief stored alongside the work.") });
  const name = page.getByRole("button", { name: filename, exact: true });
  await expect(name).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload files", exact: true })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("artifact-search-surface.png"), fullPage: true, animations: "disabled" });
  await testInfo.attach("Artifact search surface", { path: testInfo.outputPath("artifact-search-surface.png"), contentType: "image/png" });

  await name.click();
  const drawer = page.getByRole("dialog", { name: filename, exact: true });
  const close = drawer.getByRole("button", { name: "Close details" });
  await expect(close).toBeFocused();
  await expect(drawer).toBeVisible();
  await expect.poll(async () => {
    const bounds = await drawer.boundingBox();
    return bounds ? Math.abs(bounds.x + bounds.width - page.viewportSize()!.width) : Infinity;
  }).toBeLessThan(2);
  await expect(drawer).toContainText("r1");
  await page.keyboard.press("/");
  await expect(close).toBeFocused();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    // Native dialogs may yield focus to browser chrome at the end of the tab cycle.
    expect(await drawer.evaluate((element) => document.activeElement === document.body || element.contains(document.activeElement))).toBe(true);
  }
  await close.focus();
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["Not an upload"], "drawer-paste.txt"));
    document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true }));
  });
  await page.screenshot({ path: testInfo.outputPath("artifact-details-drawer.png"), animations: "disabled" });
  await testInfo.attach("Right-side artifact details", { path: testInfo.outputPath("artifact-details-drawer.png"), contentType: "image/png" });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(name).toBeFocused();
  await expect(page.getByRole("button", { name: "drawer-paste.txt", exact: true })).toHaveCount(0);
  await name.click();
  await close.click();
  await expect(name).toBeFocused();
  if (testInfo.project.name === "chromium-desktop") {
    await name.click();
    await page.mouse.click(5, 5);
    await expect(drawer).toBeHidden();
    await expect(name).toBeFocused();
  }
  await page.locator(".theme-selector label").filter({ has: page.getByRole("radio", { name: "Dark", exact: true }) }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: testInfo.outputPath("artifact-search-dark.png"), fullPage: true, animations: "disabled" });
  await testInfo.attach("Artifact search in dark mode", { path: testInfo.outputPath("artifact-search-dark.png"), contentType: "image/png" });
  await name.click();
  await page.screenshot({ path: testInfo.outputPath("artifact-details-dark.png"), animations: "disabled" });
  await testInfo.attach("Artifact details in dark mode", { path: testInfo.outputPath("artifact-details-dark.png"), contentType: "image/png" });
});
