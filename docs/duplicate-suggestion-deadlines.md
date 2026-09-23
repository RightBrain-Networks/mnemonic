# Duplicate suggestion response deadlines

Application/API/MCP/dashboard 0.74.0 amends the Phase 9 and 0.73.0 timeout
contract. Tool arguments, successful response schemas, retry guidance, model
composition, cache identity, and migration head are unchanged. Claude Code and
Codex clients need no update or timeout increase.

## Ordered budgets

| Boundary | Ceiling |
| --- | ---: |
| Backend response, starting before request admission and body handling | 45 seconds |
| MCP adapter's complete upstream request | 50 seconds |
| Claude HTTP's default per-request timer | 60 seconds |

The backend setting `MNEMONIC_DUPLICATE_SUGGESTION_TIMEOUT_SECONDS` defaults to
45 and accepts integers from 1 through 45. Before recreating the API, change
any explicit value above 45 in `.env` to 45 or lower. Compose's default is 45.
Rebuild/recreate the API and MCP services together; the dashboard release
metadata advances with them. No database migration is required.

The adapter's duplicate-specific HTTP phase timeout and outer asyncio deadline
both use its 50-second constant. A cross-package regression loads the backend's
actual ceiling and requires at least five seconds between each boundary.
Other adapter extended reads retain their existing 60-second ceiling. The
dashboard proxy's existing 60-second budget also exceeds the API ceiling.
[Claude's documented HTTP timer](https://code.claude.com/docs/en/mcp#managing-your-servers)
can be extended by client settings; this fix does not rely on an extension.

## Useful responses before worker completion

Request admission is clipped to the remaining response budget. The outer
middleware bounds body reception, executor waits, database checkout and reads,
model loading, inference, and downstream response production. PostgreSQL
transaction/statement/lock timeouts and per-checkout queue deadlines stop late
database work where possible.

Internal work reserves one second for response delivery, or half the remaining
budget when less than two seconds remain. One coherent snapshot captures
candidate identities, lexical ranking, bounded composition text, and disposable
vectors before any query inference. Cache vectors are checked against their
stored composition/version and then against the query vector's dimensions.

The worker retains a lexical response before model loading or query/document
inference. If its await deadline expires, the route returns that response with
`semantic.inference.reason=deadline_exceeded`,
`semantic.comparison_incomplete=true`, and the existing one-retry-after-one-second
guidance. If no coherent lexical result exists yet, it returns
`503 duplicate_suggestion_unavailable` with the same semantic disposition.

Completed semantic ranking is separately retained before cache publication.
A stalled cache write preserves that ranking and reports
`semantic.cache_refresh={status: failed, reason: cache_refresh_failed}`.
Retained pages are independent of subsequent worker updates. An internal worker
still running after fallback prevents starting another external worker; supplied
external candidates report `external_scope=unavailable` in that case.

A native call cannot be forcibly cancelled safely. Its inference permit and
the request's permit remain held until actual worker completion. Expiry and
cancellation prevent queued or subsequent native calls. Saturated request
capacity returns `duplicate_suggestion_busy`; it never blocks `create_work`.
MCP errors preserve the documented busy/unavailable code in their error text.
Adapter timeout or an unrecognized upstream 5xx reports
`duplicate_suggestion_unavailable`, with no automatic retry.

## Incident and regression evidence

On September 23, the deployed 0.73.1 API and MCP timeout-path source hashes
matched checkout `361d65e`. Replaying the reported draft and a minimal draft
both returned adapter failure at approximately 60.05 seconds. Backend document
inference fallback warnings followed at 63.4 and 72.2 seconds respectively.
The native inference wrapper checked time after returning from the native call;
the asynchronous outer response timeout competed with the adapter's identical
60-second timer. The model queue was already bounded. PR #134 stabilized tests
and did not modify runtime behavior.

Before the fix, all three initial PostgreSQL fault-injection cases failed:
query inference, document inference, and cache publication slept three seconds
under a two-second response budget and returned only at the outer deadline.
With the fix, those cases return useful results within budget while their
workers remain active. Additional regressions cover slow model loading,
snapshot failure, request admission, permit release, independent creation,
budget ordering, and adapter transport cancellation.
