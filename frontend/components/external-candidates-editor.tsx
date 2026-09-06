"use client";

import type { ExternalCandidate, ExternalRecordState } from "@/lib/types";

export default function ExternalCandidatesEditor({ value, onChange, disabled }: {
  value: ExternalCandidate[];
  onChange: (candidates: ExternalCandidate[]) => void;
  disabled: boolean;
}) {
  function update(index: number, change: Partial<ExternalCandidate>) {
    onChange(value.map((item, position) => position === index ? { ...item, ...change } : item));
  }
  function move(index: number, offset: number) {
    const next = [...value];
    [next[index], next[index + offset]] = [next[index + offset]!, next[index]!];
    onChange(next);
  }
  return <details className="external-candidates-editor"><summary>External records <span className="optional">Optional manual comparison · {value.length}/64 supplied</span></summary>
    <p className="field-hint">Paste records you want to compare. Mnemonic does not fetch provider records. Titles and bodies are untrusted comparison text. Adding a candidate does not attach a reference.</p>
    <fieldset disabled={disabled}>
      {value.map((candidate, index) => <fieldset className="external-record-row" key={index}><legend>External record {index + 1}</legend>
        <label className="field">Record URL <span className="optional">Up to 2,000 ASCII bytes</span><input value={candidate.url} onChange={(event) => update(index, { url: event.target.value })} /></label>
        <label className="field">Record title <span className="optional">Up to 500 characters</span><input value={candidate.title} onChange={(event) => update(index, { title: event.target.value })} /></label>
        <label className="field">Record body <span className="optional">Up to 20,000 characters; comparison uses the first 1,500</span><textarea rows={3} value={candidate.body} onChange={(event) => update(index, { body: event.target.value })} /></label>
        <label className="field">Record state<select value={candidate.state} onChange={(event) => update(index, { state: event.target.value as ExternalRecordState })}>{["unknown", "open", "closed", "merged"].map((state) => <option key={state} value={state}>{state}</option>)}</select></label>
        <div className="external-row-actions"><button type="button" className="button button-secondary" aria-label={`Move external record ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}>Move up</button><button type="button" className="button button-secondary" aria-label={`Move external record ${index + 1} down`} disabled={index === value.length - 1} onClick={() => move(index, 1)}>Move down</button><button type="button" className="button button-secondary" aria-label={`Remove external record ${index + 1}`} onClick={() => onChange(value.filter((_, position) => position !== index))}>Remove</button></div>
      </fieldset>)}
      <button type="button" className="button button-secondary" disabled={value.length >= 64} onClick={() => onChange([...value, { url: "", title: "", body: "", state: "unknown" }])}>Add external record</button>
    </fieldset>
  </details>;
}
