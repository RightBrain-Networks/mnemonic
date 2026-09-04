"use client";

import { useEffect, useRef, useState } from "react";
import { completionEvidenceApi, errorMessage, workItemPath } from "@/lib/api";
import {
  artifactNavigationHref,
  completionEvidenceDraftAggregateBytes,
  completionEvidenceIssueField,
  completionEvidenceTextSize,
  isExternalArtifactType,
  mergeCompletionEvidencePage,
  type CompletionEvidenceDraft,
  type CompletionEvidenceIssue
} from "@/lib/completion-evidence";
import type {
  ArtifactReferenceDraft,
  VerificationResultDraft
} from "@/lib/completion-evidence";
import type {
  ArtifactReferenceRead,
  ArtifactType,
  CompletionEvidenceEpisodeRead,
  CompletionEvidencePage,
  VerificationOutcome,
  VerificationResultRead,
  VerificationType
} from "@/lib/types";
import { sameUuid } from "@/lib/wire-guards";
import { formatDateTime } from "@/components/work-item-card";

const artifactLabels: Record<ArtifactType, string> = {
  commit: "Commit",
  pull_request: "Pull request",
  branch: "Branch",
  test_run: "Test run",
  repository_path: "Repository path",
  external_issue: "External issue",
  build_artifact: "Build artifact"
};

const outcomeLabels: Record<VerificationOutcome, string> = {
  passed: "Passed",
  failed: "Failed",
  inconclusive: "Inconclusive",
  skipped: "Skipped"
};

function draftKey(): string {
  return globalThis.crypto.randomUUID();
}

function textCounter(value: string, maxCharacters: number, maxBytes: number): string {
  const size = completionEvidenceTextSize(value);
  return `${size.characters}/${maxCharacters} characters · ${size.bytes}/${maxBytes} bytes`;
}

function move<T>(values: readonly T[], index: number, direction: -1 | 1): T[] {
  const destination = index + direction;
  if (destination < 0 || destination >= values.length) return [...values];
  const next = [...values];
  [next[index], next[destination]] = [next[destination]!, next[index]!];
  return next;
}

function FieldError({
  issues,
  family,
  index,
  rowKey,
  field
}: {
  issues: readonly CompletionEvidenceIssue[];
  family: "verification_results" | "artifact_references";
  index: number;
  rowKey: string;
  field: string;
}) {
  const matching = fieldIssue(issues, family, index, field);
  return matching
    ? <span
      className="field-error"
      id={fieldErrorId(family, rowKey, field)}
      role="alert"
    >{matching.message}</span>
    : null;
}

function fieldIssue(
  issues: readonly CompletionEvidenceIssue[],
  family: "verification_results" | "artifact_references",
  index: number,
  field: string
): CompletionEvidenceIssue | undefined {
  return issues.find((entry) => completionEvidenceIssueField(entry, family, index, field));
}

function fieldErrorId(
  family: "verification_results" | "artifact_references",
  rowKey: string,
  field: string
): string {
  return `completion-evidence-${family}-${rowKey}-${field}-error`;
}

function fieldErrorAccessibility(
  issues: readonly CompletionEvidenceIssue[],
  family: "verification_results" | "artifact_references",
  index: number,
  rowKey: string,
  field: string
): { "aria-invalid": boolean; "aria-describedby"?: string } {
  return fieldIssue(issues, family, index, field)
    ? {
      "aria-invalid": true,
      "aria-describedby": fieldErrorId(family, rowKey, field)
    }
    : { "aria-invalid": false };
}

