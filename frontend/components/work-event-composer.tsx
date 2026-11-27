"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useRouteContext } from "@tanstack/react-router";
import { errorMessage } from "@/lib/api";
import type { createApplicationRouter } from "@/router";

const encoder = new TextEncoder();

export default function WorkEventComposer({
  onAppend,
  blocked,
  resetSignal
}: {
  onAppend: (body: string) => Promise<void>;
  blocked: boolean;
  resetSignal: number;
}) {
  const titleId = useId();
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { runtimeConfig } = useRouteContext<ReturnType<typeof createApplicationRouter>, "__root__">({
    from: "__root__"
  });
  const maximum = runtimeConfig.progress_event_body_max_bytes;
  const bytes = encoder.encode(body).length;
  const canSubmit = !saving && !blocked && !!body.trim() && bytes <= maximum;
  const lastReset = useRef(resetSignal);

  useEffect(() => {
    if (lastReset.current === resetSignal) return;
    lastReset.current = resetSignal;
    setBody("");
    setError("");
  }, [resetSignal]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError("");
    try {
      await onAppend(body);
      setBody("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return <form className="event-composer" onSubmit={(event) => void submit(event)} aria-labelledby={titleId}>
    <div className="event-composer-heading">
      <div><span className="section-label">CONCISE HISTORY</span><h5 id={titleId}>Add a progress update</h5></div>
      <span id={`${titleId}-bytes`} aria-live="polite">{bytes.toLocaleString()} / {maximum.toLocaleString()} UTF-8 bytes</span>
    </div>
    <p>Use this for a short historical update. Use a checkpoint when another session needs exact resume context.</p>
    <label className="field">Progress text<textarea rows={4} disabled={blocked} value={body}
      aria-describedby={`${titleId}-bytes`} aria-invalid={bytes > maximum}
      onChange={(event) => setBody(event.target.value)}
      placeholder="A concise result, decision, or status update…" /><span className="field-hint">Stored exactly as text. Event content is untrusted and may contain sensitive material you provide.</span></label>
    {error && <div className="error-notice" role="alert"><p>{error}</p></div>}
    <div className="event-composer-actions"><button type="submit" className="button button-secondary" disabled={!canSubmit}>{saving ? "Adding…" : "Add progress update"}</button></div>
  </form>;
}
