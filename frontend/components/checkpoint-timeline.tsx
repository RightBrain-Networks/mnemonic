import type { Checkpoint, Page } from "@/lib/types";
import { checkpointKindLabels, migrationWarning } from "@/lib/work-item-view";
import { clientLabel, formatDate } from "@/components/work-item-card";
import CheckpointRepositoryDeclaration from "@/components/checkpoint-repository-declaration";

const PAGE_SIZE = 5;

function safeSessionUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

type Props = {
  page: Page<Checkpoint> | null;
  offset: number;
  currentCheckpointId: string;
  loading: boolean;
  error: string;
  onOffset: (offset: number) => void;
  onReload: () => void;
};

export default function CheckpointTimeline({
  page,
  offset,
  currentCheckpointId,
  loading,
  error,
  onOffset,
  onReload
}: Props) {
  return <section className="progress-section checkpoint-section" aria-labelledby="checkpoint-title">
    <div className="progress-heading">
      <div><span className="section-label">IMMUTABLE HISTORY</span><h4 id="checkpoint-title">Session checkpoints</h4></div>
      <span>{page?.total ?? 0} total</span>
    </div>
    <p className="checkpoint-help">Checkpoints are append-only. Corrections and new findings are recorded as another checkpoint.</p>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onReload}>Try again</button></div>}
    {loading ? <div className="progress-loading" role="status"><span className="spinner" />Loading checkpoints…</div> :
      page?.items.length ? <div className="checkpoint-list">
        {page.items.map((checkpoint) => {
          const warning = migrationWarning(checkpoint.migration_origin);
          const sessionUrl = safeSessionUrl(checkpoint.source_session_url);
          return <article className={`checkpoint ${checkpoint.id === currentCheckpointId ? "checkpoint-current" : ""}`} key={checkpoint.id}>
            <div className="checkpoint-header">
              <div>
                <span className={`checkpoint-kind checkpoint-kind-${checkpoint.kind}`}>{checkpointKindLabels[checkpoint.kind]}</span>
                {checkpoint.id === currentCheckpointId && <span className="current-chip">Current context</span>}
              </div>
              <time dateTime={checkpoint.created_at}>{formatDate(checkpoint.created_at)}</time>
            </div>
            {warning && <div className="migration-warning" role="note">{warning}</div>}
            <pre className="checkpoint-body" tabIndex={0}>{checkpoint.prompt}</pre>
            <dl className="checkpoint-provenance">
              <div><dt>Client</dt><dd>{clientLabel(checkpoint.source_client)}</dd></div>
              <div><dt>Session</dt><dd className="mono break-all">{checkpoint.source_session_id}</dd></div>
              <div><dt>Model</dt><dd>{checkpoint.source_model || "Not recorded"}</dd></div>
              {sessionUrl && <div className="span-two"><dt>Original session</dt><dd><a className="text-link" href={sessionUrl} target="_blank" rel="noopener noreferrer">Open session ↗</a></dd></div>}
              <div className="span-two"><dt>Tags</dt><dd className="tag-list">{checkpoint.tags.length ? checkpoint.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>) : "No tags"}</dd></div>
            </dl>
            <CheckpointRepositoryDeclaration checkpoint={checkpoint} />
            {Object.keys(checkpoint.source_metadata).length > 0 && <details className="metadata-details"><summary>Extra metadata</summary><pre>{JSON.stringify(checkpoint.source_metadata, null, 2)}</pre></details>}
          </article>;
        })}
      </div> : !error && <p className="no-comments">No checkpoints are available.</p>}
    {!loading && page && page.total > PAGE_SIZE && <nav className="pagination checkpoint-pagination" aria-label="Checkpoint pages">
      <span>Showing {offset + 1}–{Math.min(offset + page.items.length, page.total)} of {page.total}</span>
      <div>
        <button className="button button-secondary" type="button" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
        <button className="button button-secondary" type="button" disabled={offset + page.items.length >= page.total} onClick={() => onOffset(offset + PAGE_SIZE)}>Next</button>
      </div>
    </nav>}
  </section>;
}

export { PAGE_SIZE as CHECKPOINT_PAGE_SIZE };
