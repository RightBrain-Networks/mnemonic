# FastAPI browser API foundation

FastAPI provides the dashboard JSON surface at `/api/mnemonic`, reusing the
existing domain handlers behind `/api/v1`. The browser transport owns origin,
host, route, query, and request-body admission. Domain handlers retain database
transactions, validation, receipt replay, audit provenance, and response models.
The machine REST transport keeps its bearer authentication and public OpenAPI
contract. The browser registrations are excluded from that OpenAPI document.

These transports are additive frontend migration stages. The existing Next.js route handlers,
layouts, and WebSocket rewrite remain active, so the deployed dashboard continues
to use its current transport. Adding FastAPI routes does not redirect existing
dashboard traffic or perform a production deployment. The
[artifact browser transport](browser-artifact-api.md) and
[transcript browser transport](browser-transcript-api.md) also have explicit FastAPI
owners at `/api/artifacts` and `/api/transcripts`. The
[backup browser gateway](browser-backup-api.md) owns `/api/backups` and calls the
existing private worker with its separate credential.

## Ownership and trust

`backend/src/mnemonic_api/application/browser_routes.py` explicitly selects domain
handlers. Adding a machine route does not expose it to browsers. The matching
`browser_policy` package owns allowed paths, methods, query keys, body shapes,
dashboard provenance, and operation-control transport. The browser middleware
checks admission before FastAPI reads the request body or resolves domain
dependencies, and each browser route also requires the middleware's private
admission marker.

HTTP requests must carry a `Host` matching a configured
`MNEMONIC_DASHBOARD_ORIGINS` authority. `Sec-Fetch-Site` may be absent,
`same-origin`, or `none`. An `Origin`, when present, must exactly match a configured
origin and the request host. Only GET permits an absent Origin. Forwarded host and
origin headers do not grant trust. Machine bearer credentials do not replace
browser admission. Operation, gate, and lease controls remain forbidden in
headers and cookies; approved JSON fields retain their existing contracts.

Mutations use the same domain handlers as `/api/v1`, preserving which writes are
receipt-protected. The browser does not mint a replacement operation UUID or
rebuild a retry body. The existing frontend mutation registry continues to retain
exact bytes and classify uncertain outcomes. Successful mutations covered by the
existing live-sync mapping publish through the shared hub, with receipt replays
and unchanged operations retaining their existing suppression. Report dismissal
and report follow-up creation retain their existing absence of live-sync frames.

Browser responses use `Cache-Control: no-store, max-age=0, no-transform`,
`X-Content-Type-Options: nosniff`, and
`Cross-Origin-Resource-Policy: same-origin`. Browser admission does not enable
CORS, make agent lease APIs available, or grant access to machine-only routes.

The transport budget is 15 seconds, or 60 seconds for unified search, duplicate
suggestions and nonblank semantic work searches. Timeouts and disconnects cancel
read waiters while native inference slots remain held until their work returns.
Already executing writes finish their existing receipt and publication, allowing
an exact retry after an uncertain response. Shutdown drains these remaining tasks
before closing shared resources. GET bodies are ignored, matching the former
Next hop; mutation JSON bytes are validated without being rewritten.

## JSON route inventory

All paths below are relative to `/api/mnemonic`. `P` means
`projects/{project_id}`, `W` means `P/work-items/{work_item_id}`, and all resource
IDs are UUIDs. Query parameters are accepted only on the listed read routes;
repeated parameters are rejected except for the bounded `pinned_work_item_ids`
list. POST search is a safe read with its filter object in the JSON body.

| Path | Methods | Accepted query keys |
| --- | --- | --- |
| `runtime-config` | GET | None |
| `projects` | GET, POST | GET: `limit`, `offset` |
| `P` | GET, PATCH | None |
| `P/settings` | GET, PATCH | GET: `work_item_id` |
| `P/priority-rubric` | GET, PATCH | None |
| `P/prompts` | GET | None |
| `P/prompts/{prompt_id}` | GET, PUT | None |
| `P/prompts/{prompt_id}/render` | POST | None |
| `P/search` | POST | None |
| `P/duplicate-suggestions` | POST | None |
| `P/tasks` | GET | `kind`, `status`, `task_id`, `limit`, `offset` |
| `P/code-reviews` | GET | `state`, `availability`, `work_item_id`, `after`, `limit` |
| `P/work-agent-follow-ups` | GET | `state`, `availability`, `work_item_id`, `after`, `limit` |
| `W/code-reviews/{review_id}` | GET | None |
| `W/agent-follow-ups/{follow_up_id}` | GET | None |
| `W/agent-follow-ups/{follow_up_id}/answer` | POST | None |
| `P/activity` | GET | `after`, `start`, `limit` |
| `P/job-completion-reports/count` | GET | None |
| `P/job-completion-reports` | GET | `dismissal`, `work_item_id`, `limit`, `cursor` |
| `P/job-completion-reports/{report_id}` | GET | None |
| `P/job-completion-reports/{report_id}/dismiss` | POST | None |
| `P/job-completion-reports/{report_id}/follow-ups` | GET, POST | GET: `limit`, `cursor` |
| `W/report-follow-ups` | GET | `direction`, `limit`, `cursor` |
| `P/work-items` | GET, POST | GET: work search keys below |
| `W` | GET, PATCH | None |
| `W/checkpoints` | GET, POST | GET: `order`, `limit`, `offset` |
| `W/context` | GET | `recent_limit`, `recent_event_limit` |
| `W/children` | GET | Child-list keys below |
| `W/relationships` | GET | `direction`, `type`, `limit`, `offset` |
| `P/relationships` | POST | None |
| `P/relationships/{relationship_id}` | DELETE | None |
| `W/events` | GET, POST | GET: `order`, `event_type`, `limit`, `offset` |
| `W/completion-evidence` | GET | `limit`, `cursor` |
| `P/human-attention` | GET | `work_item_id`, `limit`, `cursor` |
| `W/gates` | GET | `status`, `limit`, `cursor` |
| `W/gates/{gate_id}/context` | GET | `recent_limit`, `recent_event_limit` |
| `W/gates/{gate_id}/resolve` | POST | None |
| `W/complete` | POST | None |
| `W/defer` | POST | None |
| `W/return-to-pending` | POST | None |
| `W/delete` | POST | None |
| `W/move` | POST | None |
| `W/merge` | POST | None |

