import { readFile } from "node:fs/promises";
import { expect, request, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")); });

test("search shows checkpoint phrase evidence with its saved source", async ({ page }, testInfo) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  const token = `evidence${crypto.randomUUID().replaceAll("-", "")}`;
  try {
    const response = await api.post(`/api/v1/projects/${state.projectId}/work-items`, { data: {
      title: "Keep expired lease diagnostics visible", summary: "Record the exact failure and recovery context.",
      status: "pending", priority: 37, initial_checkpoint: { prompt: `The command failed with ${token} lease cookie mismatch. Renew the lease before retrying. <script>untrusted source</script>`, source_client: "playwright-api", source_session_id: token, tags: ["search-evidence"] }
    } });
    expect(response.status(), await response.text()).toBe(201);
    const { work_item: work } = await response.json();
    await page.goto("/work-items");
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.locator(".sync-status")).toHaveText("Live Updates");
    await page.getByLabel("Search work items").fill(`"${token} lease cookie"`);
    const result = page.locator(`.search-result[data-work-item-id="${work.id}"]`);
    await expect(result).toBeVisible();
    const evidence = result.getByRole("note", { name: "Supporting search evidence" });
    await expect(evidence).toContainText("Checkpoint");
    await expect(evidence).toContainText(`${token} lease cookie`);
    await expect(evidence.locator("script")).toHaveCount(0);
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("checkpoint-evidence.png"), fullPage: true });
  } finally { await api.dispose(); }
});

test("incomplete duplicate comparison stays visible and creation remains available", async ({ page }, testInfo) => {
  // Force inference unavailability; the normal stack's model/cache state is nondeterministic.
  await page.route("**/api/mnemonic/projects/*/duplicate-suggestions", async (route) => {
    const input = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      items: [], limit: input.limit, mode: "lexical", semantic_available: false, semantic_scope: "unavailable",
      composition_version: "duplicate-suggestion-v1", exact_title_group_total: 0, omitted_exact_title_group_count: 0,
      semantic: { inference: { status: "unavailable", reason: "deadline_exceeded" }, candidate_scope: "none", partial_vectors: false,
        comparison_incomplete: true, retry: { max_attempts: 1, after_seconds: 1 }, cache_refresh: { status: "not_needed", reason: null } }
    }) });
  });
  await page.goto("/work-items");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.locator(".topbar").getByRole("button", { name: "New work" }).click();
  const dialog = page.getByRole("dialog", { name: "Create durable work" });
  await dialog.getByLabel("Title").fill("Compare a possible recurring payment duplicate");
  await dialog.getByLabel("Summary").fill("Check for an existing implementation before creating work.");
  await dialog.getByLabel("Initial context checkpoint").fill("The existing comparison should explain whether it completed.");
  await dialog.getByRole("button", { name: "Check existing work" }).click();
  const notice = dialog.getByText("Comparison incomplete: semantic matching was unavailable.", { exact: false });
  await expect(notice).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Create work and checkpoint" })).toBeEnabled();
  await notice.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("incomplete-comparison.png"), fullPage: true });
});
