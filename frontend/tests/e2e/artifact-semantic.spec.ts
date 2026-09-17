import { expect, request, test } from "@playwright/test";

test("semantic artifact search opens revision-bound passage evidence and discloses partial coverage", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const created = await api.post("/api/v1/projects", { data: { name: `Semantic passages ${crypto.randomUUID().slice(0, 8)}` } });
    expect(created.status(), await created.text()).toBe(201);
    const project = await created.json();
    const filename = "Remote listener decisions.txt";
    const prefix = "The project stores recipes and shopping lists. Each recipe has ingredients and a preparation time.\n".repeat(80);
    const tail = "The remote listener renews the session cookie before it expires so a long command can finish without losing its authenticated connection. The renewal uses the active lease token. <script>untrusted document text</script>";
    await page.clock.install();
    await page.goto(`/artifacts?project=${project.id}`);
    await page.getByLabel("Upload artifact files").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(prefix + tail) });
    await expect(page.getByRole("button", { name: filename, exact: true })).toBeVisible();
    const q = "Why refresh a remote listener session cookie while a long command is running?";
    const input = { q, detail: "full", facets: ["artifacts"], fulltext: true, filters: { artifacts: { semantic: true } }, limit: 50, offset: 0 };
    let native: Record<string, any> = {};
    await expect.poll(async () => {
      const response = await api.post(`/api/v1/projects/${project.id}/search`, { data: input });
      if (!response.ok()) return false;
      native = await response.json();
      return native.coverage?.artifacts?.embedding?.ready === 1 && native.items?.length === 1;
    }, { timeout: 120_000, intervals: [500, 1000, 2000] }).toBe(true);
    await page.getByRole("searchbox", { name: "Search artifact metadata and content" }).fill(q);
    await page.getByRole("switch", { name: "Search by meaning" }).check();
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.locator(".artifact-search-status")).toContainText("1 ranked candidate");
    const result = page.locator("tr").filter({ has: page.getByRole("button", { name: filename, exact: true }) });
    await expect(result).toContainText("Semantic candidate 1");
    // Correcting a rejected query back to the already loaded query must restore
    // its results immediately, without waiting for the 30-second refresh.
    await page.clock.pauseAt(new Date(Date.now() + 100));
    const submittedQueries: string[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith(`/projects/${project.id}/search`)) submittedQueries.push(request.postDataJSON().q);
    });
    const searchbox = page.getByRole("searchbox", { name: "Search artifact metadata and content" });
    await searchbox.fill(`"${q}"`);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Use an unquoted query" })).toBeVisible();
    await expect(result).toBeHidden();
    await searchbox.fill(q);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(result).toBeVisible({ timeout: 1000 });
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
    expect(submittedQueries).toEqual([]);
    await page.clock.resume();
    expect(native.items[0].artifact.passage.start_offset).toBeGreaterThan(1000);
    await expect(result.locator("script")).toHaveCount(0);
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("artifact-semantic-results.png"), fullPage: true, animations: "disabled" });
    const readRequest = page.waitForRequest((request) => /\/artifacts\/[^/]+\/text\?/.test(request.url()));
    await result.getByRole("button", { name: "Open full passage" }).click();
    const query = new URL((await readRequest).url()).searchParams;
    expect(query.get("expected_revision")).toBe("1");
    expect(query.get("expected_text_sha256")).toMatch(/^[0-9a-f]{64}$/);
    expect(Number(query.get("offset"))).toBeGreaterThan(1000);
    const dialog = page.getByRole("dialog", { name: filename, exact: true });
    const text = dialog.getByRole("textbox", { name: "Supporting passage text" });
    await expect(text).toHaveValue(/session cookie/);
    await expect(dialog).toContainText("not a probability");
    if (testInfo.project.name === "chromium-narrow") await expect.poll(async () => Math.round((await dialog.boundingBox())?.width ?? 0)).toBe(page.viewportSize()!.width);
    await page.screenshot({ path: testInfo.outputPath("artifact-semantic-passage.png"), animations: "disabled" });
    await dialog.getByRole("button", { name: "Close preview" }).click();
    const textRoute = `**/api/artifacts/projects/${project.id}/artifacts/*/text?*`;
    await page.route(textRoute, (route) => route.fulfill({ status: 409, json: { detail: { code: "artifact_text_changed", message: "Untrusted upstream error" } } }));
    await result.getByRole("button", { name: "Open full passage" }).click();
    await expect(dialog.getByRole("alert")).toContainText("This passage changed after the search");
    await expect(dialog.getByRole("alert")).not.toContainText("Untrusted upstream error");
    await dialog.getByRole("button", { name: "Close preview" }).click();
    await page.unroute(textRoute);
    // Force one pending vector using a real native response; worker timing is nondeterministic.
    const partial = structuredClone(native);
    partial.coverage.artifacts.embedding.pending += 1;
    partial.coverage.artifacts.embedding.state = "incomplete";
    partial.semantic.partial_vectors = true; partial.semantic.comparison_incomplete = true;
    partial.indexing_incomplete = true;
    partial.project_coverage[0].coverage = partial.coverage;
    partial.project_coverage[0].indexing_incomplete = true;
    await page.route(`**/api/mnemonic/projects/${project.id}/search`, (route) => route.fulfill({ json: partial }));
    await page.reload();
    await page.getByRole("searchbox").fill(q);
    await page.getByRole("switch", { name: "Search by meaning" }).check();
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.locator(".artifact-search-status")).toContainText("Semantic coverage is incomplete");
    await expect(page.getByRole("button", { name: "Open full passage" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("artifact-semantic-incomplete.png"), fullPage: true, animations: "disabled" });
  } finally { await api.dispose(); }
});
