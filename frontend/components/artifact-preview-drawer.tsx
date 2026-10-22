"use client";

import { useEffect, useState } from "react";
import DocumentPreviewDrawer, { DocumentPreviewText } from "@/components/document-preview-drawer";
import { artifactPath, type Artifact, type ArtifactSearchMatch } from "@/lib/artifacts";
import { type ArtifactPreviewKind } from "@/lib/artifact-preview";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { artifactDetailOptions, artifactPassageOptions, artifactPreviewCurrent } from "@/lib/artifact-queries";
import { readBoundedBytes } from "@/lib/bounded-json";

export default function ArtifactPreviewDrawer({ artifact, kind, match, onClose }: {
  artifact: Artifact;
  kind: ArtifactPreviewKind;
  match?: ArtifactSearchMatch;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const metadata = useQuery({ ...artifactDetailOptions(client, artifact.project_id, artifact.id), refetchInterval: 30_000 });
  const current = artifactPreviewCurrent(artifact, metadata.data) && !metadata.isError;
  const [rawText, setText] = useState<string | null>(null);
  const passageRead = useQuery(artifactPassageOptions(match, current));
  const text = !current ? null : match?.passage ? passageRead.isError ? null : passageRead.data ?? null : rawText;
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [transferError, setError] = useState("");
  const error = metadata.isError ? "Unable to verify this file. Close the preview and try again."
    : metadata.data && !current ? "This file changed after it was opened. Close the preview and open it again."
      : passageRead.error instanceof Error ? passageRead.error.message : transferError;
  useEffect(() => {
    setText(null); setImageUrl(null); setError("");
    if (!current || match?.passage) return;
    // Raw files can be up to 1 GiB. Keep transfers and object URLs outside the query cache.
    const controller = new AbortController();
    let objectUrl: string | undefined;
    async function load() {
      try {
        const response = await fetch(`${artifactPath(artifact.project_id, artifact.id)}/content`, {
          cache: "no-store", signal: controller.signal
        });
        if (!response.ok) throw new Error();
        const bytes = await readBoundedBytes(response, 1024 * 1024 * 1024);
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
        if (bytes.byteLength !== artifact.size_bytes || sha256 !== artifact.sha256) throw new Error("The file changed during transfer.");
        if (controller.signal.aborted) return;
        if (kind === "image") {
          objectUrl = URL.createObjectURL(new Blob([bytes], { type: artifact.mime_type!.split(";")[0].trim().toLowerCase() }));
          setImageUrl(objectUrl);
        } else {
          setText(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
        }
      } catch (error) {
        if (!controller.signal.aborted) setError("Unable to preview this file. Close the preview and try again, or download the file.");
      }
    }
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.project_id, artifact.id, artifact.revision, artifact.sha256, artifact.mime_type, kind, match, current]);

  return <DocumentPreviewDrawer title={artifact.filename} label={match?.passage ? "Supporting passage" : "Artifact preview"}
    text={error ? null : text} copyable={kind !== "image"} className={match?.passage ? "artifact-passage-drawer" : ""} onClose={onClose}>
    {artifact.sensitive && <p className="artifact-preview-notice"><span className="artifact-sensitive-badge">Sensitive</span> Agents need explicit human approval for each content access.</p>}
    {match?.passage && <p className="artifact-preview-notice">Semantic evidence · revision {match.passage.artifact_revision} · characters {match.passage.start_offset + 1}–{match.passage.end_offset}. Cosine similarity {match.passage.cosine_similarity.toFixed(3)}; not a probability.</p>}
    <div className="artifact-preview-body">
      {error ? <p className="error-notice" role="alert">{error}</p> : kind === "image" && imageUrl ? <>
        <div className="artifact-preview-image"><img src={imageUrl} alt={artifact.filename} onError={() => setError("This image cannot be displayed by your browser. Download the file to view it.")} /></div>
        <a className="artifact-preview-image-link" href={imageUrl} target="_blank" rel="noopener noreferrer">Open image in new tab</a>
      </> : text !== null ? <DocumentPreviewText text={text} markdown={kind === "markdown"} label={match?.passage ? "Supporting passage text" : "Plain text contents"} /> : <p role="status">Loading preview…</p>}
    </div>
  </DocumentPreviewDrawer>;
}
