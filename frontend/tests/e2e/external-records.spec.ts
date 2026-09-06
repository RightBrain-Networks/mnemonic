import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, workCard, workPane } from "./surface";

let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

async function openCreate(page: Page, title: string) {
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(page.locator(".sync-status")).toHaveText("Live updates");
  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const dialog = page.getByRole("dialog", { name: "Create durable work" });
  await dialog.getByLabel("Title", { exact: true }).fill(title);
  await dialog.getByLabel("Summary", { exact: true }).fill("Not filed yet — deliberately stale summary.");
  await dialog.getByLabel("Initial context checkpoint").fill("Preserve tracker context before the next worker selects this work.");
  return dialog;
}

test("external references remain visible before selection and ordered edits can clear them", async ({ page }, testInfo) => {
  const title = `External reference acceptance ${testInfo.project.name} ${state.runId} ${crypto.randomUUID().slice(0, 8)}`;
  const dialog = await openCreate(page, title);
  await dialog.getByRole("button", { name: "Add external reference" }).click();
  const first = dialog.getByRole("group", { name: "Reference 1", exact: true });
  await first.getByLabel("URL", { exact: true }).fill("https://example.com/issues/2188?view=all#details");
  await first.getByLabel(/Label/).fill("project#2188");
  await first.getByRole("combobox", { name: "Observed state", exact: true }).selectOption("closed");
  await first.getByLabel(/Observation time/).fill("2026-09-05T10:20:00-04:00");
  await dialog.getByRole("button", { name: "Add external reference" }).click();
  const second = dialog.getByRole("group", { name: "Reference 2", exact: true });
  await second.getByLabel("URL", { exact: true }).fill("https://example.com/research/2188");
  await second.getByRole("combobox", { name: "Kind", exact: true }).selectOption("references");
  await dialog.getByRole("button", { name: "Create work and checkpoint" }).click();
  await expect(dialog).toBeHidden();
  const pane = workPane(page);
  await expect(pane.locator(".detail-title")).toHaveText(title);
  await closeDetail(page);
  const card = workCard(page, title);
  await expect(card).toContainText("Tracked by");
  await expect(card).toContainText("Caller observed: closed");
  await expect(card).toContainText("2026-09-05T14:20:00Z");
  await expect(card).toContainText("Observation time unknown");
  const selection = await card.getAttribute("aria-selected");
  await page.route("https://example.com/**", (route) => route.fulfill({ status: 200, body: "Disposable external target" }));
  const trackerLink = card.getByRole("link", { name: /project#2188/ });
  await expect.poll(() => trackerLink.evaluate((link) => link.closest("[inert]") === null)).toBe(true);
  const popupPromise = page.waitForEvent("popup");
  await trackerLink.press("Enter");
  const popup = await popupPromise;
  await popup.close();
  await expect(card).toHaveAttribute("aria-selected", selection!);
  await card.getByText("Full reference", { exact: true }).first().click();
  await expect(card.getByText("https://example.com/issues/2188?view=all#details", { exact: true })).toBeVisible();
  await testInfo.attach("External references on discovery", { body: await card.screenshot(), contentType: "image/png" });
  await card.locator(".queue-card-title").click();
  await pane.getByRole("button", { name: "Edit work item" }).click();
  await pane.getByRole("button", { name: "Move reference 2 up" }).click();
  const replacements: unknown[] = [];
  page.on("request", (request) => {
    if (request.method() === "PATCH" && request.url().includes("/work-items/")) replacements.push(request.postDataJSON().external_references);
  });
  await pane.getByRole("button", { name: "Save changes" }).click();
  await expect(pane.getByRole("button", { name: "Edit work item" })).toBeVisible();
  expect((replacements[0] as Array<{ kind: string }>)[0]!.kind).toBe("references");
  await pane.getByRole("button", { name: "Edit work item" }).click();
  await pane.getByRole("button", { name: "Remove reference 2" }).click();
  await pane.getByRole("button", { name: "Remove reference 1" }).click();
  await pane.getByRole("button", { name: "Save changes" }).click();
  await expect(pane.getByRole("button", { name: "Edit work item" })).toBeVisible();
  expect(replacements.at(-1)).toEqual([]);
  await expect(pane.locator(".detail-summary + .external-reference-list")).toHaveCount(0);
  const activity = await openTab(pane, "Activity");
  await activity.getByText("External references · before and after", { exact: true }).first().click();
  await expect(activity.getByText("No references (cleared)", { exact: true })).toBeVisible();
});

test("manual external comparison has independent results, stale populations, unavailable copy and Create anyway", async ({ page }, testInfo) => {
  const title = `Manual external comparison ${testInfo.project.name} ${state.runId} ${crypto.randomUUID().slice(0, 8)}`;
  const dialog = await openCreate(page, title);
  let requests = 0;
  let unavailable = false;
  await page.route("**/api/mnemonic/projects/*/duplicate-suggestions", async (route) => {
    requests += 1;
    const input = route.request().postDataJSON();
    const { body: _body, ...reference } = input.external_candidates[0];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      items: [], limit: input.limit, mode: "lexical", semantic_available: false,
      semantic_scope: "unavailable", composition_version: "duplicate-suggestion-v1",
      exact_title_group_total: 0, omitted_exact_title_group_count: 0,
      external_items: unavailable ? [] : [{ rank: 1, signals: ["exact_title", "lexical"], reference }],
      external_candidate_count: input.external_candidates.length, external_scope: unavailable ? "unavailable" : "lexical"
    }) });
  });
  await dialog.locator(".external-candidates-editor > summary").click();
  await dialog.getByRole("button", { name: "Add external record" }).click();
  await dialog.getByLabel(/Record URL/).fill("https://example.com/issues/manual");
  await dialog.getByLabel(/Record title/).fill(title);
  await dialog.getByLabel(/Record body/).fill("<script>Untrusted provider prose remains text.</script>");
  expect(requests).toBe(0);
  await dialog.getByRole("button", { name: "Check existing work" }).click();
  const results = dialog.getByRole("region", { name: "External comparison results" });
  await expect(results).toContainText("1 supplied records; lexical comparison");
  await expect(dialog.getByRole("status")).toContainText("1 possible external records");
  await expect(results.getByRole("link", { name: new RegExp(title) })).toHaveAttribute("rel", "noopener noreferrer");
  await testInfo.attach("Manual external comparison", { body: await results.screenshot(), contentType: "image/png" });
  await dialog.getByRole("combobox", { name: "Record state", exact: true }).selectOption("closed");
  await expect(dialog.getByRole("status")).toContainText("External records changed");
  expect(requests).toBe(1);
  unavailable = true;
  await dialog.getByRole("button", { name: "Check existing work" }).click();
  await expect(results).toContainText("comparison unavailable");
  await expect(results).toContainText("not successfully compared");
  const create = dialog.getByRole("button", { name: "Create work and checkpoint" });
  await expect(create).toBeEnabled();
  await create.click();
  await expect(dialog).toBeHidden();
  await expect(workPane(page).locator(".detail-title")).toHaveText(title);
  await expect(workPane(page).locator(".external-reference-list")).toHaveCount(0);
});

