"use client";

import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "@/components/dashboard-link";
import { KnowledgePagination, KnowledgeReadError } from "@/components/knowledge-shared";
import { commitLookupOptions } from "@/lib/knowledge-queries";
import { validCommitHash, type DecisionScope } from "@/lib/knowledge";

export function ScopeEvidence({ entry, projectId }: { entry: Pick<DecisionScope, "commit_hash" | "repository" | "completion_report_id" | "merged_commit_hashes" | "work_item_id" | "agent_session_id">; projectId: string }) {
  return <div className="decision-scope-evidence">
    <span className="break-all">Repository: {entry.repository ?? "Unknown (not recorded)"}</span>
    <code className="break-all">{entry.commit_hash}</code>
    {!!entry.merged_commit_hashes?.length && <div>Registered merged commits:<ul>{entry.merged_commit_hashes.map((hash) => <li key={hash}><code className="break-all">{hash}</code></li>)}</ul></div>}
    {entry.work_item_id && <Link href={`/work-items?project=${projectId}&work=${entry.work_item_id}`}>Work item {entry.work_item_id}</Link>}
    {entry.completion_report_id && <span className="break-all">Completion report: {entry.completion_report_id}</span>}
    {entry.agent_session_id && <span className="break-all">Session: {entry.agent_session_id}</span>}
  </div>;
}

export default function CommitLookup({ projectId, onDecision }: { projectId: string; onDecision: (id: string, revision: number) => void }) {
  const [repository, setRepository] = useState("");
  const [hash, setHash] = useState("");
  const [query, setQuery] = useState<{ repository: string; hash: string } | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const result = useQuery(commitLookupOptions(projectId, query?.repository ?? "", query?.hash ?? "", offset, Boolean(query)));
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!repository.trim() || !validCommitHash(hash.trim())) { setError("Enter the registered repository identity and a full 40- or 64-character commit hash."); return; }
    setError(""); setOffset(0); setQuery({ repository: repository.trim(), hash: hash.trim() });
  }
  return <details className="knowledge-commit-lookup">
    <summary>Find decisions cited by a commit</summary>
    <p className="field-hint">Search a registered repository and original or merged commit hash. These are recorded citations; Mnemonic does not verify Git history. Older records without a repository cannot appear in this lookup.</p>
    <form className="form-stack" onSubmit={submit}>
      <label className="field">Repository identity<input value={repository} onChange={(event) => setRepository(event.target.value)} required maxLength={500} placeholder="github.com/organization/repository" /></label>
      <label className="field">Commit hash<input value={hash} onChange={(event) => setHash(event.target.value)} required maxLength={64} className="mono" /></label>
      <button type="submit" className="button button-secondary">Find citations</button>
    </form>
    {error && <p role="alert" className="error-notice">{error}</p>}
    {query && (result.isError ? <KnowledgeReadError error={result.error} retry={() => void result.refetch()} /> : result.isPending ? <p role="status">Looking up citations…</p> : <>
      {!result.data.items.length && <p>No registered citations found. This does not establish that no decisions influenced this commit.</p>}
      <ol className="decision-history">{result.data.items.map((entry) => <li key={entry.id}><button className="text-button" onClick={() => onDecision(entry.decision_id, entry.revision)}>{entry.decision_id}-{entry.revision}</button><ScopeEvidence entry={entry} projectId={projectId} /></li>)}</ol>
      <KnowledgePagination page={result.data} noun="citations" busy={result.isFetching} onOffset={setOffset} />
    </>)}
  </details>;
}
