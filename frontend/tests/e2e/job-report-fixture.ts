import { expect, type APIRequestContext, type Locator } from "@playwright/test";
import type { JobCompletionReportInput } from "../../lib/types";
export async function reportForFixture(client: APIRequestContext, projectId: string): Promise<JobCompletionReportInput> {
  const response = await client.get(`/api/v1/projects/${projectId}/settings`);
  expect(response.ok(), await response.text()).toBe(true);
  const settings = await response.json() as { revision: string };
  return { summary: "This browser acceptance fixture has reached its intended closeout. Its records are retained for the test to inspect.", fyi_items: [], prompt_revision: settings.revision };
}
export async function fillFixtureReport(context: Locator): Promise<void> {
  await context.getByRole("textbox", { name: /^Human summary/ }).fill("The requested changes have been saved and checked in the browser. The work is ready to review and has not been deployed.");
}
