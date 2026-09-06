import type { E2EState } from "./global.setup";
import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const COMPOSE_PROJECT = /^mnemonic-e2e-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COMMAND_TIMEOUT_MS = 180_000;
const COMMAND_BUFFER_BYTES = 4 * 1024 * 1024;

export type Phase11MigrationProof = {
  completionEventId: string;
  completionGeneration: string;
};

type MigrationProofRow = {
  revision: unknown;
  completion_event_id: unknown;
  completion_generation: unknown;
  generation_matches: unknown;
  verification_count: unknown;
  artifact_count: unknown;
  receipt_is_sparse: unknown;
  receipt_work_matches: unknown;
  receipt_checkpoint_matches: unknown;
};

export function requireDisposableE2EComposeProject(purpose: string): string {
  const composeProject = process.env.MNEMONIC_E2E_COMPOSE_PROJECT;
  if (!composeProject || !COMPOSE_PROJECT.test(composeProject)) {
    throw new Error(`${purpose} requires the disposable E2E Compose stack.`);
  }
  return composeProject;
}

function requireUuid(value: string, label: string): void {
  if (!UUID.test(value)) throw new Error(`Refusing to use a malformed ${label}.`);
}

async function runCompose(
  composeProject: string,
  args: string[]
): Promise<string> {
  const { stdout } = await execFileAsync("docker", [
    "compose",
    "-p",
    composeProject,
    "-f",
    resolve(process.cwd(), "../compose.e2e.yaml"),
    ...args
  ], {
    encoding: "utf8",
    timeout: COMMAND_TIMEOUT_MS,
    maxBuffer: COMMAND_BUFFER_BYTES
  });
  return stdout;
}

async function queryDatabase(
  composeProject: string,
  statement: string
): Promise<string> {
  const stdout = await runCompose(composeProject, [
    "exec",
    "-T",
    "postgres",
    "psql",
    "-X",
    "-U",
    "mnemonic_e2e",
    "-d",
    "mnemonic_e2e",
    "--no-align",
    "--tuples-only",
    "--set",
    "ON_ERROR_STOP=1",
    "--command",
    statement
  ]);
  return stdout.trim();
}

function parseMigrationProof(serialized: string): Phase11MigrationProof {
  let value: unknown;
  try {
    value = JSON.parse(serialized);
  } catch (error) {
    throw new Error("The Phase 11 migration proof was not valid JSON.", { cause: error });
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The Phase 11 migration proof was not an object.");
  }
  const row = value as MigrationProofRow;
  if (
    row.revision !== "0022_external_references"
    || typeof row.completion_event_id !== "string"
    || !/^[1-9][0-9]*$/.test(row.completion_event_id)
    || typeof row.completion_generation !== "string"
    || !/^-[1-9][0-9]*$/.test(row.completion_generation)
    || row.generation_matches !== true
    || row.verification_count !== 0
    || row.artifact_count !== 0
    || row.receipt_is_sparse !== true
    || row.receipt_work_matches !== true
    || row.receipt_checkpoint_matches !== true
  ) {
    throw new Error(`The Phase 11 migrated-completion proof was invalid: ${serialized}`);
  }
  if (BigInt(row.completion_generation) !== -BigInt(row.completion_event_id)) {
    throw new Error("The migrated completion generation did not match its negative event ID.");
  }
  return {
    completionEventId: row.completion_event_id,
    completionGeneration: row.completion_generation
  };
}

export async function expireLease(projectId: string, workItemId: string): Promise<void> {
  requireUuid(projectId, "project ID");
  requireUuid(workItemId, "work-item ID");
  const composeProject = requireDisposableE2EComposeProject("Lease expiry");
  const stdout = await runCompose(composeProject, [
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    "mnemonic_e2e",
    "-d",
    "mnemonic_e2e",
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    "UPDATE work_leases AS lease "
      + "SET acquired_at = clock_timestamp() - interval '3 seconds', "
      + "renewed_at = clock_timestamp() - interval '2 seconds', "
      + "expires_at = clock_timestamp() - interval '1 second' "
      + "FROM work_items AS work "
      + `WHERE lease.work_item_id = '${workItemId}'::uuid `
      + "AND work.id = lease.work_item_id "
      + `AND work.project_id = '${projectId}'::uuid;`
  ]);
  if (!stdout.includes("UPDATE 1")) {
    throw new Error(`Expected one disposable lease to expire, received: ${stdout.trim()}`);
  }
}

