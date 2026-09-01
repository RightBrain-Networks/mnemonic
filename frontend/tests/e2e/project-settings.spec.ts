import { readFile } from "node:fs/promises";
import { expect, request as playwrightRequest, test, type APIRequestContext } from "@playwright/test";
import {
  DEFAULT_RECALL_POINTER_TEMPLATE,
  RECALL_POINTER_MACROS
} from "../../lib/work-recall-pointer";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type SeededWork = {
  id: string;
  version: number;
};

async function clearRecallPointerTemplate(client: APIRequestContext): Promise<void> {
  const response = await client.patch(`/api/v1/projects/${state.projectId}/settings`, {
    data: { recall_pointer_template: null }
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function removeSeededWork(
  client: APIRequestContext,
  work: SeededWork | null
): Promise<void> {
  if (!work) return;
  const response = await client.post(
    `/api/v1/projects/${state.projectId}/work-items/${work.id}/delete`,
    { data: { expected_version: work.version } }
  );
  expect(response.ok(), await response.text()).toBe(true);
}

test("project recall pointer settings drive card and detail clipboard content", async ({ page }, testInfo) => {
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("The disposable E2E API is not configured.");

  const suffix = `${testInfo.project.name}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `Configurable recall pointer ${suffix}`;
  const summary = `Acceptance context for ${suffix}.`;
  const customTemplate = [
    "Project $PROJECT_ID",
    "Work $WORK_ITEM_ID: $WORK_ITEM_TITLE",
    "Summary: $WORK_ITEM_SUMMARY"
  ].join("\n");
  let seededWork: SeededWork | null = null;

  const client = await playwrightRequest.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });

  try {
    await clearRecallPointerTemplate(client);
    const createResponse = await client.post(
      `/api/v1/projects/${state.projectId}/work-items`,
      {
        data: {
          title,
          summary,
          priority: 23,
          initial_checkpoint: {
            prompt: "Exercise project-scoped recall pointer expansion in the dashboard.",
            source_client: "playwright-api",
            source_session_id: `project-settings-${suffix}`,
            tags: ["project-settings"],
            source_metadata: {}
          }
        }
      }
    );
    expect(createResponse.ok(), await createResponse.text()).toBe(true);
    const created = await createResponse.json() as { work_item: SeededWork };
    seededWork = created.work_item;

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    const workspaceNavigation = page.getByRole("navigation", { name: "Workspace navigation" });
    await expect(workspaceNavigation.getByRole("link", { name: "Work library" })).toBeVisible();
    await workspaceNavigation.getByRole("link", { name: "Project settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { name: /^Project settings\.?$/ })).toBeVisible();
    await page.locator("#project-select").selectOption(state.projectId);

    const content = page.getByLabel("Recall pointer content");
    await expect(content).toHaveValue(DEFAULT_RECALL_POINTER_TEMPLATE);
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Clear", exact: true })).toBeVisible();
    for (const { macro } of RECALL_POINTER_MACROS) {
      await expect(page.getByText(macro, { exact: true })).toBeVisible();
    }

    await content.fill(customTemplate);
    const saveResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === "PATCH"
        && url.pathname === `/api/mnemonic/projects/${state.projectId}/settings`;
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    expect((await saveResponse).ok()).toBe(true);

    await workspaceNavigation.getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL(/\/$/);
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(card).toHaveCount(1);

    const expectedCustomPointer = [
      `Project ${state.projectId}`,
      `Work ${seededWork.id}: ${title}`,
      `Summary: ${summary}`
    ].join("\n");
    await card.getByRole("button", { name: "Copy recall pointer" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(expectedCustomPointer);

    await card.getByRole("button", { name: title, exact: true }).click();
    const detail = page.getByRole("dialog", { name: "Work context" });
    await detail.getByRole("button", { name: "Copy recall pointer" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(expectedCustomPointer);
    await detail.getByRole("button", { name: "Close dialog" }).click();

    await workspaceNavigation.getByRole("link", { name: "Project settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.getByLabel("Recall pointer content")).toHaveValue(customTemplate);
    const clearResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === "PATCH"
        && url.pathname === `/api/mnemonic/projects/${state.projectId}/settings`;
    });
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    expect((await clearResponse).ok()).toBe(true);
    await expect(page.getByLabel("Recall pointer content"))
      .toHaveValue(DEFAULT_RECALL_POINTER_TEMPLATE);

    await page.getByRole("navigation", { name: "Workspace navigation" })
      .getByRole("link", { name: "Work library" })
      .click();
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const restoredCard = page.locator("article.work-item-card").filter({ hasText: title });
    await expect(restoredCard).toHaveCount(1);
    await restoredCard.getByRole("button", { name: "Copy recall pointer" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
      `Recall the Mnemonic work item "${title}" (project_id ${state.projectId}, work_item_id ${seededWork.id}) using recall_work, then summarise its current context and wait for my direction.`
    );
  } finally {
    await clearRecallPointerTemplate(client);
    await removeSeededWork(client, seededWork);
    await client.dispose();
  }
});
