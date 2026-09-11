import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

async function upload(page: Page, name: string, body: string | Buffer, mimeType = "text/plain") {
  await page.goto(`/artifacts?project=${state.projectId}`);
  await page.getByLabel("Upload artifact files").setInputFiles({ name, mimeType, buffer: typeof body === "string" ? Buffer.from(body) : body });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  await expect(row).toBeVisible();
  return row;
}

async function remove(page: Page, name: string) {
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: `Delete ${name}`, exact: true }).click();
  await expect(page.getByRole("button", { name, exact: true })).toBeHidden();
}

test("artifact preview shows plain text in a half-width modal and copies exact contents", async ({ page }, testInfo) => {
  const name = `preview-text-${testInfo.project.name}.txt`;
  const source = "Project notes\r\n\tPreserve whitespace, café and 日本語.\r\n<script>alert('inert')</script>\r\n";
  const row = await upload(page, name, source);
  const view = row.getByRole("button", { name: `View ${name}`, exact: true });
  const actions = await row.locator(".artifact-actions > *").allTextContents();
  expect(actions.slice(0, 2)).toEqual(["View", "Download"]);
  await view.click();
  const drawer = page.getByRole("dialog", { name, exact: true });
  const contents = drawer.getByRole("textbox", { name: "Plain text contents" });
  await expect(contents).toHaveValue(source.replaceAll("\r\n", "\n"));
  await expect(contents).toHaveAttribute("readonly", "");
  expect(await contents.evaluate((element) => getComputedStyle(element).fontFamily)).toContain("Atkinson Hyperlegible Mono");
  await expect(drawer.getByRole("button", { name: "Close preview" })).toBeFocused();
  await expect.poll(async () => Math.round((await drawer.boundingBox())!.x)).toBe(0);
  const bounds = (await drawer.boundingBox())!;
  expect(bounds.width).toBe(page.viewportSize()!.width / 2);
  expect(bounds.height).toBe(page.viewportSize()!.height);
  expect(await drawer.evaluate((element) => getComputedStyle(element, "::backdrop").backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
  await page.keyboard.press("Tab");
  expect(await drawer.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await drawer.getByRole("button", { name: "Copy contents" }).click();
  await expect(drawer.getByRole("status")).toHaveText("Contents copied.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(source);
  const uploads: string[] = [];
  page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/artifacts")) uploads.push(request.url()); });
  await drawer.evaluate((element) => {
    const transfer = new DataTransfer(); transfer.items.add(new File(["Preview must not upload"], "accidental.txt"));
    element.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
    document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  });
  expect(uploads).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("artifact-preview-text.png") });
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(view).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
  await remove(page, name);
});

test("artifact preview reuses safe scrollable Markdown rendering and copies the source", async ({ page }, testInfo) => {
  const name = `preview-markdown-${testInfo.project.name}.md`;
  const source = "# Project handoff\n\nA **durable** plan with `code`.\n\n| Task | Status |\n| --- | --- |\n| Preview | Ready |\n\n<script>window.previewExecuted = true</script>\n\n![Remote](https://example.invalid/tracking.png)\n\n" + "## Next steps\n\n- Review the notes\n- Share the result\n\n".repeat(35);
  const row = await upload(page, name, source);
  await row.getByRole("button", { name: `View ${name}`, exact: true }).click();
  const drawer = page.getByRole("dialog", { name, exact: true });
  const contents = drawer.getByRole("region", { name: "Markdown contents" });
  await expect(contents.getByRole("heading", { name: "Project handoff" })).toBeVisible();
  await expect(contents.locator("strong")).toHaveText("durable");
  await expect(contents.getByRole("table")).toBeVisible();
  await expect(contents.locator("script, img")).toHaveCount(0);
  await expect(contents).toContainText("<script>window.previewExecuted = true</script>");
  expect(await contents.evaluate((element) => element.scrollHeight > element.clientHeight && getComputedStyle(element).overflowY === "auto")).toBe(true);
  await drawer.getByRole("button", { name: "Copy contents" }).click();
  await expect(drawer.getByRole("status")).toHaveText("Contents copied.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(source);
  await page.screenshot({ path: testInfo.outputPath("artifact-preview-markdown.png") });
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  await page.screenshot({ path: testInfo.outputPath("artifact-preview-markdown-dark.png") });
  await contents.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(drawer.getByRole("button", { name: "Close preview" })).toBeInViewport();
  await page.mouse.click(page.viewportSize()!.width - 10, 30);
  await expect(drawer).toHaveCount(0);
  await remove(page, name);
});

test("artifact preview fits large images, preserves small images and opens an image tab", async ({ page }, testInfo) => {
  await page.goto(`/artifacts?project=${state.projectId}`);
  for (const size of [2400, 32]) {
    const name = `preview-image-${size}-${testInfo.project.name}.png`;
    const png = await page.evaluate((size) => {
      const canvas = document.createElement("canvas"); canvas.width = size; canvas.height = size * 2 / 3;
      const context = canvas.getContext("2d")!;
      const gradient = context.createLinearGradient(0, 0, size, canvas.height);
      gradient.addColorStop(0, "#251130"); gradient.addColorStop(1, "#8d54ff");
      context.fillStyle = gradient; context.fillRect(0, 0, size, canvas.height);
      context.fillStyle = "#ff6c27"; context.fillRect(size / 8, size / 8, size / 12, size / 12);
      context.fillStyle = "#fdfbf5"; context.font = `${size / 18}px sans-serif`;
      context.fillText("Project overview", size / 8, size / 3);
      return canvas.toDataURL("image/png").split(",")[1];
    }, size);
    const row = await upload(page, name, Buffer.from(png, "base64"), "image/png");
    await row.getByRole("button", { name: `View ${name}`, exact: true }).click();
    const drawer = page.getByRole("dialog", { name, exact: true });
    const image = drawer.getByRole("img", { name, exact: true });
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(size);
    const dimensions = await image.evaluate((element: HTMLImageElement) => {
      const image = element.getBoundingClientRect(); const area = element.parentElement!.getBoundingClientRect();
      return { width: image.width, height: image.height, areaWidth: area.width, areaHeight: area.height };
    });
    expect(dimensions.width).toBeLessThanOrEqual(dimensions.areaWidth);
    expect(dimensions.height).toBeLessThanOrEqual(dimensions.areaHeight);
    if (size === 32) expect(dimensions.width).toBeCloseTo(32, 4);
    else expect(dimensions.width).toBeLessThan(size);
    await expect(drawer.getByRole("button", { name: "Copy contents" })).toHaveCount(0);
    const link = drawer.getByRole("link", { name: "Open image in new tab" });
    await expect(link).toBeInViewport();
    const popupPromise = page.waitForEvent("popup");
    await link.click();
    const popup = await popupPromise;
    await expect(popup.locator("img")).toBeVisible();
    await popup.close();
    const imageUrl = await image.getAttribute("src");
    if (size === 2400) await page.screenshot({ path: testInfo.outputPath("artifact-preview-image.png") });
    await drawer.getByRole("button", { name: "Close preview" }).click();
    await expect(drawer).toHaveCount(0);
    expect(await page.evaluate(async (url) => { try { await fetch(url!); return false; } catch { return true; } }, imageUrl)).toBe(true);
    await remove(page, name);
  }
});

test("artifact preview handles empty text, clipboard failures, failed and cancelled loads, and unsupported files", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const name = `preview-empty-${testInfo.project.name}.txt`;
  const row = await upload(page, name, "");
  const view = row.getByRole("button", { name: `View ${name}`, exact: true });
  await view.click();
  const drawer = page.getByRole("dialog", { name, exact: true });
  await expect(drawer.getByRole("textbox")).toHaveValue("");
  expect(await drawer.evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error("Denied"); }; });
  await drawer.getByRole("button", { name: "Copy contents" }).click();
  await expect(drawer.getByRole("status")).toContainText("Unable to copy");
  await page.keyboard.press("Escape");
  const path = await row.getByRole("link", { name: `Download ${name}` }).getAttribute("href");
  await page.route(`**${path}`, (route) => route.fulfill({ status: 404, json: { detail: "Not found" } }));
  await view.click();
  await expect(drawer.getByRole("alert")).toContainText("Unable to preview");
  await expect(drawer.getByRole("button", { name: "Copy contents" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.unroute(`**${path}`);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**${path}`, async (route) => { await gate; await route.fulfill({ body: "Late response" }); });
  const started = page.waitForRequest(`**${path}`);
  await view.click();
  await started;
  await expect(drawer.getByRole("status")).toHaveText("Loading preview…");
  await page.keyboard.press("Escape");
  release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(drawer).toHaveCount(0);
  await remove(page, name);
  await page.getByLabel("Show deleted").check();
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: `View ${name}`, exact: true })).toHaveCount(0);
  const pdfName = `preview-unsupported-${testInfo.project.name}.pdf`;
  const pdf = await upload(page, pdfName, "%PDF-1.4\nSynthetic PDF fixture", "application/pdf");
  await expect(pdf.getByRole("button", { name: `View ${pdfName}`, exact: true })).toHaveCount(0);
  await expect(pdf.getByRole("link", { name: `Download ${pdfName}` })).toBeVisible();
  await remove(page, pdfName);
});
