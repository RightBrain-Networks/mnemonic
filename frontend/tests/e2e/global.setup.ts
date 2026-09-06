import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { type FullConfig } from "@playwright/test";
import {
  seedMigratedHistoricalCompletion,
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
  projectDescription: string;
  runId: string;
  historicalCompletion: HistoricalCompletionFixture;
};

export const statePath = join(process.cwd(), "test-results", "e2e-state.json");

export default async function globalSetup(_config: FullConfig) {
  if (!process.env.MNEMONIC_E2E_API_URL || !process.env.MNEMONIC_E2E_API_KEY) {
    throw new Error("Run browser acceptance tests through scripts/test-e2e.sh.");
  }
  requireDisposableE2EComposeProject("Browser acceptance global setup");
  const state = await seedMigratedHistoricalCompletion();
  await mkdir(dirname(statePath), { recursive: true });
  await writeFile(statePath, JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
}
