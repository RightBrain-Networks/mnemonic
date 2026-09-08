import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

test("artifact directory supports upload, sorting, downloads, atomic replacement, clipboard, drop and deletion", async ({ page }, testInfo) => {
  const token = `${state.runId.slice(0, 8)}-${testInfo.project.name}`;
  const filename = `report-${token}.txt`;
  const pasted = `clipboard-${token}.txt`;
  const dropped = `drop-${token}.txt`;
  await page.goto("/artifacts");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.getByRole("heading", { name: "Artifacts." })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await expect(navigation.getByRole("link", { name: "Artifacts" })).toHaveAttribute("aria-current", "page");
  const names = await navigation.locator("a").allTextContents();
  expect(names.findIndex((name) => name.includes("Artifacts"))).toBe(names.findIndex((name) => name.includes("Needs Attention")) + 1);

  await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("Original artifact bytes") });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: filename, exact: true }) });
  await expect(row).toBeVisible();
  await expect(row).toContainText("r1");
  const downloaded = page.waitForEvent("download");
  await row.getByRole("link", { name: `Download ${filename}` }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe(filename);
  expect(await readFile((await download.path())!, "utf8")).toBe("Original artifact bytes");

  await page.getByRole("button", { name: "Modified", exact: false }).click();
  await expect(page.getByRole("columnheader", { name: "Modified" })).toHaveAttribute("aria-sort", "ascending");
  await page.getByRole("button", { name: "Modified", exact: false }).click();
  await expect(page.getByRole("columnheader", { name: "Modified" })).toHaveAttribute("aria-sort", "descending");

  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: `Replace ${filename}` }).click();
  await page.getByLabel("Replace artifact content").setInputFiles({ name: "replacement.txt", mimeType: "text/plain", buffer: Buffer.from("Replacement artifact bytes") });
  await expect(row).toContainText("r2");
  const replacementDownload = page.waitForEvent("download");
  await row.getByRole("link", { name: `Download ${filename}` }).click();
  expect(await readFile((await (await replacementDownload).path())!, "utf8")).toBe("Replacement artifact bytes");

  await page.evaluate((name) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["Pasted content"], name, { type: "text/plain" }));
    document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true }));
  }, pasted);
  await expect(page.getByRole("button", { name: pasted, exact: true })).toBeVisible();
  const transfer = await page.evaluateHandle((name) => {
    const value = new DataTransfer(); value.items.add(new File(["Dropped content"], name, { type: "text/plain" })); return value;
  }, dropped);
  await page.getByRole("region", { name: "Artifact library", exact: true }).dispatchEvent("drop", { dataTransfer: transfer });
  await expect(page.getByRole("button", { name: dropped, exact: true })).toBeVisible();

  await page.getByLabel("Search artifact metadata and audit history").fill(token);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole("region", { name: "Artifact library", exact: true }).evaluate((element) => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
  if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1280, height: 1000 });
  await page.locator(".artifact-table-scroll").evaluate((element) => { element.scrollLeft = 0; });
  await page.evaluate(() => { window.scrollTo(0, 0); if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
  await page.screenshot({ path: testInfo.outputPath("artifact-directory.png"), fullPage: true });
  await testInfo.attach("Artifact directory", { path: testInfo.outputPath("artifact-directory.png"), contentType: "image/png" });

  for (const name of [filename, pasted, dropped]) {
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: `Delete ${name}`, exact: true }).click();
    await expect(page.getByRole("button", { name, exact: true })).toBeHidden();
  }
  await page.getByLabel("Show deleted").check();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  await expect(row).toContainText("Deleted");
  await expect(row.getByRole("link", { name: `Download ${filename}` })).toBeHidden();
});

