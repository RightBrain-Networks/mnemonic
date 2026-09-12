# Codex transcript review and validation

Two fresh subagents independently performed cold adversarial reviews before the
pull request was opened. They read repository instructions, source changes, and
tests, and froze findings before reading implementation context or discussing
remediation. One reviewed the full integration; the other concentrated on native
Codex records, parser bounds, and coverage reporting.

The reviews identified three issues, with encrypted arguments reported by both:

- An unreadable imported Codex source could retain a provisional Claude client
  after permissions were repaired. Imported extraction now detects the client
  from the current bounded source bytes and publishes it under the existing
  generation/token guard. Rebuild preserves source identity and import receipts;
  explicit agent client assertions remain authoritative.
- Encrypted function arguments were omitted without incomplete coverage.
  Readable arguments remain searchable and encrypted omissions now set
  `truncated`, consistent with encrypted reasoning.
- Completed plan and function-result events can carry durable text without a
  matching raw response item. The adapter explicitly extracts those event items
  while continuing to suppress canonical message mirrors.

Synthetic regression tests cover every finding, including repaired file access,
receipt replay, stale worker publication, encrypted arguments, and event-only
text. Review follow-up checks confirm the fixes before PR publication.

Validation includes the complete PostgreSQL-backed backend suite; MCP tests and
static checks; dashboard tests, type checking and production build; Codex browser
acceptance on desktop and narrow screens; and the isolated production-Compose
mount probe. The probe verifies all three same-path read-only roots, UID access,
credential exclusion, missing sources, and disabled defaults. Browser acceptance
uses synthetic primary and child transcripts with real Tika and Tantivy, and
checks metadata-only search, opt-in contents, safe preview, download and rebuild.

The first MCP run exposed a stale OpenAPI version snapshot (corrected) and an
intermittent pre-existing stdio notification assertion. Both failed checks passed
on targeted rerun. Required GitHub CI remains the merge gate.

Screenshots: [desktop](images/codex-transcripts-desktop.png) and
[narrow](images/codex-transcripts-narrow.png).
