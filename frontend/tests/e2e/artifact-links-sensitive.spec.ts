import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

test("dashboard links artifacts in both directions and work items from either surface, with sensitive flags and exact retries", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const token = `${state.runId.slice(0, 8)}-${testInfo.project.name}`;
  const first = `sensitive-design-${token}.txt`;
  const needle = `confidentialneedle${token.replaceAll("-", "")}`;
  const sensitiveContent = `Sensitive design content ${needle}`;
  const second = `related-notes-${token}.txt`;
  const third = `work-reference-${token}.txt`;
  const collection = `/api/artifacts/projects/${state.projectId}/artifacts`;
  const workId = state.historicalCompletion.workItemId;
  const client = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    await page.goto(`/artifacts?project=${state.projectId}`);
    await page.getByText("Upload description, links and sensitivity", { exact: true }).click();
    await page.getByLabel("Mark new uploads as sensitive").check();
    await page.getByLabel("Upload artifact files").setInputFiles({ name: first, mimeType: "text/plain", buffer: Buffer.from(sensitiveContent) });
    const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: first, exact: true }) });
    await expect(row).toContainText("Sensitive");
    await page.getByLabel("Mark new uploads as sensitive").uncheck();
    for (const name of [second, third]) {
      await page.getByLabel("Upload artifact files").setInputFiles({ name, mimeType: "text/plain", buffer: Buffer.from("Linked reference content") });
      await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
    }
    await page.getByRole("button", { name: first, exact: true }).click();
    const details = page.getByRole("region", { name: `Metadata for ${first}` });
    await expect(details).toContainText("explicit human approval before each read or content search");
    await details.getByRole("button", { name: "Remove sensitive flag", exact: true }).click();
    await expect(details.getByRole("button", { name: "Mark as sensitive", exact: true })).toBeEnabled();
    await details.getByRole("button", { name: "Mark as sensitive", exact: true }).click();
    await expect(details.getByRole("button", { name: "Remove sensitive flag", exact: true })).toBeEnabled();

    const attempts: { body: string | null; operation: string | undefined }[] = [];
    await page.route((url) => url.pathname.startsWith(collection), async (route) => {
      const req = route.request();
      if (req.method() !== "PATCH" || !req.postDataJSON().related_artifact_ids) { await route.continue(); return; }
      attempts.push({ body: req.postData(), operation: req.headers()["x-client-operation-id"] });
      const response = await route.fetch();
      if (attempts.length === 1) { await route.abort("failed"); return; }
      await route.fulfill({ response });
    });
    await details.getByText("Link another artifact", { exact: true }).click();
    await details.getByLabel("Find an artifact", { exact: true }).fill(second);
    await details.getByRole("button", { name: `Link artifact ${second}`, exact: true }).click();
    await expect(page.getByRole("button", { name: "Retry pending action", exact: true })).toBeEnabled();
    await expect(details.getByRole("button", { name: "Close details", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: first, exact: true })).toBeVisible();
    await expect(details.getByRole("button", { name: "Retry pending action", exact: true })).toBeEnabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("button", { name: "Retry pending action", exact: true }).click();
    await expect(details.getByRole("button", { name: second, exact: true })).toBeVisible();
    expect(attempts).toHaveLength(2); expect(attempts[0]).toEqual(attempts[1]);
    await details.getByRole("button", { name: second, exact: true }).click();
    const secondDetails = page.getByRole("region", { name: `Metadata for ${second}` });
    await expect(secondDetails.getByRole("button", { name: `${first} · Sensitive`, exact: true })).toBeVisible();
    await secondDetails.getByRole("button", { name: `${first} · Sensitive`, exact: true }).click();
    await details.getByText("Link a work item", { exact: true }).click();
    await details.getByLabel("Find a work item", { exact: true }).fill(state.historicalCompletion.title);
    await details.getByRole("button", { name: `Link work item ${state.historicalCompletion.title}`, exact: true }).click();
    await expect(details.getByRole("link", { name: workId, exact: true })).toBeVisible();
    await details.scrollIntoViewIfNeeded();
    const screenshot = testInfo.outputPath("artifact-links-sensitive.png");
    await details.screenshot({ path: screenshot });
    await testInfo.attach("Artifact links and sensitivity", { path: screenshot, contentType: "image/png" });

    await details.getByRole("button", { name: "Close details" }).click();
    const downloadEvent = page.waitForEvent("download");
    await row.getByRole("link", { name: `Download ${first}`, exact: true }).click();
    expect(await readFile((await (await downloadEvent).path())!, "utf8")).toBe(sensitiveContent);

    await expect.poll(async () => {
      const response = await client.get(`/api/v1/projects/${state.projectId}/artifacts`, { params: { q: first } });
      return (await response.json()).items[0].extraction.status;
    }, { timeout: 60000 }).toBe("ready");
    await page.getByLabel("Include contents").check();
    await page.getByLabel("Search artifact metadata and content").fill(needle);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(row.locator(".artifact-search-excerpt")).toContainText(needle);
    await page.goto(`/?work=${workId}`);
    const workLinks = page.locator(".work-artifact-links");
    await expect(workLinks).toContainText(first);
    const workAttempts: string[] = [];
    await page.route((url) => url.pathname.startsWith(collection), async (route) => {
      if (route.request().method() !== "PATCH") { await route.continue(); return; }
      workAttempts.push(route.request().postData()!);
      const response = await route.fetch();
      if (workAttempts.length === 1) { await route.abort("failed"); return; }
      await route.fulfill({ response });
    });
    await workLinks.getByRole("button", { name: "Link existing artifact", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Link an existing artifact", exact: true });
    await dialog.getByLabel("Find an artifact", { exact: true }).fill(third);
    await dialog.getByRole("button", { name: `Link artifact ${third}`, exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Retry pending link", exact: true })).toBeEnabled();
    await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Retry pending link", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(workAttempts).toHaveLength(2); expect(workAttempts[0]).toBe(workAttempts[1]);
    await expect(workLinks).toContainText(third);
    const workScreenshot = testInfo.outputPath("work-artifact-links.png");
    await page.locator(".detail-header").screenshot({ path: workScreenshot });
    await testInfo.attach("Work artifact links", { path: workScreenshot, contentType: "image/png" });

    const listed = await client.get(`/api/v1/projects/${state.projectId}/artifacts`, { params: { q: token } });
    const artifacts = (await listed.json()).items as { id: string; filename: string; sensitive: boolean; related_artifact_ids: string[]; related_work_item_ids: string[] }[];
    const a = artifacts.find((artifact) => artifact.filename === first)!;
    const b = artifacts.find((artifact) => artifact.filename === second)!;
    const c = artifacts.find((artifact) => artifact.filename === third)!;
    expect(a.sensitive).toBe(true); expect(a.related_artifact_ids).toContain(b.id); expect(b.related_artifact_ids).toContain(a.id);
    expect(a.related_work_item_ids).toContain(workId); expect(c.related_work_item_ids).toContain(workId);
  } finally { await client.dispose(); }
});
