import { readFile } from "node:fs/promises";
import { expect, request as playwrightRequest, test, type APIRequestContext } from "@playwright/test";
import {
  DEFAULT_RECALL_POINTER_TEMPLATE,
  RECALL_POINTER_MACROS
} from "../../lib/work-recall-pointer";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, selectWork, workCard } from "./surface";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type SeededWork = {
  id: string;
  version: number;
};

async function clearRecallPointerTemplate(client: APIRequestContext): Promise<void> {
  const current = await client.get(`/api/v1/projects/${state.projectId}/settings`);
  const { revision } = await current.json() as { revision: string };
  const response = await client.patch(`/api/v1/projects/${state.projectId}/settings`, {
    data: { expected_revision: revision, recall_pointer_template: null }
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

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

test("project details can be edited above recall pointer content", async ({ page }) => {
  const updatedName = `Renamed project ${state.runId.slice(0, 8)}`;
  const updatedSlug = `renamed-${state.runId.slice(0, 8)}`;
  const updatedDescription = "Updated from the project settings page.";
  const updatedRepositoryUrl = "https://example.test/mnemonic";
  let patchBody: Record<string, unknown> | null = null;

  await page.route(`**/api/mnemonic/projects/${state.projectId}`, async (route) => {
    if (route.request().method() !== "PATCH") {
      await route.continue();
      return;
    }
    patchBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: state.projectId,
        name: updatedName,
        slug: updatedSlug,
        description: updatedDescription,
        repository_url: updatedRepositoryUrl,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-02T00:00:00Z"
      })
    });
  });

  await page.goto("/settings");
  await page.locator("#project-select").selectOption(state.projectId);

  const cards = page.locator(".settings-card");
  await expect(cards.nth(0).getByRole("heading", { name: "Code reviews", exact: true })).toBeVisible();
  const details = cards.filter({ has: page.getByRole("heading", { name: "Project details", exact: true }) });
  await expect(cards.nth(1).getByRole("heading", { name: "Project details" })).toBeVisible();
  await expect(cards.nth(2).getByRole("heading", {
    name: "Recall pointer content",
    exact: true
  })).toBeVisible();

  const name = page.getByLabel("Project name", { exact: true });
  const slug = page.getByLabel("Project slug", { exact: true });
  const description = page.getByLabel("Description", { exact: false });
  const repositoryUrl = page.getByLabel("Repository URL", { exact: false });
  await expect(name).toHaveValue(state.projectName);
  await expect(slug).toHaveValue(`e2e-${state.runId}`);
  await expect(description).toHaveValue("Disposable historical completion acceptance fixture.");
  await expect(repositoryUrl).toHaveValue("");

  await name.fill(updatedName);
  await slug.fill(updatedSlug);
  await description.fill(updatedDescription);
  await repositoryUrl.fill(updatedRepositoryUrl);
  await details.getByRole("button", { name: "Save project details" }).click();

  await expect.poll(() => patchBody).toEqual({
    name: updatedName,
    slug: updatedSlug,
    description: updatedDescription,
    repository_url: updatedRepositoryUrl
  });
  await expect(page.locator("#project-select option:checked")).toHaveText(updatedName);
  await expect(page.locator(".toast[role=status]")).toContainText(
    `Project details saved for “${updatedName}”.`
  );
  await expect(details.getByRole("button", {
    name: "Save project details"
  })).toBeDisabled();
});

