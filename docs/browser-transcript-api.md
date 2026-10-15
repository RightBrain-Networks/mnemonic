# Transcript browser transport

FastAPI owns the additive `/api/transcripts` surface. The current Next.js handlers
remain active in normal deployment. The browser port calls the existing transcript
domain handlers and preserves settings revisions, import and rebuild receipts,
source-root policy, native copies, normalized segments, and indexing services.
No schema change or production cutover is required.

## Explicit route inventory

Paths below are relative to `/api/transcripts`. `P` is `projects/{project_id}` and
`T` is `P/transcripts/{transcript_id}`. Resource IDs use the same UUID syntax as
the existing dashboard proxy. These nine method/path pairs form the complete
inventory; unlisted methods and paths are rejected before domain execution.
The machine-only `POST P/transcripts/search-content` route is not registered.

| Path | Method | Accepted query keys |
| --- | --- | --- |
| `P/transcripts` | GET | `query`, `query_mode`, `sort_by`, `sort_direction`, `content_kinds`, `fulltext`, `detail`, `work_item_id`, `limit`, `offset`, `diagnostics`, `created_after`, `created_before`, `updated_after`, `updated_before` |
| `P/transcripts/health` | GET | None |
| `T` | GET | None |
| `T/text` | GET | `limit`, `offset`, `expected_sha256`, `segment_id`, `expected_normalized_revision`, `before`, `after` |
| `T/content` | GET | `expected_sha256` |
| `P/transcript-settings` | GET | None |
| `P/transcript-settings` | PATCH | None |
| `P/transcripts/rebuild` | POST | None |
| `P/transcripts/import` | POST | None |

Query keys cannot repeat. Listing queries accept nonblank text of at most 200
Unicode characters; query modes are `terms`, `phrase`, or `literal`. Sorting uses
`name`, `size`, `session`, `indexing`, or `updated`, with `asc` or `desc` direction.
`content_kinds` accepts one of `human_text`, `assistant_text`, `tool_call`,
`tool_result`, `system_text`, `reasoning`, `summary`, or `unsupported`.
`fulltext` is `true` or `false`, detail is `compact` or `full`, and diagnostics is
`on_empty`, `always`, or `off`. Work filters require a UUID. Listing limits are
1 through 100 and offsets are 0 through 1,073,741,824. Date bounds require valid
timezone-qualified timestamps with at most six fractional-second digits; each
after bound must precede its corresponding before bound. Domain query models
retain their additional validation and search-coverage behavior.

Text limits are 1 through 20,000, with character offsets from 0 through
1,073,741,824. `expected_sha256` and `expected_normalized_revision` are lowercase
64-digit hexadecimal digests. A 24-digit lowercase hexadecimal `segment_id`
requires `expected_normalized_revision`. Each surrounding-context count is 0
through 20, their sum cannot exceed 20, and a positive `before` count cannot
combine with a nonzero offset. Without a segment ID, a normalized revision or
positive surrounding-context count is rejected.

## Admission and authority

The port uses the foundation's exact Host, Origin, and fetch-metadata trust rules.
Only GET can omit Origin. Forwarded host headers, bearer credentials, cookies,
and caller artifact-access or grant headers confer no browser authority. A private
admission marker protects every registered handler. The domain receives a closed
header set containing `Accept-Encoding: identity`, the external Host, and JSON
content type for mutations; it receives no manufactured machine bearer credential.

Operation, lease, and gate controls remain forbidden in headers and cookies.
Import and rebuild operation UUIDs belong only in their JSON bodies. The browser
surface does not expose claims, transcript enrollment, or approval capabilities.
Machine REST remains independently protected under `/api/v1` and retains its
existing bearer and domain contracts.

Imports use the existing domain discovery policy: the browser cannot widen the
operator-approved source roots or bypass regular-file and source-path checks.
The import directory must be an absolute, nonblank path of at most 4,096 Unicode
characters, without `..` components, control characters below U+0020, NUL, or
invalid Unicode. Settings remain subject to the operator's configured size limit.

## Body, response, and retry contracts

All mutation bodies use `application/json`, identity coding, and a 10-second body
read deadline. Reads accept no body. Body objects require exactly these fields:

| Action | Exact body fields and bounds |
| --- | --- |
| Settings PATCH | `enabled` boolean; `max_file_size_bytes` integer from 1 through 1,073,741,824; positive safe-integer `expected_revision`; at most 4 KiB |
| Rebuild POST | UUID `client_operation_id`; at most 4 KiB |
| Import POST | UUID `client_operation_id` and validated `directory`; at most 32 KiB |