test("an artifact upload with a lost response preserves the file and receipt until an identical retry succeeds", async ({ page }, testInfo) => {
  const filename = `retry-${state.runId.slice(0, 8)}-${testInfo.project.name}.txt`;
  const attempts: { id: string | undefined; metadata: string | undefined; body: string | null }[] = [];
  await page.route(`**/api/artifacts/projects/${state.projectId}/artifacts`, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    const headers = route.request().headers();
    attempts.push({ id: headers["x-client-operation-id"], metadata: headers["x-artifact-metadata"], body: route.request().postData() });
    const response = await route.fetch();
    if (attempts.length === 1) { await route.abort("failed"); return; }
    if (attempts.length === 2) { await route.fulfill({ status: 404, json: { unrelated: "An unrecognized intermediary response" } }); return; }
    await route.fulfill({ response });
  });
  await page.goto("/artifacts");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("Retry me exactly") });
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeVisible();
  await expect(page.locator("#project-select")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Upload files", exact: true })).toBeDisabled();
  await page.getByRole("link", { name: "Work library" }).click();
  await expect(page).toHaveURL(/\/artifacts(?:\?|$)/);
  await page.getByRole("button", { name: "Retry pending action" }).click();
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeEnabled();
  await expect(page.locator("#project-select")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Upload files", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry pending action" }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeHidden();
  expect(attempts).toHaveLength(3);
  expect(attempts[0]).toEqual(attempts[1]);
  expect(attempts[0]).toEqual(attempts[2]);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: `Delete ${filename}`, exact: true }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeHidden();
});

test("artifact work links restore their project after another tab changes it and project switching clears upload provenance", async ({ page }, testInfo) => {
  const token = `${state.runId.slice(0, 8)}-${testInfo.project.name}`;
  const client = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  try {
    const response = await client.post("/api/v1/projects", { data: { name: `Artifact destination ${token}`, slug: `artifact-destination-${token}` } });
    expect(response.status()).toBe(201);
    const destination = await response.json() as { id: string };
    const workId = state.historicalCompletion.workItemId;
    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.goto(`/?work=${workId}`);
    const link = page.getByRole("link", { name: "View or upload linked files" });
    await expect(link).toHaveAttribute("href", `/artifacts?project=${state.projectId}&work=${workId}`);
    const otherTab = await page.context().newPage();
    await otherTab.goto(`/artifacts?project=${destination.id}`);
    await expect(otherTab.locator("#project-select")).toHaveValue(destination.id);
    await expect.poll(() => otherTab.evaluate(() => localStorage.getItem("mnemonic.project"))).toBe(destination.id);
    await otherTab.close();
    await link.click();
    await expect(page.locator("#project-select")).toHaveValue(state.projectId);
    await expect(page.locator(".artifact-filter-note")).toContainText(workId);
    await page.getByText("Upload description and work links", { exact: true }).click();
    await expect(page.getByLabel("Originating work item ID", { exact: true })).toHaveValue(workId);

    await page.locator("#project-select").selectOption(destination.id);
    await expect(page).toHaveURL(new RegExp(`/artifacts\\?project=${destination.id}$`));
    await expect(page.locator(".artifact-filter-note")).toBeHidden();
    await page.getByText("Upload description and work links", { exact: true }).click();
    await expect(page.getByLabel("Originating work item ID", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Related work item IDs", { exact: true })).toHaveValue("");
    const filename = `project-switch-${token}.txt`;
    const uploaded = page.waitForRequest((request) => request.method() === "POST" && request.url().includes(`/projects/${destination.id}/artifacts`));
    await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("Correct project") });
    expect(JSON.parse((await uploaded).headers()["x-artifact-metadata"]!)).not.toHaveProperty("work_item_id");
    await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
    await page.reload();
    await expect(page.locator("#project-select")).toHaveValue(destination.id);
    await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  } finally { await client.dispose(); }
});

