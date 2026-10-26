# Knowledge graph

Release 0.101.0 and plugin 0.64.0 canonicalize explicit GitHub repository identities
on fresh scope appends and completion reports. Supported aliases match during
commit lookup; historical evidence and attribution remain unchanged. See
[code provenance](#code-provenance) for exact retry and reconciliation rules.
The migration head remains `0062_review_scope_notices`.

Release 0.99.0 and plugin 0.62.0 add a read-only grouped view of required decision
impact candidates. It distinguishes stable documents, captured revisions and
per-episode obligations, with bounded pagination and a coherent scope snapshot.
No new configuration or migration is required beyond `0062_review_scope_notices`.

Release 0.96.0 and plugin 0.59.0 add atomic batches of 1–100 dispositions for one
assessment and optional progress renewal through its associated review's active
implementation lease. Migration `0061_batch_dispositions` adds the batch receipt
kind; existing singleton requests and historical receipts retain their behavior.

Release 0.95.0 and plugin 0.58.0 add opt-in compact impact and assessment reads.
Full defaults and immutable snapshots remain unchanged; the schema stays at
`0060_supersession_reuse`.

Release 0.94.0, plugin 0.57.0 and migration `0060_supersession_reuse` retain explicit
unchanged-supersession declarations. A revision can reuse the same original
assessment when its author names the exact unchanged ruling and current evidence
still matches. Existing callers retain the conservative default behavior.

Release 0.93.0, plugin 0.56.0 and migration `0059_domain_decision_reviews` add
per-domain automatic review overrides. Existing and new domains default to
`inherit`, preserving the project policy until explicitly changed.

Release 0.92.0, plugin 0.55.0 and migration `0058_decision_changes` group reviews by
explicit decision change, preserve inherited review boundaries, and require owner
admission before a closed review-derived change starts another cycle. See
[automatic decision impact reviews](#automatic-decision-impact-reviews).

Release 0.91.0 added required dispositions for incoming related citations alongside
the origin and declared dependents, preserving unknown causality and historical
assessment requirements. Those obligations remain in force.

Release 0.87.0, plugin 0.52.0 and migration `0056_product_decisions` make Needs
Attention questions Product Decision Records (PDRs) and create a default Product
Decision Records domain for every project.
Release 0.86.0 added pinned dependencies, impact reassessment, and completion
provenance through migrations 0054 and 0055. Project-owned decision domains and permanently retained
revisions began with `0053_knowledge_graph` in 0.85.0. Existing work graphs and
artifact storage remain separate. Database backups include decision text and history.

A domain has a case-insensitively unique name and a unique prefix within its project. Prefixes are uppercased
and must match `[A-Z]{1,5}`; the prefix cannot change after creation. Domain names
and review overrides can be updated together using their current version.
Domain deletion is available only through the dashboard; agents cannot delete domains.
Each domain allocates increasing ordinals starting at 1. Ordinals are padded to a
minimum of five digits, never recycled, and can grow beyond five digits. Revision
numbers start at 0: `ADR-00194-0` identifies one immutable revision, while
`ADR-00194` identifies the document and defaults to its current revision.

## Dashboard document and domain management

Knowledge graph is a top-level navigation group with **Decision documents**
(`/knowledge/decisions`) followed by **Manage domains** (`/knowledge/domains`).
The previous `/knowledge` URL still opens decision documents. Document state tabs
use the work list filter pattern. Commit lookup uses the selected project's
Repository URL from Project settings; it has no separate repository identity input.

Humans can upload UTF-8 Markdown or plain text, edit a document, replace its text
from a file, and download its current revision. Upload and revision writes use the
existing receipt-protected decision PUT with human authorship. Editing and replacing
pin the revision that was loaded. A concurrent agent write wins: the stale human
save returns a conflict, retains the draft, and displays the current revision for
explicit review before another save. Both text and metadata commit atomically.
Managed Product Decision Records continue to use the Needs Attention workflow.

Each domain can export its current documents as a `.tar.gz`. Every document has a
Markdown member and a JSON metadata sidecar, named with its stable decision ID.
Exports read a coherent database snapshot. Downloads invoke the browser's save
picker when supported, with the normal browser download behavior as fallback.

Dashboard deletion first loads a preview with the document count, relationship
counts, active impact reviews, and a fingerprint of the documents, revisions and edges. Confirmation
shows the affected relationship total and requires acknowledgement only when that
total is nonzero. Active impact reviews retain their captured audit evidence and
remain actionable; the confirmation warns when their original documents will
become unavailable. An empty or orphaned resource still requires modal confirmation.
The server rechecks the preview in the mutation transaction, so new documents,
revisions or relationships require another review. There are no MCP deletion tools
or machine API deletion routes.

Deletion removes live decision, dependency, supersession, artifact, work and commit
edges in one transaction. Immutable edge snapshots are archived against the deletion
receipt for historical integrity checks and project backups. Domain/document
identities and immutable revision history remain as tombstones. Deleted references
return `knowledge_domain_deleted` or `decision_deleted` with their deletion time;
lists, unified search and commit lookups exclude them. Prefixes and ordinals are
never reused. The built-in PDR domain and documents managed by Needs Attention are
protected so pending questions and their accepted answers remain usable.

Migration `0065_knowledge_management` adds deletion timestamps, the edge archive,
and local domain review priorities. Upgrade existing deployments only with the
normal production-operation authorization; older processes must not run against
the new schema. Existing review inheritance is resolved to each domain's effective
setting during migration. New domains default to reviews off and priority 40.

## Product decisions and Needs Attention

New projects automatically receive a `PDR` domain named Product Decision Records.
Existing projects also receive a designated product decision domain during the
upgrade. `list_knowledge_domains` identifies it with `is_product_decision_domain`;
its display name can be changed while its prefix and role remain fixed.
An existing `PDR` domain is reused without renaming or renumbering documents. If
the default name belongs to another prefix, the new domain uses Product Decision
Records (PDR), with a numeric suffix when needed.

Every Needs Attention item is a canonical decision document linked to its human
gate and primary work item. A new question creates proposed revision 0. Rewriting
the open question appends an immutable decision revision and keeps its gate,
document ID, queue position and history. Question version N corresponds to
decision revision N-1. A human answer appends revision N containing the question
and answer, with human authorship and accepted state. **Accepted means the owner's
decision was recorded**, even when the answer declines the proposed action or says
"No longer needed". It does not mean approval to perform the proposed action.

Agents use `request_human_input`, with truthful native session and model identity,
after retrieving the current `request-human-review` prompt. Optional
`decision_metadata` provides a title, related decision/work/artifact links, exact
dependencies and supersession details. On revision, omitted metadata or individual
fields preserve their current values; an empty list clears optional links. The
primary work link remains for new questions. New questions default to the work title and no
optional links. All ordinary decision relationship rules apply.

The gate's `decision` includes document/domain identity, exact reference and revision,
state, content hash and complete metadata. `decision_get` exposes its `human_gate`
with gate/work identity, `work_project_id`, current question version and
unresolved/resolved status. Follow that project ID when opening the work: a
historical resolved question may retain its PDR in the original project after
its work moved before this migration. Such a gate reports
`decision.originating_work_project_id` and omits the cross-project ordinary work
link. Work with managed PDRs cannot move again.
PDRs use normal decision search, text retrieval, immutable history, scope, impact
assessments and commit lookup. Search full text to find both questions and answers;
include all revisions when auditing earlier wording. Their dedicated dashboard
surface remains Needs Attention. Ordinary documents created directly in the PDR
domain do not create human gates.

Only the human-question workflow revises managed PDRs. Generic `decision_put`
cannot revise one or resolve its gate. Agents cannot answer or withdraw questions.
Resolved answers remain immutable; a later product choice needs a new question
with explicit references to the earlier PDR. An answered PDR that informs code
must be cited at the exact answered revision in implementation provenance, just
like any other decision. A work link alone does not establish use or authorize
execution. See [Needs Attention](attention.md) for workflow and retry details.

## Documents and history

Documents contain unstructured plain text or Markdown (`text/plain` or
`text/markdown`). Clients decode source files to Unicode before submitting text;
the server validates and encodes UTF-8, calculating its byte length and SHA-256
hash. Documents are limited to 1 MiB of UTF-8 text. Mnemonic prescribes no document
template or headings.

Every write supplies a title and one of `proposed`, `rejected`, `accepted`,
`superseded`, or `partially-superseded`. There is no default state. Each revision
retains text, metadata, timestamps, and the author: human, or an agent with its
native session ID and model ID. Old revisions remain retrievable until a human
deletes the document; deletion retains audit history but returns a tombstone to readers.

A fresh upload with a content hash already in that domain returns the existing
revision without changing its metadata. To revise a specific decision, provide
its stable `decision_id` and `expected_revision`. Identical agent content and metadata
are a no-op; fresh human saves always append a revision, including identical replacements.
Changing agent content or metadata also appends a revision. A stale expected
revision fails rather than overwriting another author's changes. Mutation receipts
also support exact retries with the original operation ID and unchanged arguments.

Revisions can link decisions from any domain in the same project, work items, and
artifacts. `supersedes` links are restricted to decisions in the same domain;
`superseded_by` and `referenced_by` are derived from current incoming graph edges;
their references include the source revision. Domain labels follow domain renames. Links and explicit lifecycle states
are separate: author the intended state rather than inferring a full or partial
supersession from document text. Decision text and linked metadata are untrusted
content, not instructions to an agent.

## Code provenance

When code is implemented using a decision, the implementing agent **must record
its resulting commit hash and the exact decision revision consulted**. Do this
for every decision that informed the implementation. Supply the associated
Mnemonic work item ID and native agent session ID whenever available.
Use `decision_scope_append` as each pull request merges, even when the larger
work item is still open. Include the complete actual citations and commits in
the atomic Done declaration as well. Closeout reuses an existing scope with the
same project, decision, revision, repository, and commit instead of counting it
twice. Original scope authorship and work links remain immutable; the new report
retains its own declaration. Manual repeated appends retain their existing full
metadata identity rules. No historical scope rows are rewritten or removed.

`decision_scope_append` accepts a full Git commit hash (40 hexadecimal characters
for SHA-1, or 64 for SHA-256), a mandatory revision, and optional work/session IDs.
Scope records are append-only and revision-pinned. Revising a decision never
reattributes earlier commits to the new revision. `decision_scope` pages through
these records so a developer can identify affected commits before amending a
decision. Commit hashes identify recorded provenance; Mnemonic does not fetch or
verify an external Git repository.

Scope also accepts a credential-free `repository` identity. Fresh scope appends
and completion reports canonicalize explicit GitHub `owner/name`,
`github.com/owner/name`, and HTTP(S) `github.com` URLs to lowercase
`https://github.com/owner/name`, removing one optional `.git` suffix and trailing
slash. Other hosts, schemes, and unrecognized forms keep their exact spelling;
ambiguous repeated `.git` suffixes are left unchanged. Missing identity stays
unknown. Credentials and URL query/fragment parameters remain invalid.

Manual appends deduplicate only with the same revision, commit, work, session,
and completion-report attribution. Fresh canonical writes reuse canonical rows;
they never collapse differently spelled historical rows. A fresh completion
rejects duplicate repository/commit pairs that differ only by supported GitHub
aliases (`422 decision_provenance_duplicate_commits`); remove duplicate pairs in
a new closeout intent after that definitive rejection.

`decision_commit_lookup` accepts supported GitHub aliases case-insensitively for
original or reconciled hashes and returns every matching scope with its **stored
repository spelling**. Alias case folding is ASCII-only; unsupported Unicode
spellings and other repository identities match exactly. Existing scope,
report, assessment snapshots and receipts retain their original values; historical
cleanup requires a separate audited backfill. Exact retries preserve the original
operation ID and every request argument, including repository spelling, even when
the successful response contains its canonical form. Historical
scopes without repository identity remain readable but cannot prove which
repository contained a commit or match an explicit repository during closeout.
After a verified squash/rebase/merge,
`decision_scope_reconcile` appends an original-to-merged hash mapping; it never
rewrites the original scope record. Reconciliation deliberately requires the exact
**stored repository value returned by lookup**, including historical spelling; it
does not expand aliases or combine differently spelled historical rows. Finding both objects alone does not prove an
integration relationship: inspect actual merge evidence first.

Scope and commit-lookup results expose `completion_citations` for later Done
reports that cited the same exact scope, including report/work IDs, author client,
session/model, and timestamp. Up to 50 are returned on ordinary scope reads and
10 in impact evidence; `completion_citations_truncated` marks additional reports.
The original scope's work/session fields keep their original meaning.

Fresh Done closeouts in projects with knowledge domains require a nested
`job_completion_report.decision_provenance` declaration:

Because every project now has a default PDR domain, this applies even when a
project has no decision documents. An agent that used none can supply the
explicit uncited disposition described below; no document has to be invented.
The dashboard's manual Done action opens a completion decision form requiring
the same deliberate citation or explained uncited declaration. It does not infer
that no decisions were used or that no code was produced.

```json
{
  "disposition": "cited",
  "citations": [{"decision_id": "ADR-00194", "revision": 0}],
  "commits": [{"repository": "github.com/team/repository", "commit_hash": "FULL_HASH"}]
}
```

`cited` requires nonempty exact citations and commits, with every citation
applying to every listed commit. At most 100 citations, 100 commits, and 500
pairs are accepted. The closeout transaction adds missing scope with the work,
session, and completion report identity and reuses exact existing citations.
`no-applicable-decisions` requires a concrete
reason and no citations; commits may be supplied. `not-code` requires a reason
and neither citations nor commits. A read does not imply use. Missing historical
declarations mean unknown provenance, and structural completeness never proves
all influences were disclosed. Existing immutable closeout receipts remain
replayable with their original arguments.

`scripts/decision_provenance.py` resolves actual local Git commits and emits the
declaration without network access or writes. It also prepares reconciliation
payloads using `--original` and `--merged`. The helper ships in the plugin and
portable skills. It verifies local commit existence, not semantic equivalence
or server verification; repository and decision use remain caller declarations.

## Dependencies and change impact

Each immutable decision revision can declare `depends_on` entries containing
the stable target `decision_id`, exact target `revision`, and nonblank `rationale`.
These are explicit causal dependencies within the project, including across
domains. Generic related links keep their informational meaning; imported
associations are never silently promoted to dependencies. Changing a dependency
creates a revision and retains its prior target pin.

`supersession_details` adds exact target revision, `extent` (`full` or `partial`),
and a reason explaining the overridden portion. Supersession remains restricted
to the same domain and does not infer or overwrite the target's explicit state.
Legacy supersedes links without details retain unknown extent.

`decision_impact` traces reverse dependencies from an exact decision revision.
The default origin is current; retain the returned `origin_reference` for a
repeatable investigation. `kind="code-impact"` is the default: it includes the
origin decision, its reverse `depends_on` dependents, and records whose own
related links cite a decision on that trace. Incoming citations retain
`relationship="related-only"` and `related_direction="incoming"`: causality is
unknown, but a disposition is required. Outward references carry
`related_direction="outgoing"`. Explicit dependency paths take precedence, and
incoming citations take precedence over optional context when both directions
exist. Related links are one-hop leads; they do not extend transitive dependency
traversal. Candidates include historical and current revisions,
one deterministic shortest explanatory path, registered commit scope, and linked
work/artifacts. Read `kind="context"` separately for outward
`depends-on-prerequisite` and `related-only` records. These provide context for
checking whether the replacement contradicts another decision; they do not
establish downstream code impact. `candidate_count` and `context_count` count
these groups separately. `total` and `next_offset` apply to the requested kind.
Follow each kind's pages; inspect coverage and every
`evidence_truncated` flag. Traversal is bounded to 500 candidates and defaults
to depth 8 (maximum 16). Candidate evidence is bounded to 10 scopes, 20 work links,
and 20 artifact links. Retrieve the exact decision and paginated scope to inspect
omitted evidence. `provenance_complete` remains false because unregistered
influences cannot be discovered from an explicit graph. Structured warnings name
the exact origin revision when it has no linked commits. Revision author/session/model
and observed active-work information let reviewers identify author/implementation
overlap without treating it as proof of a defect or implementation ownership.

## Reassessment after changes

Actual revision changes and supersession create durable assessment episodes.
Revising a replacement can retain a supersession's original episode when its
governing ruling and captured evidence remain unchanged; identical content and
metadata create no episode. New or changed supersessions create fresh episodes.
Each captures before/after references, a bounded text/metadata diff, candidate
evidence, and coverage at the change. A candidate is a reason to investigate;
the change does not automatically invalidate its implementation. Later graph
or scope registrations do not rewrite the captured snapshot; use a fresh impact
query to inspect newer evidence.

`decision_assessments` lists episodes, optionally for a decision.
The decision filter matches origin, replacement, or captured candidates. Summary
counts distinguish unassessed code-impact candidates from those requiring changes, with a
derived `pending`, `change-required`, or `resolved` status. Resolved describes
the recorded review, not proof that unregistered downstream impacts are absent.
`decision_assessment_get` pages captured code-impact candidates by default; use
`kind="context"` to inspect contextual records and their optional dispositions.
Context-only records do not require individual verdicts, including related-only
records retained in old immutable snapshots without captured direction. New
snapshots retain direction; old snapshots are never reclassified from live links.
Existing verdict history remains.
`decision_assessment_history` pages every immutable disposition. The diff is
bounded to 16 KiB with explicit truncation; read both exact decision revisions
when the full text matters. Candidate pages retain the current assessment version
so concurrent review can be detected.

Impact and assessment reads support optional projections without changing the
stored evidence or existing defaults. Use `detail="compact"`,
`include_diff=false`, and `include_scopes="summary"` for smaller navigation
responses. `detail="full"`, `include_diff=true`, and `include_scopes="full"`
remain the defaults on REST and MCP. Nondefault responses include a `projection`
object with the three effective options; omitted fields mean deliberately omitted
detail, not empty evidence.

- Compact candidates retain exact identities, state/currentness, relationships,
  directions, paths, and `evidence_truncated`. They omit title, rationale, and
  linked work/artifact lists. Author context retains identity and observation
  timestamps, replacing active-work descriptions with `active_work_item_ids`.
- Scope `summary` replaces `scopes` with `scope_summary`: the number of captured
  scope rows, distinct captured original commit hashes, and evidence truncation.
  `captured_scope_count` is not an exact live scope total: captures are bounded,
  and later registrations do not modify a snapshot. Repository identities, merged
  commit mappings, attribution, and completion citations require full scope reads.
  Scope `none` omits both scope rows and their summary; coverage remains visible.
- `include_diff=false` omits only `diff.unified_diff`; changed fields, before/after
  hashes, and the original truncation flag remain. Compact latest dispositions
  retain candidate reference, version, verdict, and remediation work ID, omitting
  record IDs, prose, attribution, and creation time. Full assessment reads and
  `decision_assessment_history` retain those details.

Projection preserves every candidate, latest disposition, count, and continuation
offset. A compact navigation page is not sufficient evidence for a verdict:
retrieve relevant exact decision text, full detail/history, and `decision_scope`
before relying on omitted evidence. There is no universal compact byte limit;
use a smaller `limit` for long paths or many distinct commit hashes and follow
`next_offset`. Assessment lists retain ordinary offset/limit pagination.

`decision_assessment_append` records an exact candidate reference, current
`expected_version`, truthful actor provenance, a concrete reason, and one of
`still-valid`, `change-required`, `not-affected`, or `resolved`. Remediation work
may be linked. Linked remediation belongs to the same project; its immutable
knowledge reference prevents moving that work to another project. Only record
resolved after inspecting completed remediation.
The append is receipt-protected and retains earlier judgments. It neither
changes document lifecycle nor completes work, resolves human questions, or
grants execution authority. A definitive version conflict requires a fresh read
before a new operation; uncertain retries preserve the original full intent.

`decision_assessment_append_batch` records 1–100 dispositions for one episode in
one transaction. Supply `assessment_id`, the current `expected_version`, one
truthful `actor`, and ordered `items` with unique exact `candidate_reference`,
`disposition`, concrete `reason`, and optional `remediation_work_item_id`. Each
item uses the same validation as the single append and receives the next version
in input order. An invalid item rolls back every disposition. The result returns
`project_id`, `assessment_id`, final `version`, and the ordered immutable `items`.
One operation UUID protects the whole batch: preserve every argument and item
order for an exact retry. Batches do not reduce the per-candidate, per-episode
requirements for review Done or copy dispositions to another episode.

Both append tools accept an optional private `lease_token`. A fresh successful
write renews only the matching active implementation lease of a current review
driver associated with the episode. An episode may belong to several changes;
the token selects the matching driver, including an adopted review or work moved
to another project. The duration is its last granted duration clamped to that
work project's current minimum and maximum. Unrelated tokens and code-review
capabilities are refused. Invalid writes roll back renewal; token-free writes
and exact receipt replays never renew. Tokens are omitted from results and must
never be included in reasons or actor fields. Use `renew_claim` explicitly when
the estimated remaining time changes.

REST retains the single `POST /projects/{project_id}/knowledge/assessments/{id}/dispositions`
and adds `POST .../dispositions/batch`, with receipt kind `assess_decision_impact_batch`.
The batch endpoint is available to authenticated machine clients and is not
registered as a dashboard mutation. Migration `0061_batch_dispositions` admits
the new receipt kind. Backup integrity checks bind every retained disposition to
exactly one single or batch receipt and validate batch membership, order and
consecutive versions. Downgrade refuses to discard existing batch receipts.

`decision_change_candidates(project_id, change_id)` provides a read-only grouped
view of the change's required code-impact candidates. `decision_count` counts
stable documents, `revision_count` counts distinct captured revision references,
and `occurrence_count` counts each candidate in each assessment episode. For example, 191
required occurrences across six episodes, 71 distinct revisions and 50 documents
remain 191 separate review obligations.
`unassessed_count` and `change_required_count` also count required occurrences;
optional context candidates are excluded from this navigation view. Existing
completion rules, including remediation for recorded contextual findings, remain.

Each group retains compact occurrences with assessment ID/version, exact
before/after and candidate references, captured relationship/direction, and latest
disposition ID/version/result/remediation link. Read the assessment or history for
the actual evidence, actor and reason. When a declared unchanged supersession reuses
a candidate conclusion, `reused_from` pins its source assessment, disposition ID and
version. This is a reference to immutable history; no verdict text is copied. Historical related-only candidates without captured incoming
direction remain context; live links never reclassify captured scope.

All counts, groups and occurrences in a response share a coherent database
snapshot and exact `scope_version`/`acknowledged_scope_version`. Whole-change totals
remain global when filtering `decision_id`; `total` counts selected groups.
Default pagination is 20 groups and 10 occurrences per group. Both limits accept
1–100, with `limit * occurrence_limit <= 200`. Follow `next_offset` for groups;
use a group's stable `decision_id`, `limit=1`, and `next_occurrence_offset` to reach
its remaining occurrences. `captured_references` derives only from the returned
occurrence page; `references_complete` and the full `revision_count` explicitly
show whether more references remain. Reads do not acknowledge scope or change
Done requirements. REST exposes the same projection at
`GET /projects/{project_id}/knowledge/changes/{change_id}/candidates`.

Supersession assessment reads also expose `supersession_delta`. It compares the
superseding document with its immediately preceding revision, or marks a new
supersession when the target was not declared previously. Exact replacement
`before_reference`/`after_reference` and `target_reference` identify the evidence;
`details_before`/`details_after` compare only the captured target's structured
supersession details, including target revision, extent and reason. The original
cross-document `diff` remains available unchanged.

The comparison extracts literal Markdown `Supersedes` headings (ATX or setext,
excluding fenced examples) and their subordinate sections. It reports section
presence and count, scans at most 65,536 source characters per revision, retains
at most 8,192 UTF-8 bytes per side and 16,384 bytes of diff, and marks truncation.
A missing section or empty section diff never establishes unchanged rule scope:
headings are evidence aids, and the governing rule may appear anywhere in either
exact revision. When both captured extent and reason are null, coverage explicitly
warns `supersession_extent_reason_unknown`; inspect the replacement and preceding
revision before judging scope. These fields and warnings are read projections;
historical episodes, diffs, coverage snapshots and dispositions remain immutable.
Compact reads may omit section text and diff prose while retaining identity,
structured details, bounds and coverage metadata.

Dispositions may include `finding_id`, an authored, case-sensitive key of 1–100
ASCII letters, digits, dots, colons, underscores or hyphens, beginning with a letter
or digit. Reuse a key only when the evidence establishes the same finding across
candidates or episodes in the selected change. It is an opaque grouping key, not a
work ID or inferred equivalence. On `change-required` dispositions,
`remediation_target` may be `code`, `decision-record`, or `both`. A decision-record
target distinguishes a wording correction from a code change; it does not grant
authority to make either change or remove the remediation requirement.

Assessment and change reads expose `finding_summary`. `named_finding_count`
counts distinct explicit IDs among the latest change-required dispositions;
`unlabelled_change_required_count` counts rows without an ID whose relationship
to other findings is unknown; `change_required_row_count` retains the total
number of those rows. Thus 19 rows naming one finding plus two unlabelled rows
report one named finding, two unlabelled rows and 21 change-required rows.
Unlabelled rows are never deduplicated by shared remediation work, similar reasons
or candidates. These summaries do not change candidate counts, required verdicts
or review Done checks. Corrections append new version-checked dispositions;
summaries use their latest values and history retains the previous metadata.
Historical rows and receipt bodies keep their original shape, with absent
finding metadata remaining unknown. The dashboard shows the same distinction
and offers optional finding and remediation-target fields when recording a verdict.

The [narrow finding-entry form](images/impact-finding-form-narrow.png) shows the
optional fields and save controls. Browser acceptance captures also show the
separate finding and candidate counts and retained disposition metadata:

| View | Desktop | Narrow |
| --- | --- | --- |
| Assessment counts | [Summary](images/impact-finding-summary-desktop.png) | [Summary](images/impact-finding-summary-narrow.png) |
| Recorded finding | [Disposition](images/impact-finding-record-desktop.png) | [Disposition](images/impact-finding-record-narrow.png) |

## MCP workflow

1. Call `list_knowledge_domains` and select a domain. Create one with
   `create_knowledge_domain` if needed; `rename_knowledge_domain` changes its name.
2. Discover decisions through `search`, including the `decisions` facet.
   `filters.decisions.domain_ids` narrows domains, `all_revisions=true` includes
   history, and `state` selects a lifecycle state. The default is current revisions.
   Set `fulltext=true` to search document text, otherwise only metadata is searched.
3. Read `decision_get(project_id, decision_id="ADR-00194")`. Pass `revision`, or
   use a revision-qualified reference, to pin history. Optional `line_start` and
   `line_end` are inclusive, one-based bounds. `decision_history` lists revisions.
4. Author using `decision_put`, or implement code using the consulted decisions.
5. Register each implemented commit with `decision_scope_append` as its pull
   request merges, using the exact consulted revision and explicit repository.
   Include complete actual citations and commits in the Done report; closeout
   registers missing scope and reuses exact existing citations. Preserve the
   exact mutation intent and operation ID for retries.

Unified search includes decisions by default. Multi-term queries continue to omit
transcripts unless explicitly selected. Decision hits expose a revision UUID,
human-readable reference, content hash, bounded excerpt, and author metadata.
All-revision hits have distinct identities and participate in common ranking and
pagination. Follow `next_offset`, including when a page is shortened by the shared
32 KiB response budget. Document bodies are retrieved with `decision_get`.

## Dashboard and deployment

Knowledge graph > Decision documents provides a document list and domain tabs;
Manage domains provides domain administration. The shared document preview drawer has Markdown rendering, revision
history, pinned dependencies, supersession details, and commit scope. The impact
panel shows candidate paths, evidence, immutable assessment snapshots, and
reassessment history. Users can append a reasoned disposition and optional
remediation work link. Reverse lookup finds the decisions cited by a repository
and commit. The Done form collects the required provenance declaration.
Human document uploads, revision edits, replacements, downloads and deletions are
available in Decision documents; domain export and deletion live in Manage domains.
Managed human answers are authored through the existing Needs Attention surface.

Apply `0056_product_decisions` after `0055_decision_provenance` through the normal
authorized deployment process. Existing revisions, scopes and mutation receipts
are preserved. Existing human questions and answers become PDR revisions using
their retained authors and timestamps; missing historical model identity stays
unknown. Historical dependencies and implementation provenance are not inferred.
Stop old writers before migration, and retain a pre-upgrade database backup for
recovery. Earlier migrations also refuse downgrades that discard populated
impact or provenance history.
Production API, MCP, web, and worker rollout still requires explicit operator
approval to interrupt services. No filesystem content migration is needed.

## Automatic decision impact reviews

**Automatic decision impact reviews** are configured for each domain in
**Knowledge graph > Manage domains**. New domains default to `off`, with
`decision_review_priority` 40 (integer 0–100). Set `automatic_decision_reviews`
to `on` to create investigations for that domain. The changed record's domain
governs, including when dependencies reach records in other domains.
Supersession retains its existing same-domain restriction.

REST `PATCH /projects/{project_id}/knowledge/domains/{domain_id}` and MCP
`rename_knowledge_domain` accept `expected_version` and any of a name,
`automatic_decision_reviews`, and `decision_review_priority`. Empty updates and
null settings are invalid; prefixes remain immutable. `inherit` is retained only
for exact historical receipt replay and is rejected on fresh writes. The project
settings API no longer exposes or edits these controls.

Migration `0065_knowledge_management` resolves existing inherited policies to
their prior effective on/off setting and copies the prior project priority into
each domain. New domains always start off unless explicitly enabled. Historical
project columns remain solely to retain backup history; review creation never
reads them. Policy changes do not backfill earlier changes or cancel existing work.
Substantive assessment episodes are captured even when review creation is disabled;
an existing grouped review still accumulates its obligations. Unanswered PDR
questions remain maintenance; accepted answers can open reviews according to the
PDR domain policy and existing admission rules.

When enabled, a substantive decision change has one native change identity and
at most one ordinary Pending review driver. Related revisions recording that
ruling join the same change. Initial creation without supersession, unchanged
saves, maintenance, and receipt replay create no additional review. A replacement
that supersedes existing decisions can create review obligations at revision zero.
There is no backlog on enable and no automatic cleanup of historical work.

Fresh agent `decision_put` calls declare `source_work_item_id`: the exact session
origin, or explicit null for a human/external trigger. Omission is reserved for
historical receipt replay. `change` defaults to `{kind:"substantive"}`. Recording
an existing ruling uses `{kind:"record-existing", change_id, governing_reference,
reason}` with an exact revision reference; genuine formatting/provenance upkeep
uses `{kind:"maintenance", reason}`. Uncertainty stays substantive. Section headings,
timing, state labels and authorship cannot establish classification. Decision reads
retain the declaration in `change`, including source work and native change ID.

Unchanged supersession obligations reuse an existing immutable episode only when
the governing transition and captured candidates/coverage match. Existing exact
`record-existing` governance remains supported. For a revision governed elsewhere,
or a substantive revision changing other rules, explicitly declare each unchanged
supersession in `change.unchanged_supersessions`, for example:

```json
{
  "kind": "substantive",
  "unchanged_supersessions": [{
    "target_reference": "ADR-00194-0",
    "governing_reference": "ADR-00201-2",
    "reason": "Only the unrelated transport rule changes; this target's supersession is unchanged."
  }]
}
```

The governing reference must identify the replacement revision of the latest
prior supersession capture for that target, in the same replacement document.
The source author is explicitly asserting an unchanged ruling; matching metadata
or a section heading alone cannot establish that. The server also checks that the
target revision, extent and reason have been continuously unchanged, including
through maintenance revisions. Invalid or interrupted declarations are rejected.
Declarations are immutable, retained with their source revision, and returned in
`decision_get`/history under `change.unchanged_supersessions`.

With an explicit unchanged-supersession declaration, a fresh episode can reuse
individual reviewed conclusions when candidate evidence is unchanged, even if
other candidates have new evidence. Every candidate row stays in the new snapshot.
The comparison excludes only `is_current` from candidate evidence and `snapshot_at`
from coverage; changed scopes, completion citations, paths, revisions or relationships
still require fresh review. Incomplete traversal, truncated evidence or missing
comparison fields fall back to new obligations. Unreviewed rows never count as reviewed.
`reused_candidate_count` counts effective reused required rows; each effective
conclusion carries `reused_from` with the original assessment, disposition ID and
version. A fresh local disposition overrides reuse for that assessment. The local
assessment version remains the write guard; a reused conclusion retains its source
version and is absent from the new assessment's authored disposition history.
Changed targets, extent/reason or interrupted supersessions remain ineligible.

Whole-episode reuse requires current candidates and coverage to match the latest
capture. New scope, incoming citations or changed coverage create a fresh episode;
only eligible unchanged candidates can reference a prior conclusion as described above. The new revision
episode always captures the text and metadata transition. An unchanged supersession
can link the same original immutable episode into a new change; this preserves the
original conclusion history without creating a duplicate episode. Without an
explicit unchanged declaration or existing exact governance, changed replacement
text receives a fresh supersession episode. An unpinned target advancing to another
revision also creates a new obligation.
Review work and discovered-from
follow-ups retain change lineage, including later descendants. An open driver
accumulates obligations. Closed changes stop for explicit owner admission before
new obligations; a dashboard `admit` action creates a successor change without
reopening or rewriting completed work. An accepted PDR alone does not perform
admission. Managed answers remain durable with `change.requires_admission` when
new obligations await admission; the change exposes `pending_revision_count`.
The separate admit action materializes those retained transitions in the successor.
When follow-up work moves to another project, its answers use a local held change
while retaining the original project's lineage. That driverless held change also
requires explicit admission. Agents cannot admit a successor or evade lineage
through an unrelated root or false external origin.

Fresh creation can set `inherit_decision_change=false` with a nonblank
`decision_change_inheritance_reason` (at most 4,000 Unicode characters) for an
independent work item. The declared `discovered_from_work_item_id` must remain the
actual, non-null session origin. The server retains the discovery edge and its
selected origin-owned checkpoint, and seals an immutable exclusion for that exact
child-to-origin edge. The default automatic edge uses the origin's initial
checkpoint; a supplied edge retains the caller-selected checkpoint.

This is an edge-specific creation choice. It skips current and future propagation
through that edge only. Other initial discovery edges and alternate paths still
inherit; explicit adoption or a new change originating on the child still binds
the child and its ordinary descendants. Already recorded memberships and existing
work are never detached. Removing/re-adding the same discovery edge preserves its
exclusion; relationship deletion does not delete provenance from the sealed
creation receipt. No project-based heuristic changes lineage.

Full work reads expose `decision_change_inheritance_exclusion` with
`origin_work_item_id`, `origin_context_checkpoint_id`, the exact `reason`, and
`created_at`. Compact/status-only reads do not include its prose. REST false
creation requires `client_operation_id`; MCP supplies an omitted fresh operation
UUID normally. False without a declared origin/reason and a reason with true
return `invalid_decision_change_inheritance` (422). Keep the whole frozen intent
unchanged on retries. Explicit/default true and absent reason preserve historical
fingerprints, and old responses remain sparse. The creation-only table and its
receipt/edge seal are introduced by migration `0064_work_change_inheritance`,
without backfill. Exclusions follow their child work in project backups; restore
checks retain the original receipt, selected checkpoint and reason.

The dashboard retains this explanation after refresh and completion. These
isolated acceptance fixtures show literal text, preserved newlines and wrapping
of long references: [desktop](images/work-change-inheritance/desktop.png) and
[narrow](images/work-change-inheritance/narrow.png).

The task card displays **DECISION IMPACT REVIEW**. Its initial checkpoint records the
exact previous and replacement revision references, source project, impact
assessment IDs, and a rendered snapshot of the editable `decision-impact-review`
prompt from Settings > Prompts. Investigations use ordinary work claims,
checkpoints, gates, priorities, and closeout rules. They are separate from the
specialized code-review queue. Agents investigate downstream code and create
separate standard work with `create_work` for remediation or further research;
the investigation itself must not change code. The default prompt first classifies
the semantic change as rule, scope, references-only, or a combination, based on
the full before/after documents. A heading such as "Related records" does not
make a scope change reference-only. It then separates required code-impact verdicts
(including incoming related citations)
from optional brief contradiction checks of prerequisite and related records.

When linked commits are missing, reviewers search local commit messages for the
stable decision ID (for example, `git log --all --fixed-strings --grep='BDR-00039'`),
inspect files named by the decision, and search source text for its ID with
`rg -n -F -- 'BDR-00039' .`. Matching mentions are leads to inspect, not automatic
proof of decision use; absent local matches do not prove remote or unregistered
implementation is absent. The report retains search and provenance limitations.

REST knowledge domain reads and MCP `list_knowledge_domains` expose
`automatic_decision_reviews` and `decision_review_priority` for each domain. Work detail, task
summaries, ready pointers and compact search expose `decision_review_change_id`
for native drivers and `decision_change_ids` for lineage. Historical
`decision_review_revision_id` markers remain readable and still display the chip.
Work detail includes `decision_change_projects` to resolve inherited changes in
their source projects. Change reads identify the driver's current project,
whether its review is closed, and any already admitted successor.

`decision_change_get(project_id, change_id)` returns the driver, parent change,
current `scope_version`, `acknowledged_scope_version`, and revision/assessment
counts. Page `decision_assessments(change_id=...)` for all current obligations.
The initial checkpoint is a retained starting reference, not the current scope.
`decision_change_update(action="acknowledge", change_id, expected_scope_version,
actor_client, actor_session_id, actor_model, lease_token)` records inspection of
that scope; new obligations invalidate it. `action="adopt"` instead accepts an
ordinary pending `work_item_id`, `expected_work_version`, and 1–100 unique
`assessment_ids` to make existing combined work a native driver. Neither action
merges historical work or changes dispositions. Both use receipt-protected
operation IDs and exact uncertain retries.

Each fresh scope-growth checkpoint names the causing revision and recorded author
session, every newly bound assessment, and old/new captured scope totals. It keeps
three separate counts: code-impact candidate occurrences across episodes, distinct
revision references, and distinct stable decision IDs. The checkpoint restates
per-episode inspection, disposition, remediation, paging, and acknowledgement steps.
Reused episodes count once per change binding; repeated adoption adds no obligation.

A pending `review_scope_changed` notice appears on `get_work(status_only=true)`,
implementation claims/renewals, and fresh token-bearing `add_checkpoint` and
`append_event` responses, plus token-bearing single/batch disposition responses.
Batch responses contain one top-level notice. It contains coordination IDs, old/new scope versions,
episodes added, and the three added-row counts; it contains no revision text,
author context, or findings. Code-review leases never receive it. Status reads can
still show a pending notice when no lease is active. Inspect the current scope and
use the existing version-checked acknowledgement action to clear it. Acknowledging
is not a verdict or permission to implement. A receipt replay returns its original
notice, even after acknowledgement; read status again for current coordination.

A fresh `decision_put` that extends an existing open driver returns
`extended_review`, including that write's scope delta and the observed active
implementation holder (or null). Distinct identities in added rows may already
occur in earlier episodes: these counts are not net-new identities or a claim of
exhaustive real-world impact. Migration `0062_review_scope_notices` records an
immutable per-change `attachment_scope_version` for fresh bindings. Historical
bindings remain null. When an acknowledgement-relative boundary cannot be
established, all three added-row counts are null; the exact version/episode delta
still appears. There is no guessed ordering or historical backfill. Backup audits
check uniqueness and the known suffix after any historical unknown bindings.

The Work details scope panel shows current episodes, allows acknowledgement and
explicit adoption, and offers the human-only successor admission action for closed
drivers and driverless held changes, including inherited changes from another
project. Admission remains visible after refresh. The two agent tools never
expose `admit`. REST uses one receipt-protected
`POST /projects/{project_id}/knowledge/changes` action surface, with
`GET /projects/{project_id}/knowledge/changes/{change_id}` for reads.

`decision_change_get` also returns `lineage_work`, a page of other work carrying
this change identity. It includes current project/work identity, title, lifecycle,
readiness and safe active-lease facts, decision-source membership, discovery origins,
and code-review/remediation status. The current review driver is excluded; deleted
and duplicate members retain explicit identity. Use `lineage_limit` (1–100, default
20) and `lineage_offset`, following `lineage_work.next_offset` until complete. The
same query parameters apply to the REST change GET. Discovery origins are bounded
separately with totals and omitted counts. Mutation receipts do not include this
live page. Inspect relevant open remediation and source work before conclusions
and recheck before completion. Membership is an investigation lead, not proof of
relevance, and this visibility does not create an automatic readiness dependency.

Deploy aligned API, worker, MCP and dashboard with migration
`0066_candidate_reuse`. It adds immutable candidate-conclusion references and preserves
historical assessments, dispositions and
terminal work without creating a review backlog. No new service or environment
variable is required. Migration 0066 refuses downgrade while candidate reuse references
remain. Migration 0064 refuses downgrade while inheritance exclusions
remain; 0063 refuses downgrade while dispositions retain finding IDs or remediation
targets. Earlier migrations also refuse to discard exact attachment versions or
retained batch receipts.

Each investigation drives its existing impact-assessment episodes. Start with
`decision_assessment_get` for the retained diff and candidate pages; the task
stores references rather than a second diff or disposition ledger. Done returns
409 `decision_impact_review_incomplete` until every attached code-impact candidate has an
assessment disposition and each `change-required` candidate, including optional
context dispositions, has linked, distinct,
undeleted remediation work. This does not require remediation to be finished
before the investigation closes. Native grouped reviews also require acknowledgement
of their current scope; `decision_change_scope_unacknowledged` and
`decision_change_scope_conflict` require rereading and inspecting the new scope.
Acknowledgement never substitutes for dispositions. Historical ungrouped reviews
retain their original pinned-episode guard. Additional evidence found by a fresh impact
trace is reported explicitly; it cannot rewrite an immutable episode snapshot.

Check whether code already satisfies the replacement decision first. An
already-compliant result is normal and should receive an evidenced disposition.
Remediation work must name the replacement revision, and its implementation
closeout must record that exact citation and resulting commits through the
standard decision-provenance declaration. The closeout appends scope atomically;
verified integration mappings preserve later merge hashes.

Missing recorded scope does not suppress substantive reviews: unregistered code
and indirect dependencies remain unknown coverage. Bulk changes use explicit
classification and grouping; operators can disable automation before an import,
without creating a backlog when it is enabled again. Existing completed or promoted
reviews and their evidence remain intact.

### Literal citation excerpts

`decision_impact` and `decision_assessment_get` accept optional
`include_citation_excerpts=true`. Defaults omit this evidence to preserve existing
payloads and compact-read budgets. Each candidate then includes `citation_excerpts`
with the exact revision's `source_sha256`, up to three literal `excerpts` of at
most 400 Unicode characters each, and `truncated`. Matches use stable decision IDs
on the candidate's explanatory path, excluding the candidate's own stable ID
even when an earlier revision of that document occurs on the path. Each
match includes a neighboring sentence where the cap permits; Markdown line breaks
also delimit sentences. Long context is clipped around the citation, and omitted
matches or clipped context set `truncated=true`.

Assessment excerpts come from the captured candidate revision, including when a
newer revision exists. They are computed on reads and never modify stored snapshots,
dispositions, or completion obligations. Empty excerpts do not establish that a
candidate is unaffected: text can discuss a rule without naming its decision ID.
Use the pinned full decision text whenever an excerpt is insufficient. Smaller
candidate pages can keep explicitly requested evidence within the client's budget.
