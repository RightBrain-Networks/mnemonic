import { expect, request, test } from "@playwright/test";

test("a project can define capacity, observe an agent claim, and manually release it", async ({ page }, testInfo) => {
  const api = await request.newContext({ baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` } });
  try {
    const project = await api.post("/api/v1/projects", { data: { name: "Test infrastructure",
      slug: `external-leases-${crypto.randomUUID()}`, description: "Browser acceptance tests." } });
    expect(project.ok(), await project.text()).toBe(true);
    const { id } = await project.json();
    const base = `/projects/${id}/external-resources`;
    await page.goto(`/external-leases?project=${id}`);
    const resources = page.getByRole("region", { name: "Resources", exact: true });
    await expect(resources.getByText("No resources defined.")).toBeVisible();
    await resources.getByRole("button", { name: "New resource" }).click();
    const form = resources.getByRole("form");
    await form.getByLabel("Resource name").fill("Concurrent pytest workers");
    await form.getByLabel("Total units").fill("4294967296");
    await expect(form.getByRole("button", { name: "Save resource" })).toBeDisabled();
    await form.getByLabel("Total units").fill("16");
    await form.getByRole("button", { name: "Save resource" }).click();
    await expect(resources.getByText("16 of 16 units available")).toBeVisible();
    await resources.getByRole("button", { name: "Edit Concurrent pytest workers" }).click();
    await form.getByLabel("Default (minutes)").fill("30");
    await form.getByRole("button", { name: "Save resource" }).click();
    await expect(resources.getByText(/Default 30 min/)).toBeVisible();
    const response = await api.get(`/api/v1${base}`);
    expect(response.ok()).toBe(true);
    const resource = (await response.json()).items[0];
    const claim = await api.post(`/api/v1${base}/${resource.id}/claim`, { data: {
      holder_client: "codex", holder_session_id: "integration-tests", quantity: 8, claim_request_id: crypto.randomUUID()
    } });
    expect(claim.ok(), await claim.text()).toBe(true);
    await page.reload();
    const active = page.getByRole("region", { name: "Active leases" });
    await expect(resources.getByText("8 of 16 units available")).toBeVisible();
    await expect(active.getByText("8 units held by codex")).toBeVisible();
    await expect(active.getByRole("spinbutton")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "External Leases", exact: true })).toHaveAttribute("aria-current", "page");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => document.fonts.ready);
    const screenshot = testInfo.outputPath(`external-leases-${testInfo.project.name}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach("External Leases", { path: screenshot, contentType: "image/png" });

    const requests: string[] = [];
    await page.route("**/leases/*/revoke", async (route) => {
      requests.push(route.request().postData()!);
      if (requests.length === 1) {
        // The server commits before this browser loses its response.
        expect((await route.fetch()).ok()).toBe(true);
        await route.abort();
      } else {
        await route.continue();
      }
    });
    page.once("dialog", (dialog) => dialog.accept());
    await active.getByRole("button", { name: /Release .* lease/ }).click();
    await expect(page.getByLabel("Select project")).toBeDisabled();
    await page.getByRole("button", { name: "Retry same action" }).click();
    await expect(active.getByText("No active leases.")).toBeVisible();
    await expect(resources.getByText("16 of 16 units available")).toBeVisible();
    await expect(page.getByLabel("Select project")).toBeEnabled();
    expect(requests).toHaveLength(2);
    expect(requests[0]).toEqual(requests[1]);
  } finally { await api.dispose(); }
});
