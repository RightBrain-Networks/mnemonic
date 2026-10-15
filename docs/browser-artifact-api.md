# Artifact browser transport

FastAPI owns the additive `/api/artifacts` surface. The current Next.js handlers
remain active in normal deployment. The browser port calls the existing artifact
domain handlers and preserves the separate artifact operation journal, storage,
approval grants, audit records, extraction, and semantic retrieval services.
No schema change or production cutover is required.

## Explicit route inventory

Paths below are relative to `/api/artifacts`. `P` is `projects/{project_id}` and
`A` is `P/artifacts/{artifact_id}`. Resource IDs use the same UUID syntax as the
existing dashboard proxy. Unlisted methods and paths are rejected before domain
execution; grants and machine-only transfer capabilities are not registered.

| Path | Methods | Accepted query keys |
| --- | --- | --- |
| `status` | GET | None |
| `P/artifacts` | GET | `q`, `sort`, `order`, `limit`, `offset`, `include_deleted`, `work_item_id` |
| `P/artifacts` | POST | None |
| `P/artifacts/search-content` | POST | None |
| `A` | GET, PATCH, DELETE | None |
| `A/content` | GET, PUT | None |
| `A/text` | GET | `expected_revision`, `expected_text_sha256`, `offset`, `limit` |
| `A/history` | GET | `limit`, `offset` |

Query keys cannot repeat, and serialized query metadata is limited to 16,000
characters. Passage reads require a positive 32-bit revision, a lowercase SHA-256
text digest, offset from 0 through 8,000,000, and limit from 1 through 20,000.
The existing domain query models retain their additional validation.

## Admission and authority

The port uses the foundation's exact Host, Origin, and fetch-metadata trust rules.
Only GET can omit Origin. Forwarded host headers, bearer credentials, cookies,
caller approval tokens, and caller artifact-access headers confer no browser
authority. A private admission marker protects every registered handler.

The boundary sets `X-Artifact-Access: human-dashboard` for admitted reads and
content search. It supplies a closed header set to the domain handler. Machine
REST continues to require its bearer credential and sensitive-content approval
contract. Broad agent searches retain their existing withheld-content behavior.

`X-Client-Operation-ID` is permitted only for artifact mutations and must be a
UUID distinct from the API credential. Other operation, lease, and gate controls
remain forbidden in headers and cookies. The metadata header is bounded to
16,384 ASCII characters and its fields are allowlisted. DELETE permits only
actor/session metadata. PUT and DELETE require `X-Artifact-Expected-Revision`.
PATCH uses bounded JSON with its matching operation UUID and expected revision;
binary metadata/revision headers are rejected for PATCH and content search.

## Body, response, and retry contracts

| Transfer | Bound and behavior |
| --- | --- |
| Upload / replacement | `application/octet-stream`, identity coding, required filename, streamed with a fixed 1 GiB transport ceiling |
| Metadata PATCH | `application/json`, at most 32 KiB; preserves original request bytes |
| Content search | `application/json`, at most 4 KiB, strict dashboard fields |
| Status response | At most 16 KiB; validates and exposes enabled state, effective maximum, and dashboard message |
| Mutation JSON response | At most 1 MiB |
| History JSON response | At most 6 MiB |
| Other JSON responses | At most 4 MiB |
| Content download | HTTP 200, decimal Content-Length at most 1 GiB, exact streamed byte count |

Zero configured artifact capacity disables every data operation before reading
its body; status remains available. A positive upload limit is enforced by the
domain after permanent-receipt lookup. Keeping the transport ceiling fixed lets
an exact upload retry recover its receipt after the configured positive limit
decreases. Existing content can still be downloaded above that lowered limit.

Raw file chunks flow through ASGI receive/send with bounded prefetch and
backpressure. They are never accumulated into a complete in-memory response.
The transport budget is 300 seconds. Reads and incomplete uploads are cancelled
when the browser disconnects; a mutation whose complete input has already entered
execution can finish its existing receipt after an uncertain response. Application
shutdown drains these retained tasks. Clients retry the preserved bytes, metadata,
operation UUID and revision; the boundary never invents replacement intent.
Native download handles close on header rejection and cancellation. Cleanup after
the final response bytes remains tracked even if the transport deadline expires.

Responses reject redirects and nonidentity coding. Downloads force
`application/octet-stream`, an attachment disposition with a sanitized filename,
and exact length checks. An invalid stream discovered after headers have been
sent terminates the transfer. Bounded JSON must decode as UTF-8 and parse as JSON.
Operation/replay headers are exposed only when the response echoes the exact
request operation ID. All responses carry no-store/no-transform, nosniff,
same-origin resource policy, and a restrictive sandbox CSP.

The existing preview UI continues to interpret bounded downloaded bytes; there is
no new server-side preview execution endpoint. Metadata updates still advance the
revision and invalidate previous passage pins. Search coverage and extraction
signals remain domain-owned.

## Validation and later routing

Browser tests exercise admission, protected headers, pinned passages, disabled
storage, operation replay and sensitive browser versus agent access. Shared ASGI
tests cover bounded transfers, response validation, cancellation, backpressure,
and completion after lost mutation responses. Existing backend artifact tests and
frontend artifact codecs/mutation tests remain applicable.

Use `scripts/test-e2e.sh --browser-transports` with the artifact Playwright specs
to exercise the current dashboard against FastAPI. The test-only Compose overlay
routes `/api/artifacts` to the API and all other browser requests to Next.js. It
uses the isolated acceptance stack and synthetic storage, not production services.
The ordinary acceptance command continues to test the existing Next delivery.

For later deployment, preserve the external Host and Origin, disable request and
response buffering for raw transfers, allow 1 GiB bodies and the metadata header,
and retain no-transform/identity behavior. The checked-in nginx test overlay shows
these settings without changing production routing. Transcript and backup ports
remain separate migration work.
