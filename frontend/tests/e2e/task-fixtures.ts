import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { reportForFixture } from "./job-report-fixture";

export async function createProject(api: APIRequestContext) {
  const response = await api.post("/api/v1/projects", { data: { name: "Agent workspace", slug: `task-dashboard-${crypto.randomUUID()}` } });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as { id: string; name: string };
}

export async function createWork(api: APIRequestContext, projectId: string, title: string) {
  const response = await api.post(`/api/v1/projects/${projectId}/work-items`, { data: {
    title, summary: "Preserve agent context across sessions and verify the completed changes.",
    initial_checkpoint: { prompt: "Implement and validate the requested change.", source_client: "codex", source_session_id: "task-author" }
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()).work_item as { id: string; version: number };
}

export async function createReview(api: APIRequestContext, projectId: string, title: string) {
  const work = await createWork(api, projectId, title);
  const response = await api.post(`/api/v1/projects/${projectId}/work-items/${work.id}/complete`, { data: {
    expected_version: work.version, client_operation_id: crypto.randomUUID(), subagent_transcripts: null,
    checkpoint: { prompt: "Completed the change and verified its behavior.", source_client: "codex", source_session_id: "task-author" },
    job_completion_report: await reportForFixture(api, projectId),
    code_review_handoff: {
      scope: { repositories: [{ repository_key: "main", checkout_path: "/srv/example", object_format: "sha1", base_commit: "a".repeat(40), head_commit: "b".repeat(40) }] },
      handoff: { change_summary: "Improve context handoffs.", decisions: [], focus_areas: ["Concurrent updates"], traps: [], validation_summary: "Unit tests passed." }
    }
  } });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()).code_review_request as { id: string; work_item_id: string };
}

export async function claim(api: APIRequestContext, projectId: string, workId: string, reviewId?: string) {
  const response = await api.post(`/api/v1/projects/${projectId}/work-items/${workId}/claim`, { data: {
    holder_client: "codex", holder_session_id: reviewId ? "review-session" : "implementation-session",
    claim_request_id: crypto.randomUUID(), session_transcript: null,
    ...(reviewId ? { purpose: "code_review", code_review_id: reviewId, mode: "cold" } : {})
  } });
  expect(response.ok(), await response.text()).toBe(true);
}

export async function openProject(page: Page, projectId: string) {
  await page.goto("/");
  await page.locator("#project-select").selectOption(projectId);
  await expect(page.getByRole("heading", { name: "Dashboard.", exact: true })).toBeVisible();
}
