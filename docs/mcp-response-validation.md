# MCP read response validation

The HTTP request boundary runs model validation, then a typed `response_matches`
predicate for request-specific facts. `response_validation.py` owns UUID equality,
requested page parameters, count arithmetic, and row uniqueness. Tool validators
choose the identities and filters available in their response shape. A rejected
read returns the shared sanitized unexpected-response error without retrying or
including upstream values.

| Read shape | Request checks | Count and identity checks | Domain policy retained |
| --- | --- | --- | --- |
| Projects | Limit, offset | Offset bounds; unique project IDs | No project was requested |
| Work search | Project, limit, offset, view, duplicate scope and canonical target | Offset bounds; unique returned work IDs | Matched-member identity and pointer-only summaries; matching/filter semantics stay with the API |
| Ready work | Limit, offset, minimum priority | Offset bounds; unique work IDs | Pending/dropped readiness; compact rows have no project, tag, or parent field to validate |
| Checkpoint history | Work ID, limit, offset | Offset bounds; unique checkpoint IDs | Full immutable checkpoint content; rows have no project ID |
| Exact relationship | Project and relationship IDs | Exact neutral edge shape | No counterpart body or automatic traversal |
| Relationship history | Project, relative work ID, direction, type, limit, offset | Offset bounds; unique edge IDs; endpoint/direction/counterpart coherence | Different edges may share one counterpart; undirected related edges; pointer-only counterpart projection |
| Event history | Project, work ID, event type, limit, offset | Offset bounds; unique event IDs | Existing event metadata and ownership projections; incomplete legacy-history flag |
| Human attention | Project, optional work ID, limit | Cursor-page bounds; unique gate IDs | Distinct gates may refer to one work item; limit zero is text-free count mode |
| Gate history | Project, work ID, status, limit | Cursor-page bounds; unique gate IDs | Paired resolution state and opaque cursor handling |
| Exact work and bounded recall | Requested project and work IDs; requested recent checkpoint/event limits | Existing model ownership, omission counts, uniqueness and projection checks | Exact alias audit context, canonical pointers, no evidence expansion |
| Completion evidence | Work ID, limit; request/response cursor project and work scope | Common count bounds plus existing episode/checkpoint/child uniqueness | High-water snapshot, ordering, parent ownership, current completion and structured totals; strict bounded wire validation |
| Project settings | Exact project ID | Positive canonical revision; bounded nonblank effective prompt | User guidance is untrusted; settings reads do not author reports |
| Project activity | Exact project, stream, cursor position, and requested limit/start | Contiguous ascending sequence, next-cursor progress, head bounds, typed reference matrix | Compact references only; imported coverage is recorded work events; stale streams fail explicitly |
| Job report list | Exact project, dismissal/work filters, cursor scope, and requested limit | Unique reports, descending creation sequences, initial high-water bounds, exact continuation | Immutable source-owned report text plus current review/source state; reads never dismiss |
| Exact job report | Exact project and report IDs | Report/work/outcome/checkpoint coherence; authoring-prompt hash | Detail includes the immutable prompt snapshot; deleted/alias sources retain exact ownership |
| Duplicate suggestions | Limit, excluded work ID, exact-title matching | Existing unique canonical and matched-member IDs, rank and exact-title counts | Compact response has no project ID; advisory safe-read POST |

Offset bounds allow empty pages even when the offset exceeds the current total,
and do not require a page to be filled to its limit. Cursor-page counts are not
offset counts: a continuation may report a history total larger than the current
page, and gate cursors remain opaque. Completion evidence keeps its stricter
snapshot and continuation contract. UUID comparisons use parsed identities, so
case changes do not manufacture a new identity.

Protected-write request validators and the HTTP transport's mutation-outcome
classification remain responsible for uncertain writes and their single attempt.
These read helpers do not retry, infer missing identities, add response fields, or
change the 32-tool/11-protected-write catalog.

`mcp/tests/test_response_validation.py` applies common malformed-success cases to
all ordinary page tools and covers the valid exceptions above. The evidence,
duplicate-suggestion, exact-work, and protected-write suites retain their domain
contract and mutation-uncertainty coverage.

Phase 12 uses identity-encoded bounded reads: activity pages at 512 KiB, report
lists at 2 MiB, report detail at 256 KiB, and settings at 1 MiB. The settings cap
also accommodates the existing large Recall pointer content. Canonical
sequence/revision values are decimal strings, not floating-point numbers.
The adapter rejects malformed cursor structure, noncanonical encoding,
controls in report prose, aggregate text overflow, and request/result drift
without reflecting upstream text or control values.

Report-bearing `complete_work` and `update_work` responses bind the exact
requested summary, ordered FYIs, prompt revision, work, outcome, version, and
author. Done also binds its checkpoint. A report has its own database insertion
time; it need not equal the checkpoint or work timestamp. Sparse historical
receipt responses remain sparse, but cannot satisfy a fresh report-bearing
intent. Transport parsing retains historical terminal-create requests and old
report omission only for exact permanent receipt replay; the backend enforces
fresh creation and closeout rules after receipt lookup.
