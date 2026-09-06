"use client";

import type { ExternalReference, ExternalRecordState } from "@/lib/types";

export default function ExternalReferencesEditor({ value, onChange, disabled = false }: {
  value: ExternalReference[];
  onChange: (references: ExternalReference[]) => void;
  disabled?: boolean;
}) {
  function update(index: number, change: Partial<ExternalReference>) {
    onChange(value.map((reference, position) => position === index ? { ...reference, ...change } : reference));
  }
  function optional(index: number, key: "label" | "state_observed_at", text: string) {
    const next = value.map((item) => ({ ...item }));
    if (text) next[index]![key] = text; else delete next[index]![key];
    onChange(next);
  }
  function move(index: number, offset: number) {
    const next = [...value];
    [next[index], next[index + offset]] = [next[index + offset]!, next[index]!];
    onChange(next);
  }
  return <fieldset className="external-reference-editor" disabled={disabled}>
    <legend>External references</legend>
    <p className="field-hint">Up to 10 ordered links. Use credential-free stable HTTP(S) URLs. State and observation time are caller supplied; links do not complete or claim work.</p>
    {value.map((reference, index) => <fieldset key={index} className="external-record-row">
      <legend>Reference {index + 1}</legend>
      <label className="field">URL<input required maxLength={2000} value={reference.url} onChange={(event) => update(index, { url: event.target.value })} /></label>
      <label className="field">Kind<select value={reference.kind} onChange={(event) => update(index, { kind: event.target.value as ExternalReference["kind"] })}><option value="tracked-by">Tracked by</option><option value="references">Reference</option></select></label>
      <label className="field">Label <span className="optional">Optional · 120 characters</span><input value={reference.label ?? ""} onChange={(event) => optional(index, "label", event.target.value)} /></label>
      <label className="field">Observed state<select value={reference.state} onChange={(event) => update(index, { state: event.target.value as ExternalRecordState })}>{["unknown", "open", "closed", "merged"].map((state) => <option key={state} value={state}>{state}</option>)}</select></label>
      <label className="field">Observation time <span className="optional">Optional · RFC 3339 with timezone</span><input placeholder="2026-09-05T14:20:00Z" value={reference.state_observed_at ?? ""} onChange={(event) => optional(index, "state_observed_at", event.target.value)} /></label>
      <div className="external-row-actions"><button type="button" className="button button-secondary" aria-label={`Move reference ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}>Move up</button><button type="button" className="button button-secondary" aria-label={`Move reference ${index + 1} down`} disabled={index === value.length - 1} onClick={() => move(index, 1)}>Move down</button><button type="button" className="button button-secondary" aria-label={`Remove reference ${index + 1}`} onClick={() => onChange(value.filter((_, position) => position !== index))}>Remove</button></div>
    </fieldset>)}
    <button type="button" className="button button-secondary" disabled={value.length >= 10} onClick={() => onChange([...value, { url: "", kind: "tracked-by", state: "unknown" }])}>Add external reference</button>
  </fieldset>;
}