test("large escaped Unicode descriptions pass the artifact HTTP header envelope", async ({ page }, testInfo) => {
  const filename = `unicode-${state.runId.slice(0, 8)}-${testInfo.project.name}.txt`;
  const description = "é".repeat(2600);
  await page.goto(`/artifacts?project=${state.projectId}`);
  await expect(page.locator("#project-select")).toHaveValue(state.projectId);
  await page.getByText("Upload description and work links", { exact: true }).click();
  await page.getByLabel("Description", { exact: true }).fill(description);
  const uploaded = page.waitForRequest((request) => request.method() === "POST" && request.url().includes(`/projects/${state.projectId}/artifacts`));
  await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("Unicode metadata") });
  const header = (await uploaded).headers()["x-artifact-metadata"]!;
  expect(header.length).toBeGreaterThan(15_000);
  expect(header.length).toBeLessThanOrEqual(16_384);
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("region", { name: `Metadata for ${filename}` })).toContainText(description);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: `Delete ${filename}`, exact: true }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeHidden();
});

test("disabled artifacts stay visible without listing files or accepting clipboard and drop uploads", async ({ page }, testInfo) => {
  const dataRequests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/api/artifacts/projects/")) dataRequests.push(request.url()); });
  await page.route("**/api/artifacts/status", (route) => route.fulfill({ json: { enabled: false, max_bytes: 0, message: "The artifact library is disabled. Stored files and metadata are preserved." } }));
  await page.goto(`/artifacts?project=${state.projectId}`);
  await expect(page.getByRole("heading", { name: "Artifact library disabled", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Workspace navigation" }).getByRole("link", { name: "Artifacts" })).toBeVisible();
  await expect(page.getByLabel("Artifact library", { exact: true })).toContainText("0 bytes");
  await expect(page.getByLabel("Artifact library", { exact: true })).toContainText("Existing files are preserved");
  await expect(page.getByLabel("Upload artifact files")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Upload files", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Sortable artifact directory" })).toHaveCount(0);
  const untouchedPaste = await page.evaluate(() => {
    const data = new DataTransfer(); data.items.add(new File(["private bytes"], "disabled.txt"));
    const paste = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
    document.dispatchEvent(paste);
    document.querySelector('[aria-label="Artifact library"]')!.dispatchEvent(new DragEvent("drop", { dataTransfer: data, bubbles: true, cancelable: true }));
    return !paste.defaultPrevented;
  });
  expect(untouchedPaste).toBe(true);
  expect(dataRequests).toEqual([]);
  const screenshot = testInfo.outputPath("artifact-library-disabled.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach("Disabled artifact library", { path: screenshot, contentType: "image/png" });
  await page.goto(`/?work=${state.historicalCompletion.workItemId}`);
  await expect(page.locator(".work-artifact-links")).toContainText("Artifact library disabled");
  await expect(page.locator(".work-artifact-links")).not.toContainText("retry loading linked files");
  await expect(page.locator(".work-artifact-links").getByRole("link", { name: "Open artifact library" })).toBeVisible();
  expect(dataRequests).toEqual([]);
});

test("enabled artifacts display the configured per-file limit and reject oversized new selections", async ({ page }) => {
  let mutations = 0;
  page.on("request", (request) => { if (request.method() !== "GET" && request.url().includes("/api/artifacts/projects/")) mutations++; });
  await page.route("**/api/artifacts/status", (route) => route.fulfill({ json: { enabled: true, max_bytes: 8, message: "Up to 8 bytes per file." } }));
  await page.goto(`/artifacts?project=${state.projectId}`);
  await expect(page.locator(".artifact-upload-hint")).toContainText("8 B (8 bytes) per file");
  await page.getByLabel("Upload artifact files").setInputFiles({ name: "over-limit.txt", mimeType: "text/plain", buffer: Buffer.from("123456789") });
  await expect(page.getByLabel("Artifact library", { exact: true }).getByRole("alert")).toContainText("exceeds the 8 B (8 bytes) per-file limit");
  expect(mutations).toBe(0);
});

test("disabling artifacts preserves an uncertain file and UUID until an exact retry after re-enable", async ({ page }, testInfo) => {
  const filename = `disabled-retry-${state.runId.slice(0, 8)}-${testInfo.project.name}.txt`;
  let maximum = 64;
  const attempts: { id: string | undefined; metadata: string | undefined; body: string | null }[] = [];
  await page.route("**/api/artifacts/status", (route) => route.fulfill({ json: { enabled: maximum > 0, max_bytes: maximum, message: maximum ? `Up to ${maximum} bytes per file.` : "The artifact library is disabled. Stored files are preserved." } }));
  await page.route(`**/api/artifacts/projects/${state.projectId}/artifacts`, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    const headers = route.request().headers();
    attempts.push({ id: headers["x-client-operation-id"], metadata: headers["x-artifact-metadata"], body: route.request().postData() });
    const response = await route.fetch();
    if (attempts.length === 1) { await route.abort("failed"); return; }
    await route.fulfill({ response });
  });
  await page.goto(`/artifacts?project=${state.projectId}`);
  await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from("Preserve these bytes") });
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeEnabled();
  maximum = 0;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Artifact library disabled", exact: true })).toBeVisible();
  await expect(page.getByLabel("Artifact library", { exact: true }).getByRole("alert")).toContainText(filename);
  await expect(page.getByLabel("Artifact library", { exact: true }).getByRole("alert")).toContainText(attempts[0].id!);
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeDisabled();
  await expect(page.locator("#project-select")).toBeDisabled();
  expect(attempts).toHaveLength(1);
  maximum = 1; // The preserved request is larger than the new positive selection limit.
  await page.getByRole("button", { name: "Check artifact status", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry pending action" })).toBeEnabled();
  await page.getByRole("button", { name: "Retry pending action" }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry pending action" })).toHaveCount(0);
  expect(attempts).toHaveLength(2); expect(attempts[0]).toEqual(attempts[1]);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: `Delete ${filename}`, exact: true }).click();
  await expect(page.getByRole("button", { name: filename, exact: true })).toBeHidden();
});

