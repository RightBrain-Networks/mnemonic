import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { expect, request, test, type APIRequestContext } from "@playwright/test";
import { requireDisposableE2EComposeProject } from "./database";
import { reportForFixture } from "./job-report-fixture";

const execFileAsync = promisify(execFile);
const transcriptRoot = "/var/lib/mnemonic/artifacts/transcripts";

async function fixture(api: APIRequestContext, client = "claude-code", limitation?: "image" | "unknown") {
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
  const rows: Record<string, unknown>[] = client === "codex" ? [
    { type: "session_meta", timestamp: "2026-02-01T14:00:00Z", payload: { id: runId, source: "cli" } },
    { type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "Investigate the magenta otter indexing fixture." }] } },
    { type: "response_item", timestamp: "2026-02-01T14:30:00Z", payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: "The magenta otter result is ready. <script>window.transcriptExecuted = true</script>" }] } }
  ] : [
    { type: "user", timestamp: "2026-02-01T14:00:00Z", sessionId: runId, uuid: crypto.randomUUID(), parentUuid: null, isSidechain: false, message: { role: "user", content: "Investigate the magenta otter indexing fixture." } },
    { type: "assistant", timestamp: "2026-02-01T14:30:00Z", sessionId: runId, uuid: crypto.randomUUID(), parentUuid: null, isSidechain: false, message: { role: "assistant", model: "fixture-model", content: [{ type: "text", text: "The magenta otter result is ready. <script>window.transcriptExecuted = true</script>" }] } }
  ];
  const context = client === "codex" ? [
    { type: "response_item", payload: { type: "reasoning", summary: [], encrypted_content: "synthetic-opaque-state" } },
    { type: "token_usage_record", payload: { turn_usage: { input_tokens: 7 } } },
    { type: "compacted", payload: { message: "", replacement_history: [{ type: "message", role: "developer", content: [{ type: "input_text", text: "recoveredcontextneedle" }] }] } }
  ] : [
    { type: "worktree-state", state: {} },
    { type: "attachment", attachment: { type: "instructions", files: [{ content: "recoveredcontextneedle", path: "/example/AGENTS.md" }] } }
  ];
  rows.splice(rows.length - 1, 0, ...context);
  if (limitation) rows.push({ role: "user", content: [{ type: limitation === "image" ? "image" : "future-block", data: "synthetic-native-only" }] });
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
    let busySearches = 0;
    await page.route(`**/api/transcripts/projects/${project.id}/transcripts?*`, async (route) => {
      if (busySearches++ < 2) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { code: "transcript_search_busy", message: "Transcript search is busy. Try again shortly.", context: {} } }) });
      } else await route.continue();
    });
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
    await expect(page.getByText("Normalization warnings", { exact: false })).toHaveCount(0);
    await expect(page.getByText("Coverage incomplete", { exact: false })).toHaveCount(0);
    await search.fill("recoveredcontextneedle");
    await search.press("Enter");
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    await expect(page.locator(".artifact-search-excerpt").first()).toContainText("recoveredcontextneedle");
    await search.fill("magenta otter");
    await search.press("Enter");
    await expect(page.locator(".artifact-search-excerpt").first()).toContainText("magenta otter");
    const filename = `${runId}.jsonl`;
    const name = page.getByRole("button", { name: filename, exact: true });
    const contentKind = page.getByRole("combobox", { name: "Conversation content", exact: true });
    await contentKind.selectOption("tool_result");
    await expect(page.getByText("No matching transcripts.", { exact: true })).toBeVisible();
    await contentKind.selectOption("assistant_text");
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    await page.getByRole("button", { name: `Open match in ${filename}`, exact: true }).click();
    const conversation = page.getByRole("dialog", { name: filename, exact: true });
    await expect(conversation.getByRole("region", { name: "Conversation context", exact: true })).toContainText("magenta otter");
    await expect(conversation.getByRole("heading", { name: /Assistant messages.*Search match/ })).toBeVisible();
    await expect(conversation.getByRole("button", { name: "Next context", exact: true })).toBeDisabled();
    if (testInfo.project.name === "chromium-narrow") {
      await expect.poll(async () => (await conversation.boundingBox())?.width).toBe(page.viewportSize()!.width);
    }
    expect(await page.evaluate(() => (window as Window & { transcriptExecuted?: boolean }).transcriptExecuted)).toBeUndefined();
    await page.screenshot({ path: testInfo.outputPath("transcript-conversation.png"), animations: "disabled" });
    await testInfo.attach("Normalized conversation match context", { path: testInfo.outputPath("transcript-conversation.png"), contentType: "image/png" });
    await conversation.getByRole("button", { name: "Close preview", exact: true }).click();
    await contentKind.selectOption("");
    await name.click();
    const details = page.getByRole("dialog", { name: filename, exact: true });
    await expect(details).toContainText("Indexing started");
    await expect(details).toContainText("Index created");
    await expect(details).toContainText("Last updated");
    await expect(details).toContainText("Native session");
    await expect(page.locator(".transcript-table th").filter({ hasText: "Last Updated" })).toHaveCount(1);
    await expect(details).toContainText("Copy status");
    await expect(details).toContainText("Conversation revision");
    await expect(details).toContainText("Conversation blocks");
    await expect(details).toContainText("All supported readable content represented");
    if (client === "codex") await expect(details).toContainText("Provider-encrypted content has no readable text");
    await expect(details).toContainText(primary);
    await expect(details.getByRole("link", { name: work.id })).toHaveAttribute("href", `/?project=${project.id}&work=${work.id}`);
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
    await page.goto(`/work-items?project=${project.id}&work=${work.id}`);
    const linked = page.getByRole("region", { name: "Linked transcripts", exact: true });
    await expect(linked.getByRole("link", { name: "Transcripts (2)", exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("work-transcript-links.png"), animations: "disabled" });
    await testInfo.attach("Reciprocal work transcript links", { path: testInfo.outputPath("work-transcript-links.png"), contentType: "image/png" });
    await linked.getByRole("link", { name: filename, exact: true }).click();
    await expect(page.getByRole("dialog", { name: filename, exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: filename, exact: true })).toContainText("Native session");
    await page.getByRole("button", { name: "Close details", exact: true }).click();
    await page.getByRole("button", { name: "Transcript indexing", exact: true }).click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    await expect(settings).toContainText(transcriptRoot);
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).uncheck();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByText("Transcript settings saved.", { exact: true })).toBeVisible();
    await expect(settings.getByRole("button", { name: "Rebuild index", exact: true })).toBeDisabled();
    await settings.getByRole("checkbox", { name: "Enable transcript indexing" }).check();
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByRole("button", { name: "Rebuild index", exact: true })).toBeEnabled();
    // Only remove these two synthetic fixtures. Rebuilding must use retained
    // snapshots even after both the primary and subagent sources disappear.
    await execFileAsync("docker", ["compose", "-p", requireDisposableE2EComposeProject("Transcript source removal"), "-f", resolve(process.cwd(), "../compose.e2e.yaml"), "exec", "-T", "api", "python", "-c", "import pathlib,sys; [pathlib.Path(path).unlink() for path in sys.argv[1:]]", primary, subagent]);
    await settings.getByRole("button", { name: "Rebuild index", exact: true }).click();
    await expect(settings.getByText(/2 transcripts queued for rebuilding/)).toBeVisible();
    await expect.poll(async () => {
      const response = await api.get(collection);
      const { items } = await response.json() as { items: { status: string; copy_status: string; index_status: string }[] };
      return items.map((item) => [item.status, item.copy_status, item.index_status]);
    }, { timeout: 60000 }).toEqual([["ready", "ready", "ready"], ["ready", "ready", "ready"]]);
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
    await page.getByRole("button", { name: "Transcript indexing", exact: true }).click();
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
    await page.getByRole("link", { name: "Work items", exact: true }).click();
    await expect(page).toHaveURL(/\/transcripts(?:\?|$)/);
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
    await page.getByRole("button", { name: "Transcript indexing", exact: true }).click();
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
    await page.getByRole("link", { name: "Work items", exact: true }).click();
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
    await page.getByRole("button", { name: "Transcript indexing", exact: true }).click();
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
    await page.getByRole("button", { name: "Transcript indexing", exact: true }).click();
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
    await page.getByRole("link", { name: "Work items", exact: true }).click();
    await expect(page).toHaveURL(/\/transcripts(?:\?|$)/);
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

