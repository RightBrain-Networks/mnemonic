import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import type { JobCompletionReportInput, JobReportEnvelope, JobReportPage } from "../../lib/types";
import { reportForFixture } from "./job-report-fixture";
import { createProject, createWork } from "./task-fixtures";

async function client() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
}

async function createReport(api: APIRequestContext, projectId: string, title: string,
  report: JobCompletionReportInput) {
  const work = await createWork(api, projectId, title);
  const response = await api.post(`/api/v1/projects/${projectId}/work-items/${work.id}/complete`, { data: {
    expected_version: work.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
    checkpoint: { prompt: "Created a disposable summary to verify bulk dismissal.",
      source_client: "playwright-api", source_session_id: "clear-summaries" },
    job_completion_report: report
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()).job_completion_report.id as string;
}

async function reportDetail(api: APIRequestContext, projectId: string, reportId: string) {
  const response = await api.get(`/api/v1/projects/${projectId}/job-completion-reports/${reportId}`);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as JobReportEnvelope;
}

async function inbox(api: APIRequestContext, projectId: string) {
  const response = await api.get(`/api/v1/projects/${projectId}/job-completion-reports?limit=50`);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as JobReportPage;
}

async function openSummaries(page: Page, projectId: string) {
  await page.goto("/summaries");
  await page.locator("#project-select").selectOption(projectId);
  await expect(page.getByRole("heading", { name: "Summaries.", exact: true })).toBeVisible();
  await expect(page.locator("#project-select")).toHaveValue(projectId);
}

function clearDialog(page: Page) {
  return page.getByRole("dialog", { name: "Clear all summaries?", exact: true });
}

async function confirmClear(page: Page) {
  await page.getByRole("button", { name: "Clear all", exact: true }).click();
  const dialog = clearDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Clear all", exact: true })).toBeEnabled();
  return dialog;
}

test("Clear all confirms the entire pending summary inbox across pages and preserves other projects", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const api = await client();
  try {
    const project = await createProject(api);
    const other = await createProject(api);
    const report = await reportForFixture(api, project.id);
    const dismissed = await createReport(api, project.id, "Previously dismissed summary", report);
    const dismissal = await api.post(`/api/v1/projects/${project.id}/job-completion-reports/${dismissed}/dismiss`, { data: {
      client_operation_id: crypto.randomUUID(), actor: {
        actor_client: "dashboard", actor_session_id: "clear-summaries-existing-dismissal"
      }
    } });
    expect(dismissal.ok(), await dismissal.text()).toBe(true);
    const previousDismissal = (await reportDetail(api, project.id, dismissed)).human_dismissal;
    const reportIds: string[] = [];
    for (let index = 1; index <= 23; index += 1) {
      reportIds.push(await createReport(api, project.id, `Completed delivery ${index}`, report));
    }
    const otherReport = await createReport(api, other.id, "Another project's pending summary",
      await reportForFixture(api, other.id));
    const dismissalBodies: string[] = [];
    const cursorRequests: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/job-completion-reports**`, async (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "GET" && url.pathname.endsWith("/job-completion-reports")) {
        url.searchParams.set("limit", "7");
        if (url.searchParams.has("cursor")) cursorRequests.push(url.searchParams.get("cursor")!);
        await route.fulfill({ response: await route.fetch({ url: url.href }) });
      } else {
        if (route.request().method() === "POST" && url.pathname.endsWith("/dismiss")) {
          dismissalBodies.push(route.request().postData()!);
        }
        await route.continue();
      }
    });
    await openSummaries(page, project.id);
    await expect(page.locator("article.job-report-card")).toHaveCount(7);
    await expect(page.getByRole("button", { name: "Load more summaries", exact: true })).toBeVisible();
    await expect(page.getByRole("article", { name: "Report for Completed delivery 1", exact: true })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("clear-summaries-page.png"), animations: "disabled" });

    let dialog = await confirmClear(page);
    await expect(dialog).toContainText("23");
    await expect(dialog).toContainText(project.name);
    await page.screenshot({ path: testInfo.outputPath("clear-summaries-confirmation.png"), animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Clear all", exact: true })).toBeFocused();
    expect(dismissalBodies).toHaveLength(0);
    dialog = await confirmClear(page);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(dismissalBodies).toHaveLength(0);
    expect((await inbox(api, project.id)).items).toHaveLength(23);

    dialog = await confirmClear(page);
    await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(page.getByRole("heading", { name: "You’re caught up.", exact: true })).toBeVisible({ timeout: 25_000 });
    await expect(page.getByRole("heading", { name: "Reports to review", exact: true })).toBeFocused();
    await expect(page.locator(".summary-nav-count")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Clear all", exact: true })).toBeDisabled();
    expect(dismissalBodies).toHaveLength(23);
    expect(new Set(dismissalBodies.map((body) => JSON.parse(body).client_operation_id)).size).toBe(23);
    expect(cursorRequests.length).toBeGreaterThanOrEqual(3);
    expect((await inbox(api, project.id)).items).toHaveLength(0);
    for (const reportId of reportIds) {
      expect(await reportDetail(api, project.id, reportId)).toMatchObject({
        human_dismissed: true, report: { id: reportId }
      });
    }
    expect((await reportDetail(api, project.id, dismissed)).human_dismissal).toEqual(previousDismissal);
    expect(await reportDetail(api, other.id, otherReport)).toMatchObject({ human_dismissed: false });
    await page.locator("#project-select").selectOption(other.id);
    await expect(page.getByRole("article", { name: "Report for Another project's pending summary", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await api.dispose(); }
});

test("Clear all leaves summaries created after the confirmation snapshot pending", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const report = await reportForFixture(api, project.id);
    const original = await createReport(api, project.id, "Summary included in confirmation", report);
    await openSummaries(page, project.id);
    const dialog = await confirmClear(page);
    const later = await createReport(api, project.id, "Summary arriving after confirmation opened", report);
    await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("article", { name: "Report for Summary arriving after confirmation opened", exact: true })).toBeVisible();
    await expect(page.locator("article.job-report-card")).toHaveCount(1);
    await expect(page.locator(".summary-nav-count")).toHaveText("1");
    expect(await reportDetail(api, project.id, original)).toMatchObject({ human_dismissed: true });
    expect(await reportDetail(api, project.id, later)).toMatchObject({ human_dismissed: false });
  } finally { await api.dispose(); }
});

test("Clear all stops on an uncertain dismissal and preserves the exact retry request", async ({ page }) => {
  const api = await client();
  try {
    const project = await createProject(api);
    const report = await reportForFixture(api, project.id);
    const oldest = await createReport(api, project.id, "Summary after the uncertain request", report);
    const uncertain = await createReport(api, project.id, "Summary with a lost dismissal response", report);
    const newest = await createReport(api, project.id, "Summary dismissed before the lost response", report);
    const requests: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/job-completion-reports/${uncertain}/dismiss`, async (route) => {
      requests.push(route.request().postData()!);
      const response = await route.fetch();
      if (requests.length === 1) await route.fulfill({ status: 502, json: { detail: "Lost response." } });
      else await route.fulfill({ response });
    });
    await openSummaries(page, project.id);
    const dialog = await confirmClear(page);
    await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(dialog.getByText("Dismiss summary · outcome unknown", { exact: true })).toBeVisible();
    expect(await reportDetail(api, project.id, newest)).toMatchObject({ human_dismissed: true });
    expect(await reportDetail(api, project.id, uncertain)).toMatchObject({ human_dismissed: true });
    expect(await reportDetail(api, project.id, oldest)).toMatchObject({ human_dismissed: false });
    await dialog.getByRole("button", { name: "Retry exact request", exact: true }).click();
    await expect(page.getByText("Dismiss summary · outcome unknown", { exact: true })).toHaveCount(0);
    expect(requests).toHaveLength(2);
    expect(requests[1]).toBe(requests[0]);
    expect(await reportDetail(api, project.id, oldest)).toMatchObject({ human_dismissed: false });
    await dialog.getByRole("button", { name: "Continue clearing", exact: true }).click();
    await expect(page.getByRole("heading", { name: "You’re caught up.", exact: true })).toBeVisible();
  } finally { await api.dispose(); }
});
