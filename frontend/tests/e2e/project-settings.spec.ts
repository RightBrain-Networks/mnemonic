import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("project details can be edited on the Workspace settings route", async ({ page }) => {
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

  await page.goto("/settings/workspace");
  await page.locator("#project-select").selectOption(state.projectId);

  const cards = page.locator(".settings-card");
  const details = cards.filter({ has: page.getByRole("heading", { name: "Project details", exact: true }) });
  await expect(details).toBeVisible();

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
