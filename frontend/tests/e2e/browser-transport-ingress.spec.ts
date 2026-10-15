import { expect, request, test } from "@playwright/test";

test("FastAPI browser ingress serves all four families, upgrades sync and keeps private routes closed", async ({ page }) => {
  test.skip(process.env.MNEMONIC_E2E_BROWSER_TRANSPORTS !== "true", "Requires the isolated FastAPI routing overlay.");
  const api = await request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
  try {
    const created = await api.post("/api/v1/projects", {
      data: { name: "Browser transport parity", slug: `transport-${crypto.randomUUID()}` }
    });
    expect(created.status(), await created.text()).toBe(201);
    const project = await created.json() as { id: string };
    for (const path of [
      "/api/mnemonic/runtime-config",
      `/api/artifacts/projects/${project.id}/artifacts`,
      `/api/transcripts/projects/${project.id}/transcripts`,
      `/api/backups/projects/${project.id}/backups`
    ]) {
      const response = await page.request.get(path);
      expect(response.status(), `${path}: ${await response.text()}`).toBe(200);
      const headers = response.headers();
      expect(headers["cache-control"]).toContain("no-store");
      expect(headers["cache-control"]).toContain("no-transform");
      expect(headers["x-content-type-options"]).toContain("nosniff");
      expect(headers["x-frame-options"]).toBe("DENY");
      expect(headers["cross-origin-resource-policy"]).toContain("same-origin");
      expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    }
    for (const path of ["/api/v1/projects", "/worker/healthz", "/docs", "/redoc", "/openapi.json"]) {
      expect((await page.request.get(path)).status(), path).toBe(404);
    }
    await page.goto("/");
    const ready = await page.evaluate(() => new Promise<unknown>((resolve, reject) => {
      const socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/mnemonic/sync`);
      const timeout = setTimeout(() => { socket.close(); reject(new Error("No sync ready frame")); }, 10_000);
      socket.onmessage = (event) => { clearTimeout(timeout); resolve(JSON.parse(event.data)); socket.close(); };
      socket.onerror = () => { clearTimeout(timeout); reject(new Error("Sync upgrade failed")); };
    }));
    expect(ready).toEqual({ type: "ready", revision: expect.any(Number) });
  } finally {
    await api.dispose();
  }
});