function RowActions({
  label,
  index,
  length,
  disabled,
  onMove,
  onRemove
}: {
  label: string;
  index: number;
  length: number;
  disabled: boolean;
  onMove: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  return <div className="evidence-row-actions">
    <button type="button" className="button button-secondary" disabled={disabled || index === 0} aria-label={`Move ${label} up`} onClick={() => onMove(-1)}>↑</button>
    <button type="button" className="button button-secondary" disabled={disabled || index === length - 1} aria-label={`Move ${label} down`} onClick={() => onMove(1)}>↓</button>
    <button type="button" className="button button-secondary" disabled={disabled} aria-label={`Remove ${label}`} onClick={onRemove}>Remove</button>
  </div>;
}

export function CompletionEvidenceEditor({
  draft,
  issues,
  disabled,
  onChange
}: {
  draft: CompletionEvidenceDraft;
  issues: readonly CompletionEvidenceIssue[];
  disabled: boolean;
  onChange: (draft: CompletionEvidenceDraft) => void;
}) {
  const count = draft.verificationResults.length + draft.artifactReferences.length;
  const bytes = completionEvidenceDraftAggregateBytes(draft);
  const rootIssue = issues.find((entry) => entry.path.length === 1);

  function updateResult(index: number, update: Partial<VerificationResultDraft>) {
    onChange({
      ...draft,
      verificationResults: draft.verificationResults.map((entry, current) => (
        current === index ? { ...entry, ...update } : entry
      ))
    });
  }

  function updateArtifact(index: number, update: Partial<ArtifactReferenceDraft>) {
    onChange({
      ...draft,
      artifactReferences: draft.artifactReferences.map((entry, current) => (
        current === index ? { ...entry, ...update } : entry
      ))
    });
  }

  return <section
    className="completion-evidence-editor"
    aria-labelledby="completion-evidence-editor-title"
    onKeyDown={(event) => {
      if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
        event.preventDefault();
      }
    }}
  >
    <div className="evidence-editor-heading">
      <div>
        <span className="section-label">OPTIONAL COMPLETION EVIDENCE</span>
        <h5 id="completion-evidence-editor-title">Record observed checks and artifact references</h5>
      </div>
      <div className={`evidence-budget ${count > 20 || bytes > 32_768 ? "is-over" : ""}`} aria-live="polite">
        <span>{count}/20 entries</span>
        <span>{bytes.toLocaleString("en-US")}/32,768 bytes</span>
      </div>
    </div>
    <p className="authority-note">Recorded as caller-reported evidence. Mnemonic does not run these checks, verify their claims, execute commands, or fetch artifact references. Do not include secrets or raw logs.</p>

    {draft.verificationResults.map((result, index) => <fieldset className="evidence-edit-row" key={result.key} disabled={disabled}>
      <legend>Verification result {index + 1}</legend>
      <RowActions
        label={`verification result ${index + 1}`}
        index={index}
        length={draft.verificationResults.length}
        disabled={disabled}
        onMove={(direction) => onChange({
          ...draft,
          verificationResults: move(draft.verificationResults, index, direction)
        })}
        onRemove={() => onChange({
          ...draft,
          verificationResults: draft.verificationResults.filter((_, current) => current !== index)
        })}
      />
      <div className="evidence-field-grid">
        <label className="field">Result type
          <select value={result.verificationType} onChange={(event) => updateResult(index, { verificationType: event.target.value as VerificationType })}>
            <option value="observation">Observation</option>
            <option value="command">Command</option>
          </select>
        </label>
        <label className="field">Reported outcome
          <select {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "outcome")} value={result.outcome} onChange={(event) => updateResult(index, { outcome: event.target.value as VerificationOutcome })}>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
            <option value="inconclusive">Inconclusive</option>
            <option value="skipped" disabled={result.verificationType === "command"}>Skipped</option>
          </select>
          <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="outcome" />
        </label>
      </div>
      <label className="field">Name
        <input {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "name")} value={result.name} onChange={(event) => updateResult(index, { name: event.target.value })} />
        <span className="field-hint">{textCounter(result.name, 200, 800)}</span>
        <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="name" />
      </label>
      <label className="field">Result summary
        <textarea {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "summary")} rows={4} value={result.summary} onChange={(event) => updateResult(index, { summary: event.target.value })} />
        <span className="field-hint">{textCounter(result.summary, 4_000, 16_000)}</span>
        <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="summary" />
      </label>
      {result.verificationType === "command" && <>
        <label className="field">Command
          <textarea {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "command")} className="mono" rows={3} value={result.command} onChange={(event) => updateResult(index, { command: event.target.value })} />
          <span className="field-hint">{textCounter(result.command, 4_096, 16_384)} · stored as inert text</span>
          <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="command" />
        </label>
        <label className="field">Exit code <span className="optional">required for passed/failed; absent for inconclusive</span>
          <input {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "exit_code")} className="mono" inputMode="numeric" value={result.exitCode} onChange={(event) => updateResult(index, { exitCode: event.target.value })} />
          <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="exit_code" />
        </label>
      </>}
      <div className="evidence-field-grid">
        <label className="field">Observed at <span className="optional">optional RFC 3339</span>
          <input {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "observed_at")} className="mono" placeholder="2026-09-03T18:01:02Z" value={result.observedAt} onChange={(event) => updateResult(index, { observedAt: event.target.value })} />
          <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="observed_at" />
        </label>
        <label className="field">Observed commit <span className="optional">optional lowercase hex</span>
          <input {...fieldErrorAccessibility(issues, "verification_results", index, result.key, "observed_at_commit")} className="mono" value={result.observedAtCommit} onChange={(event) => updateResult(index, { observedAtCommit: event.target.value })} />
          <FieldError issues={issues} family="verification_results" index={index} rowKey={result.key} field="observed_at_commit" />
        </label>
      </div>
    </fieldset>)}

    {draft.artifactReferences.map((artifact, index) => <fieldset className="evidence-edit-row" key={artifact.key} disabled={disabled}>
      <legend>Artifact reference {index + 1}</legend>
      <RowActions
        label={`artifact reference ${index + 1}`}
        index={index}
        length={draft.artifactReferences.length}
        disabled={disabled}
        onMove={(direction) => onChange({
          ...draft,
          artifactReferences: move(draft.artifactReferences, index, direction)
        })}
        onRemove={() => onChange({
          ...draft,
          artifactReferences: draft.artifactReferences.filter((_, current) => current !== index)
        })}
      />
      <label className="field">Artifact type
        <select value={artifact.artifactType} onChange={(event) => updateArtifact(index, { artifactType: event.target.value as ArtifactType })}>
          {Object.entries(artifactLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="field">Label
        <input {...fieldErrorAccessibility(issues, "artifact_references", index, artifact.key, "label")} value={artifact.label} onChange={(event) => updateArtifact(index, { label: event.target.value })} />
        <span className="field-hint">{textCounter(artifact.label, 200, 800)}</span>
        <FieldError issues={issues} family="artifact_references" index={index} rowKey={artifact.key} field="label" />
      </label>
      <label className="field">Reference
        <input {...fieldErrorAccessibility(issues, "artifact_references", index, artifact.key, "reference")} className="mono" value={artifact.reference} onChange={(event) => updateArtifact(index, { reference: event.target.value })} />
        <span className="field-hint">{isExternalArtifactType(artifact.artifactType)
          ? "Exact canonical https:// URL; credentials, queries, and fragments are rejected."
          : artifact.artifactType === "repository_path"
            ? "Exact relative repository path; globs and traversal are rejected."
            : artifact.artifactType === "branch"
              ? "Exact branch spelling; edge whitespace is rejected."
              : "7–64 lowercase hexadecimal characters."}</span>
        <FieldError issues={issues} family="artifact_references" index={index} rowKey={artifact.key} field="reference" />
      </label>
    </fieldset>)}

    {rootIssue && <div className="error-notice" role="alert"><p>{rootIssue.message}</p></div>}
    <div className="evidence-add-actions">
      <button
        type="button"
        className="button button-secondary"
        disabled={disabled || count >= 20}
        onClick={() => onChange({
          ...draft,
          verificationResults: [...draft.verificationResults, {
            key: draftKey(),
            verificationType: "observation",
            name: "",
            outcome: "passed",
            summary: "",
            command: "",
            exitCode: "",
            observedAt: "",
            observedAtCommit: ""
          }]
        })}
      >Add verification result</button>
      <button
        type="button"
        className="button button-secondary"
        disabled={disabled || count >= 20}
        onClick={() => onChange({
          ...draft,
          artifactReferences: [...draft.artifactReferences, {
            key: draftKey(),
            artifactType: "commit",
            label: "",
            reference: ""
          }]
        })}
      >Add artifact reference</button>
    </div>
  </section>;
}

function VerificationResult({ result }: { result: VerificationResultRead }) {
  return <li className="evidence-read-row">
    <div className="evidence-read-heading">
      <strong><bdi dir="auto">{result.name}</bdi></strong>
      <span className={`evidence-outcome is-${result.outcome}`}>Reported outcome: {outcomeLabels[result.outcome]}</span>
    </div>
    <p><span className="evidence-type">{result.verification_type === "command" ? "Command result" : "Observation"}</span></p>
    <p className="evidence-literal"><bdi dir="auto">{result.summary}</bdi></p>
    {result.verification_type === "command" && <dl className="evidence-read-facts">
      <div><dt>Command (inert text)</dt><dd><pre><bdi dir="auto">{result.command}</bdi></pre></dd></div>
      {result.exit_code !== undefined && <div><dt>Exit code</dt><dd className="mono"><bdi>{result.exit_code}</bdi></dd></div>}
    </dl>}
    {(result.observed_at || result.observed_at_commit) && <dl className="evidence-read-facts">
      {result.observed_at && <div><dt>Observed at</dt><dd><time dateTime={result.observed_at}>{formatDateTime(result.observed_at)}</time></dd></div>}
      {result.observed_at_commit && <div><dt>Observed commit</dt><dd><code><bdi>{result.observed_at_commit}</bdi></code></dd></div>}
    </dl>}
  </li>;
}

function ArtifactReference({ artifact }: { artifact: ArtifactReferenceRead }) {
  const href = artifactNavigationHref(artifact);
  const hostname = href ? new URL(href).hostname : null;
  return <li className="evidence-read-row">
    <div className="evidence-read-heading">
      <strong><bdi dir="auto">{artifact.label}</bdi></strong>
      <span className="evidence-type">{artifactLabels[artifact.artifact_type]}</span>
    </div>
    {href && hostname
      ? <a
        className="evidence-external-link"
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`${artifactLabels[artifact.artifact_type]} on ${hostname} (opens in a new tab)`}
      ><bdi>{hostname}</bdi><span aria-hidden="true"> ↗</span></a>
      : <code className="evidence-inert-reference"><bdi dir="auto">{artifact.reference}</bdi></code>}
    <small>{href
      ? "Caller-reported external locator. Mnemonic has not checked its availability."
      : artifact.artifact_type === "branch"
        ? "Caller-reported mutable branch name."
        : "Caller-reported inert reference. Mnemonic has not resolved it."}</small>
  </li>;
}

