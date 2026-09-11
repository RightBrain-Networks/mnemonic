# Existing transcript imports: review and validation

Application/API/MCP/dashboard 0.44.0 adds existing Claude Code transcript imports
through workspace settings, with migration `0033_transcript_imports`.
See [import behavior and deployment](transcripts.md#import-existing-transcripts).

Before opening the pull request, an independent reviewer received a frozen source
worktree, the repository instructions, and the user requirements. The reviewer
received no implementation discussions and froze findings before any follow-up.
Initial review covered commit `0d27ed3`; fixes were independently verified at
`b8f0144` and `8a981f0`.

Three P2 findings were reproduced with synthetic files and isolated PostgreSQL schemas:

| Finding | Resolution |
| --- | --- |
| Trailing separators and `.` components could create import/enrollment duplicates. | Normalize SQL identity comparisons to match filesystem path semantics; retain exact agent assertions. Tests cover both enrollment orders through completed indexing. |
| Moving enrolled work into a project with the same imported source created duplicate records. | Remove only the redundant import within the existing move transaction. Preserve the enrolled ID, snapshot, work/lease history, and import receipts. Tests cover pending, processing and ready imports, stale workers, rollback and receipt replay. |
| Exactly two leading slashes deduplicated correctly but failed filesystem containment. | Share canonical path spelling across discovery, enrollment lookup and filesystem containment while preserving raw assertions. Test `/`, `//`, and `///` source/folder spellings, including configured roots. |

The final independent verification found no additional actionable issues. The
reviewer independently passed 97 affected tests, checked all original reproductions,
raced imports against moves, and verified symlink, traversal and outside-root rejection.
No private transcript bodies or production database data were read.

Implementation validation:

- Full backend suite: 2,280 tests passed with PostgreSQL enabled before review fixes;
  all 97 affected storage, discovery, import, indexing, migration and move tests
  passed after the final fixes.
- Full MCP suite: 1,475 passed; full dashboard unit suite: 432 passed.
- Python Ruff/type checks and dashboard type checking and production build passed.
- All ten transcript Playwright tests passed on desktop and narrow layouts using
  the isolated stack and real Tika. Four import checks passed again when capturing
  the final screenshots. Isolated backup-service acceptance checks also passed.
- Required gitleaks commit hooks and the all-files pre-commit check passed.

The independent reviewer did not repeat the full repository suites, browser stack,
or live Tika/deployment checks. The implementation team performed those checks;
the final shared-path changes were covered by the affected integration tests.
Repository merge and production deployment are separate operations.

[Desktop screenshot](images/transcript-import-settings-desktop.png) ·
[Narrow screenshot](images/transcript-import-settings-narrow.png)
