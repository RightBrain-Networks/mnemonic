import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { requireDisposableE2EComposeProject } from "./database";
import { reportForFixture } from "./job-report-fixture";

const execFileAsync = promisify(execFile);
const transcriptRoot = "/var/lib/mnemonic/artifacts/transcripts";

async function fixture(api: APIRequestContext, client = "claude-code") {
  const runId = crypto.randomUUID();
  const projectResponse = await api.post("/api/v1/projects", { data: { name: `Transcript library ${runId.slice(0, 8)}` } });
  expect(projectResponse.ok(), await projectResponse.text()).toBe(true);
  const project = await projectResponse.json() as { id: string };
  const workResponse = await api.post(`/api/v1/projects/${project.id}/work-items`, { data: { title: "Index primary and subagent sessions", summary: "Synthetic transcript acceptance fixture.", priority: 4, initial_checkpoint: { prompt: "Exercise transcript indexing.", source_client: "claude-code", source_session_id: runId } } });
  expect(workResponse.ok(), await workResponse.text()).toBe(true);
  const { work_item: work } = await workResponse.json() as { work_item: { id: string; version: number } };
  const folder = `${transcriptRoot}/${runId}`;
  const primary = `${folder}/${runId}.jsonl`;
  const subagent = `${folder}/${runId}/subagents/agent-${runId}.jsonl`;
  const rows = client === "codex" ? [
    { type: "session_meta", payload: { id: runId, source: "cli" } },
    { type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "Investigate the magenta otter indexing fixture." }] } },
    { type: "response_item", payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: "The magenta otter result is ready. <script>window.transcriptExecuted = true</script>" }] } }
  ] : [
    { type: "user", sessionId: runId, uuid: crypto.randomUUID(), parentUuid: null, isSidechain: false, message: { role: "user", content: "Investigate the magenta otter indexing fixture." } },
    { type: "assistant", sessionId: runId, uuid: crypto.randomUUID(), parentUuid: null, isSidechain: false, message: { role: "assistant", model: "fixture-model", content: [{ type: "text", text: "The magenta otter result is ready. <script>window.transcriptExecuted = true</script>" }] } }
  ];
  const source = `import json,pathlib,sys\nroot=pathlib.Path(${JSON.stringify(transcriptRoot)})\nroot.mkdir(parents=True,exist_ok=True)\ndata=json.loads(sys.argv[1])\nfor path,rows in data.items():\n pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)\n pathlib.Path(path).write_text(''.join(json.dumps(row)+'\\n' for row in rows))\n`;
  await execFileAsync("docker", ["compose", "-p", requireDisposableE2EComposeProject("Transcript fixture"), "-f", resolve(process.cwd(), "../compose.e2e.yaml"), "exec", "-T", "api", "python", "-c", source, JSON.stringify({ [primary]: rows, [subagent]: rows.map((row) => ({ ...row, isSidechain: true })) })]);
  return { project, work, runId, primary, subagent, folder };
}

async function apiContext() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
}