test("transcript permission warnings name the blocked path and clear after automatic recovery", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const api = await apiContext();
  const { project, work, runId, primary } = await fixture(api);
  const compose = ["compose", "-p", requireDisposableE2EComposeProject("Transcript health fixture"), "-f", resolve(process.cwd(), "../compose.e2e.yaml")];
  const permissions = (mode: string) => execFileAsync("docker", [...compose, "exec", "-T", "api", "python", "-c", "import os,sys; os.chmod(sys.argv[1],int(sys.argv[2],8))", primary, mode]);
  try {
    // The fixture creates the shared source directory after worker startup. Wait for
    // its next observation before testing this file's independent permission failure.
    await expect.poll(async () => {
      const result = await api.get(`/api/v1/projects/${project.id}/transcripts/health`);
      return (await result.json()).warnings;
    }, { timeout: 60000 }).toEqual([]);
    const path = `/api/v1/projects/${project.id}/work-items/${work.id}`;
    const claimed = await api.post(path + "/claim", { data: { holder_client: "claude-code", holder_session_id: runId, claim_request_id: crypto.randomUUID(), session_transcript: { client: "claude_code", path: primary } } });
    expect(claimed.ok(), await claimed.text()).toBe(true);
    const lease = await claimed.json();
    await permissions("0000");
    const released = await api.post(path + "/release-claim", { data: { lease_token: lease.lease_token, actor: { actor_client: "claude-code", actor_session_id: runId }, subagent_transcripts: null } });
    expect(released.ok(), await released.text()).toBe(true);
    await expect.poll(async () => {
      const result = await api.get(`/api/v1/projects/${project.id}/transcripts/health`);
      const value = await result.json();
      return value.warnings?.some((warning: { code: string; path: string }) => warning.code === "transcript_permission_denied" && warning.path === primary);
    }, { timeout: 60000 }).toBe(true);
    await page.goto(`/transcripts?project=${project.id}`);
    const notice = page.getByRole("status", { name: "Transcript access warnings" });
    await expect(notice).toContainText(primary);
    await expect(notice).toContainText("read (r) permission");
    await expect(notice).toContainText("10001");
    expect(await notice.evaluate((element) => element.getBoundingClientRect().top)).toBeLessThan(await page.getByLabel("Search transcript metadata and content").evaluate((element) => element.getBoundingClientRect().top));
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("transcript-access-warning.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Transcript access warning", { path: testInfo.outputPath("transcript-access-warning.png"), contentType: "image/png" });
    await permissions("0600");
    // Advance only this synthetic project's retry deadline; never rebuild or alter its source.
    await execFileAsync("docker", [...compose, "exec", "-T", "api", "python", "-c",
      "import sys; from sqlalchemy import text; from mnemonic_api.config import Settings; from mnemonic_api.database import build_engine; engine=build_engine(Settings()); c=engine.connect(); c.execute(text('UPDATE transcripts SET copy_next_attempt_at=clock_timestamp() WHERE work_item_id=:work'),{'work':sys.argv[1]}); c.commit(); c.close()", work.id]);
    await expect.poll(async () => {
      const result = await api.get(`/api/v1/projects/${project.id}/transcripts`);
      return (await result.json()).items[0]?.copy_status;
    }, { timeout: 60000 }).toBe("ready");
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(notice).not.toBeVisible();
  } finally { await permissions("0600"); await api.dispose(); }
});