test("live artifact status re-enables a page opened with a zero server-rendered limit", async ({ page }, testInfo) => {
  const token = `${state.runId.slice(0, 8)}-${testInfo.project.name}`;
  const filenames = [`reenabled-a-${token}.txt`, `reenabled-b-${token}.txt`];
  let maximum = 0;
  let patchedInitialLimit = false;
  await page.route(`**/artifacts?project=${state.projectId}`, async (route) => {
    const response = await route.fetch();
    const body = await response.text();
    // Simulate the initial RSC prop retained by a tab opened before reconfiguration.
    const patched = body.replace(/(\\?"artifactMaxBytes\\?":)\d+/g, (_match, prefix: string) => {
      patchedInitialLimit = true; return `${prefix}0`;
    });
    const headers = { ...response.headers() };
    delete headers["content-encoding"]; delete headers["content-length"];
    await route.fulfill({ response, headers, body: patched });
  });
  await page.route("**/api/artifacts/status", (route) => route.fulfill({ json: { enabled: maximum > 0, max_bytes: maximum, message: maximum ? "Up to 64 bytes per file." : "The artifact library is disabled. Stored files are preserved." } }));
  await page.goto(`/artifacts?project=${state.projectId}`);
  await expect(page.getByRole("heading", { name: "Artifact library disabled", exact: true })).toBeVisible();
  expect(patchedInitialLimit).toBe(true);
  maximum = 64;
  await page.getByRole("button", { name: "Check artifact status", exact: true }).click();
  await expect(page.getByRole("button", { name: "Upload files", exact: true })).toBeEnabled();
  await expect(page.getByRole("region", { name: "Sortable artifact directory" })).toBeVisible();
  await page.getByLabel("Upload artifact files").setInputFiles(filenames.map((name) => ({ name, mimeType: "text/plain", buffer: Buffer.from("Re-enabled upload") })));
  for (const filename of filenames) await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
  for (const filename of filenames) {
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: `Delete ${filename}`, exact: true }).click();
    await expect(page.getByRole("button", { name: filename, exact: true })).toBeHidden();
  }
});