test("a background settings refresh cannot disable or overwrite a save", async ({ page }) => {
  const oldTemplate = "Old recall pointer for $WORK_ITEM_ID";
  const newTemplate = "New recall pointer for $WORK_ITEM_TITLE";
  const settingsURL = `**/api/mnemonic/projects/${state.projectId}/settings`;
  const backgroundStarted = deferred();
  const releaseBackground = deferred();
  const backgroundSettled = deferred();
  const authoritativeRefresh = deferred();
  let storedTemplate: string | null = oldTemplate;
  let holdNextGet = false;
  let patchCount = 0;

  await page.routeWebSocket(/\/api\/mnemonic\/sync$/, () => {});
  await page.route(settingsURL, async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      const responseTemplate = storedTemplate;
      if (holdNextGet) {
        holdNextGet = false;
        backgroundStarted.resolve();
        await releaseBackground.promise;
        try {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              project_id: state.projectId,
              revision: String(patchCount + 1), job_completion_report_prompt: "Write a concise human summary.",
              code_review_required_min_priority: 100, code_review_optional_min_priority: 100, allow_remediation_code_reviews: false,
          recall_pointer_template: responseTemplate
            })
          });
        } catch {
          // The save path aborts this stale request before starting its authoritative refresh.
        } finally {
          backgroundSettled.resolve();
        }
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: state.projectId,
          revision: String(patchCount + 1), job_completion_report_prompt: "Write a concise human summary.",
          code_review_required_min_priority: 100, code_review_optional_min_priority: 100, allow_remediation_code_reviews: false,
          recall_pointer_template: responseTemplate
        })
      });
      if (patchCount > 0) authoritativeRefresh.resolve();
      return;
    }
    if (method === "PATCH") {
      patchCount += 1;
      const body = route.request().postDataJSON() as { recall_pointer_template: string | null };
      storedTemplate = body.recall_pointer_template;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: state.projectId,
          revision: String(patchCount + 1), job_completion_report_prompt: "Write a concise human summary.",
          code_review_required_min_priority: 100, code_review_optional_min_priority: 100, allow_remediation_code_reviews: false,
          recall_pointer_template: storedTemplate
        })
      });
      return;
    }
    await route.abort();
  });

  await page.goto("/settings");
  await page.locator("#project-select").selectOption(state.projectId);
  const content = page.getByLabel("Recall pointer content");
  const save = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "Recall pointer content", exact: true }) }).getByRole("button", { name: "Save", exact: true });
  const clear = page.getByRole("button", { name: "Clear", exact: true });
  await expect(content).toHaveValue(oldTemplate);
  await content.fill(newTemplate);

  holdNextGet = true;
  await page.locator(".page-heading").getByRole("button", { name: "Refresh" }).click();
  await backgroundStarted.promise;
  await expect(content).toBeEnabled();
  await expect(save).toBeEnabled();
  await expect(clear).toBeEnabled();

  await save.click();
  await authoritativeRefresh.promise;
  expect(patchCount).toBe(1);
  releaseBackground.resolve();
  await backgroundSettled.promise;
  await expect(content).toHaveValue(newTemplate);
  await expect.poll(() => patchCount).toBe(1);
});

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
    await expect(page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "Recall pointer content", exact: true }) }).getByRole("button", { name: "Save", exact: true })).toBeVisible();
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
    await page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "Recall pointer content", exact: true }) }).getByRole("button", { name: "Save", exact: true }).click();
    expect((await saveResponse).ok()).toBe(true);

    await workspaceNavigation.getByRole("link", { name: "Work library" }).click();
    await expect(page).toHaveURL(/\/$/);
    await page.locator("#project-select").selectOption(state.projectId);
    await page.getByLabel("Search work items").fill(title);
    const card = workCard(page, title);
    await expect(card).toHaveCount(1);

    const expectedCustomPointer = [
      `Project ${state.projectId}`,
      `Work ${seededWork.id}: ${title}`,
      `Summary: ${summary}`
    ].join("\n");
    await card.getByRole("button", { name: /Copy recall pointer/ }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(expectedCustomPointer);

    // Overwrite the clipboard so the pane copy below proves its own content instead of
    // inheriting the value the card just wrote.
    const clipboardSentinel = `clipboard sentinel ${suffix}`;
    await page.evaluate((value) => navigator.clipboard.writeText(value), clipboardSentinel);
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(clipboardSentinel);

    const pane = await selectWork(page, title);
    // The card and the pane share one copied state, so the pane button briefly reads "Copied"
    // after the card copy; the exact-name locator waits for the label to revert.
    await pane.getByRole("button", { name: "Copy recall pointer", exact: true }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(expectedCustomPointer);
    // On the narrow project the open pane is a sheet over the navigation; desktop is a no-op.
    await closeDetail(page);

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
    const restoredCard = workCard(page, title);
    await expect(restoredCard).toHaveCount(1);
    await restoredCard.getByRole("button", { name: /Copy recall pointer/ }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
      `Recall the mnemonic work item "${title}" (project_id ${state.projectId}, work_item_id ${seededWork.id}) using \`recall_work\`. Verify its premises and, if confirmed, proceed with the work as described.

If the stated premises are refuted or you determine that no work is needed, close the issue as "won't do" with a detailed disposition explanation. If you acquire a work lease, create a background task to remind you to renew it prior to expiration. Reset the timer upon work release renewal.`
    );
  } finally {
    await clearRecallPointerTemplate(client);
    await removeSeededWork(client, seededWork);
    await client.dispose();
  }
});
