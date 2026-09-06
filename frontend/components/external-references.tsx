"use client";

import type { ExternalReference } from "@/lib/types";
import { validExternalUrl } from "@/lib/external-references";

export default function ExternalReferences({ references = [] }: { references?: ExternalReference[] }) {
  if (!references.length) return null;
  return <ul className="external-reference-list" aria-label="External references" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") event.stopPropagation(); }}>
    {references.map((reference) => <li key={reference.url} className={`external-reference state-${reference.state}`}>
      <span className="external-reference-kind">{reference.kind === "tracked-by" ? "Tracked by" : "Reference"}</span>
      {validExternalUrl(reference.url) && <a href={reference.url} target="_blank" rel="noopener noreferrer"><bdi dir="auto">{reference.label ?? reference.url}</bdi><span className="sr-only"> (opens in a new tab)</span></a>}
      <span className="external-reference-host">{reference.url.match(/^https?:\/\/([^/?#]+)/i)?.[1]}</span>
      <span className="external-reference-state">Caller observed: {reference.state}</span>
      <span>{reference.state_observed_at
        ? <time dateTime={reference.state_observed_at}>{reference.state_observed_at}</time>
        : "Observation time unknown"}</span>
      <details><summary>Full reference</summary><p className="break-all"><bdi dir="ltr">{reference.url}</bdi></p><p>Caller supplied context. This does not change readiness or confirm completion.</p></details>
    </li>)}
  </ul>;
}
