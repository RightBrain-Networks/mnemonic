"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationScope,
} from "@/lib/mutation-intent";
import {
  decodeCodeReviewDetail,
  decodeReviewQueuePage,
  decodeWorkFollowUpDetail,
  validFollowUpAnswer,
  type CodeReviewDetail,
  type FollowUpAnswerInput,
  type ReviewQueuePage,
  type WorkFollowUpDetail,
} from "@/lib/code-reviews";
import type { WorkContext } from "@/lib/types";
import CodeReviewHandoffEditor, {
  emptyReviewHandoff,
} from "@/components/code-review-handoff-editor";
import { formatDateTime } from "@/components/work-item-card";

function ReviewResult({
  detail,
  onOpen,
}: {
  detail: CodeReviewDetail;
  onOpen: (id: string) => void;
}) {
  const { review, handoff, result, remediation } = detail;
  return (
    <article className="review-record">
      <div className="review-section-heading">
        <h4>
          {review.request_reason === "mandatory" ? "Mandatory" : "Recommended"}{" "}
          review
        </h4>
        <span className={`review-state review-state-${review.state}`}>
          {review.state}
        </span>
      </div>
      <p>
        Requested {formatDateTime(review.created_at)} ·{" "}
        {review.requesting_client}
        {result ? ` · ${result.mode} review (reviewer reported)` : ""}
      </p>
      {detail.source_work_state.deleted && (
        <p className="field-hint">
          The source work item was deleted. Its review history is retained.
        </p>
      )}
      <p className="field-hint">
        Done records implementation completion. Review does not block
        dependencies or approve a release.
      </p>
      <details>
        <summary>Pinned repository scope</summary>
        {detail.scope.repositories.map((row) => (
          <dl className="review-scope metadata-grid" key={row.repository_key}>
            <div>
              <dt>Repository</dt>
              <dd>{row.repository_key}</dd>
            </div>
            <div>
              <dt>Location</dt>
              <dd className="mono break-all">
                {row.repository_url ?? row.checkout_path}
              </dd>
            </div>
            <div>
              <dt>Base</dt>
              <dd className="mono break-all">{row.base_commit}</dd>
            </div>
            <div>
              <dt>Head</dt>
              <dd className="mono break-all">{row.head_commit}</dd>
            </div>
          </dl>
        ))}
      </details>
      <details className="review-handoff">
        <summary>Warm review handoff</summary>
        <p className="review-prose">{handoff.change_summary}</p>
        {(
          [
            ["decisions", "Decisions and reasons"],
            ["focus_areas", "Areas of concern"],
            ["traps", "Implementation and testing traps"],
          ] as const
        ).map(([key, label]) => (
          <section key={key}>
            <h5>{label}</h5>
            {handoff[key].length ? (
              <ul>
                {handoff[key].map((text, index) => (
                  <li className="review-prose" key={index}>
                    {text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="field-hint">None recorded.</p>
            )}
          </section>
        ))}
        <h5>Validation and limitations</h5>
        <p className="review-prose">{handoff.validation_summary}</p>
      </details>
      {result && (
        <section className="review-findings">
          <h4>Review result</h4>
          <p className="review-prose">{result.summary}</p>
          <p>
            {result.findings.length} actionable finding
            {result.findings.length === 1 ? "" : "s"} · {result.actor_client} ·{" "}
            {formatDateTime(result.created_at)}
          </p>
          {result.limitations.length > 0 && (
            <>
              <h5>Limitations</h5>
              <ul>
                {result.limitations.map((text, index) => (
                  <li className="review-prose" key={index}>
                    {text}
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.findings.map((finding) => (
            <details key={finding.finding_key} className="review-finding">
              <summary>
                <span
                  className={`review-severity severity-${finding.severity}`}
                >
                  {finding.severity}
                </span>{" "}
                {finding.finding_key} · {finding.title}
              </summary>
              <p className="mono break-all">
                {finding.repository_key}:{finding.path}
                {finding.start_line
                  ? `:${finding.start_line}${finding.end_line ? `–${finding.end_line}` : ""}`
                  : ""}{" "}
                ({finding.location_side})
              </p>
              <dl>
                {(
                  [
                    ["problem", "Defect"],
                    ["triggering_conditions", "Triggering conditions"],
                    ["impact", "Impact"],
                    ["evidence", "Evidence / reproduction"],
                    ["recommended_verification", "Verify the fix"],
                  ] as const
                ).map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd className="review-prose">{finding[key]}</dd>
                  </div>
                ))}
              </dl>
            </details>
          ))}
          {remediation && (
            <button
              type="button"
              className="button button-primary"
              onClick={() => onOpen(remediation.remediation_work_item_id)}
            >
              Open remediation · all findings
            </button>
          )}
          {!remediation && <p>No remediation work item was needed.</p>}
        </section>
      )}
    </article>
  );
}

function FollowUp({
  detail,
  onChanged,
}: {
  detail: WorkFollowUpDetail;
  onChanged: () => Promise<void> | void;
}) {
  const { follow_up: question, answer } = detail;
  const registry = useMutationIntentRegistry();
  const [recommend, setRecommend] = useState<"" | "yes" | "no">("");
  const [rationale, setRationale] = useState("");
  const [handoff, setHandoff] = useState(emptyReviewHandoff);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [session, setSession] = useState("");
  useEffect(() => {
    setSession(dashboardSessionId());
  }, []);
  const owner =
    question.origin_client === "dashboard" &&
    question.origin_session_id === session;
  const workKey = mutationWorkKey(question.project_id, question.work_item_id);
  const scope = useMutationScope({ conflictKeys: [workKey] }, registry);
  const input: FollowUpAnswerInput = {
    kind: "code_review_recommendation",
    recommend_review: recommend === "yes",
    rationale,
    ...(recommend === "yes" ? { code_review_handoff: handoff } : {}),
  };
  async function submit() {
    if (!recommend || !validFollowUpAnswer(input) || !owner) return;
    setSaving(true);
    setError("");
    try {
      await registry.execute({
        kind: "respond_to_work_follow_up",
        slot: `review-answer:${question.project_id}:${question.id}`,
        projectId: question.project_id,
        conflictKeys: [workKey],
        method: "POST",
        path: `/projects/${question.project_id}/work-items/${question.work_item_id}/agent-follow-ups/${question.id}/answer`,
        payload: {
          expected_follow_up_version: question.version,
          actor: {
            actor_client: "dashboard",
            actor_session_id: session,
            actor_model: null,
          },
          answer: input,
        },
      });
      await onChanged();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }
  return (
    <article className="review-follow-up">
      <div className="review-section-heading">
        <h4>Review recommendation</h4>
        <span className="review-state">{question.state}</span>
      </div>
      <p className="review-prose">{question.question}</p>
      <p className="field-hint">
        Requested from {question.origin_client} ·{" "}
        {formatDateTime(question.created_at)}. This is separate from human
        approval gates.
      </p>
      {answer && (
        <>
          <p>
            <strong>
              {answer.recommend_review
                ? "Review recommended"
                : "Review not recommended"}
            </strong>
          </p>
          <p className="review-prose">{answer.rationale}</p>
          <p className="field-hint">
            Answered by {answer.actor_client} ·{" "}
            {formatDateTime(answer.created_at)}
          </p>
        </>
      )}
      {question.state === "superseded" && (
        <p>
          This question was superseded when the source was reopened. No answer
          was inferred.
        </p>
      )}
      {question.state === "pending" && !owner && (
        <p className="review-recovery">
          The originating session must answer this question. Resume that
          session, or explicitly reopen the source to supersede this question
          and complete it again with accurate attribution. Another session
          cannot supply the author's answer.
        </p>
      )}
      {question.state === "pending" && owner && (
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="field">
            Do you recommend a review?
            <select
              required
              value={recommend}
              disabled={saving || scope.blocked}
              onChange={(event) =>
                setRecommend(event.target.value as typeof recommend)
              }
            >
              <option value="">Choose an answer</option>
              <option value="yes">Yes, request a review</option>
              <option value="no">No, a review is unnecessary</option>
            </select>
          </label>
          <label className="field">
            Reason
            <textarea
              required
              rows={3}
              maxLength={2000}
              disabled={saving || scope.blocked}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </label>
          {recommend === "yes" && (
            <CodeReviewHandoffEditor
              value={handoff}
              onChange={setHandoff}
              disabled={saving || scope.blocked}
            />
          )}
          {error && (
            <p className="error-notice" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            className="button button-primary"
            disabled={
              saving ||
              scope.blocked ||
              !recommend ||
              !validFollowUpAnswer(input)
            }
          >
            {saving ? "Recording…" : "Record recommendation"}
          </button>
          {scope.blocked && (
            <p role="status">
              Use the pending-action recovery controls to retry the same answer.
            </p>
          )}
        </form>
      )}
    </article>
  );
}

export default function CodeReviewPanel({
  context,
  allowRemediationReviews,
  refreshSignal,
  onChanged,
  onOpen,
}: {
  context: WorkContext;
  allowRemediationReviews?: boolean;
  refreshSignal: number;
  onChanged: () => void | Promise<void>;
  onOpen: (id: string) => void;
}) {
  const projectId = context.work_item.project_id,
    workId = context.work_item.id;
  const [review, setReview] = useState<CodeReviewDetail | null>(null);
  const [follow, setFollow] = useState<WorkFollowUpDetail | null>(null);
  const [pages, setPages] = useState<{
    reviews: ReviewQueuePage | null;
    questions: ReviewQueuePage | null;
  }>({ reviews: null, questions: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const reviewSelection = useRef(0);
  const questionSelection = useRef(0);
  const base = `/projects/${projectId}/work-items/${workId}`;
  async function showReview(id: string) {
    const request = generation.current;
    const selection = ++reviewSelection.current;
    try {
      const value = await api<unknown>(`${base}/code-reviews/${id}`);
      const next = decodeCodeReviewDetail(value, projectId, workId, id);
      if (
        request === generation.current &&
        selection === reviewSelection.current
      ) {
        setReview(next);
        setError("");
      }
    } catch (reason) {
      if (
        request === generation.current &&
        selection === reviewSelection.current
      )
        setError(errorMessage(reason));
    }
  }
  async function showQuestion(id: string) {
    const request = generation.current;
    const selection = ++questionSelection.current;
    try {
      const value = await api<unknown>(`${base}/agent-follow-ups/${id}`);
      const next = decodeWorkFollowUpDetail(value, projectId, workId, id);
      if (
        request === generation.current &&
        selection === questionSelection.current
      ) {
        setFollow(next);
        setError("");
      }
    } catch (reason) {
      if (
        request === generation.current &&
        selection === questionSelection.current
      )
        setError(errorMessage(reason));
    }
  }
  useEffect(() => {
    const request = ++generation.current;
    const controller = new AbortController();
    setReview(null);
    setFollow(null);
    setPages({ reviews: null, questions: null });
    setLoading(true);
    setError("");
    Promise.all([
      api<unknown>(
        `/projects/${projectId}/code-reviews?state=all&work_item_id=${workId}&limit=20`,
        { signal: controller.signal },
      ),
      api<unknown>(
        `/projects/${projectId}/work-agent-follow-ups?state=all&work_item_id=${workId}&limit=20`,
        { signal: controller.signal },
      ),
    ])
      .then(async ([reviews, questions]) => {
        if (request !== generation.current) return;
        const reviewPage = decodeReviewQueuePage(reviews, projectId, "reviews"),
          questionPage = decodeReviewQueuePage(
            questions,
            projectId,
            "follow-ups",
          );
        setPages({ reviews: reviewPage, questions: questionPage });
        const reviewId =
          context.code_review_context?.current_review?.id ??
          reviewPage.items[0]?.id;
        const questionId =
          context.code_review_context?.pending_follow_up?.id ??
          questionPage.items[0]?.id;
        await Promise.all([
          reviewId ? showReview(reviewId) : undefined,
          questionId ? showQuestion(questionId) : undefined,
        ]);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (request === generation.current) setLoading(false);
      });
    return () => {
      generation.current += 1;
      controller.abort();
    };
  }, [projectId, workId, refreshSignal, reload]);
  async function more(kind: "reviews" | "questions") {
    const page = pages[kind];
    if (!page?.has_more) return;
    const request = generation.current;
    try {
      const url = kind === "reviews" ? "code-reviews" : "work-agent-follow-ups";
      const value = await api<unknown>(
        `/projects/${projectId}/${url}?state=all&work_item_id=${workId}&limit=20&after=${encodeURIComponent(page.next_cursor)}`,
      );
      const next = decodeReviewQueuePage(
        value,
        projectId,
        kind === "reviews" ? "reviews" : "follow-ups",
      );
      if (request === generation.current)
        setPages((current) => ({
          ...current,
          [kind]: {
            ...next,
            items: [
              ...page.items,
              ...next.items.filter(
                (row) => !page.items.some((old) => old.id === row.id),
              ),
            ],
          },
        }));
    } catch (reason) {
      if (request === generation.current) setError(errorMessage(reason));
    }
  }
  const origin = context.code_review_context?.remediation_origin;
  return (
    <section className="code-review-panel" aria-label="Code reviews">
      {origin && (
        <aside className="review-provenance">
          <p>Remediation generation {origin.depth}</p>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => onOpen(origin.source_work_item_id)}
          >
            Remediation of original work
          </button>
          {origin.depth === 2 ? (
            <p>Further reviews are disabled for this remediation generation.</p>
          ) : (
            <p>
              {allowRemediationReviews === undefined
                ? "Loading the current remediation review policy…"
                : allowRemediationReviews
                  ? "One review of this remediation is enabled. Project priority thresholds still apply at Done."
                  : "Reviews of remediation work are disabled in project settings."}
            </p>
          )}
        </aside>
      )}
      {loading && <p role="status">Loading code review history…</p>}
      {error && (
        <div className="error-notice" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setReload((value) => value + 1)}
          >
            Retry code reviews
          </button>
        </div>
      )}
      {follow && (
        <FollowUp
          key={follow.follow_up.id}
          detail={follow}
          onChanged={async () => {
            setReload((value) => value + 1);
            await onChanged();
          }}
        />
      )}
      {review && <ReviewResult detail={review} onOpen={onOpen} />}
      {!loading && !error && !review && !follow && (
        <p>
          No code review has been requested for this work item. Future Done
          closeouts follow project review settings.
        </p>
      )}
      {(
        [
          ["reviews", "Review history"],
          ["questions", "Recommendation history"],
        ] as const
      ).map(
        ([key, title]) =>
          pages[key] && (
            <details key={key} className="review-history">
              <summary>
                {title} · {pages[key]!.items.length}
              </summary>
              <ul>
                {pages[key]!.items.map((row) => (
                  <li key={row.id}>
                    <button
                      className="button button-secondary"
                      type="button"
                      onClick={() =>
                        void (key === "reviews"
                          ? showReview(row.id)
                          : showQuestion(row.id))
                      }
                    >
                      {formatDateTime(row.created_at)} · {row.state}
                    </button>
                  </li>
                ))}
              </ul>
              {pages[key]!.has_more && (
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => void more(key)}
                >
                  Load more history
                </button>
              )}
            </details>
          ),
      )}
    </section>
  );
}