Validated request bytes reach the existing domain handler. Settings retain
optimistic revision checks. Import and rebuild retain their durable operation
receipts: after an uncertain response, retry the same operation UUID and unchanged
body. Import receipt replay does not rescan a directory that has disappeared.
The boundary never generates replacement operation intent.

| Response | Bound and behavior |
| --- | --- |
| Text and normalized segment JSON | At most 1 MiB |
| Other JSON responses | At most 16 MiB |
| Content download | HTTP 200; at most 1 GiB; optional decimal Content-Length, enforced exactly when supplied |

Text and content reads support the existing optional `expected_sha256` pin;
normalized segment reads additionally require their revision pin. Stale pins and
unavailable text retain the domain's conflict responses. Settings conflicts and
operation-identity conflicts likewise retain the existing domain response.
Content downloads stream the published normalized text, not the native session
JSONL, and preserve its bytes and digest. They force `application/octet-stream`
and `attachment; filename="{transcript_id}.txt"`.

Raw chunks flow through ASGI send with backpressure and are never assembled into
a complete in-memory download. The execution/response deadline is 60 seconds.
Disconnected or timed-out reads are cancelled; a dispatched mutation with complete
input can finish its existing receipt after the response becomes uncertain.
Application shutdown drains retained tasks. Native iterators close on rejected
headers and cancellation. Cleanup after final response bytes remains tracked even
if the transport deadline expires. A length mismatch or oversized stream detected
after response headers have left the process terminates the transfer.

Responses reject redirects and nonidentity coding. Bounded JSON must decode as
UTF-8 and parse as JSON. All responses carry no-store/no-transform, nosniff,
same-origin resource policy, and a restrictive sandbox CSP. Source text remains
untrusted, and the existing UI codecs and normalization/coverage warnings remain
applicable.

Local admission failures use the exact envelope
`{"detail":{"code":"transcript_proxy_rejected","message":"..."}}` only for the
following status/message combinations. These responses prove that the boundary
did not dispatch the request:

| Status | Exact message |
| --- | --- |
| 400 | `The request contains a forbidden control header.` |
| 400 | `Invalid transcript query.` |
| 400 | `Invalid transcript settings request.` |
| 400 | `Invalid or oversized transcript settings request.` |
| 400 | `Transcript reads do not accept a body.` |
| 403 | `This dashboard request is not from a trusted origin.` |
| 404 | `Route not found.` |
| 415 | `Encoded requests are not supported.` |
| 415 | `Send transcript settings as JSON.` |

Domain responses cannot manufacture that evidence: any returned JSON containing
`detail.code = "transcript_proxy_rejected"` becomes HTTP 502 with the plain detail
`Mnemonic returned an invalid rejection response.` Other transport failures also
use plain-detail HTTP 502 responses, never the local rejection marker. The generic
uncertain response says `The transcript request could not be completed. For a
pending import or rebuild, retry the preserved request.` A domain error or lost
response must not discard a pending import or rebuild as though admission failed.

## Validation and later routing

Browser tests exercise closed routes, trust and control headers, query and body
bounds, segment locators, rejection provenance, response budgets, and download
headers. PostgreSQL parity tests compare metadata, settings, health, text,
segments, content bytes, stale pins, settings revisions, import replay, and rebuild
replay after an uncertain response. Shared ASGI tests cover backpressure,
disconnects, deadlines, bounded transfers, and resource cleanup. Existing backend
transcript tests and frontend codecs remain applicable.

Run `scripts/test-e2e.sh --browser-transports tests/e2e/transcripts.spec.ts` to
exercise the current transcript UI against the additive transport. The test-only
nginx configuration routes `/api/transcripts` to FastAPI alongside
`/api/artifacts`; other browser requests continue to Next.js. The runner provisions
an isolated acceptance stack and synthetic transcript sources. The ordinary
acceptance command continues to test the existing Next delivery.

For later deployment, preserve the external Host and Origin, disable proxy
response buffering and compression for streamed text, and preserve the response
length, identity, and no-transform contracts. Permit the 32 KiB import body and
allow the API's 60-second execution budget plus its separate body-read budget.
Keep long-lived cleanup tracked at the application boundary. The nginx test
overlay is validation infrastructure, not authorization to change production
routing or restart production services. Backup transport remains separate
migration work.