test("resuming a draft after inspecting suggestions preserves authored references", async ({ page }, testInfo) => {
  const title = `Resumed reference draft ${testInfo.project.name} ${state.runId} ${crypto.randomUUID().slice(0, 8)}`;
  const dialog = await openCreate(page, title);
  await dialog.getByRole("button", { name: "Add external reference" }).click();
  const url = "https://example.com/issues/retained-draft";
  await dialog.getByRole("group", { name: "Reference 1", exact: true }).getByLabel("URL", { exact: true }).fill(url);
  const response = await page.request.get(`/api/mnemonic/projects/${state.projectId}/work-items/${state.historicalCompletion.workItemId}`);
  const { work_item: work } = await response.json();
  await page.route("**/api/mnemonic/projects/*/duplicate-suggestions", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      items: [{ canonical_work: { work_item_id: work.id, title: work.title, summary: work.summary,
        status: work.status, updated_at: work.updated_at, duplicate_member_count: 0 },
        matched_member: { id: work.id, title: work.title, status: work.status }, rank: 1, signals: ["lexical"] }],
      limit: 5, mode: "lexical", semantic_available: false, semantic_scope: "unavailable",
      composition_version: "duplicate-suggestion-v1", exact_title_group_total: 0, omitted_exact_title_group_count: 0
    })
  }));
  await dialog.getByRole("button", { name: "Check existing work" }).click();
  await dialog.getByRole("button", { name: "Inspect existing work" }).click();
  await expect(dialog).toBeHidden();
  await expect(workPane(page).locator(".detail-title")).toHaveText(work.title);
  await closeDetail(page);
  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue(title);
  await expect(dialog.getByRole("group", { name: "Reference 1", exact: true }).getByLabel("URL", { exact: true })).toHaveValue(url);
  await dialog.getByRole("button", { name: "Create work and checkpoint" }).click();
  await expect(dialog).toBeHidden();
  await expect(workPane(page).getByRole("link", { name: /retained-draft/ })).toHaveAttribute("href", url);
});
