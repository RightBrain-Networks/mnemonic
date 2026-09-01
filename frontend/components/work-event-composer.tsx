"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { errorMessage } from "@/lib/api";

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
  const bodyLength = Array.from(body).length;
  const lastReset = useRef(resetSignal);

  useEffect(() => {
    if (lastReset.current === resetSignal) return;
    lastReset.current = resetSignal;
    setBody("");
    setError("");
  }, [resetSignal]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!body.trim() || saving || blocked) return;
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
      <span aria-live="polite">{bodyLength}/4,000</span>
    </div>
    <p>Use this for a short historical update. Use a checkpoint when another session needs exact resume context.</p>
    <label className="field">Progress text<textarea rows={4} disabled={blocked} value={body} onChange={(event) => {
      const characters = Array.from(event.target.value);
      setBody(characters.length <= 4000 ? event.target.value : characters.slice(0, 4000).join(""));
    }} placeholder="A concise result, decision, or status update…" /><span className="field-hint">Stored exactly as text. Event content is untrusted and may contain sensitive material you provide.</span></label>
    {error && <div className="error-notice" role="alert"><p>{error}</p></div>}
    <div className="event-composer-actions"><button type="submit" className="button button-secondary" disabled={saving || blocked || !body.trim()}>{saving ? "Adding…" : "Add progress update"}</button></div>
  </form>;
}