for (const client of ["claude-code", "codex"]) {
test(`${client} transcripts index after closeout and support search, metadata, safe preview, download and rebuild`, async ({ page }, testInfo) => {
  test.setTimeout(120000);
  if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1280, height: 1000 });
  const api = await apiContext();
  try {
    const { project, work, runId, primary, subagent } = await fixture(api, client);
    const workPath = `/api/v1/projects/${project.id}/work-items/${work.id}`;
    const claim = await api.post(`${workPath}/claim`, { data: { holder_client: "claude-code", holder_session_id: runId, claim_request_id: crypto.randomUUID(), session_transcript: { client, path: primary } } });
    expect(claim.ok(), await claim.text()).toBe(true);
    const lease = await claim.json() as { lease_token: string };
    const collection = `/api/v1/projects/${project.id}/transcripts`;
    const waiting = await api.get(collection);
    expect(await waiting.json()).toMatchObject({ total: 1, items: [{ status: "waiting", kind: "primary" }] });
    const completed = await api.post(`${workPath}/complete`, { data: { expected_version: work.version, lease_token: lease.lease_token, client_operation_id: crypto.randomUUID(), checkpoint: { prompt: "Verified transcript fixtures.", source_client: "claude-code", source_session_id: runId }, job_completion_report: await reportForFixture(api, project.id), subagent_transcripts: [{ client, path: subagent }] } });
    expect(completed.ok(), await completed.text()).toBe(true);
    await expect.poll(async () => {
      const response = await api.get(collection);
      return (await response.json() as { items: { status: string }[] }).items.map((item) => item.status).sort();
    }, { timeout: 60000 }).toEqual(["ready", "ready"]);
    await page.goto(`/transcripts?project=${project.id}`);
    await expect(page.getByRole("region", { name: "Transcript library", exact: true })).toBeVisible();
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    await expect(page.locator(".artifact-type").first()).toContainText(client === "codex" ? "OpenAI Codex" : "Claude Code");
    await expect(page.getByRole("switch", { name: "Include contents" })).not.toBeChecked();
    await page.keyboard.press("/");
    const search = page.getByRole("searchbox", { name: "Search transcript metadata and content" });
    await expect(search).toBeFocused();
    await search.fill("magenta otter");
    await search.press("Enter");
    await expect(page.getByText("No matching transcripts.", { exact: true })).toBeVisible();
    await page.getByRole("switch", { name: "Include contents" }).check();
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    await expect(page.locator(".artifact-search-excerpt").first()).toContainText("magenta otter");
    const filename = `${runId}.jsonl`;
    const name = page.getByRole("button", { name: filename, exact: true });
    await name.click();
    const details = page.getByRole("dialog", { name: filename, exact: true });
    await expect(details).toContainText("Indexing started");
    await expect(details).toContainText("Indexing completed");
    await expect(details).toContainText(primary);
    await expect(details.getByRole("link", { name: work.id })).toHaveAttribute("href", `/?work=${work.id}`);
    await expect(details.getByRole("button", { name: "Close details" })).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath("transcript-details.png"), animations: "disabled" });
    await testInfo.attach("Transcript indexing metadata", { path: testInfo.outputPath("transcript-details.png"), contentType: "image/png" });
    await page.keyboard.press("Escape");
    await expect(name).toBeFocused();
    await page.getByRole("button", { name: `View ${filename}`, exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Transcript text" })).toHaveValue(/magenta otter/);
    expect(await page.evaluate(() => (window as Window & { transcriptExecuted?: boolean }).transcriptExecuted)).toBeUndefined();
    await page.getByRole("button", { name: "Close preview" }).click();
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("link", { name: `Download ${filename}`, exact: true }).click();
    expect((await downloadEvent).suggestedFilename()).toMatch(/\.txt$/);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator(".page-content").evaluate((element) => element.scrollTo(0, 0));
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath("transcripts-library.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Transcript search library", { path: testInfo.outputPath("transcripts-library.png"), contentType: "image/png" });
    await page.getByRole("link", { name: "Index settings", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    await expect(settings).toContainText(transcriptRoot);
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).uncheck();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByText("Transcript settings saved.", { exact: true })).toBeVisible();
    await expect(settings.getByRole("button", { name: "Rebuild index", exact: true })).toBeDisabled();
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).check();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByRole("button", { name: "Rebuild index", exact: true })).toBeEnabled();
    await settings.getByRole("button", { name: "Rebuild index", exact: true }).click();
    await expect(settings.getByText(/2 transcripts queued for rebuilding/)).toBeVisible();
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    await page.screenshot({ path: testInfo.outputPath("transcript-settings.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Transcript settings and rebuild", { path: testInfo.outputPath("transcript-settings.png"), contentType: "image/png" });
  } finally { await api.dispose(); }
});
}

