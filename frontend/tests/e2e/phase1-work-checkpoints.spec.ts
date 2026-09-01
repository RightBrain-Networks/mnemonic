import { readFile } from "node:fs/promises";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("external API writes appear through live browser sync", async ({ page }, testInfo) => {
  const title = `Live sync ${testInfo.project.name} ${state.runId.slice(0, 8)}`;
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("The disposable E2E API is not configured.");

  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.locator(".sync-status")).toHaveText("Live updates");
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();

  const client = await playwrightRequest.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const response = await client.post(
      `/api/v1/projects/${state.projectId}/work-items`,
      {
        data: {
          title,
          summary: "Created outside the dashboard after its initial list loaded.",
          priority: 5,
          initial_checkpoint: {
            prompt: "This item must arrive over the live invalidation connection.",
            source_client: "playwright-api",
            source_session_id: `live-sync-${state.runId}`,
            tags: [],
            source_metadata: {}
          }
        }
      }
    );
    expect(response.ok(), await response.text()).toBe(true);
  } finally {
    await client.dispose();
  }

  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
});

test("one work item groups immutable checkpoints through its full dashboard lifecycle", async ({ page }, testInfo) => {
  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Grouped work ${suffix} ${state.runId.slice(0, 8)}`;
  const initial = `Initial immutable context for ${suffix}.`;
  const progress = `Progress learned by the ${suffix} session.`;
  const replacement = `Replacement current context from ${suffix}.`;
  const completion = `Completion evidence for ${suffix}.`;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Work library." })).toBeVisible();
  await page.locator("#project-select").selectOption(state.projectId);

  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create durable work" });
  await createDialog.getByLabel("Title").fill(title);
  await createDialog.getByLabel("Summary").fill("A single durable objective shared across session checkpoints.");
  await createDialog.getByLabel("Priority").fill("17");
  await createDialog.getByLabel("Initial context checkpoint").fill(initial);
  await createDialog.getByRole("button", { name: "Create work and checkpoint" }).click();

  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("1 checkpoint");
  await card.getByRole("button", { name: title, exact: true }).click();

  let detail = page.getByRole("dialog", { name: "Work context" });
  await expect(detail.locator(".prompt-body")).toHaveText(initial);
  await expect(detail.locator("article.checkpoint")).toHaveCount(1);

  await detail.getByLabel("Checkpoint text").fill(progress);
  await detail.getByRole("button", { name: "Add checkpoint" }).click();
  await expect(detail.locator("article.checkpoint")).toHaveCount(2);

  await detail.getByLabel("Checkpoint kind").selectOption("context");
  await detail.getByLabel("Checkpoint text").fill(replacement);
  await detail.getByRole("button", { name: "Add checkpoint" }).click();
  await expect(detail.locator(".prompt-body")).toHaveText(replacement);
  await expect(detail.locator("article.checkpoint")).toHaveCount(3);
  await expect(detail.locator(".checkpoint textarea, .checkpoint input, .checkpoint button")).toHaveCount(0);
  await expect(detail.locator("article.checkpoint").filter({ hasText: initial })).toHaveCount(1);
  await expect(detail.locator("article.checkpoint").filter({ hasText: progress })).toHaveCount(1);
  await expect(detail.locator("article.checkpoint").filter({ hasText: replacement })).toHaveCount(1);

  await detail.getByRole("button", { name: "Close dialog" }).click();
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("3 checkpoints");

  await page.getByLabel("Search work items").fill(title);
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "Copy recall pointer" }).click();
  const pointer = await page.evaluate(() => navigator.clipboard.readText());
  expect(pointer).toContain("work_item_id");
  expect(pointer).toContain("recall_work");

  await card.getByRole("button", { name: title, exact: true }).click();
  detail = page.getByRole("dialog", { name: "Work context" });
  await detail.getByRole("button", { name: "Edit work item" }).click();
  const editor = page.getByRole("dialog", { name: "Edit work item" });
  await editor.getByLabel("Summary").fill("Updated durable objective; checkpoint history remains unchanged.");
  await editor.getByRole("button", { name: "Save changes" }).click();
  detail = page.getByRole("dialog", { name: "Work context" });
  await expect(detail).toContainText("Updated durable objective; checkpoint history remains unchanged.");

  await detail.getByLabel("Checkpoint text").fill(completion);
  await detail.getByRole("button", { name: "Complete with summary" }).click();
  await expect(detail.locator(".status-badge")).toHaveText(/Done/);
  await expect(detail.locator("article.checkpoint")).toHaveCount(4);
  await detail.getByRole("button", { name: "Close dialog" }).click();

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: `Delete ${title}` }).click();
  const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
  await deleteDialog.getByRole("button", { name: "Delete work item" }).click();
  await expect(card).toHaveCount(0);
});
