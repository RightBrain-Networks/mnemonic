# Agent transcript indexing: adversarial review and validation

Release 0.42.0 adds Claude Code transcript indexing, five MCP reads, the transcript
library and workspace settings. Plugin 0.25.0 and migration
`0032_agent_transcripts` ship together. See [deployment and behavior](transcripts.md).

## Cold review

Before opening the pull request, independent fresh reviewer processes examined
frozen source snapshots without implementation discussions, design documents, or
previous findings. Backend and client reviews ran separately and were repeated
after fixes. A final cold pass inspected the rebased implementation and its
interaction with human review decisions. Review coverage included source-path containment, untrusted parsing,
worker lifetime and lease concurrency, project isolation, metadata and text
identity, migration/backup behavior, bounded search, MCP validation, and browser
mutation recovery.

The reviews combined direct source inspection with supplied frozen snapshots.
Early reviewer processes could not start their shell sandbox, so those passes used
only the supplied source. Findings were based on static review; reviewers did not
independently execute the implementation team’s integration tests. The implementation team ran the PostgreSQL, transport, real Tika,
and browser checks described below. No private transcript bodies were added to
fixtures, review artifacts, or this repository.

The actionable findings and their resolutions were:

| Finding | Resolution and regression coverage |
| --- | --- |
| Truncation could be omitted from successful metadata. | Normalize before cutting; propagate omissions and nested/binary content limits. Parser and indexing tests assert incomplete coverage. |
| Search could materialize a large corpus before checking its budget. | Admit searches using database byte/count aggregates before loading text, with one bounded indexing slot and coherent read snapshots. Capacity and concurrent-growth tests cover rejection. |
| Required closeout arguments blocked historical receipt retries. | Preserve omitted arguments on MCP transport and sparse canonical requests. Reject fresh omissions only after receipt replay; explicit null is authored before new browser requests are frozen. PostgreSQL rollback/replay and MCP transport tests cover both forms. |
| A definite rebuild rejection could trap navigation; a rejected retry could erase an earlier unknown closeout. | Distinguish a fresh rejection from a previous uncertain dispatch. Preserve exact UUID/body until authoritative receipt lookup confirms the outcome. Unit and browser response-loss tests cover both paths. |
| MCP initialization instructions exceeded their established budget. | Condense instructions and retain detailed workflow guidance in plugin references. Catalog and initialization tests enforce the limit. |
| Deep JSON tool arguments could terminate the worker. | Bound nesting iteratively and translate encoder failures into durable extraction failures. The actual background-loop regression proves the next valid transcript indexes. |
| A renewal in progress could allow indexing during an Active lease. | Lock project, work, lease, then transcript; recheck current expiry after the renewal commits. A concurrent-renewal PostgreSQL test exercises this ordering. |
| Migration downgrade could discard durable data. | Lock all new tables and reject populated downgrade. Empty downgrade/upgrade and populated rejection tests preserve snapshots, settings, and receipts. |
| Browser request intake and MCP reads lacked total deadlines. | Bound and cancel incoming request streams; apply complete-request deadlines to transcript JSON and binary reads. Stalled headers, slow bodies, and disconnected request regressions cover cleanup. |
| Failed retries could mix metadata from different source snapshots. | Replace source-derived metadata together for each attempt and clear it on rebuild. A changed-file retry regression rejects stale provenance. |
| Rebuild loaded every transcript object into memory. | Reset the project corpus with one database update and retain the affected count in the same permanent receipt transaction. Rebuild tests cover bounded loading, replay, and stale-worker fencing. |

The final cold review of the rebased source found no actionable P1/P2 issues.

A reviewer also flagged an undefined browser-test helper in a transient snapshot
captured during edits. The final transcript test file defines the helper and
passes TypeScript checking; its actual browser scenarios are included below.

## Format investigation

The operator-designated Claude Code project directory contained 776 JSONL files,
including 629 nested subagent files, at inspection time. Structural inspection
found user/assistant/system records, administrative records, and text, thinking,
tool-use, tool-result, image, and document blocks. No malformed JSONL lines were
observed. Only aggregate structure was recorded here.

The client factory selects Claude Code; its parser detects native/stream JSONL,
JSON arrays, and JSON message envelopes. Synthetic fixtures exercise each format,
nested tool results, administrative-only input, unsupported clients, malformed
input, Unicode, nesting and size limits, and explicit truncation. Binary
attachments are omitted with incomplete-coverage metadata.

## Validation

Validation uses disposable PostgreSQL schemas and Compose stacks. The production
database and client-owned transcript files are not modified. Database-backed tests
are run with `TEST_DATABASE_URL`; skipping that suite is not equivalent validation.

- Backend: full PostgreSQL suite, transcript concurrency/lifecycle suites, migration
  parity, populated backup/restore, Ruff, ty, and generated OpenAPI correspondence.
- MCP: complete suite plus total-deadline transport regressions; Ruff and ty.
- Frontend: complete unit suite, TypeScript and production build; desktop and narrow
  Chromium acceptance for transcripts, artifact search, navigation, work closeouts,
  duplicate merges, receipt recovery, and code reviews.
- Integration: actual pinned Tika extraction for native JSONL, JSON arrays and message
  exports alongside existing text/HTML/DOCX/PDF, truncation and XXE cases; private
  container isolation checks; isolated backup-service acceptance.
- Plugin: packaged-skill tests and release metadata checks; the platform-specific
  macOS Bash runtime test remains covered by its dedicated CI job.

Final execution counts and screenshots are recorded in [validation](validation.md).