test("transcript rebuild retries preserve the request and block navigation after an uncertain response", async ({ page }) => {
  const api = await apiContext();
  try {
    const result = await api.post("/api/v1/projects", { data: { name: `Transcript retry ${crypto.randomUUID()}` } });
    expect(result.ok(), await result.text()).toBe(true);
    const project = await result.json() as { id: string };
    await page.goto(`/transcripts?project=${project.id}`);
    await page.getByRole("link", { name: "Index settings", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    const requests: string[] = [];
    await page.route(`**/api/transcripts/projects/${project.id}/transcripts/rebuild`, async (route) => {
      requests.push(route.request().postData()!);
      if (requests.length === 2) {
        const rejected = await route.fetch({ postData: JSON.stringify({ client_operation_id: "invalid" }) });
        await route.fulfill({ response: rejected });
        return;
      }
      const response = await route.fetch();
      if (requests.length === 1) await route.abort("failed");
      else await route.fulfill({ response });
    });
    await settings.getByRole("button", { name: "Rebuild index", exact: true }).click();
    const retry = settings.getByRole("button", { name: "Retry pending rebuild" });
    await expect(retry).toBeEnabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("link", { name: "Work library", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/workspace$/);
    await retry.click();
    await expect(settings.getByText("Invalid transcript settings request.", { exact: true })).toBeVisible();
    await expect(retry).toBeEnabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await retry.click();
    await expect(settings.getByText(/0 transcripts queued for rebuilding/)).toBeVisible();
    expect(requests).toHaveLength(3);
    expect(requests[1]).toBe(requests[0]);
    expect(requests[2]).toBe(requests[0]);
    await expect(page.locator("#project-select")).toBeEnabled();
  } finally { await api.dispose(); }
});
test("transcript rebuild definitive proxy rejection releases navigation without starting a retry", async ({ page }) => {
  const api = await apiContext();
  try {
    const result = await api.post("/api/v1/projects", { data: { name: `Transcript rejection ${crypto.randomUUID()}` } });
    expect(result.ok(), await result.text()).toBe(true);
    const project = await result.json() as { id: string };
    await page.goto(`/transcripts?project=${project.id}`);
    await page.getByRole("link", { name: "Index settings", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    await page.route(`**/api/transcripts/projects/${project.id}/transcripts/rebuild`, async (route) => {
      // Send an invalid request through the real proxy; it cannot reach the API.
      const rejected = await route.fetch({ postData: JSON.stringify({ client_operation_id: "invalid" }) });
      expect(rejected.status()).toBe(400);
      await route.fulfill({ response: rejected });
    });
    await settings.getByRole("button", { name: "Rebuild index", exact: true }).click();
    await expect(settings.getByText("Invalid transcript settings request.", { exact: true })).toBeVisible();
    await expect(settings.getByRole("button", { name: "Retry pending rebuild" })).toHaveCount(0);
    await expect(page.locator("#project-select")).toBeEnabled();
    await page.getByRole("link", { name: "Work library", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
  } finally { await api.dispose(); }
});


test("workspace imports existing transcripts recursively and deduplicates active enrolled sources", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const api = await apiContext();
  try {
    const { project, work, runId, primary, subagent, folder } = await fixture(api);
    const claim = await api.post(`/api/v1/projects/${project.id}/work-items/${work.id}/claim`, { data: { holder_client: "claude-code", holder_session_id: runId, claim_request_id: crypto.randomUUID(), session_transcript: { client: "claude-code", path: primary } } });
    expect(claim.ok(), await claim.text()).toBe(true);
    await page.goto(`/transcripts?project=${project.id}`);
    await page.getByRole("link", { name: "Index settings", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).uncheck();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByText("Transcript settings saved.", { exact: true })).toBeVisible();
    await settings.getByRole("textbox", { name: "Transcript folder" }).fill(folder);
    await settings.getByRole("button", { name: "Import transcripts", exact: true }).click();
    await expect(settings.getByRole("status")).toContainText("1 transcript imported; 1 already registered.");
    await settings.getByRole("button", { name: "Import transcripts", exact: true }).click();
    await expect(settings.getByRole("status")).toContainText("0 transcripts imported; 2 already registered.");
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).check();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByText("Transcript settings saved.", { exact: true })).toBeVisible();
    await settings.getByRole("button", { name: "Import transcripts", exact: true }).click();
    await expect(settings.getByRole("status")).toContainText("0 transcripts imported; 2 already registered.");
    const collection = `/api/v1/projects/${project.id}/transcripts`;
    await expect.poll(async () => {
      const response = await api.get(collection);
      return (await response.json()).items.map((item: { status: string }) => item.status).sort();
    }, { timeout: 60000 }).toEqual(["ready", "waiting"]);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    await settings.locator(".transcript-import").screenshot({ path: testInfo.outputPath("transcript-import-settings.png"), animations: "disabled" });
    await testInfo.attach("Import existing transcripts", { path: testInfo.outputPath("transcript-import-settings.png"), contentType: "image/png" });
    await page.goto(`/transcripts?project=${project.id}`);
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    const filename = subagent.split("/").at(-1)!;
    await page.getByRole("button", { name: filename, exact: true }).click();
    const details = page.getByRole("dialog", { name: filename, exact: true });
    await expect(details).toContainText("Imported without a work item");
    await expect(details.locator('a[href="/?work=null"]')).toHaveCount(0);
  } finally { await api.dispose(); }
});

test("workspace import retains exact retry requests and allows correcting fresh folder failures", async ({ page }) => {
  const api = await apiContext();
  try {
    const { project, folder } = await fixture(api);
    await page.goto(`/transcripts?project=${project.id}`);
    await page.getByRole("link", { name: "Index settings", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    const directory = settings.getByRole("textbox", { name: "Transcript folder" });
    await directory.fill(`${folder}/missing`);
    await settings.getByRole("button", { name: "Import transcripts", exact: true }).click();
    await expect(settings.getByRole("alert")).toContainText("The folder could not be read.");
    await expect(directory).toBeEnabled();
    await expect(page.locator("#project-select")).toBeEnabled();
    await directory.fill(folder);
    const requests: string[] = [];
    await page.route(`**/api/transcripts/projects/${project.id}/transcripts/import`, async (route) => {
      requests.push(route.request().postData()!);
      if (requests.length === 2) {
        await route.fulfill({ status: 422, json: { detail: { code: "transcript_import_scan_failed", message: "Retry scan failed.", context: {} } } });
        return;
      }
      const response = await route.fetch();
      if (requests.length === 1) await route.abort("failed");
      else await route.fulfill({ response });
    });
    await settings.getByRole("button", { name: "Import transcripts", exact: true }).click();
    const retry = settings.getByRole("button", { name: "Retry pending import" });
    await expect(retry).toBeEnabled();
    await expect(directory).toBeDisabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await page.getByRole("link", { name: "Work library", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/workspace$/);
    await retry.click();
    await expect(settings.getByText("Retry scan failed.", { exact: true })).toBeVisible();
    await expect(retry).toBeEnabled();
    await expect(page.locator("#project-select")).toBeDisabled();
    await retry.click();
    await expect(settings.getByRole("status")).toContainText("2 transcripts imported; 0 already registered.");
    expect(requests).toHaveLength(3);
    expect(requests[1]).toBe(requests[0]);
    expect(requests[2]).toBe(requests[0]);
    await expect(page.locator("#project-select")).toBeEnabled();
  } finally { await api.dispose(); }
});
