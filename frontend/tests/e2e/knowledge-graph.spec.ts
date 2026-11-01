import { expect, request, test, type APIRequestContext } from "@playwright/test";
import type { Decision, KnowledgeDomain } from "../../lib/knowledge";

async function apiClient() {
  return request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}`, Accept: "application/json" } });
}

async function seed(api: APIRequestContext) {
  const projectResult = await api.post("/api/v1/projects", { data: { name: `Knowledge graph ${crypto.randomUUID().slice(0, 8)}` } });
  expect(projectResult.ok(), await projectResult.text()).toBe(true);
  const project = await projectResult.json() as { id: string; name: string };
  const path = `/api/v1/projects/${project.id}/knowledge`;
  async function domain(name: string, prefix: string) {
    const response = await api.post(`${path}/domains`, { data: { name, prefix, client_operation_id: crypto.randomUUID() } });
    expect(response.ok(), await response.text()).toBe(true);
    return await response.json() as KnowledgeDomain;
  }
  const architecture = await domain("Architecture", "ADR");
  const owners = await domain("Owner decisions", "ODR");
  const input = { domain_id: architecture.id, title: "Keep a durable decision history", content: "Original plain text decision.\n", media_type: "text/plain", state: "proposed", author_kind: "agent", agent_session_id: "knowledge-acceptance-session", model_id: "codex", source_work_item_id: null };
  const initial = await api.put(`${path}/decisions`, { data: { ...input, client_operation_id: crypto.randomUUID() } });
  expect(initial.ok(), await initial.text()).toBe(true);
  const previous = (await initial.json()).decision as Decision;
  const updated = await api.put(`${path}/decisions`, { data: { ...input, decision_id: previous.decision_id, expected_revision: 0, content: "# Durable decision history\n\nEvery revision remains available for **audit and attribution**.\n\n- Cite the exact revision.\n- Record the resulting commit.\n", media_type: "text/markdown", state: "accepted", client_operation_id: crypto.randomUUID() } });
  expect(updated.ok(), await updated.text()).toBe(true);
  const current = (await updated.json()).decision as Decision;
  const related = await api.put(`${path}/decisions`, { data: { ...input, domain_id: owners.id,
    title: "Retain owner approvals", content: "Owner approvals cite the durable architecture history.\n",
    state: "accepted", related_decision_ids: [current.decision_id], client_operation_id: crypto.randomUUID() } });
  expect(related.ok(), await related.text()).toBe(true);
  const downstream = (await related.json()).decision as Decision;
  const scope = await api.post(`${path}/decisions/${current.decision_id}/scope`, { data: { revision: 0, commit_hash: "abc01234".repeat(5), agent_session_id: "implementation-session", client_operation_id: crypto.randomUUID() } });
  expect(scope.ok(), await scope.text()).toBe(true);
  return { project, current, architecture, downstream };
}

test("knowledge graph browses domains, immutable revisions and revision-pinned code scope", async ({ page }, testInfo) => {
  if (testInfo.project.name === "chromium-desktop") await page.setViewportSize({ width: 1440, height: 1100 });
  const api = await apiClient();
  try {
    const { project, current, downstream } = await seed(api);
    await page.goto(`/knowledge?project=${project.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Decision documents.");
    await expect(page.getByRole("link", { name: "Decision documents", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("button", { name: current.title, exact: true })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Revisions", exact: true })).toBeVisible();
    await expect(page.getByRole("row").filter({ has: page.getByRole("button", { name: current.title, exact: true }) }).getByRole("cell").nth(2)).toHaveText("2");
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await page.getByRole("group", { name: "Filter by domain" }).getByRole("button", { name: /Owner decisions/ }).click();
    await expect(page.getByRole("button", { name: current.title, exact: true })).toHaveCount(0);
    await page.getByRole("group", { name: "Filter by domain" }).getByRole("button", { name: /Architecture/ }).click();
    await expect(page.getByRole("button", { name: current.title, exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("knowledge-graph-directory.png"), fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: `View ${current.reference}`, exact: true }).click();
    const drawer = page.getByRole("dialog", { name: current.title, exact: true });
    await expect(drawer.getByRole("button", { name: "Close preview" })).toBeFocused();
    if (testInfo.project.name === "chromium-narrow") await expect.poll(async () => (await drawer.boundingBox())?.width).toBe(page.viewportSize()!.width);
    await expect(drawer.getByRole("region", { name: "Markdown contents" })).toContainText("audit and attribution");
    await page.screenshot({ path: testInfo.outputPath("knowledge-graph-document.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "Revisions", exact: true }).click();
    await drawer.getByRole("button", { name: `${current.decision_id}-0`, exact: true }).click();
    await expect(drawer.getByRole("textbox", { name: "Plain text contents" })).toHaveValue("Original plain text decision.\n");
    await expect(drawer.getByRole("textbox", { name: "Plain text contents" })).toHaveAttribute("readonly", "");
    await drawer.getByRole("button", { name: "Code scope", exact: true }).click();
    await expect(drawer).toContainText("abc01234".repeat(5));
    await expect(drawer).toContainText("implementation-session");
    await expect(drawer.getByRole("button", { name: `${current.decision_id}-0`, exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("knowledge-graph-scope.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "View current revision" }).click();
    await expect(drawer.getByRole("region", { name: "Markdown contents" })).toBeVisible();
    await drawer.getByRole("button", { name: "Details & links" }).click();
    await expect(drawer).toContainText("knowledge-acceptance-session");
    await expect(drawer).toContainText(current.sha256);
    await drawer.getByRole("button", { name: downstream.reference, exact: true }).click();
    await expect(page.getByRole("dialog", { name: downstream.title, exact: true }).getByRole("textbox", { name: "Plain text contents" })).toHaveValue(/Owner approvals/);
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("button", { name: `View ${current.reference}`, exact: true })).toBeFocused();
    await page.goto(`/knowledge?project=${project.id}&decision=${current.decision_id}&revision=0`);
    await expect(page.getByRole("textbox", { name: "Plain text contents" })).toHaveValue("Original plain text decision.\n");
  } finally { await api.dispose(); }
});

test("domain management normalizes prefixes and preserves uncertain retries", async ({ page }) => {
  const api = await apiClient();
  try {
    const { project } = await seed(api);
    await page.goto(`/knowledge/domains?project=${project.id}`);
    await page.getByRole("button", { name: "Add domain", exact: true }).click();
    let dialog = page.getByRole("dialog", { name: "Add domain", exact: true });
    await dialog.getByRole("textbox", { name: "Domain name", exact: true }).fill("Bookkeeping");
    await dialog.getByRole("textbox", { name: "Decision prefix" }).fill("bdr");
    await expect(dialog.getByRole("textbox", { name: "Decision prefix" })).toHaveValue("BDR");
    const bodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/knowledge/domains`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      bodies.push(route.request().postData()!);
      if (bodies.length === 1) {
        const response = await route.fetch();
        expect(response.ok(), await response.text()).toBe(true);
        return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Response interrupted after saving." }) });
      }
      return route.continue();
    });
    await dialog.getByRole("button", { name: "Create domain", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("outcome is unconfirmed");
    await expect(dialog.getByRole("textbox", { name: "Domain name", exact: true })).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
    await expect(page.getByRole("combobox", { name: "Select project" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Retry pending action", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    await expect(page.getByRole("row", { name: "Bookkeeping domain", exact: true })).toBeVisible();
    await page.getByRole("row", { name: "Bookkeeping domain", exact: true }).getByRole("button", { name: "Rename", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Rename domain", exact: true });
    await expect(dialog.getByRole("textbox", { name: "Decision prefix" })).toHaveValue("BDR");
    await expect(dialog.getByRole("textbox", { name: "Decision prefix" })).toBeDisabled();
    await dialog.getByRole("textbox", { name: "Domain name", exact: true }).fill("Finance decisions");
    await dialog.getByRole("button", { name: "Save name", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("row", { name: "Finance decisions domain", exact: true })).toBeVisible();
    await expect(page.getByRole("row", { name: "Finance decisions domain", exact: true }).getByRole("button", { name: "Delete", exact: true })).toBeEnabled();
  } finally { await api.dispose(); }
});

test("humans upload, filter, edit with revision conflicts, replace and download decisions", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, architecture } = await seed(api);
    await page.addInitScript(() => Object.defineProperty(window, "showSaveFilePicker", { value: undefined, configurable: true }));
    await page.goto(`/knowledge/decisions?project=${project.id}`);
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    let dialog = page.getByRole("dialog", { name: "Upload decision document", exact: true });
    await expect(dialog.getByRole("textbox", { name: "Document contents", exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("combobox", { name: "Format", exact: true })).toHaveCount(0);
    await dialog.getByRole("combobox", { name: "Knowledge domain" }).selectOption(architecture.id);
    await dialog.getByLabel("Document file").setInputFiles({ name: "Human policy.md", mimeType: "text/markdown", buffer: Buffer.from("# Human policy\n\nInitial ruling.\n") });
    await expect(dialog.getByRole("textbox", { name: "Document title", exact: true })).toHaveValue("Human policy");
    await dialog.screenshot({ path: testInfo.outputPath("decision-upload.png") });
    await dialog.getByRole("button", { name: "Upload document", exact: true }).click();
    await expect(dialog).toBeHidden();
    const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Human policy", exact: true }) });
    await expect(row).toContainText("Proposed");
    const path = `/api/v1/projects/${project.id}/knowledge`;
    const list = await api.get(`${path}/decisions?q=Human%20policy`);
    expect(list.ok()).toBe(true);
    const created = (await list.json()).items[0] as Decision;
    expect(created.media_type).toBe("text/markdown");
    await page.getByRole("group", { name: "Filter decisions by state" }).getByRole("button", { name: "Accepted", exact: true }).click();
    await expect(row).toHaveCount(0);
    await page.getByRole("group", { name: "Filter decisions by state" }).getByRole("button", { name: "Proposed", exact: true }).click();
    await row.getByRole("button", { name: `Edit ${created.reference}` }).click();
    dialog = page.getByRole("dialog", { name: "Edit decision document", exact: true });
    await expect(dialog.getByRole("textbox", { name: "Document contents", exact: true })).toHaveValue(/Initial ruling/);
    await dialog.getByRole("textbox", { name: "Document contents", exact: true }).fill("# Human draft\n\nMy updated ruling.\n");
    const updated = await api.put(`${path}/decisions`, { data: {
      domain_id: architecture.id, decision_id: created.decision_id, expected_revision: 0,
      title: created.title, content: "# Agent revision\n\nPreserve this concurrent clarification.\n", media_type: "text/markdown", state: "proposed",
      author_kind: "agent", agent_session_id: "concurrent-edit", model_id: "codex", source_work_item_id: null, client_operation_id: crypto.randomUUID()
    } });
    expect(updated.ok(), await updated.text()).toBe(true);
    await dialog.getByRole("button", { name: "Save revision", exact: true }).click();
    await expect(dialog.getByRole("textbox", { name: "Document contents", exact: true })).toHaveValue(/My updated ruling/);
    await expect(dialog.getByRole("textbox", { name: "Current saved contents", exact: true })).toHaveValue(/concurrent clarification/);
    await expect(dialog.getByRole("button", { name: "Save revision", exact: true })).toBeDisabled();
    await dialog.screenshot({ path: testInfo.outputPath("knowledge-human-agent-conflict.png"), animations: "disabled" });
    await dialog.getByRole("textbox", { name: "Document contents", exact: true }).fill("# Merged ruling\n\nMy updated ruling with the concurrent clarification.\n");
    await dialog.getByRole("button", { name: "I incorporated the current revision into my draft", exact: true }).click();
    await dialog.getByRole("button", { name: "Save revision", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(row).toContainText(`${created.decision_id}-2`);
    await row.getByRole("button", { name: `More actions for ${created.decision_id}-2`, exact: true }).click();
    await page.getByRole("menuitem", { name: "Replace", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Replace decision document", exact: true });
    await expect(dialog.getByLabel("Replacement file")).toBeEnabled();
    await dialog.getByLabel("Replacement file").setInputFiles({ name: "replacement.txt", mimeType: "text/plain", buffer: Buffer.from("Atomic replacement.\n") });
    await expect(dialog.getByRole("textbox", { name: "Document contents", exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("combobox", { name: "Format", exact: true })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Replace document", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(row).toContainText(`${created.decision_id}-3`);
    const download = page.waitForEvent("download");
    await row.getByRole("button", { name: `Download ${created.decision_id}-3`, exact: true }).click();
    expect((await download).suggestedFilename()).toBe(`${created.decision_id}-3.txt`);
    // An explicit replacement creates one revision even for identical bytes;
    // an interrupted response retries the same operation without another revision.
    const replacementBodies: string[] = [];
    await page.route(`**/api/mnemonic/projects/${project.id}/knowledge/decisions`, async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      replacementBodies.push(route.request().postData()!);
      if (replacementBodies.length === 1) {
        const response = await route.fetch(); expect(response.ok()).toBe(true);
        return route.fulfill({ status: 503, json: { detail: "Interrupted after replacement." } });
      }
      return route.continue();
    });
    await row.getByRole("button", { name: `More actions for ${created.decision_id}-3`, exact: true }).click();
    await page.getByRole("menuitem", { name: "Replace", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Replace decision document", exact: true });
    await expect(dialog.getByLabel("Replacement file")).toBeEnabled();
    await dialog.getByLabel("Replacement file").setInputFiles({ name: "replacement.txt", mimeType: "text/plain", buffer: Buffer.from("Atomic replacement.\n") });
    await dialog.getByRole("button", { name: "Replace document", exact: true }).click();
    await expect(dialog.getByText("The outcome is unconfirmed. Keep this page open and retry the saved action.")).toBeVisible();
    await dialog.getByRole("button", { name: "Retry pending save", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(row).toContainText(`${created.decision_id}-4`);
    expect(replacementBodies).toHaveLength(2); expect(replacementBodies[1]).toBe(replacementBodies[0]);
  } finally { await api.dispose(); }
});

test("domain deletions show counted relationships and protect changed previews", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, architecture, current, downstream } = await seed(api);
    const path = `/api/v1/projects/${project.id}/knowledge`;
    await page.addInitScript(() => Object.defineProperty(window, "showSaveFilePicker", { value: undefined, configurable: true }));
    await page.goto(`/knowledge/domains?project=${project.id}`);
    const card = page.getByRole("row", { name: "Architecture domain", exact: true });
    await expect(card.getByRole("button", { name: "Export", exact: true })).toHaveCount(0);
    await card.getByRole("button", { name: "Delete", exact: true }).click();
    let dialog = page.getByRole("dialog", { name: "Delete domain?", exact: true });
    await expect(dialog.getByRole("checkbox")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Delete permanently", exact: true })).toBeDisabled();
    await dialog.screenshot({ path: testInfo.outputPath("knowledge-domain-delete-confirmation.png"), animations: "disabled" });
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.goto(`/knowledge/decisions?project=${project.id}`);
    await page.getByRole("button", { name: `More actions for ${downstream.reference}`, exact: true }).click();
    await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Delete decision document?", exact: true });
    await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(dialog).toBeHidden();
    const tombstone = await api.get(`${path}/decisions/${downstream.decision_id}`);
    expect(tombstone.status()).toBe(410);
    await page.goto(`/knowledge/domains?project=${project.id}`);
    const orphan = await api.post(`${path}/domains`, { data: { name: "Orphaned", prefix: "TMP", client_operation_id: crypto.randomUUID() } });
    expect(orphan.ok()).toBe(true);
    await page.reload();
    await page.getByRole("row", { name: "Orphaned domain", exact: true }).getByRole("button", { name: "Delete", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Delete domain?", exact: true });
    await expect(dialog.getByRole("button", { name: "Delete permanently", exact: true })).toBeEnabled();
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("row", { name: "Orphaned domain", exact: true })).toHaveCount(0);
    const raceResponse = await api.post(`${path}/domains`, { data: { name: "Changing domain", prefix: "RACE", client_operation_id: crypto.randomUUID() } });
    expect(raceResponse.ok()).toBe(true);
    const raceDomain = await raceResponse.json();
    await page.reload();
    await page.getByRole("row", { name: "Changing domain domain", exact: true }).getByRole("button", { name: "Delete", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Delete domain?", exact: true });
    await expect(dialog.getByRole("button", { name: "Delete permanently", exact: true })).toBeEnabled();
    const incoming = await api.put(`${path}/decisions`, { data: { domain_id: raceDomain.id, title: "New linked decision", content: "Added after preview", media_type: "text/plain", state: "proposed", author_kind: "human", related_decision_ids: [current.decision_id], client_operation_id: crypto.randomUUID() } });
    expect(incoming.ok(), await incoming.text()).toBe(true);
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(dialog.getByRole("checkbox")).toBeVisible();
    await expect(dialog.getByRole("checkbox")).not.toBeChecked();
    await expect(dialog.getByRole("button", { name: "Delete permanently", exact: true })).toBeDisabled();
    await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect((await api.get(`${path}/decisions/${current.decision_id}`)).ok()).toBe(true);
  } finally { await api.dispose(); }
});
