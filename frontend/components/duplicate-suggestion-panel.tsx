"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import { StatusBadge, formatDateTime } from "@/components/work-item-card";
import { ApiError, api } from "@/lib/api";
import {
  decodeDuplicateSuggestionPage,
  duplicateSuggestionInputFromForm
} from "@/lib/duplicate-suggestions";
import type { DuplicateSuggestionPage, DuplicateSuggestionSignal } from "@/lib/types";

type SuggestionState = {
  phase: "idle" | "invalid" | "loading" | "ready" | "empty" | "stale" | "error";
  page: DuplicateSuggestionPage | null;
  message: string;
};

const initialState: SuggestionState = { phase: "idle", page: null, message: "" };
const signalLabels: Record<DuplicateSuggestionSignal, string> = {
  exact_title: "Exact title",
  lexical: "Related text",
  semantic: "Related meaning"
};

function failureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429 || error.code === "duplicate_suggestion_busy") {
      return "Existing-work comparison is busy. You can create now or check again shortly.";
    }
    if (error.status === 503 || error.code === "duplicate_suggestion_unavailable") {
      return "Existing-work comparison is unavailable. You can still create this work.";
    }
    if (error.status === 0) {
      return "Existing-work comparison is offline. You can still create this work.";
    }
  }
  if (error instanceof Error && error.message.startsWith("Mnemonic returned")) {
    return "Mnemonic returned an invalid existing-work comparison. You can still create this work.";
  }
  return "Existing-work comparison could not finish. You can still create this work.";
}

function scopeLabel(page: DuplicateSuggestionPage): string {
  if (page.semantic_scope === "full_project") return "Semantic scope: full project";
  if (page.semantic_scope === "lexical_shortlist") {
    return "Semantic scope: lexical shortlist";
  }
  return "Lexical comparison · semantic matching unavailable";
}

