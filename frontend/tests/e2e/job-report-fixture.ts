import { expect, type APIRequestContext, type Locator } from "@playwright/test";
import type { JobCompletionReportInput } from "../../lib/types";
const fixtureReason = "This creates a disposable browser acceptance record; no application code is implemented by the fixture.";
export async function reportForFixture(client: APIRequestContext, projectId: string): Promise<JobCompletionReportInput> {
  const response = await client.get(`/api/v1/projects/${projectId}/settings`);
  expect(response.ok(), await response.text()).toBe(true);
  const settings = await response.json() as { revision: string };
  return { summary: "This browser acceptance fixture has reached its intended closeout. Its records are retained for the test to inspect.", fyi_items: [], prompt_revision: settings.revision,
    decision_provenance: { disposition: "not-code", reason: fixtureReason, citations: [], commits: [] } };
}
export async function fillFixtureReport(context: Locator): Promise<void> {
  await context.getByRole("textbox", { name: /^Human summary/ }).fill("The requested changes have been saved and checked in the browser. The work is ready to review and has not been deployed.");
  await fillFixtureDecisionProvenance(context);
}
export async function fillFixtureDecisionProvenance(context: Locator): Promise<void> {
  await context.getByRole("combobox", { name: "Decision use", exact: true }).selectOption("not-code");
  await context.locator(".decision-provenance-editor").getByRole("textbox", { name: "Reason", exact: true }).fill(fixtureReason);
}
