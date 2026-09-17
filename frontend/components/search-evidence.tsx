import type { WorkSearchHit } from "@/lib/types";

const labels = { title: "Title", summary: "Summary", tags: "Tags", checkpoint: "Checkpoint", identifiers: "Identifier", provenance: "Provenance" };
export default function SearchEvidence({ hit }: { hit: WorkSearchHit }) {
  if (hit.evidence_mode === "browse") return null;
  return <div className="artifact-search-excerpt work-search-evidence" role="note" aria-label="Supporting search evidence">
    {hit.evidence_mode === "semantic" ? <p>Related meaning · ranked candidate {hit.rank}. This ordering is not a confidence estimate.</p> : <>
      <span className="artifact-match-fields">Matched {hit.matched_fields.map((field) => labels[field]).join(" · ")}</span>
      {hit.excerpts.map((excerpt, index) => <div key={`${excerpt.field}:${index}`}><span className="artifact-match-fields">{labels[excerpt.field]}{excerpt.checkpoint_id ? " · saved checkpoint" : ""}</span><p><bdi dir="auto">{excerpt.text}</bdi></p></div>)}
      {hit.excerpts_truncated && <p>Some supporting text is omitted. Open the work record for more context.</p>}
    </>}
  </div>;
}