function CompletionEpisode({
  episode,
  page
}: {
  episode: CompletionEvidenceEpisodeRead;
  page: CompletionEvidencePage;
}) {
  const current = sameUuid(
    page.current_completion_checkpoint_id,
    episode.completion_checkpoint.id
  );
  const empty = episode.verification_results.length === 0
    && episode.artifact_references.length === 0;
  return <article className={`completion-episode ${current ? "is-current" : ""}`}>
    <header>
      <div>
        <span className="section-label">{current ? "CURRENT COMPLETION" : "PRIOR COMPLETION"}</span>
        <h4>{formatDateTime(episode.completion_checkpoint.created_at)}</h4>
      </div>
      <span className="mono evidence-event-id">Event {episode.completion_event_id}</span>
    </header>
    <dl className="evidence-episode-meta">
      <div><dt>Caller</dt><dd><bdi dir="auto">{episode.completion_checkpoint.source_client}</bdi></dd></div>
      <div><dt>Session</dt><dd className="mono"><bdi dir="auto">{episode.completion_checkpoint.source_session_id}</bdi></dd></div>
      {episode.completion_checkpoint.source_model && <div><dt>Model</dt><dd><bdi dir="auto">{episode.completion_checkpoint.source_model}</bdi></dd></div>}
      {episode.completion_checkpoint.repository_branch && <div><dt>Branch</dt><dd><code><bdi dir="auto">{episode.completion_checkpoint.repository_branch}</bdi></code></dd></div>}
      {episode.completion_checkpoint.verified_against && <div><dt>Checkpoint verified against</dt><dd><code><bdi>{episode.completion_checkpoint.verified_against}</bdi></code></dd></div>}
    </dl>
    {empty
      ? <p className="evidence-empty-episode">No structured completion evidence recorded</p>
      : <>
        {episode.verification_results.length > 0 && <section>
          <h5>Verification results</h5>
          <ol className="evidence-read-list">{episode.verification_results.map((result) => <VerificationResult key={result.id} result={result} />)}</ol>
        </section>}
        {episode.artifact_references.length > 0 && <section>
          <h5>Artifact references</h5>
          <ol className="evidence-read-list">{episode.artifact_references.map((artifact) => <ArtifactReference key={artifact.id} artifact={artifact} />)}</ol>
        </section>}
      </>}
  </article>;
}