test("a transcript moved into a worktree after claim is copied after release", async ({ page }) => {
  test.setTimeout(120000);
  const api = await apiContext();
  try {
    const { project, work, runId, primary, folder } = await fixture(api);
    const compose = ["compose", "-p", requireDisposableE2EComposeProject("Transcript relocation fixture"), "-f", resolve(process.cwd(), "../compose.e2e.yaml")];
    const workPath = `/api/v1/projects/${project.id}/work-items/${work.id}`;
    const collection = `/api/v1/projects/${project.id}/transcripts`;
    const payload = { holder_client: "claude-code", holder_session_id: runId, claim_request_id: crypto.randomUUID(), session_transcript: { client: "claude_code", path: primary } };
    const claimed = await api.post(workPath + "/claim", { data: payload });
    expect(claimed.ok(), await claimed.text()).toBe(true);
    const lease = await claimed.json();
    const destination = `${folder}/worktree/${runId}.jsonl`;
    await execFileAsync("docker", [...compose, "exec", "-T", "api", "python", "-c",
      "import pathlib,sys; source=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); target.parent.mkdir(); source.rename(target)", primary, destination]);
    const replay = await api.post(workPath + "/claim", { data: payload });
    expect(await replay.json()).toEqual(lease);
    const waiting = await (await api.get(collection)).json();
    expect(waiting.items[0].copy_status).toBe("pending");
    const released = await api.post(workPath + "/release-claim", { data: { lease_token: lease.lease_token, actor: { actor_client: "claude-code", actor_session_id: runId }, subagent_transcripts: null } });
    expect(released.ok(), await released.text()).toBe(true);
    await expect.poll(async () => {
      const result = await api.get(collection + "?detail=full");
      const row = (await result.json()).items[0];
      return [row?.copy_status, row?.status, row?.source_path];
    }, { timeout: 60000 }).toEqual(["ready", "ready", primary]);
    await expect.poll(async () => (await (await api.get(collection + "/health")).json()).warnings, { timeout: 60000 }).toEqual([]);
    await page.goto(`/transcripts?project=${project.id}`);
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(1);
    await expect(page.getByRole("status", { name: "Transcript access warnings" })).not.toBeVisible();
    await page.getByRole("searchbox", { name: "Search transcript metadata and content" }).fill("magenta otter");
    await page.getByRole("switch", { name: "Include contents" }).check();
    await expect(page.locator(".transcript-table tbody tr")).toHaveCount(1);
  } finally { await api.dispose(); }
});