export default function DuplicateSuggestionPanel({
  projectId,
  draftGeneration,
  disabled,
  onInspect
}: {
  projectId: string;
  draftGeneration: number;
  disabled: boolean;
  onInspect: (workItemId: string) => void;
}) {
  const [state, setState] = useState<SuggestionState>(initialState);
  const controller = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const observedDraftGeneration = useRef(draftGeneration);
  const observedProjectId = useRef(projectId);
  const latestDraftGeneration = useRef(draftGeneration);
  const latestProjectId = useRef(projectId);
  latestDraftGeneration.current = draftGeneration;
  latestProjectId.current = projectId;

  useEffect(() => {
    if (
      observedDraftGeneration.current === draftGeneration
      && observedProjectId.current === projectId
    ) return;
    const projectChanged = observedProjectId.current !== projectId;
    observedDraftGeneration.current = draftGeneration;
    observedProjectId.current = projectId;
    requestGeneration.current += 1;
    controller.current?.abort();
    controller.current = null;
    setState((current) => {
      if (projectChanged) return initialState;
      if (current.phase === "idle") return current;
      return {
        phase: "stale",
        page: current.page,
        message: "Draft changed. Check existing work again for current results."
      };
    });
  }, [draftGeneration, projectId]);

  useEffect(() => () => controller.current?.abort(), []);

  async function checkExistingWork(event: MouseEvent<HTMLButtonElement>): Promise<void> {
    const form = event.currentTarget.form;
    if (!form || !form.reportValidity()) {
      setState({
        phase: "invalid",
        page: null,
        message: "Complete the required create-work fields before checking existing work."
      });
      return;
    }

    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    const generation = ++requestGeneration.current;
    const checkedDraftGeneration = draftGeneration;
    const request = duplicateSuggestionInputFromForm(new FormData(form));
    setState({ phase: "loading", page: null, message: "Checking existing work…" });
    try {
      const value = await api<unknown>(
        `/projects/${encodeURIComponent(projectId)}/duplicate-suggestions`,
        {
          method: "POST",
          body: JSON.stringify(request),
          signal: currentController.signal
        }
      );
      const page = decodeDuplicateSuggestionPage(value, request);
      if (
        currentController.signal.aborted
        || generation !== requestGeneration.current
        || checkedDraftGeneration !== latestDraftGeneration.current
        || projectId !== latestProjectId.current
      ) return;
      setState({
        phase: page.items.length ? "ready" : "empty",
        page,
        message: page.items.length
          ? `${page.items.length} possible existing work ${page.items.length === 1 ? "group" : "groups"}.`
          : "No possible existing work was found."
      });
    } catch (error) {
      if (
        currentController.signal.aborted
        || generation !== requestGeneration.current
        || checkedDraftGeneration !== latestDraftGeneration.current
        || projectId !== latestProjectId.current
      ) return;
      setState({ phase: "error", page: null, message: failureMessage(error) });
    } finally {
      if (controller.current === currentController) controller.current = null;
    }
  }

  const stale = state.phase === "stale";
  return <section
    className={`duplicate-suggestions ${stale ? "duplicate-suggestions-stale" : ""}`}
    aria-labelledby="duplicate-suggestion-heading"
    aria-busy={state.phase === "loading"}
  >
    <div className="duplicate-suggestion-heading">
      <div>
        <h3 id="duplicate-suggestion-heading">Possible existing work — compare manually</h3>
        <p>This advisory check never selects, merges, or changes work.</p>
      </div>
      <button
        type="button"
        className="button button-secondary"
        disabled={disabled || state.phase === "loading"}
        onClick={(event) => void checkExistingWork(event)}
      >{state.phase === "loading" ? "Checking…" : "Check existing work"}</button>
    </div>

    {state.phase === "idle" && <p className="duplicate-suggestion-state">
      Check only when you want a fresh comparison; typing does not send draft content.
    </p>}
    {state.phase === "loading" && <p className="duplicate-suggestion-state" role="status">
      <span className="spinner" />{state.message}
    </p>}
    {state.phase !== "idle" && state.phase !== "loading" && <p
      className={`duplicate-suggestion-state ${state.phase === "error" || state.phase === "invalid" ? "is-error" : ""}`}
      role={state.phase === "error" || state.phase === "invalid" ? "alert" : "status"}
    >{state.message}</p>}

    {state.page && <>
      <div className="duplicate-suggestion-scope" aria-label="Suggestion comparison scope">
        <span>{scopeLabel(state.page)}</span>
        {state.page.exact_title_group_total > 0 && <span>
          {state.page.exact_title_group_total} exact-title {state.page.exact_title_group_total === 1 ? "group" : "groups"}
          {state.page.omitted_exact_title_group_count > 0
            ? ` · ${state.page.omitted_exact_title_group_count} omitted by limit`
            : ""}
        </span>}
      </div>
      <ol className="duplicate-suggestion-list">
        {state.page.items.map((suggestion) => {
          const candidate = suggestion.canonical_work;
          const titleId = `duplicate-candidate-${candidate.work_item_id}`;
          const memberIsCanonical = candidate.work_item_id.toLowerCase()
            === suggestion.matched_member.id.toLowerCase();
          return <li key={candidate.work_item_id}>
            <article className="duplicate-suggestion-card" aria-labelledby={titleId}>
              <div className="duplicate-suggestion-card-heading">
                <StatusBadge status={candidate.status} />
                <span className="duplicate-suggestion-rank">Suggestion {suggestion.rank}</span>
              </div>
              <h4 id={titleId}><bdi dir="auto">{candidate.title}</bdi></h4>
              <p className="duplicate-suggestion-summary"><bdi dir="auto">{candidate.summary}</bdi></p>
              <code className="duplicate-suggestion-canonical-id">{candidate.work_item_id}</code>
              <dl className="duplicate-suggestion-facts">
                <div><dt>Last activity</dt><dd><time dateTime={candidate.updated_at}>{formatDateTime(candidate.updated_at)}</time></dd></div>
                <div><dt>Duplicate members</dt><dd>{candidate.duplicate_member_count}</dd></div>
              </dl>
              <div className="duplicate-suggestion-match" role="note">
                <span>{memberIsCanonical ? "Matched canonical work" : "Matched duplicate member"}</span>
                <bdi dir="auto">{suggestion.matched_member.title}</bdi>
                <code>{suggestion.matched_member.id}</code>
              </div>
              <div className="duplicate-suggestion-signals" aria-label="Categorical match signals">
                {suggestion.signals.map((signal) => <span key={signal}>{signalLabels[signal]}</span>)}
              </div>
              <button
                type="button"
                className="button button-secondary duplicate-suggestion-inspect"
                aria-describedby={titleId}
                onClick={() => onInspect(candidate.work_item_id)}
              >Inspect existing work</button>
            </article>
          </li>;
        })}
      </ol>
    </>}
  </section>;
}

export { failureMessage as duplicateSuggestionFailureMessage, scopeLabel as suggestionScopeLabel };
