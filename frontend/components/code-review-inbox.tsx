"use client";

import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import {
  decodeReviewQueuePage,
  type ReviewQueuePage,
} from "@/lib/code-reviews";

export default function CodeReviewInbox({
  projectId,
  refreshSignal,
  onOpen,
}: {
  projectId: string;
  refreshSignal: number;
  onOpen: (id: string) => void;
}) {
  const [kind, setKind] = useState<"reviews" | "follow-ups">("reviews");
  const [available, setAvailable] = useState(false);
  const [page, setPage] = useState<ReviewQueuePage | null>(null);
  const [cursor, setCursor] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    setCursor("");
    setPage(null);
  }, [projectId, kind, available, refreshSignal]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      state: kind === "reviews" ? "requested" : "pending",
      limit: "20",
    });
    if (kind === "reviews")
      params.set("availability", available ? "unclaimed" : "all");
    if (cursor) params.set("after", cursor);
    api<unknown>(
      `/projects/${projectId}/${kind === "reviews" ? "code-reviews" : "work-agent-follow-ups"}?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted)
          setPage(decodeReviewQueuePage(value, projectId, kind));
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, refreshSignal, kind, available, cursor, reload]);
  useEffect(() => {
    const expiries =
      page?.items
        .flatMap((row) => (row.lease ? [Date.parse(row.lease.expires_at)] : []))
        .filter((expiry) => expiry > Date.now()) ?? [];
    if (!expiries.length) return;
    const timer = window.setTimeout(
      () => setReload((value) => value + 1),
      Math.min(
        2_147_483_647,
        Math.max(0, Math.min(...expiries) - Date.now()) + 50,
      ),
    );
    return () => window.clearTimeout(timer);
  }, [page]);
  return (
    <section className="review-inbox" aria-label="Review queue">
      <div className="review-section-heading">
        <h3>Code reviews</h3>
        <button
          className="button button-secondary"
          type="button"
          onClick={() => {
            setCursor("");
            setReload((value) => value + 1);
          }}
        >
          Refresh reviews
        </button>
      </div>
      <p className="field-hint">
        Reviews and unanswered recommendations stay on their original work
        items. Implementation remains Done.
      </p>
      <div className="review-inbox-filters">
        <label className="field">
          Show
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as typeof kind)}
          >
            <option value="reviews">Requested reviews</option>
            <option value="follow-ups">Unanswered recommendations</option>
          </select>
        </label>
        {kind === "reviews" && (
          <label>
            <input
              type="checkbox"
              checked={available}
              onChange={(event) => setAvailable(event.target.checked)}
            />{" "}
            Available to review
          </label>
        )}
      </div>
      {error && (
        <p className="error-notice" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Loading review queue…</p>}
      {!loading && page && page.items.length === 0 && (
        <p>
          No{" "}
          {kind === "reviews"
            ? "requested reviews"
            : "unanswered recommendations"}{" "}
          on this page.
        </p>
      )}
      <ul className="review-queue-list">
        {page?.items.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              className="review-queue-link"
              onClick={() => onOpen(row.work_item_id)}
            >
              <span>{row.title}</span>
              <span className="review-state">
                {kind === "follow-ups"
                  ? "Awaiting author"
                  : row.lease
                    ? "Review in progress"
                    : "Review requested"}
              </span>
            </button>
            <p className="field-hint">
              {row.work_status === "done" ? "Done" : row.work_status}
              {row.remediation_depth
                ? ` · Remediation generation ${row.remediation_depth}`
                : ""}
              {row.lease ? ` · ${row.lease.holder_client}` : ""}
            </p>
          </li>
        ))}
      </ul>
      <div className="review-queue-pagination">
        {cursor && (
          <button
            type="button"
            className="button button-secondary"
            disabled={loading}
            onClick={() => setCursor("")}
          >
            Back to first page
          </button>
        )}
        {page?.has_more && (
          <button
            type="button"
            className="button button-secondary"
            disabled={loading}
            onClick={() => setCursor(page.next_cursor)}
          >
            Next reviews
          </button>
        )}
      </div>
    </section>
  );
}