test("normalization warnings do not label intact searchable text as truncated", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const api = await apiContext();
  try {
    const { project, work, runId, primary } = await fixture(api);
    const compose = ["compose", "-p", requireDisposableE2EComposeProject("Transcript coverage fixture"), "-f", resolve(process.cwd(), "../compose.e2e.yaml")];
    await execFileAsync("docker", [...compose, "exec", "-T", "api", "python", "-c",
      "import json,sys; f=open(sys.argv[1],'a'); f.write(json.dumps({'type':'future-unknown-record','state':'synthetic unknown state'})+'\\n'); f.close()", primary]);
    const path = `/api/v1/projects/${project.id}/work-items/${work.id}`;
    const claimed = await api.post(path + "/claim", { data: { holder_client: "claude-code", holder_session_id: runId, claim_request_id: crypto.randomUUID(), session_transcript: { client: "claude_code", path: primary } } });
    expect(claimed.ok(), await claimed.text()).toBe(true);
    const lease = await claimed.json();
    const released = await api.post(path + "/release-claim", { data: { lease_token: lease.lease_token, actor: { actor_client: "claude-code", actor_session_id: runId }, subagent_transcripts: null } });
    expect(released.ok(), await released.text()).toBe(true);
    await expect.poll(async () => {
      const result = await api.get(`/api/v1/projects/${project.id}/transcripts?detail=full`);
      const row = (await result.json()).items[0];
      return [row?.status, row?.normalization_incomplete, row?.truncated];
    }, { timeout: 60000 }).toEqual(["ready", true, false]);
    await page.goto(`/transcripts?project=${project.id}`);
    await expect(page.locator(".transcript-status")).toHaveText("Indexed · Unrecognized native message type");
    await page.getByRole("button", { name: `${runId}.jsonl`, exact: true }).click();
    await expect(page.getByText("No length limit reached", { exact: true })).toBeVisible();
    await expect(page.getByText("Complete native copy retained", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("transcript-coverage.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Separate transcript coverage warnings", { path: testInfo.outputPath("transcript-coverage.png"), contentType: "image/png" });
  } finally { await api.dispose(); }
});


for (const limitation of ["image", "unknown"] as const) {
  test(`transcript coverage explains ${limitation} limitations without hiding them`, async ({ page }, testInfo) => {
    test.setTimeout(120000);
    const api = await apiContext();
    try {
      const { project, folder, runId } = await fixture(api, "claude-code", limitation);
      const collection = `/api/v1/projects/${project.id}/transcripts`;
      const imported = await api.post(`${collection}/import`, { data: { directory: folder, client_operation_id: crypto.randomUUID() } });
      expect(imported.ok(), await imported.text()).toBe(true);
      await expect.poll(async () => {
        const response = await api.get(collection);
        return (await response.json()).items.map((item: { status: string }) => item.status);
      }, { timeout: 60000 }).toEqual(["ready", "ready"]);
      await page.goto(`/transcripts?project=${project.id}`);
      const label = limitation === "image" ? "Images not searchable" : "Unrecognized native content";
      await expect(page.locator(".transcript-table tbody tr").first()).toContainText(label);
      await page.getByRole("button", { name: `${runId}.jsonl`, exact: true }).click();
      const details = page.getByRole("dialog", { name: `${runId}.jsonl`, exact: true });
      await expect(details).toContainText(`${label} (1 block)`);
      await expect(details).toContainText("The native copy retains the original records.");
      await page.screenshot({ path: testInfo.outputPath(`transcript-${limitation}-coverage.png`), animations: "disabled" });
      await testInfo.attach("Specific transcript coverage limitation", { path: testInfo.outputPath(`transcript-${limitation}-coverage.png`), contentType: "image/png" });
    } finally { await api.dispose(); }
  });
}

test("transcript controls use MB, collapse, sort, report storage and remember dismissed failures", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  await page.setViewportSize({ width: testInfo.project.name === "chromium-desktop" ? 1280 : 390, height: 1100 });
  const api = await apiContext();
  try {
    const { project, folder } = await fixture(api);
    const collection = `/api/v1/projects/${project.id}/transcripts`;
    await api.post(collection + "/import", { data: { directory: folder, client_operation_id: crypto.randomUUID() } });
    await expect.poll(async () => (await (await api.get(collection)).json()).items.every((row: { status: string }) => row.status === "ready"), { timeout: 60000 }).toBe(true);
    await page.goto(`/settings/workspace?project=${project.id}`);
    await expect(page.getByRole("region", { name: "Transcript indexing", exact: true })).toHaveCount(0);
    await page.goto(`/transcripts?project=${project.id}`);
    const toggle = page.getByRole("button", { name: "Transcript indexing", exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    const settings = page.getByRole("region", { name: "Transcript indexing", exact: true });
    const maximum = settings.getByRole("spinbutton", { name: "Maximum transcript size (MB)" });
    await expect(maximum).toBeVisible();
    await maximum.fill("32");
    await settings.getByRole("button", { name: "Save transcript settings" }).click();
    await expect(settings.getByText("Transcript settings saved.", { exact: true })).toBeVisible();
    expect((await (await api.get(`/api/v1/projects/${project.id}/transcript-settings`)).json()).max_file_size_bytes).toBe(33554432);
    await toggle.click();
    await expect(maximum).not.toBeVisible();
    for (const name of ["Name", "Size", "Session", "Indexing", "Last Updated"]) {
      const header = page.getByRole("columnheader").filter({ has: page.getByRole("button", { name: new RegExp(`^${name}`) }) });
      await header.getByRole("button").click();
      await expect(header).toHaveAttribute("aria-sort", "ascending");
      await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
      await header.getByRole("button").click();
      await expect(header).toHaveAttribute("aria-sort", "descending");
      await expect(page.locator(".transcript-table tbody tr")).toHaveCount(2);
    }
    const storage = page.getByRole("region", { name: "Transcript storage usage" });
    await expect(storage).toContainText("Retained transcripts");
    await expect(storage).toContainText("Search index");
    await expect(storage).toContainText("free on volume");
    await page.route(`**/api/transcripts/projects/${project.id}/transcripts/health`, async (route) => {
      const response = await route.fetch();
      const health = await response.json();
      const warning = { code: "transcript_source_missing", service: "worker", path: "/approved/deleted-session.jsonl", message: "Source missing", action: "Restore this exact native source.", affected: 1, retry_at: null, uid: 10001, gid: 10001, owner_uid: null, owner_gid: null, mode: null };
      await route.fulfill({ response, json: { ...health, warnings: [warning], warnings_omitted: 0, affected_transcripts: 1 } });
    });
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.getByRole("button", { name: "Dismiss source missing notification" }).click();
    await expect(page.getByText("Transcript access needs attention", { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: "Show dismissed notifications" })).toBeVisible();
    await expect(page.getByText("Transcript access needs attention", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Show dismissed notifications" }).click();
    await expect(page.getByText("Transcript access needs attention", { exact: true })).toBeVisible();
    expect((await (await api.get(collection)).json()).total).toBe(2);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await toggle.click();
    await page.screenshot({ path: testInfo.outputPath("transcript-controls-storage.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Compact transcript controls and storage", { path: testInfo.outputPath("transcript-controls-storage.png"), contentType: "image/png" });
    await toggle.click();
    await page.getByRole("button", { name: "Dismiss source missing notification" }).click();
    await page.screenshot({ path: testInfo.outputPath("transcript-library-storage.png"), fullPage: true, animations: "disabled" });
    await testInfo.attach("Collapsed transcript controls and sortable library", { path: testInfo.outputPath("transcript-library-storage.png"), contentType: "image/png" });
  } finally { await api.dispose(); }
});
