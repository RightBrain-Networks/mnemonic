import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { request, type FullConfig } from "@playwright/test";

export type E2EState = {
  projectId: string;
  projectName: string;
  runId: string;
};

export const statePath = join(process.cwd(), "test-results", "e2e-state.json");

export default async function globalSetup(_config: FullConfig) {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) throw new Error("Run browser acceptance tests through scripts/test-e2e.sh or provide the disposable-stack API URL and key.");

  const runId = crypto.randomUUID();
  const client = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  try {
    const response = await client.post("/api/v1/projects", {
      data: {
        name: `E2E Phase 1 ${runId}`,
        description: "Disposable browser fixture for work-item grouping and immutable checkpoints."
      }
    });
    if (!response.ok()) throw new Error(`Could not seed the disposable E2E project (${response.status()}): ${await response.text()}`);
    const project = await response.json() as { id: string; name: string };
    const state: E2EState = { projectId: project.id, projectName: project.name, runId };
    await mkdir(dirname(statePath), { recursive: true });
    await writeFile(statePath, JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
  } finally {
    await client.dispose();
  }
}
