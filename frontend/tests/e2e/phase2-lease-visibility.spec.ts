import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

const execFileAsync = promisify(execFile);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

async function expireLease(projectId: string, workItemId: string) {
  if (!UUID.test(projectId)) throw new Error("Refusing to use a malformed project ID.");
  if (!UUID.test(workItemId)) throw new Error("Refusing to expire a malformed work-item ID.");
  const composeProject = process.env.MNEMONIC_E2E_COMPOSE_PROJECT;
  if (!composeProject?.startsWith("mnemonic-e2e-")) {
    throw new Error("Lease expiry requires the disposable E2E Compose stack.");
  }
  const { stdout } = await execFileAsync("docker", [
    "compose",
    "-p",
    composeProject,
    "-f",
    "../compose.e2e.yaml",
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    "mnemonic_e2e",
    "-d",
    "mnemonic_e2e",
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    "UPDATE work_leases AS lease " +
      "SET acquired_at = clock_timestamp() - interval '3 seconds', " +
      "renewed_at = clock_timestamp() - interval '2 seconds', " +
      "expires_at = clock_timestamp() - interval '1 second' " +
      "FROM work_items AS work " +
      `WHERE lease.work_item_id = '${workItemId}'::uuid ` +
      "AND work.id = lease.work_item_id " +
      `AND work.project_id = '${projectId}'::uuid;`
  ]);
  expect(stdout).toContain("UPDATE 1");
}

test("an active lease is visible without exposing its capability and refreshes at expiry", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Leased work ${suffix} ${state.runId.slice(0, 8)}`;
  const holderSession = `lease-e2e-${suffix}-${crypto.randomUUID()}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workItemId = "";
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Verify human-safe active-session visibility and expiry refresh.",
        status: "pending",
        priority: 23,
        initial_checkpoint: {
          prompt: "This checkpoint must remain separate from the temporary lease.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: holderSession,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-2", "lease-visibility"],
          source_metadata: {}
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create lease fixture (${created.status()}): ${await created.text()}`);
    workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${workItemId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: holderSession,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim lease fixture (${claimed.status()}): ${await claimed.text()}`);
  } finally {
    await client.dispose();
  }

  await page.clock.install();
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByRole("button", { name: "Active", exact: true }).click();
  await page.getByLabel("Search work items").fill(title);

  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText(/Active/);
  await expect(card.getByRole("button", { name: `Defer ${title}` })).toBeDisabled();
  await expect(card.getByLabel("Active work lease")).toContainText("Active session");
  await expect(card.getByLabel("Active work lease")).toContainText("Claude Code");
  await expect(card.getByLabel("Active work lease")).toContainText(holderSession);
  await expect(card.getByLabel("Active work lease")).toContainText("Lease acquired");
  await expect(card.getByLabel("Active work lease")).toContainText("Renewed");
  await expect(card.getByLabel("Active work lease")).toContainText("Expires");

  const browserListPayload = await page.evaluate(async ({ projectId, title }) => {
    const query = new URLSearchParams({ q: title, status: "pending", view: "full", limit: "20", offset: "0" });
    return (await fetch(`/api/mnemonic/projects/${projectId}/work-items?${query}`)).text();
  }, { projectId: state.projectId, title });
  expect(browserListPayload).not.toContain("lease_token");
  await expect(page.getByRole("button", { name: /^(?:claim(?: work)?|force release(?: work)?)$/i })).toHaveCount(0);

  await expireLease(state.projectId, workItemId);
  await page.clock.fastForward(61 * 1000);
  await expect(card).toHaveCount(0);

  await page.getByRole("button", { name: "Dropped", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText(/Dropped/);
  await expect(card.getByLabel("Active work lease")).toHaveCount(0);
});

test("a human can defer a pending card and return it to the queue", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Human deferred work ${suffix} ${state.runId.slice(0, 8)}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let workItemId = "";
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Verify the dashboard-only deferral workflow.",
        status: "pending",
        priority: 19,
        initial_checkpoint: {
          prompt: "A human will temporarily hold this work out of the queue.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: `defer-e2e-${suffix}`,
          tags: ["deferred", "dashboard"]
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create deferral fixture (${created.status()}): ${await created.text()}`);
    workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;
  } finally {
    await client.dispose();
  }

  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await page.getByLabel("Search work items").fill(title);

  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Pending");
  await card.getByRole("button", { name: `Defer ${title}` }).click();
  await expect(page.locator(".toast")).toContainText("Deferred and held out of the work queue");
  await expect(card).toHaveCount(0);

  await page.getByRole("button", { name: "Deferred", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Deferred");
  await card.getByRole("button", { name: `Move ${title} to Pending` }).click();
  await expect(page.locator(".toast")).toContainText("Pending and available in the work queue");
  await expect(card).toHaveCount(0);

  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await expect(card).toHaveCount(1);
  await expect(card.locator(".status-badge")).toHaveText("Pending");

  const verification = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const response = await verification.get(
      `/api/v1/projects/${state.projectId}/work-items/${workItemId}`
    );
    expect(response.ok()).toBeTruthy();
    expect((await response.json() as { status: string }).status).toBe("pending");
  } finally {
    await verification.dispose();
  }
});

test("a claim committed before an identity edit is reconciled in the visible detail", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("Run this test through the disposable E2E stack.");

  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Edit claim race ${suffix} ${state.runId.slice(0, 8)}`;
  const updatedSummary = `Identity edit reconciled the active lease for ${suffix}.`;
  const holderSession = `edit-claim-${suffix}-${crypto.randomUUID()}`;
  const client = await request.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const created = await client.post(`/api/v1/projects/${state.projectId}/work-items`, {
      data: {
        title,
        summary: "Ready work that will be claimed while its identity editor is open.",
        status: "pending",
        priority: 24,
        initial_checkpoint: {
          prompt: "Open this ready work for editing before the direct API claim.",
          source_client: "dashboard-e2e-seeder",
          source_session_id: holderSession,
          source_model: null,
          source_session_url: null,
          repository_branch: null,
          verified_against: null,
          tags: ["phase-2", "edit-claim-race"],
          source_metadata: {}
        }
      }
    });
    if (!created.ok()) throw new Error(`Could not create edit-race fixture (${created.status()}): ${await created.text()}`);
    const workItemId = (await created.json() as { work_item: { id: string } }).work_item.id;

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(card).toHaveCount(1);
    await card.getByRole("button", { name: `Edit ${title}` }).click();

    const editor = page.getByRole("dialog", { name: "Edit work item" });
    await expect(editor.locator(".status-badge")).toHaveText("Pending");
    await editor.getByLabel("Summary").fill(updatedSummary);

    const claimed = await client.post(`/api/v1/projects/${state.projectId}/work-items/${workItemId}/claim`, {
      data: {
        holder_client: "claude-code",
        holder_session_id: holderSession,
        claim_request_id: crypto.randomUUID()
      }
    });
    if (!claimed.ok()) throw new Error(`Could not claim edit-race fixture (${claimed.status()}): ${await claimed.text()}`);

    await editor.getByRole("button", { name: "Save changes" }).click();
    const detail = page.getByRole("dialog", { name: "Work context" });
    await expect(detail).toContainText(updatedSummary);
    await expect(detail.locator(".status-badge")).toHaveText("Active");
    await expect(detail.getByLabel("Active work lease")).toContainText(holderSession);
  } finally {
    await client.dispose();
  }
});
