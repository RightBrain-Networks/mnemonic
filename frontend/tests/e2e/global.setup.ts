import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { request, type FullConfig } from "@playwright/test";
import {
  migrateEvidenceFreeCompletionThrough0018,
  requireDisposableE2EComposeProject
} from "./database";

export type HistoricalCompletionRequest = {
  expected_version: number;
  checkpoint: {
    prompt: string;
    source_client: string;
    source_session_id: string;
    source_model: null;
    source_session_url: null;
    repository_branch: null;
    verified_against: null;
    tags: string[];
    source_metadata: Record<string, never>;
  };
  client_operation_id: string;
};

export type HistoricalCompletionFixture = {
  title: string;
  workItemId: string;
  completionCheckpointId: string;
  clientOperationId: string;
  requestBody: HistoricalCompletionRequest;
  responseBody: string;
  completionEventId: string;
  completionGeneration: string;
};

export type E2EState = {
  projectId: string;
  projectName: string;
  runId: string;
  historicalCompletion: HistoricalCompletionFixture;
};

export const statePath = join(process.cwd(), "test-results", "e2e-state.json");

export default async function globalSetup(_config: FullConfig) {
  const baseURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!baseURL || !apiKey) {
    throw new Error("Run browser acceptance tests through scripts/test-e2e.sh.");
  }
  requireDisposableE2EComposeProject("Browser acceptance global setup");

  const runId = crypto.randomUUID();
  const client = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }
  });
  let project: { id: string; name: string };
  let historicalCompletion: Omit<
    HistoricalCompletionFixture,
    "completionEventId" | "completionGeneration"
  >;
  try {
    const response = await client.post("/api/v1/projects", {
      data: {
        name: `E2E Phase 1 ${runId}`,
        description: "Disposable browser fixture for work-item grouping and immutable checkpoints."
      }
    });
    if (!response.ok()) throw new Error(`Could not seed the disposable E2E project (${response.status()}): ${await response.text()}`);
    project = await response.json() as { id: string; name: string };

    const title = `Phase 11 migrated 0018 completion ${runId.slice(0, 8)}`;
    const workResponse = await client.post(
      `/api/v1/projects/${project.id}/work-items`,
      {
        data: {
          title,
          summary: "A receipt-backed evidence-free completion carried through migration 0019.",
          status: "pending",
          priority: 31,
          initial_checkpoint: {
            prompt: "Initial context for the historical completion migration fixture.",
            source_client: "playwright-api",
            source_session_id: `phase11-historical-create-${runId}`,
            source_model: null,
            source_session_url: null,
            repository_branch: null,
            verified_against: null,
            tags: ["phase-11", "historical-completion"],
            source_metadata: {}
          }
        }
      }
    );
    if (!workResponse.ok()) {
      throw new Error(
        `Could not seed the historical work item (${workResponse.status()}): ${await workResponse.text()}`
      );
    }
    const work = (await workResponse.json() as {
      work_item: { id: string; version: number };
    }).work_item;
    const clientOperationId = crypto.randomUUID();
    const requestBody: HistoricalCompletionRequest = {
      expected_version: work.version,
      checkpoint: {
        prompt: "Historical completion recorded without structured evidence before Phase 11.",
        source_client: "playwright-api",
        source_session_id: `phase11-historical-complete-${runId}`,
        source_model: null,
        source_session_url: null,
        repository_branch: null,
        verified_against: null,
        tags: ["phase-11", "historical-completion"],
        source_metadata: {}
      },
      client_operation_id: clientOperationId
    };
    const completionResponse = await client.post(
      `/api/v1/projects/${project.id}/work-items/${work.id}/complete`,
      { data: requestBody }
    );
    const responseBody = await completionResponse.text();
    if (!completionResponse.ok()) {
      throw new Error(
        `Could not seed the historical completion (${completionResponse.status()}): ${responseBody}`
      );
    }
    const completion = JSON.parse(responseBody) as {
      work_item?: { id?: unknown };
      checkpoint?: { id?: unknown };
      completion_evidence?: unknown;
    };
    if (
      completion.work_item?.id !== work.id
      || typeof completion.checkpoint?.id !== "string"
      || Object.hasOwn(completion, "completion_evidence")
    ) {
      throw new Error(
        "The historical completion seed did not have the sparse Phase 10 response shape."
      );
    }
    historicalCompletion = {
      title,
      workItemId: work.id,
      completionCheckpointId: completion.checkpoint.id,
      clientOperationId,
      requestBody,
      responseBody
    };
  } finally {
    await client.dispose();
  }

  const migrationProof = await migrateEvidenceFreeCompletionThrough0018(
    project.id,
    historicalCompletion.workItemId,
    historicalCompletion.completionCheckpointId,
    historicalCompletion.clientOperationId
  );
  const state: E2EState = {
    projectId: project.id,
    projectName: project.name,
    runId,
    historicalCompletion: { ...historicalCompletion, ...migrationProof }
  };
  await mkdir(dirname(statePath), { recursive: true });
  await writeFile(statePath, JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
}
