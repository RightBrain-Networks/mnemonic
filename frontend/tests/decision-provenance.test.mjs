import assert from "node:assert/strict";
import test from "node:test";
import { emptyProvenanceDraft, provenanceFromDraft, validDecisionProvenance } from "../lib/decision-provenance.ts";
import { decodeJobReport, matchCloseoutReport, jobReportFromDraft, validJobReportInput, jobReportDraftHasEdits } from "../lib/job-completion-reports.ts";
import * as f from "./phase12-fixtures.mjs";
const cited = { disposition: "cited", reason: null, citations: [{ decision_id: "ADR-00001", revision: 0 }], commits: [{ repository: "github.com/team/project", commit_hash: "a".repeat(40) }] };

test("completion declarations require exact revisions, repository identities and full commit hashes", () => {
  assert.equal(validDecisionProvenance(cited), true);
  for (const change of [{ citations: [] }, { commits: [] }, { citations: [{ decision_id: "ADR-00001-0", revision: 0 }] }, { citations: [{ decision_id: "ADR-00001", revision: -1 }] }, { commits: [{ repository: "https://user:secret@example.com/repo", commit_hash: "a".repeat(40) }] }, { commits: [{ repository: "team/repo", commit_hash: "abcdef0" }] }, { citations: [...cited.citations, ...cited.citations] }, { commits: [...cited.commits, ...cited.commits] }]) assert.equal(validDecisionProvenance({ ...cited, ...change }), false);
  assert.equal(validDecisionProvenance({ disposition: "not-code", reason: "Documented the rollout checklist", citations: [], commits: [] }), true);
  assert.equal(validDecisionProvenance({ disposition: "no-applicable-decisions", reason: "Reviewed ADR and owner domains; none cover this typo", citations: [], commits: cited.commits }), true);
  assert.equal(validDecisionProvenance({ ...cited, disposition: "not-code", citations: [], reason: "Documentation" }), false);
});
test("completion drafts block missing decisions or failed coverage reads only for Done", () => {
  const draft = { summary: f.reportInput.summary, fyiItems: f.reportInput.fyi_items, promptRevision: f.reportInput.prompt_revision };
  assert.throws(() => jobReportFromDraft(draft, true), /domains/);
  for (const provenanceRequirement of ["loading", "error", "required"]) assert.throws(() => jobReportFromDraft({ ...draft, provenanceRequirement }, true));
  assert.deepEqual(jobReportFromDraft({ ...draft, provenanceRequirement: "optional" }, true), f.reportInput);
  assert.deepEqual(jobReportFromDraft({ ...draft, provenanceRequirement: "required" }), f.reportInput);
  const decisionProvenance = { ...emptyProvenanceDraft(), disposition: "not-code", reason: "Only research notes were changed." };
  assert.equal(jobReportFromDraft({ ...draft, provenanceRequirement: "required", decisionProvenance }, true).decision_provenance.disposition, "not-code");
  assert.equal(jobReportDraftHasEdits({ summary: "", fyiItems: [], promptRevision: null, decisionProvenance }), true);
});
test("draft normalization preserves revision zero and normalizes full Git hashes", () => {
  assert.deepEqual(provenanceFromDraft({ disposition: "cited", reason: "", citations: [{ decision_id: "adr-00001", revision: "0" }], commits: [{ repository: "github.com/team/project", commit_hash: "A".repeat(40) }] }), cited);
  assert.throws(() => provenanceFromDraft({ disposition: "cited", reason: "", citations: [{ decision_id: "ADR-00001", revision: "" }], commits: cited.commits }));
});
test("report readers retain old omissions and validate new declared provenance", () => {
  assert.deepEqual(decodeJobReport(f.report, f.project), f.report);
  assert.deepEqual(decodeJobReport({ ...f.report, decision_provenance: cited }, f.project).decision_provenance, cited);
  assert.equal(validJobReportInput({ ...f.reportInput, decision_provenance: cited }), true);
  assert.throws(() => decodeJobReport({ ...f.report, decision_provenance: { ...cited, commits: [] } }, f.project));
});

test("closeout receipt validation binds the exact decision revision and implementation commit declaration", () => {
  const report = { ...f.report, decision_provenance: cited }, input = { ...f.reportInput, decision_provenance: cited };
  assert.deepEqual(matchCloseoutReport(report, f.project, f.workItem, input, f.actor, report.completion_checkpoint_id), report);
  assert.throws(() => matchCloseoutReport({ ...report, decision_provenance: { ...cited, citations: [{ decision_id: "ADR-00001", revision: 1 }] } }, f.project, f.workItem, input, f.actor, report.completion_checkpoint_id));
});
