import { decodeArtifact, decodeArtifactLimitError, type Artifact } from "./artifacts.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { decodeMutationError } from "./mutation-responses.ts";
import { objectValue, sameUuid } from "./wire-guards.ts";

export const ARTIFACT_MUTATION_KINDS = ["upload_artifact", "replace_artifact", "delete_artifact", "update_artifact"] as const;

const DEFINITIVE_ARTIFACT_ERRORS = new Map<number, ReadonlySet<string>>([
  [404, new Set(["project_not_found", "artifact_not_found", "artifact_work_item_not_found", "artifact_related_artifact_not_found"])],
  [409, new Set(["artifact_revision_conflict", "artifact_filename_immutable", "artifact_origin_immutable"])],
  [410, new Set(["artifact_deleted"])],
  [413, new Set(["artifact_too_large"])],
  [415, new Set(["artifact_encoding_unsupported"])],
  [422, new Set([
    "artifact_filename_unsafe", "artifact_header_invalid", "artifact_metadata_invalid",
    "artifact_operation_id_invalid", "artifact_revision_invalid", "artifact_query_forbidden",
    "artifact_length_invalid", "artifact_body_forbidden", "artifact_link_limit", "artifact_self_link",
    "client_operation_secret_echo"
  ])]
]);

export interface ArtifactMutation {
  readonly method: "POST" | "PUT" | "DELETE" | "PATCH";
  readonly path: string;
  readonly projectId: string;
  readonly operationId: string;
  readonly metadata: string;
  readonly file?: File;
  readonly artifactId?: string;
  readonly expectedRevision?: number;
}

export function artifactMetadataHeader(metadata: object): string {
  const encoded = JSON.stringify(metadata).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
  if (encoded.length > 16384) throw new Error("Artifact metadata exceeds the upload limit.");
  return encoded;
}

export type ArtifactMutationOutcome = { type: "success"; artifact: Artifact } | { type: "rejected" | "unresolved" | "safety_conflict" | "disabled"; message: string };

export async function dispatchArtifactMutation(intent: ArtifactMutation, fetcher: typeof fetch = fetch): Promise<ArtifactMutationOutcome> {
  try {
    const headers = new Headers({ "X-Client-Operation-ID": intent.operationId, "X-Artifact-Metadata": intent.metadata });
    if (intent.expectedRevision !== undefined) headers.set("X-Artifact-Expected-Revision", String(intent.expectedRevision));
    if (intent.file) headers.set("Content-Type", "application/octet-stream");
    if (intent.method === "PATCH") {
      headers.delete("X-Artifact-Metadata"); headers.delete("X-Artifact-Expected-Revision");
      headers.set("Content-Type", "application/json");
    }
    const response = await fetcher(intent.path, {
      method: intent.method, headers, body: intent.method === "PATCH" ? intent.metadata : intent.file, cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(300000)
    });
    const value: unknown = await readBoundedJson(response, 1024 * 1024);
    if (!response.ok) {
      const limitError = decodeArtifactLimitError(value, response.status);
      if (limitError) return { type: limitError.code === "artifact_library_disabled" ? "disabled" : "rejected", message: limitError.message };
      const detail = decodeMutationError(value);
      if (detail?.category === "application") {
        if (response.status === 409 && detail.code === "client_operation_conflict") {
          return { type: "safety_conflict", message: detail.message };
        }
        if (detail.code && DEFINITIVE_ARTIFACT_ERRORS.get(response.status)?.has(detail.code)) {
          return { type: "rejected", message: detail.message };
        }
      }
      if (response.status === 422 && detail?.category === "validation") {
        return { type: "rejected", message: detail.message };
      }
      return { type: "unresolved", message: "The outcome is unknown. Retry this same pending action." };
    }
    // Permanent pre-0029 receipts preserve their original wire bytes. Only an
    // explicitly replayed original mutation may omit fields introduced in 0029.
    const row = objectValue(value);
    const historicalReplay = intent.method !== "PATCH"
      && response.headers.get("X-Artifact-Operation-Replayed") === "true"
      && row && !("sensitive" in row) && !("related_artifact_ids" in row);
    const artifact = decodeArtifact(historicalReplay ? { ...row, sensitive: false, related_artifact_ids: [] } : value, intent.projectId);
    const expectedMetadata = JSON.parse(intent.metadata) as { filename?: string; sensitive?: boolean; related_artifact_ids?: string[]; related_work_item_ids?: string[] };
    const expectedRevision = intent.method === "POST" ? 1 : intent.expectedRevision! + (intent.method === "DELETE" ? 0 : 1);
    if (response.status !== (intent.method === "POST" ? 201 : 200)
      || !sameUuid(response.headers.get("X-Client-Operation-ID"), intent.operationId)
      || intent.artifactId && !sameUuid(artifact.id, intent.artifactId)
      || artifact.revision !== expectedRevision
      || (intent.method === "POST" || intent.method === "PUT") && artifact.filename !== expectedMetadata.filename
      || expectedMetadata.sensitive !== undefined && artifact.sensitive !== expectedMetadata.sensitive
      || expectedMetadata.related_artifact_ids?.some((id) => !artifact.related_artifact_ids.some((linked) => sameUuid(id, linked)))
      || expectedMetadata.related_work_item_ids?.some((id) => !sameUuid(id, artifact.originating_work_item_id) && !artifact.related_work_item_ids.some((linked) => sameUuid(id, linked)))
      || intent.method === "DELETE" && (artifact.deleted_at === null || artifact.content_available)
      || intent.method !== "DELETE" && (artifact.deleted_at !== null || !artifact.content_available)
      || (intent.method === "POST" || intent.method === "PUT") && artifact.size_bytes !== intent.file?.size) {
      return { type: "unresolved", message: "The response did not match the pending artifact action. Retry the same action." };
    }
    return { type: "success", artifact };
  } catch { return { type: "unresolved", message: "The outcome is unknown. Retry this same pending action." }; }
}

export function artifactUpdateIntent(artifact: Artifact, changes: { sensitive?: boolean; related_artifact_ids?: string[]; related_work_item_ids?: string[] }, sessionId: string): ArtifactMutation {
  const operationId = crypto.randomUUID();
  return Object.freeze({
    method: "PATCH", path: `/api/artifacts/projects/${artifact.project_id}/artifacts/${artifact.id}`,
    projectId: artifact.project_id, artifactId: artifact.id, expectedRevision: artifact.revision,
    operationId, metadata: JSON.stringify({ ...changes, client_operation_id: operationId,
      expected_revision: artifact.revision, agent_session_id: sessionId, actor_client: "dashboard" })
  });
}