export async function seedMigratedHistoricalCompletion(): Promise<E2EState> {
  const composeProject = requireDisposableE2EComposeProject("Offline historical migration acceptance");
  const count = await queryDatabase(composeProject, "SELECT count(*) FROM projects;");
  if (count !== "0") throw new Error("Historical E2E setup requires a fresh disposable database with no projects.");
  await runCompose(composeProject, ["stop", "web", "api"]);
  try {
    await runCompose(composeProject, ["run", "--rm", "--no-deps", "api", "alembic", "downgrade", "0018_repository_freshness"]);
    const output = await runCompose(composeProject, [
      "run", "--rm", "--no-deps", "--volume",
      `${resolve(process.cwd(), "../scripts/seed_e2e_historical_completion.py")}:/tmp/seed_e2e_historical_completion.py:ro`,
      "api", "python", "/tmp/seed_e2e_historical_completion.py", "--run-id", crypto.randomUUID()
    ]);
    const seed = JSON.parse(output.trim()) as E2EState;
    const projectId = seed.projectId;
    const workItemId = seed.historicalCompletion.workItemId;
    const checkpointId = seed.historicalCompletion.completionCheckpointId;
    const clientOperationId = seed.historicalCompletion.clientOperationId;
    requireUuid(projectId, "project ID"); requireUuid(workItemId, "work-item ID");
    requireUuid(checkpointId, "completion checkpoint ID"); requireUuid(clientOperationId, "client operation ID");
    await runCompose(composeProject, ["run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head"]);
    const serializedProof = await queryDatabase(
      composeProject,
      `
        SELECT pg_catalog.json_build_object(
          'revision', (SELECT version_num FROM alembic_version),
          'completion_event_id', event.id::text,
          'completion_generation', checkpoint.completion_generation::text,
          'generation_matches', checkpoint.completion_generation = -event.id,
          'verification_count', (
            SELECT pg_catalog.count(*)::integer
            FROM verification_results AS result
            WHERE result.work_item_id = work.id
              AND result.completion_checkpoint_id = checkpoint.id
          ),
          'artifact_count', (
            SELECT pg_catalog.count(*)::integer
            FROM artifact_references AS artifact
            WHERE artifact.work_item_id = work.id
              AND artifact.completion_checkpoint_id = checkpoint.id
          ),
          'receipt_is_sparse', NOT (operation.response_body ? 'completion_evidence'),
          'receipt_work_matches',
            operation.response_body #>> '{work_item,id}' = work.id::text,
          'receipt_checkpoint_matches',
            operation.response_body #>> '{checkpoint,id}' = checkpoint.id::text
        )::text
        FROM work_items AS work
        JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = work.id
         AND checkpoint.id = '${checkpointId}'::uuid
         AND checkpoint.kind = 'completion'
        JOIN work_events AS event
          ON event.work_item_id = work.id
         AND event.checkpoint_id = checkpoint.id
         AND event.event_type = 'work_completed'
        JOIN client_operations AS operation
          ON operation.project_id = work.project_id
         AND operation.client_operation_id = '${clientOperationId}'::uuid
         AND operation.operation_kind = 'complete_work'
         AND operation.state = 'completed'
        WHERE work.project_id = '${projectId}'::uuid
          AND work.id = '${workItemId}'::uuid;
      `
    );
    const proof = parseMigrationProof(serializedProof);
    return { ...seed, historicalCompletion: { ...seed.historicalCompletion, ...proof } };
  } finally {
    await runCompose(composeProject, ["up", "-d", "--wait", "api", "web"]);
  }
}