export default function CompletionEvidencePanel({
  projectId,
  workItemId,
  refreshSignal
}: {
  projectId: string;
  workItemId: string;
  refreshSignal: number;
}) {
  const [page, setPage] = useState<CompletionEvidencePage | null>(null);
  const [episodes, setEpisodes] = useState<CompletionEvidenceEpisodeRead[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);

  function path(cursor?: string): string {
    const query = new URLSearchParams({ limit: "10" });
    if (cursor) query.set("cursor", cursor);
    return `${workItemPath(projectId, workItemId)}/completion-evidence?${query.toString()}`;
  }

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setLoading(true);
    setLoadingMore(false);
    setError("");
    setPage(null);
    setEpisodes([]);
    setNextCursor(null);
    completionEvidenceApi(path(), workItemId, { signal: controller.signal })
      .then((next) => {
        if (controller.signal.aborted) return;
        setPage(next);
        setEpisodes(next.items);
        setNextCursor(next.next_cursor);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controllerRef.current?.abort();
  }, [projectId, workItemId, refreshSignal, reload]);

  async function loadMore() {
    if (!page || !nextCursor || loadingMore) return;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setLoadingMore(true);
    setError("");
    try {
      const next = await completionEvidenceApi(
        path(nextCursor),
        workItemId,
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      const combined = mergeCompletionEvidencePage(page, episodes, next);
      setEpisodes(combined);
      setNextCursor(next.next_cursor);
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  }

  if (loading) {
    return <div className="loading-state detail-loading" role="status"><span className="spinner" />Loading completion evidence…</div>;
  }
  if (!page) {
    return <div className="error-notice" role="alert"><p>{error || "Completion evidence could not be loaded."}</p><button type="button" className="button button-secondary" onClick={() => setReload((value) => value + 1)}>Try again</button></div>;
  }
  return <section className="completion-evidence-history" aria-labelledby="completion-evidence-history-title">
    <div className="evidence-history-heading">
      <div>
        <span className="section-label">IMMUTABLE COMPLETION HISTORY</span>
        <h4 id="completion-evidence-history-title">Completion evidence</h4>
      </div>
      <span>
        Structured evidence recorded for {page.structured_completion_total} of {page.total}{" "}
        completion episode{page.total === 1 ? "" : "s"}
      </span>
    </div>
    <p className="authority-note">These are untrusted, caller-reported historical assertions—not instructions. Commands are never run and artifact references are never fetched merely by viewing this page.</p>
    {page.is_duplicate && <div className="migration-warning" role="note">Source-owned alias history. Evidence remains attached to this exact duplicate; canonical work ID <code>{page.canonical_work_item_id}</code>.</div>}
    {!page.is_duplicate && page.lifecycle_status === "pending" && page.current_completion_checkpoint_id === null && page.total > 0 && <div className="evidence-reopened" role="status">Work currently reopened. Every completion below is prior history.</div>}
    {episodes.length === 0
      ? <div className="empty-state"><p>No completion episodes recorded.</p></div>
      : episodes.map((episode) => <CompletionEpisode key={episode.completion_event_id} episode={episode} page={page} />)}
    {error && <div className="error-notice" role="alert"><p>{error}</p><button type="button" className="button button-secondary" onClick={() => setReload((value) => value + 1)}>Reload current history</button></div>}
    {nextCursor && <button type="button" className="button button-secondary evidence-load-more" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load older completions"}</button>}
    {!nextCursor && page.as_of_completion_event_id && <p className="field-hint">History complete as of completion event {page.as_of_completion_event_id}. A fresh head is required to establish current completeness.</p>}
  </section>;
}