Work search accepts `q`, `semantic`, `status`, `status_scope`, `sort`, `tag`,
`source_client`, `external_url`, `source_session_id`, `view`, `detail`,
`duplicate_scope`, `canonical_work_item_id`, `created_after`, `created_before`,
`updated_after`, `updated_before`, `diagnostics`, `query_mode`, `work_fields`,
`limit`, `offset`, and `pinned_work_item_ids`. Child lists accept `status`,
`status_scope`, `sort`, `tag`, `source_client`, `source_session_id`, `limit`,
`offset`, and `pinned_work_item_ids`.

Prompt IDs are limited to `recall-pointer`, `job-completion-report`,
`cold-code-review`, `warm-code-review`, `review-remediation`, `resume-work`,
`review-recommendation`, and `request-human-review`. Priority guidance has its
separate `priority-rubric` route. Body admission preserves each route's exact
dashboard fields and provenance requirements; the table is not authorization for
arbitrary JSON. JSON request budgets are 16 KiB for unified search, 2 MiB for
duplicate suggestions, and 1 MiB for other JSON routes.

## Runtime configuration and sync

`GET /api/mnemonic/runtime-config` exposes exactly four public fields:

| Field | Runtime setting | Default and bounds |
| --- | --- | --- |
| `timezone` | `TIMEZONE` | Trimmed IANA timezone or fixed UTC offset; missing, blank, or invalid values use `UTC`. Compose supplies `America/Detroit` by default. |
| `work_summary_max_chars` | `MNEMONIC_WORK_SUMMARY_MAX_CHARS` | `2048`; integer from 1 through `2^53 - 1` |
| `artifact_max_bytes` | `MNEMONIC_ARTIFACT_MAX_BYTES` | `67108864`; integer from 0 through `1073741824`; 0 disables artifact uploads |
| `backup_max_bytes` | `MNEMONIC_BACKUP_MAX_BYTES` | `67108864`; positive browser-safe integer through `2^53 - 1` |

The response is assembled from those fields, never from a serialization of the
service settings. Database URLs, API keys, backup tokens, storage paths, and
internal service addresses are excluded. The backup worker retains its separate
archive limits; this field preserves the dashboard's configured upload bound.
Compose passes timezone and backup size to API as well as the existing web
service. A later static client can initialize itself without build-time secrets
or environment substitution in JavaScript bundles.

`/api/mnemonic/sync` is a WebSocket alias for `/api/v1/sync`. Both use the same hub
and require an exact configured Origin. Initial frames contain only `type` and
`revision`; invalidation frames add only `scope`. Neither carries record contents
or bearer credentials. The existing Next.js rewrite continues to use the machine
path until a later migration stage changes routing.

## Later static frontend routing

A future static host should route `/api/mnemonic/` to FastAPI while preserving
the external `Host` authority, Origin, request bytes, and WebSocket upgrade.
It must not substitute an internal upstream host or rely on `X-Forwarded-Host`
to satisfy browser admission. Configure the exact public browser origin in
`MNEMONIC_DASHBOARD_ORIGINS`, and keep the browser and machine transports distinct
in reverse-proxy configuration. Existing raw artifact/transcript and backup
locations must be routed deliberately before Next.js can be removed. All four
browser families now have FastAPI owners; the isolated transport overlay proves
their cumulative boundary while production routing remains unchanged.

The source registry, browser policy, and behavioral tests must change together
when the browser surface changes. Runtime and WebSocket tests cover public-field
exposure, defaults and limits, rejected origins, and shared invalidation. Existing
domain and receipt tests remain responsible for transaction and replay behavior.
`test_browser_policy.py` pins the current TypeScript policy and every permitted
method/path pair; `test_browser_boundary.py` and `test_browser_responses.py` cover
admission and response transport. `test_browser_mutations.py` exercises all 15
protected main JSON writes against PostgreSQL, including lost responses,
cross-transport replay, conflicts and notification suppression.
`test_browser_suggestions.py` covers resource admission, deadlines and native-slot
retention through the browser entrypoint. These replace the corresponding Next
boundary coverage when child 4 removes those server handlers; keep the current
frontend codec and mutation-registry tests through that cutover.
No schema migration or production service operation is part of this foundation.
