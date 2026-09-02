import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function expireLease(projectId: string, workItemId: string): Promise<void> {
  if (!UUID.test(projectId)) throw new Error("Refusing to use a malformed project ID.");
  if (!UUID.test(workItemId)) throw new Error("Refusing to expire a malformed work-item ID.");
  const composeProject = process.env.MNEMONIC_E2E_COMPOSE_PROJECT;
  if (!composeProject?.startsWith("mnemonic-e2e-")) {
    throw new Error("Lease expiry requires the disposable E2E Compose stack.");
  }
  const { stdout } = await execFileAsync("docker", [
    "compose",
    "-p",
    composeProject,
    "-f",
    "../compose.e2e.yaml",
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
