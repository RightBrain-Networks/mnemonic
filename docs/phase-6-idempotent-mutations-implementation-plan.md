# Mnemonic Phase 6 — Idempotent Mutations Implementation Plan

**Status:** Implemented and fully verified 2026-09-01; post-implementation cold review pending

**Scope:** Roadmap Phase 6, “Idempotent Mutations”

**Source of product intent:** `docs/roadmap.md`

**Planning precedent:** `docs/phases-4-5-implementation-plan.md`

**Implementation baseline:** implementation rebased onto main at commit `13357b1`, where
`0012_pending_deferred_statuses` follows `0011_project_settings`; the shipped Phase 5
milestone is `f959ea5`

**Planning constraint:** this document defines implementation work; it does not implement it.

**Observed implementation evidence:** `docs/validation.md`, “Phase 6 idempotent-mutation
validation”

**Final integration amendment (2026-09-01):** While Phase 6 was being implemented, main gained
`0012_pending_deferred_statuses` and the human-only dashboard deferral action. The implementation
was semantically rebased onto that history: Phase 6 is Alembic revision
`0013_idempotent_mutations`; live work uses Pending/Deferred while historical event snapshots
continue to accept legacy `open`; and `defer_work` is enrolled as the tenth protected REST and
ninth protected browser operation. It is deliberately not an MCP tool, so the MCP protected set
remains the original nine. References below to the nine original operations describe the shared
MCP/API set; final REST and browser acceptance includes deferral as specified by this amendment.

## 1. Outcome

After Phase 6, a client can assign one stable operation UUID to a supported mutation before it
sends the request. If the response is lost, the client can repeat the same validated request and
receive the original successful status and JSON result without performing the mutation again.
Mnemonic, rather than prose-based recovery or a client-side search, decides whether the operation
already committed.

The completed phase must have these observable properties:

1. One client operation can produce at most one successful domain mutation, even when identical
   requests arrive concurrently.
2. A retry of the same successful request returns the original JSON value and HTTP status,
   including original IDs, timestamps, versions, and `created`, `removed`, or `released` flags.
3. The domain mutation, its authoritative `WorkEvent` rows, and its replay receipt commit in one
   PostgreSQL transaction or all roll back.
4. Reusing a successful operation UUID for a different operation, project-local target, actor,
   version, capability, body, metadata object, or other semantic input fails closed with one
   sanitized conflict and performs no work.
5. Replay is resolved before current work visibility, lifecycle, version, relationship, or lease
   checks. A committed completion, deletion, edge removal, or release can therefore be recovered
   after the source fact has changed or disappeared.
6. A replay creates no work, checkpoint, event, edge, version increment, activity timestamp
   change, lease change, or semantic-cache write. An applied replay may repeat only the existing
   data-free live-sync invalidation so a retry heals a commit-before-publication failure window.
7. Only successful 2xx results bind operation UUIDs. Authentication, validation, domain conflict,
   rollback, and unavailable-database outcomes leave no committed receipt.
8. Canonical MCP mutation tools in scope require a caller-generated `client_operation_id` and are
   honestly annotated idempotent. Direct REST callers may omit the field and retain the existing,
   explicitly unprotected behavior.
9. Operation receipts contain no bearer credential, raw lease token, claim request ID, raw request
   body, or automatic copy into a checkpoint, event, normal response, WebSocket message, log, or
   metric label.
10. Receipt retention is durable and has no TTL in Phase 6. Backup and restore preserve replay
    knowledge; an old successful key never silently becomes executable again.
11. Existing projects, work, checkpoints, leases, relationships, events, and soft-deleted data are
    unchanged. Historical operations receive no fabricated keys or results.
12. Phase 7 gates and Phase 11 verification results can adopt the same service and persistence
    contract without Phase 6 creating placeholder domain models or public operations for them.

Phase 6 is retry identity, not semantic de-duplication. Searching before `create_work` remains
necessary because two different operation UUIDs can intentionally create two work items with the
same words. Phase 6 is also not authentication: client and session fields remain asserted
provenance under Mnemonic's shipped shared-bearer trust boundary.

## 2. Shipped Phase 5 baseline

Phase 6 starts from the implementation in the repository, not only from the earlier plan.
Mutation services stage domain rows and authoritative events without committing; route functions
own the single commit. `get_session` rolls an unfinished transaction back when a request fails.
This is the foundation the receipt must join.

### 2.1 Current mutation behavior

| REST mutation | Current unknown-outcome behavior | Phase 6 decision |
| --- | --- | --- |
| `POST /projects` | A repeated create usually reaches `slug_conflict`; it cannot return the original project | Deliberately outside the project/operation-UUID-scoped Phase 6 ledger |
| `PATCH /projects/{project_id}` | Re-executes the unversioned update and advances `updated_at` | Deliberately outside Phase 6; project administration needs its own actor/scope design |
| `POST /projects/{project_id}/work-items` | Generates new work/checkpoint IDs and duplicates the logical request | Protect |
| `POST /projects/{project_id}/work-items/{work_item_id}/checkpoints` | Generates another checkpoint and event | Protect |
| `POST /projects/{project_id}/work-items/{work_item_id}/events` | Generates another progress event and activity update | Protect |
| `POST /projects/{project_id}/relationships` | Natural-key replay changes `created=true` into `created=false` | Protect all relationship types and return the original result |
| `PATCH /projects/{project_id}/work-items/{work_item_id}` | Usually becomes `version_conflict`; a same-value request would otherwise version again | Protect |
| `POST .../{work_item_id}/defer` | Leaves work Deferred; a retry with a new key becomes `invalid_status_transition` and cannot recover the original result | Protect for REST/dashboard; keep absent from MCP |
| `POST .../{work_item_id}/complete` | Becomes `work_not_pending` after a successful lost response | Protect |
| `POST .../{work_item_id}/delete` | Becomes `404` because the work is now soft-deleted | Protect |
| `DELETE /projects/{project_id}/relationships/{relationship_id}` | Natural no-op returns `removed=false`, not the original `true` | Protect |
| `POST .../{work_item_id}/release-claim` | Natural no-op returns `released=false`, not the original `true` | Protect without persisting the lease token or a capability-bearing response |
| `POST .../{work_item_id}/claim` | Has a specialized active-lease replay keyed by holder/session/`claim_request_id` | Preserve the specialized contract; do not put claim receipts in the generic ledger |
| `POST .../{work_item_id}/claim-and-recall` | Replays the lease receipt while assembling current bounded context | Preserve; the full context response intentionally is not a frozen old snapshot |
| `POST .../{work_item_id}/renew-claim` | Every success moves lease expiry forward | Preserve as deliberately non-idempotent |

The semantic-search `GET` path may commit a disposable embedding cache. It is still a logical read,
is not an agent mutation, and remains outside Phase 6.

### 2.2 Existing narrow replay and no-op behavior

Phase 6 must compose with, not erase, these shipped guarantees:

- An active identical claim request returns the retained token without extending the lease or
  adding a second claim event.
- A relationship natural-key add returns the retained edge with `created=false` and emits no
  additional endpoint events.
- An absent relationship removal and absent/mismatched-expired lease release return a successful
  `false` result and emit nothing.
- Event fact uniqueness prevents duplicate events for one checkpoint, lease generation, or
  relationship action ID, but it cannot prevent a retried request from generating a new source
  record with a new ID.
- Work updates use optimistic versions, but a version conflict cannot recover the first response.
- The live-sync middleware currently publishes for every 2xx `POST`, `PATCH`, or `DELETE` based
  only on method and path, including natural no-ops. It has no replay outcome signal yet.

### 2.3 Existing lock and transaction order

The shipped explicit order is:

1. the project row for graph mutations;
2. graph endpoint work rows in UUID order, or the focal work row for ordinary work mutations;
3. the retained lease row where relevant;
4. the relationship source fact where removal requires it;
5. authoritative event inserts;
6. one route-owned commit.

Phase 6 adds one receipt reservation before this sequence for protected requests. It must not add a
late project foreign-key lock that reverses ordinary work-to-project order at commit.

## 3. Decisions fixed by this plan

### 3.1 Exact Phase 6 coverage

The generic receipt mechanism covers ten current REST operations with truthful, project-local
provenance. Nine are also canonical MCP tools; deferral is REST/dashboard-only. Provenance is part
of the request fingerprint, not the uniqueness scope:

| Operation kind | REST request | Required provenance | Original response |
| --- | --- | --- | --- |
| `create_work` | `WorkItemCreate` | initial checkpoint `source_client` and `source_session_id` | `201 WorkCreation` |
| `add_checkpoint` | `CheckpointCreate` | checkpoint `source_client` and `source_session_id` | `201 CheckpointRead` |
| `append_event` | `ProgressEventCreate` | nested mutation actor | `201 WorkEventRead` |
| `add_relationship` | `RelationshipCreate` | `created_by_client` and `created_by_session_id` | `200 RelationshipCreationResult` |
| `update_work` | `WorkItemPatch` | nested mutation actor | `200 WorkItemRead` |
| `defer_work` | `WorkDeferralCreate` | nested mutation actor | `200 WorkItemRead` |
| `complete_work` | `WorkCompletionCreate` | completion checkpoint source client/session | `200 WorkCompletionRead` |
| `delete_work` | `WorkDeletionCreate` | nested mutation actor | `200 WorkDeletionRead` |
| `remove_relationship` | `RelationshipRemovalCreate` | nested mutation actor | `200 RelationshipRemovalResult` |
| `release_claim` | `LeaseReleaseCreate` | nested mutation actor, never the retained holder | `200 ReleaseResult` |

`add_relationship` covers `blocks`, `parent-child`, `discovered-from`, `duplicate-of`, and
`related`; do not build a dependency-only parallel path. One `create_work` receipt covers the new
work item, initial checkpoint, every actually created initial relationship, and all corresponding
events as one aggregate operation.

The following are explicitly outside the generic ledger:

- `create_project` has neither an existing project scope nor current session provenance. Adding an
  installation-global identity envelope solely for this phase would broaden the model beyond the
  roadmap's work-coordination priority.
- `update_project` is a REST-only administrative mutation with no actor seam or optimistic version.
- `claim_work` and `claim_and_recall` return a live capability. Replaying a frozen successful token
  after the lease expired or was released would falsely imply current ownership. Their existing
  `claim_request_id` recovery stays deliberately lease-lifetime bounded.
- `renew_claim` is intentionally time-relative: a new successful renewal is a new intent that
  recalculates expiry, and its response also contains the lease token.
- Gate creation and verification submission do not exist yet. Phase 7 and Phase 11 must enroll
  them when their authoritative models and response contracts exist.

Documentation and annotations must say “Phase 6-covered mutation” rather than claiming that every
2xx write in the service is generically idempotent.

### 3.2 `client_operation_id` wire contract

`client_operation_id` is a caller-generated UUID placed at the top level of a covered JSON request.
It is never accepted from a URL, query parameter, HTTP idempotency header, transport identity, or
server-generated fallback.

Use a UUID rather than a free-form 200-character string because it:

- makes high-entropy generation the normal contract;
- bounds the unique index independently of Unicode byte length;
- reduces accidental storage of prose, credentials, or other correlatable content;
- has the same canonical representation across REST, MCP, browser, and PostgreSQL.

Direct REST keeps the field optional, as proposed by the roadmap. A request without it takes the
existing transaction path and has no general recovery promise. Every covered canonical MCP tool
requires it; the MCP adapter must never generate it inside the call because a stateless repeat
would then receive a different value.

For `update_work`, `defer_work`, `delete_work`, `remove_relationship`, and
`release_claim`, an optional REST actor becomes required whenever
`client_operation_id` is present. This gives a protected mutation truthful provenance and makes
any actor change a fingerprint conflict. The service never infers the actor from the bearer key,
dashboard proxy, current lease holder, relationship creator, HTTP connection, or MCP transport.
Actor omission remains permitted only for an unkeyed direct REST request and retains the Phase 5
`unattributed` event behavior.

The operation UUID is control data, not provenance. It does not appear in:

- a work item, checkpoint, relationship, lease, or `WorkEvent` column;
- event metadata or checkpoint `source_metadata` automatically;
- an ordinary mutation response, context, resource, prompt, timeline, ready/search pointer, or
  project activity feed;
- a WebSocket invalidation;
- application/MCP/proxy logs or metric labels.

New progress-event requests recursively reserve the `client_operation_id` key, matched
case-insensitively, so callers cannot make retry control look like new historical domain content.
This is a request-generation rule, not a reinterpretation of Phase 5 metadata-v1. Keep the Phase 5
`EventMetadata`/`ProgressEventMetadata` history/read validation types and
`mnemonic_work_event_metadata_v1_is_valid` function unchanged so an existing event that legally
used that external-reference key remains readable and restorable through `WorkEventRead`. Add a
distinct Phase 6 progress-event input validator for the new request boundary and a separate
PostgreSQL `CHECK ... NOT VALID`. PostgreSQL enforces that constraint for newly inserted rows, but
the migration never validates it against preserved history.

### 3.3 Exact uniqueness scope

The durable uniqueness scope is:

```text
project_id
client_operation_id
```

Operation kind, target, and the complete validated provenance remain in the fingerprint, not the
primary key. Reusing one UUID for a different tool, target, client, session, model, or other actor
value inside one project therefore conflicts instead of opening another executable namespace.
The same UUID may exist in another project, although clients should generate a fresh random UUID
for every new intent.

This deliberately strengthens the roadmap's suggested session-scoped tuple. A caller recovering
after browser/session identity loss cannot accidentally execute the operation again merely by
asserting a new session. UUID entropy makes accidental cross-client collision negligible; a real
collision is safer as a conflict than as a second mutation.

Project scope does not authenticate the caller. Under the shared bearer model, all provenance is
still asserted. A future multi-user authorization phase must add the authenticated principal to
replay authorization without weakening the permanent project/UUID binding.

### 3.4 What counts as the “same request”

Sameness is semantic after strict request validation, not byte-for-byte HTTP equality. The server
computes a versioned canonical fingerprint over:

- API contract namespace and operation kind;
- normalized project ID and every target path ID;
- the validated, default-expanded request model;
- exact ordered array contents except initial relationships, whose order is normalized using the
  same domain sort key used before execution;
- canonical UUIDs, enum values, normalized tags, and Pydantic-trimmed identity fields; identity
  case and Unicode code points otherwise remain exact;
- exact checkpoint prompt and progress body bytes after the existing validation rules;
- the complete bounded JSON metadata value with deterministically ordered object keys;
- `expected_version`, actor/source model, initial relationships, relationship context, and every
  optional semantic field;
- an optional lease token when one was supplied.

The fingerprint excludes only `client_operation_id` and the HTTP bearer credential. Omitted and
explicit default values that validate to the same model are the same request. JSON object key order
and insignificant wire whitespace are not semantic. Array order otherwise remains semantic. A
changed actor client, session, or model remains in the same project/UUID scope and causes a
fingerprint conflict.

The canonical request is held only in process long enough to compute
`SHA-256(domain_separator || random_salt || canonical_bytes)`. The ledger stores the random
32-byte salt and 32-byte digest, never the raw request or raw lease token. The salt prevents a
reusable unsalted digest corpus and equality comparison across rows; it is not an encryption key
or protection from an attacker with unrestricted database access. Bearer-key rotation has no
effect on replay.

### 3.5 Exact replay semantics

After authentication and strict request parsing, a matching completed receipt is handled before
any current domain lookup or capability validation:

1. Match the exact uniqueness scope.
2. Recompute the canonical fingerprint with the receipt's salt.
3. Require the stored operation kind, fingerprint version, fingerprint, and response contract
   version to match the registered route contract.
4. Validate the stored JSON through the route's strict response model.
5. Return the stored HTTP status and JSON value.
6. Commit the read transaction to end receipt-wait bookkeeping, then mark the request as a replay
   for outcome-aware live-sync middleware and the existing bounded kind/outcome logger.

The replay does not re-run current checks. Consequently:

- a `create_work` replay after later edits returns the original version-1 work snapshot;
- a checkpoint or progress replay returns its original row even after newer history exists;
- a relationship-add replay after removal returns the original `created=true` result but does not
  recreate the edge;
- an update replay after another update returns the original versioned response but does not roll
  current work backward;
- a deferral replay after work returns to Pending returns the original Deferred snapshot but does
  not defer the work again;
- a completion replay after reopen returns the original completed response but does not complete
  the work again;
- a deletion replay succeeds despite ordinary work visibility now returning `404`;
- a relationship-removal replay returns its original `removed=true` even though the edge is gone;
- a release replay returns the original result and cannot delete a replacement lease.

A replay result is historical proof of the original operation outcome, not a current-state read or
execution authority. Clients that need current state follow it with `recall_work`, `get_work`,
`list_relationships`, or the relevant read operation.

“Original response” means the same HTTP status and parsed JSON value. It does not promise the same
JSON object key order, whitespace, transport headers, TCP behavior, or WebSocket delivery.

### 3.6 Key reuse conflict

If the uniqueness scope exists but the operation kind, target, fingerprint version, or fingerprint
does not match, return:

```text
409 client_operation_conflict
```

The safe message says that the operation UUID is already bound to a different successful request
and that a genuinely new intent needs a new UUID. Error context is empty. It never exposes the
operation UUID, original kind, target IDs, fingerprint, request or response fields, actor/session,
prompt, metadata, version, claim ID, or lease token.

This comparison fails closed. It does not execute the new request, return the old response to a
mismatched caller, or guess that two similar operations are equivalent.

### 3.7 Successful results bind; failures do not

Only a fully serialized successful 2xx result may commit a completed receipt. The following leave
no durable operation row because the entire request transaction rolls back:

- invalid authentication or request validation;
- project/work/checkpoint/relationship not found;
- version, lifecycle, blocker, parent, cycle, lease, or secret-echo conflict;
- event/metadata validation failure;
- domain or event flush failure;
- response-model validation or response-size failure;
- receipt finalization or database commit failure.

A subsequent call may use the UUID after a definite failure because no success owns it. Client
guidance remains simpler and stricter: unchanged retry uses the same UUID; changed intent or changed
arguments use a new UUID. Do not cache 4xx/5xx responses or create error tombstones in Phase 6.

A natural no-op 2xx is still a successful result and does bind the UUID. For example, if the first
keyed relationship add observes an already existing edge, that receipt permanently replays
`created=false`. It does not later become `true` if the edge is removed and re-added.

### 3.8 Claims and general operations remain distinct

Do not rename `claim_request_id` to `client_operation_id`, copy it into the receipt table, or make
claim results look durably replayable.

- Claim replay proves that the same retained active lease still exists and may safely return its
  token.
- General operation replay proves that one durable mutation result committed and returns a
  non-capability response snapshot.
- `claim_and_recall` deliberately returns current bounded context after acquisition/replay rather
  than freezing a potentially large capability-bearing context response.
- Renewal remains a new time-relative action on every success.

MCP annotations and workflow documentation keep these recovery instructions separate. A tool host
must not infer that Phase 6 makes claim or renewal retries generally idempotent.

### 3.9 Durable retention and contract evolution

Completed receipts remain indefinitely in Phase 6. There is no expiry column, cleanup task,
“last seen” update, attempt counter, purge endpoint, or background worker. Silently deleting a
receipt would allow an arbitrarily delayed retry to execute again, which violates the phase's
central safety claim.

Receipt response bodies can duplicate checkpoint, event, or work text already present in canonical
tables. This is an intentional cost of returning an original mutable/deleted result. The table is
private operational state, follows the project's backup/privacy boundary, and is measured during
validation. It is not exposed as a history or activity API.

Store both `request_fingerprint_version=1` and `response_contract_version=1`. The exact canonical
v1 projection for each enrolled `/api/v1` request is frozen for that API version. Because raw
requests are intentionally absent, old digests cannot be data-migrated after a breaking request
shape/default/normalization change. Such a change requires a new API contract/version; old rows
remain permanent non-executable tombstones for that project/UUID and return unavailable rather
than ever falling through to execution. Do not retain parallel runtime request unions merely to
simulate prerelease compatibility.

Stored response snapshots can be migrated in a reviewed database revision when their meaning is
unambiguous. The completed-row immutability trigger may be replaced temporarily only inside that
quiesced migration and must be restored before writers resume. Never silently reinterpret either
contract version.

### 3.10 Live-sync behavior

Phase 6 makes live invalidation outcome-aware for covered routes:

- a keyed or unkeyed original covered request that changes domain state publishes one existing
  data-free invalidation after commit;
- a keyed or unkeyed original covered natural no-op publishes none;
- an exact replay with stored `mutation_applied=true` republishes that same data-free invalidation;
- a replay with stored `mutation_applied=false` and every failed request publish none.

A repeated invalidation is an idempotent refresh hint, not a domain side effect. Republishing closes
the commit-before-middleware-publication crash/cancellation window for other still-open dashboards;
a transactional outbox would be disproportionate for the current in-process best-effort channel.
Covered-route orchestration returns `executed/replayed/unprotected` plus `mutation_applied` and
places only the boolean publication decision on request state. Write routes outside the ten
covered operations retain the shipped successful-method/path fallback when no explicit decision is
present; the middleware must not silently suppress them. No operation UUID or response data enters
the WebSocket event. Reconnect/refetch remains an additional recovery path.

## 4. Requirement identifiers

| ID | Requirement |
| --- | --- |
| `IM-1` | One project/operation UUID executes at most one successful semantic request, including under concurrency and provenance changes |
| `IM-2` | Exact retry returns the original status and parsed JSON result before current domain guards |
| `IM-3` | Key reuse with any semantic mismatch returns sanitized `client_operation_conflict` and no effect |
| `IM-4` | Domain rows, authoritative events, and the completed receipt commit or roll back together |
| `IM-5` | Truthful client/session provenance is required and fingerprinted, never inferred from transport/auth/lease state or used to open another key scope |
| `IM-6` | Raw requests, bearer credentials, lease tokens, claim IDs, and operation IDs do not leak into history, logs, errors, metrics, or unrelated responses |
| `IM-7` | Receipt reservation is first in the protected lock order and adds no project/work lock inversion |
| `IM-8` | Receipts are immutable, durable, bounded, backed up, restored, and never silently expired |
| `IM-9` | Covered MCP tools require the key and are honestly annotated; excluded lease/project operations retain distinct guidance |
| `IM-10` | Existing data is unchanged and no pre-Phase-6 operation receipt is invented |
| `IM-11` | Replays/no-ops emit no duplicate durable side effect; applied replays may repeat only a data-free healing invalidation |
| `IM-12` | The operation registry can enroll gates and verification later without placeholder Phase 7/11 models |

## 5. Persistence model

### 5.1 `client_operations`

Migration `0013_idempotent_mutations` adds one private table:

```text
id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
project_id                  UUID NOT NULL
client_operation_id         UUID NOT NULL
operation_kind              VARCHAR(40) NOT NULL
request_fingerprint_version SMALLINT NOT NULL DEFAULT 1
request_fingerprint_salt    BYTEA NOT NULL
request_fingerprint         BYTEA NOT NULL
response_contract_version   SMALLINT NOT NULL DEFAULT 1
state                       VARCHAR(16) NOT NULL DEFAULT 'pending'
response_status             SMALLINT NULL
response_body               JSONB NULL
mutation_applied            BOOLEAN NULL
created_at                  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
completed_at                TIMESTAMPTZ NULL
```

The exact `operation_kind` values are the ten names in Section 3.1. Future phases widen the
database check and application registry in the same increment that adds a new mutation; Phase 6
does not reserve fake gate or verification kinds.

Constraints and indexes:

- named unique constraint on `(project_id, client_operation_id)`; the reservation INSERT names
  this exact conflict target and does not swallow unrelated integrity errors;
- check `operation_kind` against the exact Phase 6 registry;
- check `request_fingerprint_version = 1` and `response_contract_version = 1`;
- require exactly 32 bytes for both the random salt and SHA-256 digest;
- check `state IN ('pending', 'completed')`;
- pending rows require response status/body, `mutation_applied`, and `completed_at` all null;
- completed rows require status `200..299`, a JSON object response, nonnull
  `mutation_applied`/`completed_at`, and a maximum
  `octet_length(response_body::text)` of 1 MiB;
- require `completed_at >= created_at` using database timestamps captured in the same transaction;
- no index by actor, operation kind, timestamp, target, or response content until an observed
  operator query requires one; the exact unique lookup is the hot path.

The database's 1 MiB `octet_length(response_body::text)` check is the authoritative storage
bound. The application separately measures the exact UTF-8 JSON bytes it will return and refuses a
body above the same public limit before finalization. Those serializations need not have identical
byte counts; both must pass. Boundary/property tests calculate both representations at the real
current maxima—including a 100,000-character Unicode checkpoint, escaped control characters,
16 KiB metadata, and ten initial relationships—rather than assuming character count equals bytes.

### 5.2 Logical project scope and deliberate absence of resource foreign keys

`client_operations.project_id` is a logical scope and intentionally has no foreign key to
`projects`; there are also no work, checkpoint, relationship, event, or lease foreign keys.

This exception preserves the concurrency architecture. A receipt is reserved before domain locks.
A project foreign-key check can take `KEY SHARE` before a graph mutation's project `FOR UPDATE`, or
defer a work-to-project lock until commit while another transaction holds project-to-work. Either
form can invert the shipped graph/work order and deadlock unrelated multi-agent writes.

The application validates the project through the owning mutation on first execution, the unique
scope prevents a receipt from affecting any other project, and no project-deletion operation exists
today. Receipts must also outlive soft-deleted work, hard-deleted relationships, and consumed
leases. A future physical project purge must explicitly address receipt retention and lock order in
that phase rather than gaining accidental cascade behavior now.

Direct SQL can create an orphan logical scope only inside the already trusted database/operator
boundary. It cannot mutate domain state through the receipt table.

### 5.3 Pending reservation and completed-row immutability

The table supports a pending state only inside the transaction that may perform the mutation.
Install these PostgreSQL guards:

1. A `BEFORE INSERT` trigger requires `state='pending'` and every response/result/completion
   field null. Direct SQL cannot bypass the reservation protocol by inserting a completed row.
2. A deferred constraint trigger runs at commit. Because a deferred trigger's captured `NEW`
   tuple still reflects the original event, its function queries the row again by primary key and
   rejects it unless the current stored state is `completed`. A crashed, cancelled, failed, or
   forgotten-finalization request therefore rolls back; no other transaction can observe a
   committed pending receipt.
3. A mutation trigger permits exactly one transition from pending to completed while every scope,
   operation, salt, fingerprint, version, and creation field remains unchanged.
4. The same trigger rejects every update to a completed row and every delete with SQLSTATE `55000`.
5. Test cleanup may use `TRUNCATE ... RESTART IDENTITY` only inside the existing disposable random
   PostgreSQL schema; production code never disables the trigger.

The final salt and digest are computed before and inserted with the pending row. The receipt is
reserved before domain locks and finalized after the typed response is constructed. Keeping this
pending-row protocol instead of a hashed advisory lock preserves collision-free coordination on
the full UUID and the database-level guarantee that a protected domain commit cannot omit its
receipt.

### 5.4 Canonical fingerprint v1

Add one focused, pure canonicalization module under the client-operation service. Version 1 is:

1. Start from a strict validated Pydantic request, not raw HTTP bytes.
2. Construct a fixed envelope containing `api_contract`, `operation_kind`, lower-case canonical
   project/target UUID strings, and the request model dumped in JSON mode with defaults and nulls
   included.
3. Remove only the top-level `client_operation_id`.
4. Sort every JSON object key recursively; preserve other array order and exact string values.
   Normalize `initial_relationships` with the same deterministic domain sort used by
   `create_work_records`, so input permutations that execute identically fingerprint identically.
5. Encode finite JSON as UTF-8 with fixed compact separators and no ASCII escaping dependency.
6. Generate 32 random salt bytes and compute
   `SHA-256(UTF8("mnemonic-client-operation-v1") || 0x00 || salt || canonical_bytes)` before the
   first INSERT. Retain `canonical_bytes` only until the INSERT either reserves the key or the
   conflict path recomputes with the stored row's salt; discard it immediately after the
   reservation/replay/conflict decision.

Unit tests freeze canonical vectors for every operation. They cover omitted versus explicit
defaults, UUID case, normalized tags, initial-relationship permutations and `related` endpoint
normalization, object-key order, Unicode, line endings, numeric types, nulls, semantic array order,
exact prompts/bodies, actor/client/session/model, version, and optional lease token. A future
change cannot silently alter v1 vectors.

Do not use Python `repr`, object hashes, raw JSON serialization order, database JSON text, or a bare
hash of selected “important” fields. Do not omit `expected_version` or a capability from the
fingerprint merely because it is not stored in plaintext.

### 5.5 Response snapshot v1

Each operation registry entry fixes:

- wire request model, conditional provenance validator, and an identity extractor limited to
  `project_id` plus `client_operation_id`;
- domain-payload projector that removes the control field before any service/ORM constructor;
- canonical target envelope;
- response model and exact JSON-compatible serializer/decoder;
- expected HTTP success status;
- response contract version;
- `mutation_applied` extractor;
- maximum serialized result policy;
- a flag proving the response is non-capability-bearing.

After all domain/event flushes and server defaults are available, construct the exact public
Pydantic response and render the registered `JSONResponse` inside the transaction. Validate it,
dump it once in JSON mode, enforce the application byte bound, and only then complete the receipt.
Original execution and replay both return the already rendered response made from that registered
JSON-compatible object; the route's declared response model remains the OpenAPI contract, while
manual validation prevents FastAPI from serializing a materially different post-commit value.
Replays validate stored `response_body` through the same response model and renderer before
committing their read transaction. A malformed,
unsupported-version, or capability-bearing registry entry fails closed with
`503 client_operation_unavailable`; it never falls through to domain execution.

The allowlisted Phase 6 response models contain no lease token or claim request ID. `release_claim`
is eligible because `ReleaseResult` contains only work ID and a boolean; claim, renew, and
claim-and-recall models are statically barred from registry enrollment. Tests inspect every
registered response field and fail if a future model adds a capability/control field without a
reviewed plan change.

### 5.6 Retention and storage expectations

Receipt count grows with successful keyed calls, including successful no-ops. Large checkpoint
and event responses make table growth workload-dependent. Phase 6 therefore records:

- receipt table and unique-index size at representative scale;
- average, p95, and maximum response snapshot size by bounded operation kind;
- backup/restore size and time before and after the fixture;
- exact-key lookup plan and buffer count;
- executed, replayed, no-op, conflict, and unavailable proportions from bounded
  operation-kind/outcome log aggregation, without introducing a new metrics subsystem.

Do not introduce compression, external object storage, partitioning, TTL, or response
de-duplication without measured need. PostgreSQL TOAST is the initial large-JSON storage mechanism.

## 6. Service and transaction architecture

### 6.1 Closed operation registry

Add `backend/src/mnemonic_api/services/client_operations.py` as the only place that may reserve, compare,
complete, or replay a generic receipt. Its registry is a closed mapping from the ten operation
kinds to the request/response models and policies defined in Section 5.5. Route modules select a
registered operation; they do not construct ad hoc kinds, fingerprints, response versions, or
receipt SQL.

The internal service types should make these states explicit:

```text
OperationIdentity(project_id, client_operation_id)
OperationRequest(kind, identity, target_envelope, validated_wire_payload)
ReservedOperation(receipt_id, salt, domain_payload)
ReplayedOperation(status, typed_body, mutation_applied)
CompletedOperation(status, typed_body, mutation_applied)
```

These are internal application values, not new public schemas. A route receives either a replayed
typed success or an exclusive reservation under which it may call the existing domain service.
There is no "receipt exists, continue anyway" state.

### 6.2 Reservation algorithm

Run receipt reservation only after bearer authentication, strict Pydantic parsing, provenance
normalization, request-safety validation, construction of canonical v1 bytes, and projection of a
domain-only payload. It is the first resource-coordination database work for a keyed request.

1. If the request has no operation UUID, return an explicit unprotected outcome and let the route
   follow its shipped transaction path; the route still records the Section 6.6 domain outcome for
   covered-route live publication.
2. Generate a cryptographically random 32-byte salt and compute the final v1 digest in memory.
   Salt and digest are both nonnull and immutable in the first emitted receipt INSERT.
3. Set transaction-local `lock_timeout` and `statement_timeout` to the configured receipt-wait
   bound, then attempt
   `INSERT ... ON CONFLICT ON CONSTRAINT uq_client_operations_scope DO NOTHING RETURNING` with
   the complete pending identity, kind, versions, salt, and digest.
4. If the insert returns the row, restore both timeouts to `DEFAULT`; the transaction owns the
   reservation and returns `ReservedOperation`.
5. If the insert returns nothing, issue a new statement in the same `READ COMMITTED` transaction
   to select `(project_id, client_operation_id)`, then restore the timeout defaults. PostgreSQL's
   unique-index conflict wait serializes a concurrent owner: a committed owner becomes visible to
   this subsequent statement; a rolled-back owner lets the INSERT win.
6. Recompute the candidate digest with the stored salt and compare digests in constant time.
   Mismatched kind/version/digest returns `client_operation_conflict`; an exact completed row
   returns `ReplayedOperation`.
7. A visible pending row, missing row after a reported conflict, unsupported version, invalid
   status/body, or registry mismatch is an invariant failure. Roll back and return
   `client_operation_unavailable`; never execute the domain mutation as a fallback.

Set the SQLAlchemy engine explicitly to `READ COMMITTED`; do not inherit a changed cluster
default. Add `MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS` with default 10 and a validated range of
1..10 seconds. The maximum remains below the canonical dashboard proxy's 15-second and MCP
adapter's 20-second write timeouts. Apply it only around receipt reservation, restore the database
defaults before domain locks, map PostgreSQL `55P03` and `57014` to
`503 client_operation_unavailable`, and roll back the aborted transaction. Pool-saturation tests
must prove every same-key waiter either observes the owner's commit/rollback or exits with the
sanitized unavailable error when its transaction-local receipt wait expires. Receipt lock or
statement waiting never exceeds 10 seconds, and defaults are restored before domain locking.

Do not change protected transactions to `REPEATABLE READ`/`SERIALIZABLE`, split reservation
into an independently committed transaction, or add an application-process mutex. A timeout does
not prove the key is free; the client retries the same UUID and exact semantic arguments.

### 6.3 First-execution algorithm

For the transaction that inserted the pending row:

1. Call the existing domain service with the registered domain-only payload, never the wire model
   carrying `client_operation_id`.
2. Preserve the service's established project/work/lease/relationship lock sequence.
3. Flush domain rows and authoritative events so database defaults, generated IDs, versions, and
   timestamps are final.
4. Construct and validate the route's exact public success model.
5. Dump that model once in JSON mode, calculate the response UTF-8 size, and reject an
   out-of-policy result before commit.
6. Update the owned receipt from pending to completed with the registered status, JSON object,
   `mutation_applied`, and
   `completed_at = greatest(created_at, clock_timestamp())`. Require exactly one updated row.
7. Flush the completed receipt and commit once in the route.
8. Return the registered `JSONResponse`. Post-commit middleware publishes the data-free
   invalidation when `mutation_applied=true`, including on an applied replay.

The conditional completion update includes receipt ID, `state='pending'`, operation kind, and
both contract versions. A zero-row update is an invariant failure that rolls the entire request
back. No route commits the receipt early, commits events separately, or stores an optimistic
response before all domain flushes succeed.

Response serialization is deliberately inside the transaction. If a future response includes a
lazy-loaded field, unsupported type, secret, oversized value, or model mismatch, no durable domain
success may escape without a replayable receipt.

The registry's domain projector is an explicit safety boundary, not a convention. Audit every
service that dumps a request model. In particular, `append_checkpoint_record` excludes
`client_operation_id` before `Checkpoint(...)`; `WorkItemPatch.editable_fields` and
`update_work_record` both exclude it from field-set/domain edits; and tests assert no covered
service/ORM/event constructor receives the control field. A keyed patch containing only
`expected_version`, actor, and operation UUID remains invalid because it has no domain edit.

### 6.4 Replay ordering and validation boundary

The protected route order is:

```text
authenticate bearer
  -> parse and validate the full wire request
  -> normalize provenance, enforce request secret-safety, project domain payload
  -> reserve or resolve the project/operation-UUID scope
     -> replay/conflict/unavailable: stop before domain lookup
     -> new reservation: execute current domain guards and mutation
```

Authentication and full input validation run on every retry. This prevents a stale receipt from
turning malformed JSON, a missing conditional actor, a capability copied into a forbidden field,
or an invalid UUID into an accepted request. Resource existence, soft-delete visibility,
optimistic version, lifecycle, blocker, relationship, and lease-token validity run only for the
first execution. The fingerprint still includes those submitted values, including a release lease
token, so a changed retry cannot reach the stored result.

The project ID used for receipt lookup comes from the validated route path. It need not resolve to
a current project row before replay. This is required for durable recovery and is why receipt scope
is logical rather than foreign-key-owned.

### 6.5 Global lock order

For a keyed first execution, enforce and document:

1. receipt unique-key reservation;
2. project row for graph mutations;
3. graph endpoint work rows in UUID order, or focal work row;
4. retained lease row when the domain operation requires it;
5. relationship source row when removal requires it;
6. authoritative event inserts;
7. completion update of the transaction-owned receipt;
8. one commit.

Exact-key competitors wait at step 1 and never reach resource locks concurrently. Different keys
for the same domain resources continue to serialize through the existing domain order. A replay
takes no project, work, lease, or relationship lock. The receipt table has no resource foreign key,
so insert or commit cannot acquire a late parent-key lock and invert this sequence.

Add deterministic two-connection tests for same-key and different-key contenders rather than
relying only on high-volume probabilistic tests. Include transaction rollback, statement timeout,
and a graph mutation racing an ordinary work mutation.

### 6.6 Route integration and mutation outcomes

Keep commit ownership in each FastAPI route. A small route-level orchestration helper may remove
repetition, but it must accept a registered operation and a callback to the existing domain
service; it must not hide commits inside service code.

Map `mutation_applied` exactly:

| Operation | Value on a successful first execution |
| --- | --- |
| create work | `true` |
| add checkpoint | `true` |
| append event | `true` |
| update work | `true`, including a valid same-value patch that still versions work |
| complete work | `true` |
| delete work | `true` |
| add relationship | existing service result `created` |
| remove relationship | existing service result `removed` |
| release claim | existing service result `released` |

The orchestration returns an internal `executed`, `replayed`, or `unprotected`
classification. The `mutation_applied` table above is computed for every first execution whether
or not it has a key; replay uses the stored flag. None of these internal values enters public JSON.
Covered routes place the data-free publication decision and a finite outcome category on request
state. Middleware consumes that explicit decision and uses its shipped 2xx method/path fallback
only for excluded write routes. The existing logger may record `operation_kind` plus outcome; it
never records project, operation UUID, provenance, target, fingerprint, token, payload, or response.
Phase 6 adds no metrics subsystem or metric-label instrumentation.

### 6.7 Domain events and side effects

The receipt wraps the shipped authoritative event behavior; it does not create a new operation
event type.

| Operation | First execution | Exact replay |
| --- | --- | --- |
| create work | work, initial checkpoint, created initial edges, and their existing events | stored aggregate response only |
| add checkpoint | one checkpoint and its existing event/activity update | stored checkpoint only |
| append event | one progress event and activity update | stored event only |
| add relationship | endpoint events only when `created=true` | stored original edge/result |
| update work | one version transition and existing update event | stored original work snapshot |
| complete work | completion checkpoint, lifecycle transition, and existing events | stored original completion |
| delete work | soft-delete transition and existing deletion event | stored original deletion |
| remove relationship | endpoint events only when `removed=true` | stored original result |
| release claim | lease removal/event only when `released=true` | stored original result |

A successful natural no-op completes a receipt with `mutation_applied=false`, creates no domain
event, and remains replayable forever. Semantic embedding-cache writes remain outside the ledger.

### 6.8 Failure contract

| Failure point | Public result | Receipt/effect result |
| --- | --- | --- |
| bearer authentication | existing `401` | no database work |
| malformed/invalid body or UUID | existing `422` | no reservation |
| keyed actor omission | `422` validation error | no reservation |
| control/capability secret copied into a forbidden public field | `422 client_operation_secret_echo` | no reservation |
| successful-key semantic mismatch | `409 client_operation_conflict` | existing receipt unchanged, no domain work |
| current domain validation/not-found/conflict | existing `4xx` | pending row and all staged effects roll back |
| response validation/size/registry invariant | `503 client_operation_unavailable` | everything rolls back |
| receipt wait timeout (`55P03`/`57014`) | `503 client_operation_unavailable` | transaction rolls back; outcome remains unknown; retry exact request |
| other database/commit failure | existing `503 database_unavailable` when a response is possible, otherwise transport failure | transaction rolls back or outcome is unknown; retry exact request |
| committed response lost | transport failure | exact retry returns committed snapshot |
| stored receipt corruption or unsupported contract | `503 client_operation_unavailable` | fail closed; never re-execute |

Do not reveal which receipt field failed an invariant in the response. Server logs may name the
operation kind and invariant class but must use the existing redaction policy and omit all scope,
key, fingerprint, token, payload, and stored response values.

## 7. REST API contract

### 7.1 Request schemas

Add optional top-level `client_operation_id: UUID | None = None` to exactly these strict request
models:

- `WorkItemCreate`
- `CheckpointCreate`
- `ProgressEventCreate`
- `RelationshipCreate`
- `WorkItemPatch`
- `WorkCompletionCreate`
- `WorkDeletionCreate`
- `RelationshipRemovalCreate`
- `LeaseReleaseCreate`

Do not add it to response models, actor/source submodels, claim/renew models, project models, or a
generic base that would accidentally expose it on excluded operations. Existing `extra='forbid'`
behavior rejects misspellings, nesting, and unexpected placement.

Model-level validators enforce that `actor` is present for keyed `WorkItemPatch`,
`WorkDeletionCreate`, `RelationshipRemovalCreate`, and `LeaseReleaseCreate`. The other five
models already carry mandatory source/creator/actor identity. OpenAPI descriptions and examples
must state the conditional rule because JSON Schema cannot express the product semantics as
clearly as prose.

`ProgressEventCreate` uses a Phase 6 request-only metadata validator that recursively rejects
case-insensitive `client_operation_id` keys. It does not replace the shared Phase 5 metadata type
used by `WorkEventRead`; historical metadata remains valid on reads. The independent database
`NOT VALID` constraint is the defense-in-depth insertion boundary for writes that bypass the
request model.

Every keyed route explicitly projects the validated wire model into its existing domain request
before invoking a service. Do not rely on generic `model_dump()` defaults. The projector removes
only the top-level control field while retaining it in the canonical wire envelope. Schema and
service regression tests prove checkpoint construction never passes the field to `Checkpoint`,
`WorkItemPatch.editable_fields` and `update_work_record` never treat it as a domain edit, and a
patch containing only version, actor, and operation ID remains invalid.

### 7.2 Route behavior

All ten enrolled routes keep their paths, ordinary success status, and response model. No
`/operations` endpoint, lookup route, cancellation route, replay flag, idempotency header, or
alternate response wrapper is added. The only wire change is the optional request field and the
new stable errors.

Each protected route:

1. extracts the registered project/operation-UUID scope and fingerprinted provenance;
2. constructs its operation-specific target envelope from path IDs;
3. enters the receipt orchestration before any project/resource query;
4. returns a replayed model immediately or calls the shipped domain service;
5. completes and commits the receipt with the response; and
6. sets the internal live-sync outcome after a successful commit.

Unkeyed REST requests continue through the existing behavior. This is intentional compatibility
for direct REST callers, not a hidden server-generated key or a claim of safe retries.

### 7.3 Stable errors and retry guidance

Add these application errors:

| Status/code | Meaning | Client action |
| --- | --- | --- |
| `409 client_operation_conflict` | project/operation UUID is bound to a different successful semantic request | inspect the caller bug; use a new UUID only for a genuinely new intent |
| `503 client_operation_unavailable` | receipt safety cannot be proven or the transaction outcome may be unknown | retry the same UUID and exact semantic arguments; do not invent a new key |
| `422 client_operation_secret_echo` | operation/control/capability material appeared in a forbidden public content field | remove it; use a new UUID if the corrected arguments change |

All three use an empty context object. Generic Pydantic `422` details may identify the missing
conditional actor or invalid UUID field, but never echo its submitted value. No error response
returns whether an operation exists for any other scope.

Documentation distinguishes a definite pre-commit domain `4xx` from an unavailable/transport
outcome. A caller may correct a definite failed request and reuse its never-bound UUID, but the
recommended discipline is always one UUID per immutable intent.

### 7.4 Historical response and visibility rules

A replayed response is exempt only from current domain guards, not from endpoint authentication or
request validation. It may contain the originally returned snapshot of later-edited or
soft-deleted work because the authenticated caller supplied the exact original scope and semantic
request. It never grants a lease capability and never resurrects a domain row.

REST documentation must include examples for:

- concurrent duplicate create returning one original work ID;
- retrying deletion after ordinary GET becomes `404`;
- retrying release after another holder later claims the work;
- same UUID plus changed body returning conflict;
- new UUID plus identical create being a new intent and therefore allowed;
- a successful relationship no-op permanently replaying `created=false`.

## 8. MCP adapter and agent workflow

### 8.1 Covered tool schemas

Make `client_operation_id` a required UUID argument on exactly these nine canonical MCP tools:

- `create_work`
- `add_checkpoint`
- `append_event`
- `add_relationship`
- `update_work`
- `complete_work`
- `delete_work`
- `remove_relationship`
- `release_claim`

Pass it as the top-level REST body field without transformation other than canonical UUID
validation. The adapter never generates, caches, substitutes, or retries with a UUID on the
caller's behalf. This is an intentional prerelease tool-schema break; do not add deprecated
keyless aliases or a second compatibility code path.

The `create_project`, `claim_work`, `claim_and_recall`, and `renew_claim` MCP tools remain
outside the generic contract; the REST-only `update_project` route is outside as well. Claim tools
continue to document `claim_request_id` and active-lease-bounded recovery.

### 8.2 Tool annotations and descriptions

Among mutating tools, set `idempotentHint=true` on exactly the nine protected tools. Read-only
tools retain their truthful idempotency annotation. Preserve every tool's honest `readOnlyHint`,
`destructiveHint`, and open/closed-world annotations; idempotency does not make deletion or
completion non-destructive. Excluded mutations remain `idempotentHint=false` even where a narrow
natural no-op or claim replay exists.

Each covered description states:

1. generate one UUID before the first attempt;
2. persist it with the pending intent;
3. reuse it only with exactly the same tool and arguments after timeout, disconnect, malformed
   success response, or `client_operation_unavailable`;
4. use a new UUID when changing any argument or starting a new intent; and
5. follow a replay with a read when current state, rather than the historical original result, is
   required.

The server has no receipt lookup or argument-recovery tool. The MCP caller or its host must retain
both the UUID and the complete exact tool argument object—including target IDs, actor/source
fields, explicit/defaulted values, metadata, expected version, and any release token—for as long
as recovery might be needed. If either is lost across agent, host, session, adapter restart, or
process failure, the durable server receipt cannot be recovered through MCP. The caller must stop,
inspect current state where safe, and request direction rather than guess arguments or generate a
replacement UUID under the pretense that it is a retry.

The canonical catalog remains exactly 22 tools. Phase 6 changes schemas and annotations; it does
not add helper tools, receipt inspection, or duplicate legacy tools.

### 8.3 Adapter transport behavior

Make one outbound REST attempt per MCP invocation. Automatic adapter retry would be unsafe unless
it could prove it retained the exact validated body and key across all failure branches, and it
would obscure latency/outcome from the agent. Instead, translate backend errors consistently and
say when same-key retry is safe.

Treat timeout, connection reset, EOF, invalid/malformed 2xx JSON, and proxy/backend `5xx` as
possibly committed. Preserve enough local context only to classify and report the current
invocation without logging arguments; the adapter retains no recovery state after returning. A
backend `client_operation_conflict` is not retried; guidance says that conflict on an asserted
exact retry is a caller-safety incident, never permission to generate another UUID. Strict
response-model validation remains enabled on original and replayed successes.

### 8.4 Plugin and workflow documentation

Update all three Claude Code skills and both shared references where applicable so planning and
execution workflows record the UUID plus complete immutable pending tool call in secure,
client-local orchestration state before calling a protected tool. Examples create the key once and
reuse it; they never copy the key or pending arguments into Mnemonic checkpoint/event metadata,
prose history, or tool output. Keep claim-request recovery in a separate subsection.

Bump `plugin/.claude-plugin/plugin.json` from `0.3.0` to `0.4.0` because canonical tool
arguments change. Update marketplace description/source metadata only if needed, and verify an
install resolves the inner `0.4.0` manifest. Validate a fresh install and a sequential
upgrade/cache-buster path; there is no runtime schema shim for callers pinned to the old prerelease
contract.

### 8.5 MCP validation

Tests must prove:

- all nine protected tools require a valid UUID and forward its canonical string;
- all excluded tools reject an unexpected operation ID;
- among mutating tools, annotations are true only for the protected set while read-only tools
  retain their truthful idempotency hint;
- one invocation makes at most one outbound attempt;
- lost/malformed response errors direct the caller to the same key only when it still retains the
  complete exact arguments;
- lost-key, lost-arguments, changed-arguments, malformed-2xx, and adapter-restart cases give safe
  redacted guidance and never synthesize a replacement key;
- conflict, validation, unavailable, and historical replay responses retain their stable mapping;
- tool schema snapshots and the exact 22-tool catalog are updated intentionally; and
- generated examples contain no server-side or per-retry UUID generation.

## 9. Dashboard and proxy integration

### 9.1 Browser mutation coverage

The dashboard can invoke nine protected operations:

| Dashboard action | Backend operation |
| --- | --- |
| create work | `create_work` |
| add context checkpoint | `add_checkpoint` |
| append progress | `append_event` |
| add dependency/relationship | `add_relationship` |
| edit identity or lifecycle | `update_work` |
| defer work | `defer_work` |
| complete with checkpoint | `complete_work` |
| soft-delete work | `delete_work` |
| remove relationship | `remove_relationship` |

The browser continues to receive no claim, renewal, release, or lease-token route. Project creation
and project administration stay unkeyed because they are outside Phase 6.

### 9.2 Same-document frozen-intent registry

Add one reusable helper, for example `frontend/lib/mutation-intent.ts`, and a dashboard-level
registry/context that owns pending intents across modal closure and component unmount. Component
state may render an intent, but it never owns the sole recovery copy. The registry implements:

```text
idle
  -> prepared {kind, slot, conflict keys, method, path, operation UUID, frozen serialized body}
     -> edit/cancel before dispatch: discard safely
     -> dispatch: in_flight
  -> in_flight
     -> expected status + strictly decoded coherent success body: resolved, clear
     -> known status + strictly decoded definitive non-key-conflict 4xx: rejected, clear
     -> client_operation_conflict on asserted exact request: safety_conflict, retain and block
     -> timeout/network/abort/malformed or unexpected response/5xx: unresolved, retain
  -> unresolved
     -> retry: resend the identical method, path, and body
     -> edit/close/project switch/unmount: retain; block intersecting conflict keys
  -> safety_conflict
     -> retain and block; stop ordinary retry/new-key flow and request direction
```

The helper generates `crypto.randomUUID()` once after local form validation and before the first
`fetch`. It inserts the UUID at the top level, serializes the complete request exactly once, and
freezes that string with the method, path, operation kind, logical UI slot, and explicit
conflict keys derived from project, target, and shared editor/action group. Every retry sends the
exact frozen method/path/body. It never rebuilds a payload from mutable form or component state
under an old UUID.

A prepared intent that has never been dispatched may be discarded or replaced safely. Once any
request bytes may have been dispatched and the outcome is ambiguous, ordinary editing, cancel,
dialog close, navigation within the same dashboard document, or component unmount must not delete
or overwrite the frozen intent. The UI may retain a separate draft, but it blocks every new
intent whose conflict keys intersect until same-key replay produces a definitive result. Phase 6
provides no ordinary “abandon and start over” control for unresolved actions; unrelated conflict
groups may continue independently.

Checkpoint save and completion use distinct operation slots and keys, but share an editor/work
conflict key so ambiguity in either blocks the other. Relationship add/remove, update/delete,
append actions, and work create likewise use explicit conflict groups without borrowing one
another's operation key. A second click while a request is in flight is coalesced or disabled; if
it reaches the backend, it still uses the same frozen request.

Replace the generic compile-time cast in `frontend/lib/api.ts` for these nine mutation paths with
a closed per-operation runtime response registry. Each entry fixes the expected success status and
a strict body decoder for the corresponding REST response. A response is definitive success only
after JSON parsing and runtime validation of the exact expected status, required/allowed fields,
types, UUIDs/timestamps, nested shape, and operation-specific coherence with the frozen
project/work/path and result—for example checkpoint ownership/kind, relationship endpoints, and
the exact boolean receipt field. Missing, wrong-typed, unexpected, or internally inconsistent data
remains ambiguous and must not clear recovery. Implement these decoders in a focused
`frontend/lib/mutation-responses.ts` (or equivalent), not another generic TypeScript cast. The same
helper recognizes finite sanitized error envelopes; an unparseable or unknown error is ambiguous.

Classify outcomes conservatively:

- the registered status plus a strictly decoded 2xx body is definitive success; clear the intent,
  apply only safe response state, and refetch current state because the body may be a historical
  replay;
- a recognized, strictly decoded backend/proxy non-key-conflict `4xx`—including validation,
  not-found, and domain conflict—is definitive for this attempted request; surface it before
  clearing or enabling a corrected new intent;
- `client_operation_conflict` for what the registry asserts is the exact frozen request is a
  safety incident: retain the blocked intent, surface redacted guidance, and do not generate a new
  UUID through ordinary editing;
- network failure, timeout, abort after dispatch, malformed or unexpected success/error data,
  `408`, `425`, `429`, proxy `502/504`, backend `5xx`, and
  `client_operation_unavailable` remain unresolved and offer only exact same-intent retry.

The registry is intentionally memory-only. Do not persist frozen request bodies in
`localStorage`, `sessionStorage`, IndexedDB, URLs, cookies, service-worker caches, analytics, or
error telemetry; checkpoint and progress content can be sensitive. Tab closure, page reload, or
browser-process loss destroys both key and frozen body and therefore forfeits dashboard recovery.
There is no server lookup API from which the browser can reconstruct the intent. Provide a
best-effort unload warning while unresolved intents exist, but do not claim it prevents loss or
that a newly generated UUID is a safe retry. Persisting only the UUID would still be insufficient.

Never display or copy the operation UUID into event text, metadata, source metadata, toast detail,
or user-facing error output. A generic “retry the same pending action” affordance is sufficient.

### 9.3 Proxy policy

Extend the exact body allowlists in `frontend/lib/proxy-policy.ts` for the nine browser-covered
routes with one top-level `client_operation_id`. Validate it as a UUID before forwarding and
preserve it unchanged. Do not make it a generic allowed key for excluded routes.

The proxy must continue to:

- reject claim/renew/release paths and every nested `lease_token`;
- reject operation IDs in the query string, URL path, custom idempotency header, cookies, nested
  actor/metadata, or unsupported body;
- enforce method/path/body-size/content-type allowlists before the upstream call;
- forward the frozen body exactly once and never generate or automatically replace the UUID;
- avoid request-body, operation-ID, bearer, and upstream-response logging; and
- treat a connection loss or malformed upstream response as outcome-unknown so the browser keeps
  the pending intent.

Both backend and proxy reject a `client_operation_id` that is byte-for-byte equal to the bearer
credential or to a request-known lease/control value. Backend event secret scanning also treats the
operation UUID as request-known control data and reserves its key name recursively. Errors expose
field locations only where the shipped validation contract already does so; they never echo the
value.

### 9.4 Dashboard reconciliation and tests

The public response has no replay marker, so the browser treats every strictly decoded success as
potentially historical. It may apply only response fields that are safe for the immediate action,
then refetches the affected work/list/relationship view. Reconciliation must not recreate a removed
edge, roll an edited item back to a stored snapshot, or depend on receiving exactly one
invalidation.

Frontend unit tests cover UUID creation count, exact serialized-body reuse, double submission,
dashboard-registry survival across rerender/dialog/component unmount, safe pre-dispatch
edit/discard, conflict-key blocking across checkpoint/completion and other related actions,
safety-conflict retention, every ambiguous/definite outcome, strict status/body/coherence decoding
for valid, missing, wrong-typed, unexpected, and inconsistent fields, cleanup only after definitive
resolution, unload limitations, no web-storage writes, and redacted errors. Proxy tests cover exact
route allowlists, invalid/nested/query/header IDs, excluded routes, body bounds, and no token
regression.

Add Playwright fixtures that let an upstream mutation commit, then replace the first response
with a synthetic `502` or malformed 2xx. Retry must send the identical method/path/body and UUID,
decode the valid original result, leave one domain/event effect, publish the allowed healing
invalidation, and converge without overwriting newer state. While outcome is unknown, related
inputs, alternate action buttons, cancel/close, and project switch are blocked; a prepared
never-dispatched intent remains editable/discardable. Exercise work creation, one append,
relationship add/remove, and deletion for generated IDs, natural no-op flags, and disappearance.
Separate rerender and child-unmount cases prove dashboard ownership, and storage inspection proves
no UUID or frozen body entered browser persistence.

## 10. Implementation increments

Implement Phase 6 in reviewable increments. Do not expose the MCP/dashboard contract until the
backend receipt path and its database tests are green.

### 10.1 Increment 6A — schema and contract fixtures

Deliver:

- migration `0013_idempotent_mutations`;
- the ORM receipt model and exact constraints/triggers;
- the operation-kind registry skeleton;
- top-level optional REST schema fields and conditional actors;
- v1 canonical-envelope specification and frozen test vectors;
- fixture cleanup ordering with receipts truncated before domain tables; and
- migration tests from both an empty database and a populated Phase 5 snapshot, including legacy
  nested and case-varied metadata keys that the new request contract reserves; and
- dump/restore validation proving that the unvalidated Phase 6 check preserves those historical
  rows.

Exit when upgrade and dump/restore preserve every pre-existing row, downgrade is race-safe on an
unused receipt table, database constraints reject malformed/pending/modified receipts and new
reserved metadata, and historical reads remain unchanged.

### 10.2 Increment 6B — receipt core

Deliver:

- project/operation identity extraction, canonical JSON encoder, pre-INSERT random 32-byte salt,
  salted SHA-256 digest, and constant-time compare;
- pending reservation, conflict resolution, typed replay, finalization, and fail-closed errors;
- explicit `READ COMMITTED` engine configuration and transaction-state tests;
- validated `MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS`, transaction-local timeout restoration,
  `55P03`/`57014` mapping, and saturated-pool recovery tests;
- deterministic concurrency, rollback, corruption, response-bound, and immutability tests; and
- bounded operation-kind/outcome records through the existing logger, with no identifying values
  or new metrics subsystem.

Exit when a synthetic registered mutation proves one owner under two connections, a rollback lets
a waiter execute, a commit makes a waiter replay, no pending receipt can commit, the first emitted
INSERT contains the final salt/digest, and a stuck owner cannot hold same-key waiter pool slots past
the maximum 10-second receipt wait.

### 10.3 Increment 6C — backend route enrollment

Enroll the ten routes one by one using the closed registry. For each route, add its target
envelope, project/UUID identity extractor, provenance validator, response/status contract, and
`mutation_applied` mapping; retain its domain service and one route-owned commit.

Recommended order:

1. append event and add checkpoint;
2. create work;
3. update, defer, complete, and delete work;
4. add and remove relationship; and
5. release claim.

This order exercises a simple insert first, then aggregate generated IDs, mutable/deleted targets,
graph locks/natural no-ops, and finally a request-known capability. Run the full backend database
suite after every group, not only new tests.

Exit when all ten replay before current guards, all domain failures roll receipts back, unkeyed
REST behavior remains explicitly tested, and excluded mutations cannot enter the registry.

### 10.4 Increment 6D — MCP breaking contract

Deliver required UUID inputs, model forwarding, protected-only annotations, error/retry guidance,
one-attempt transport behavior, schema snapshots, and 22-tool catalog assertions. Remove obsolete
Phase 5 “not idempotent” warnings from covered tools; retain them in accurate form for excluded
ones. Add no aliases.

Exit when a canonical MCP client cannot call a protected mutation without a UUID, can repeat an
unknown outcome with the same arguments, and receives the original typed response.

### 10.5 Increment 6E — proxy and dashboard intents

Deliver exact proxy allowlists, the dashboard-owned same-document intent registry, strict
per-operation response decoders, integration across all nine browser actions, conservative
outcome classification, current-state reconciliation, and unit/e2e lost-response coverage. Keep
browser lease denial intact.

Exit when double clicks and explicit same-document retries share one key/body; pre-dispatch edits
are safe; dispatched ambiguity survives UI unmount and blocks all intersecting conflict keys,
including checkpoint/completion; exact-key conflict enters a retained safety state; malformed or
incoherent 2xx never clears recovery; no browser persistence contains mutation content; and
lost-response acceptance converges without duplicate durable effects.

### 10.6 Increment 6F — live sync, operations, and public docs

Make middleware outcome-aware, including healing invalidation on an applied replay; add bounded
execution/replay/no-op/conflict/unavailable records through the existing logger with no metrics
subsystem; update API, architecture, operations, validation, agent, plugin, and roadmap
documentation; bump the plugin to `0.4.0`; update `scripts/check-stack.py`; and exercise
backup/restore of receipts.

Exit when a first applied execution publishes once, an applied replay republishes once, and
no-op/failure publishes none; operator runbooks never advise deleting receipts to fix conflicts;
and every supported client workflow states its key-and-arguments retention boundary.

### 10.7 Increment 6G — adversarial validation and release gate

Run concurrency/lock-order stress, fault injection around every flush/commit boundary, response
size fixtures, canonical-vector cross-runtime checks, fresh and upgraded database tests, MCP fresh
install/upgrade tests, full frontend acceptance, and backup/restore replay.

Exit only when the definition of done in Section 19 is evidenced. Fix schema/service defects
directly; do not hide them behind retries, compatibility modes, or disabled tests.

## 11. Test plan

### 11.1 Migration and database invariants

Add PostgreSQL tests that prove:

- `0011_project_settings -> 0012_pending_deferred_statuses -> 0013` preserves populated
  projects, visible/soft-deleted work, checkpoints,
  relationships, leases, embeddings, events, and their IDs/timestamps;
- no historical receipt is created and the new table starts empty;
- all ten operation kinds are accepted and unknown/future placeholder kinds are rejected;
- null/invalid project or operation UUID, invalid versions/state/status/body type, non-32-byte
  salt/digest, oversized response, and inconsistent pending/completed columns fail;
- the unique scope is exactly `(project_id, client_operation_id)`; no provenance column
  participates, changed provenance in the same project/key conflicts, and another project is
  independent;
- no project/work/lease/relationship foreign key exists;
- a direct completed INSERT is rejected, and a transaction cannot commit a pending row;
- the only allowed row transition is the service-owned pending-to-completed transition, with the
  deferred trigger re-querying the current row rather than trusting stale trigger `NEW`;
- completed update/delete and key-field mutation fail with stable SQLSTATE;
- `TRUNCATE` remains available to test cleanup;
- `pg_get_functiondef` proves the Phase 5 metadata-v1 function/check are unchanged;
- a preserved `0010` event containing nested exact- and case-varied
  `client_operation_id` keys remains readable after upgrade and survives the supported
  dump/restore path;
- the separate reservation constraint exists with `convalidated=false` and rejects every new
  case-varied occurrence; and
- downgrade drops only Phase 6 metadata/receipt objects, leaves the v1 function untouched, refuses
  a locked nonempty table, and cannot win a check/drop race against a second connection.

### 11.2 Canonical request and registry tests

For every registered operation, freeze at least one full canonical envelope and digest test vector
using an injected fixed 32-byte salt for reproducibility. Cross-check Pydantic dumps and
MCP-forwarded REST payloads. A separate integration assertion inspects the first emitted pending
INSERT and proves it already contains the final nonnull salt and digest. Cover:

- omitted versus explicit defaults;
- UUID case and enum representation;
- normalization of identity and tags;
- Unicode normalization policy, combining characters, line endings, and non-ASCII response bytes;
- JSON object-key permutations versus array order changes;
- null versus omission where models distinguish them;
- target path IDs and operation kind;
- initial relationships, including `related` endpoint input order;
- exact prompt/body, metadata nesting, actor model, expected version, and optional lease token;
- exclusion of only the operation UUID and bearer;
- operation-kind and target mismatch under one project/operation UUID;
- registry response/status/version/capability allowlists; and
- a future request-model field causing an explicit vector/registry review rather than silent
  fingerprint drift.

### 11.3 Core transaction and concurrency tests

Use real PostgreSQL connections and barriers to cover:

1. two identical concurrent calls: one domain execution, one replay, same status/body;
2. two semantically different calls with the exact same `(project_id, client_operation_id)`: one
   success and one sanitized conflict;
3. first owner fails a domain guard: pending insert rolls back and a waiting identical call can
   become the first execution;
4. first owner flushes domain/events then fails response rendering: all rows roll back;
5. first owner completes receipt then commit fails: no partial durable state;
6. response is dropped after commit: retry replays;
7. a stuck owner's waiter reaches the configured maximum at or before 10 seconds, returns
   `503 client_operation_unavailable`, rolls back, never fallback-executes, and can retry later;
8. enough same-key waiters to saturate the pool all observe owner commit/rollback or the bounded
   sanitized timeout, and unrelated work recovers;
9. replay after subsequent update, completion/reopen, soft deletion, edge removal/recreation, and
   lease replacement;
10. different keys racing on the same work/graph endpoints: no deadlock and normal domain semantics;
11. the same UUID in another project executes independently;
12. the same project/UUID with changed client or session conflicts and does not execute;
13. a deliberately changed cluster isolation default does not override the engine's explicit
   `READ COMMITTED` contract;
14. corrupted or unsupported completed receipt fails closed; and
15. a natural no-op first execution stores a false flag that remains false on every replay.

Assert exact counts for work, checkpoints, relationships, leases, events, receipt rows, versions,
and activity timestamps. A test that checks only the HTTP body is insufficient.

### 11.4 Per-operation acceptance matrix

For each of the ten REST operations, test:

- keyed first success and exact replay status/body equality;
- same-key change to each operation-specific target and at least one semantic body field;
- unkeyed direct REST behavior;
- domain failure followed by corrected successful reuse of the never-bound UUID;
- no duplicate events/activity/version/side effects on replay;
- response model and `mutation_applied` mapping;
- wire-to-domain projection for every keyed model, including checkpoint ORM construction and
  rejection of a work patch containing only version, actor, and operation ID; and
- replay before every current-state guard relevant to that operation.

Additionally:

- create work asserts stable generated work/checkpoint/relationship IDs;
- checkpoint and progress assert stable timestamps and only one immutable history row;
- update asserts original versioned snapshot after later updates;
- defer asserts the original Deferred snapshot after an explicit return to Pending;
- complete asserts one completion checkpoint and replay after reopen;
- delete asserts replay after ordinary visibility becomes `404`;
- relationship add/remove assert original true/false flags across later edge changes; and
- release asserts the original flag and preservation of a replacement lease.

### 11.5 Security and failure-output tests

Capture HTTP responses, application logs and bounded outcome records, MCP errors, proxy logs,
WebSocket frames, traces or pre-existing telemetry, event metadata, checkpoints, contexts,
timelines, and receipt columns. Assert none disclose:

- bearer credential;
- raw lease token or claim request ID;
- operation UUID outside its private receipt key/request transport;
- fingerprint salt/digest;
- raw canonical request;
- stored response in logs, traces, or pre-existing telemetry; or
- conflicting scope, operation kind, target, actor/session, or original result.

Assert the only new outcome record fields are a registered operation kind and finite outcome, and
that the implementation adds no metrics subsystem.

Exercise byte-for-byte full-value echoes of the bearer, lease token, and operation UUID across
actor/source fields, prompt/body, metadata keys/values, relationship context, and error paths; each
must be rejected without echo. Test reserved keys case-insensitively. Separate proper-substring
fixtures document the limit: they are not promised rejection and assert only that errors/logs add
no undisclosed secret material. The validator is an exact-value/reserved-key guard, not a general
substring secret detector.

### 11.6 REST, MCP, frontend, and live-sync tests

REST schema tests verify top-level placement, strict extras, conditional actors, excluded models,
OpenAPI status/error declarations, and absence from responses. MCP tests follow Section 8.5.
Frontend/proxy tests follow Section 9.4.

For live sync, assert one data-free publication for every keyed or unkeyed first applied covered
mutation, none for a covered original no-op or failure, one additional publication for an exact
replay whose stored `mutation_applied=true`, and none for replay of
`mutation_applied=false`. An excluded successful write route with no explicit decision retains
the shipped middleware fallback. Inject a post-commit publication failure, then prove same-key
replay returns the stored result and republishes the healing invalidation.

Extend the explicitly authorized writable mode of `scripts/check-stack.py`, run only against a
disposable stack, to:

- generate and retain one UUID plus exact arguments for every protected MCP write and
  dashboard-proxy mutation it issues, including cleanup;
- keep the catalog at 22 while asserting the exact nine covered mutating schemas/annotations and
  truthful read-only hints;
- replay representative exact calls, compare complete typed results, and verify unchanged
  domain/event counts;
- distinguish same-key relationship add replaying its original `created=true` from a new-key
  natural no-op that binds `created=false`, with equivalent remove/release true/false cases;
- discard one committed first result and recover it with the retained exact call;
- leave immutable receipts behind after synthetic work deletion and treat search-based cleanup
  after checker-process loss only as a safety fallback, never as exact-result recovery.

Read-only remains the checker default. The writable release run records only redacted counts and
assertions.

### 11.7 Performance and durability tests

At representative maximum payload sizes, record:

- fresh receipt insert/finalize latency;
- exact replay latency;
- same-key contention wait with a hard 10-second maximum and bounded pool recovery;
- different-key throughput;
- receipt and unique-index bytes per operation kind;
- TOAST behavior and response-size boundary;
- query plan/buffer counts for exact lookup;
- backup/restore size and duration; and
- restored same-key replay behavior.

Set regression budgets from the measured Phase 5 baseline before implementation is declared done.
Do not add an index, cache, compression layer, or retention policy merely to make an unrepresentative
microbenchmark green.

## 12. Delivery sequence and dependency gates

| Gate | Depends on | Required evidence before proceeding |
| --- | --- | --- |
| schema contract frozen | roadmap/prior-plan reconciliation | coverage, scope, canonical v1, response v1, lock order, and exclusions reviewed |
| migration merged | schema contract | populated upgrade, invariant, unused downgrade, and existing-data preservation tests |
| receipt core merged | migration | deterministic owner/waiter/rollback/commit/corruption tests |
| backend routes enrolled | receipt core | ten-operation matrix plus full unskipped PostgreSQL suite |
| MCP contract changed | backend routes | live backend replay and stable error behavior |
| dashboard/proxy changed | backend routes | frozen-intent unit tests and proxy allowlists |
| docs/plugin release changed | MCP and dashboard behavior stable | fresh/sequential plugin validation and exact examples |
| Phase 6 accepted | all prior gates | full verification, lost-response e2e, performance fixture, backup/restore replay |

Keep one quiesced schema/application deployment boundary: stop mutation writers, migrate, deploy a
server version that knows the table and new request validator, then resume writers. MCP and
dashboard releases follow backend availability. Because Mnemonic is prerelease, change canonical
tool schemas directly and coordinate versions; do not run dual keyless/keyed MCP tools or
translation shims.

## 13. Migration, deployment, and rollback

### 13.1 Upgrade

`0013_idempotent_mutations` is additive for production data:

1. create the empty receipt table, constraints, exact unique index, functions, and triggers;
2. leave the shipped `mnemonic_work_event_metadata_v1_is_valid` function and its existing check
   byte-for-byte unchanged;
3. add a separate recursive Phase 6 helper and
   `ck_work_events_client_operation_id_reserved CHECK (...) NOT VALID`; PostgreSQL enforces it for
   new rows, while the migration never validates or scans preserved rows;
4. do not rewrite or backfill domain rows, and verify the Phase 5 function definition/hash plus
   every existing trigger and constraint is unchanged; and
5. stamp `0013` only after the complete transaction succeeds.

Run the migration with mutation writers quiesced and keep them quiesced until the Phase 6 backend
is active. A pre-Phase-6 binary at revision `0012` ignores the receipt table but does not know how
to map the new
request-only `client_operation_id` metadata error; do not reopen progress writes in the
migration/backend gap. Then deploy MCP and dashboard clients. Rehearse empty-table creation and
the `NOT VALID` constraint addition against a production-sized restore and record their catalog
lock durations.

Before rollout:

- take and restore-test a database backup;
- confirm the database is at exactly `0012_pending_deferred_statuses` after first
  preserving/migrating the `0011_project_settings` content;
- confirm no locally invented `client_operations` object collides;
- run schema/trigger introspection on the restored copy; and
- validate both pre-migration data counts and post-migration content hashes for preserved tables.

### 13.2 Application rollback

If the Phase 6 application must roll back after keyed operations exist, keep revision `0013` and
the receipt data. Disable/revert MCP and dashboard clients that require the new contract before
running the older server. The older prerelease server may reject the extra request field, but it
must not be presented as idempotency-capable. Because it also cannot translate the separate
reserved-metadata database failure, keep progress-event writers quiesced until the Phase 6 service
is restored; do not add an emergency compatibility parser.

Do not delete or truncate completed receipts during an incident, deploy rollback, conflict
investigation, or performance response. Doing so converts a late retry into a second execution.
Application rollback runbooks explicitly preserve the table and restore the Phase 6 service before
re-enabling keyed clients.

### 13.3 Database downgrade

A database downgrade is allowed only before any completed receipt has ever been accepted and
requires Phase 6 clients and all mutation writers to be quiesced. In the single Alembic transaction,
acquire `LOCK TABLE client_operations IN ACCESS EXCLUSIVE MODE` before checking emptiness and
hold it through `DROP`. Abort with no force option if the locked table is nonempty. On an unused
table the same transaction:

1. drops `ck_work_events_client_operation_id_reserved` and its Phase 6-only helper;
2. drops the receipt guards and table;
3. leaves `mnemonic_work_event_metadata_v1_is_valid` and its Phase 5 check untouched; and
4. leaves every Phase 5 domain row untouched.

A deterministic two-connection migration test pauses between lock/check/drop while another
connection attempts a keyed insert. It proves the insert cannot commit into the check/drop gap:
the downgrade either sees the receipt and aborts or completes before a writer can resume.

If a later schema design replaces this ledger, migrate receipt semantics and response snapshots
forward first. Never discard them as an ordinary downgrade. This is data preservation, not a
backward-compatibility shim.

### 13.4 Backup and restore

Receipt rows are part of the authoritative PostgreSQL backup set. Operations documentation must
state their durability role, response-content sensitivity, and restore-order expectations. An
acceptance drill:

1. executes keyed mutations including deletion and release;
2. takes the supported backup;
3. restores into an isolated environment with the same application contract;
4. retries the original exact calls; and
5. proves stored responses return without any domain/event/lease mutation.

## 14. Verification strategy

### 14.1 Required automated suites

Run with Python 3.13, separate backend/MCP `uv` environments, Node 24, and a real isolated
PostgreSQL test database. A skipped PostgreSQL-marked suite is a failed Phase 6 release gate.

```sh
docker compose -f compose.test.yaml up -d --wait

cd backend
uv sync --frozen
uv run pytest -q
uv run ruff check .

cd ../mcp
uv sync --frozen
uv run pytest -q

cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e:stack
```

Supply the repository's documented `TEST_DATABASE_URL` where the test compose environment does
not inject it. Also run:

- upgrade a populated `0011_project_settings` fixture through
  `0012_pending_deferred_statuses` to head (`0013_idempotent_mutations`), including
  nested/case-varied legacy metadata, followed by the supported dump/restore path;
- fresh-database `alembic upgrade head`;
- writer-quiesced guarded downgrade on an unused `0013` database, then re-upgrade;
- refused downgrade after one completed receipt and the two-connection check/drop race;
- schema/function/constraint/trigger introspection, including unchanged metadata-v1 definition;
- plugin manifest validation, fresh install, sequential upgrade/cache-buster validation;
- the updated `scripts/check-stack.py` unknown-outcome recovery scenario; and
- `git diff --check` plus documentation link/path checks.

### 14.2 Fault-injection checkpoints

Provide deterministic test seams immediately before and after:

- pending receipt insert;
- each domain/event flush;
- response-model construction;
- response-size enforcement;
- receipt completion update;
- transaction commit;
- post-commit live publication; and
- MCP/proxy/browser response parsing.

At every pre-commit fault, assert no domain or receipt partial state. At commit-response and
post-commit delivery faults, assert same-key retry resolves to the original durable success; an
applied replay also republishes the data-free healing invalidation. Fault hooks exist only in test
wiring; do not ship a runtime compatibility or bypass switch.

### 14.3 Manual acceptance

Using two independent clients/sessions:

1. send identical keyed create requests concurrently and compare the exact parsed response;
2. lose the first response after commit and retry;
3. reuse the key with a changed title, target, actor model, and tool kind and observe sanitized
   conflict;
4. update/delete/remove/release the underlying state and replay the older operation;
5. inspect event timelines, activity timestamps, versions, and lease effects for duplicate durable
   work while allowing the specified repeated data-free invalidation on an applied replay;
6. exercise an unkeyed REST request and observe the documented unprotected behavior;
7. restart backend and MCP processes, then replay an externally retained exact request; separately
   prove dashboard component unmount recovery within one document and document reload loss; and
8. restore a backup and replay again.

Record observed commands, redacted IDs/counts, statuses, typed-response equality assertions, and
timing in the Phase 6 validation artifact. Never record response/payload hashes or paste raw
bearer/token/payload/receipt response content into that artifact.

## 15. Security, privacy, performance, and operations

### 15.1 Security boundary

Phase 6 does not strengthen identity authentication. The shared bearer still authorizes the API.
The permanent binding is exactly project/operation UUID. Client/session provenance is caller
asserted and appears only in the fingerprint; it neither opens another uniqueness namespace nor
authenticates replay.

Use database permissions so only the application/migration role can access `client_operations`.
Do not expose it through the REST API, MCP resources, dashboard, semantic search, activity feed, or
read-only reporting views. Administrative SQL access already able to read checkpoints/events must
treat response snapshots as equally sensitive.

The randomly salted SHA-256 fingerprint is an equality commitment, not encryption,
authentication, or a password store. The stored per-row value is a salt, not an HMAC key. It
prevents a reusable unsalted cross-row digest corpus and accidental plaintext retention, but does
not prevent per-row guessing by an attacker with unrestricted database access. Security claims
and comments must say this plainly.

### 15.2 Data minimization and redaction

Persist only the scope, registered kind/versions, randomized digest material, typed success
snapshot, applied flag, and timestamps. Do not add target columns, retry counts, last-seen time,
request samples, user agent, IP address, trace headers, or error bodies.

Add `client_operation_id`, `request_fingerprint`, and `request_fingerprint_salt` to logging,
exception, tracing, and serialization denylist tests. Existing structured log records use only:

```text
operation_kind = one of ten registered values
outcome = executed | replayed | no_op | conflict | unavailable
```

Phase 6 adds no metrics subsystem. Do not record project, client, session, operation UUID, target,
response/status body, salt, or digest; any future telemetry requires a separate review.

### 15.3 Capacity and latency

Receipt insertion adds one unique-index write and one row update to every keyed success. Replays
add an exact-index lookup plus response validation. Establish budgets for:

- p50/p95/p99 fresh mutation overhead versus Phase 5;
- exact replay p95;
- same-key waiter latency under the default and maximum 10-second receipt wait;
- receipt table/index/TOAST growth at forecast daily mutation volume; and
- backup/restore growth.

The validation artifact and runbook queries record conflict/unavailable/replay rates, storage,
deadlocks, receipt timeouts, and invariant failures from existing redacted logs and PostgreSQL
statistics. Operators may connect those sources to monitoring they already run; Phase 6 does not
add a metrics or alerting subsystem. A conflict spike suggests a client intent lifecycle bug, while
an unavailable/invariant spike is a safety incident.

### 15.4 Operational inspection

Operator queries may aggregate counts and bytes by registered kind/day, but must not select or log
scope IDs, UUIDs, fingerprints, or response JSON during routine checks. Provide safe examples for:

- total completed rows and storage bytes;
- bounded operation-kind/outcome aggregation from existing redacted application logs;
- invalid pending count, which must always be zero outside an open transaction;
- supported contract-version counts;
- oldest/newest timestamps without identity; and
- exact-index health and deadlock/timeout statistics.

There is no manual “mark completed,” “unlock,” “replay,” “rebind,” or “purge” procedure. A suspected
corrupt row is an incident requiring restore or a reviewed forward data migration, not a reason to
execute the mutation again.

## 16. Expected file impact

This map is specific enough to guide implementation but does not require artificial file churn.

| Area | Expected files | Planned change |
| --- | --- | --- |
| migration | `backend/alembic/versions/0013_idempotent_mutations.py` | ledger and guards; separate new-row-only `NOT VALID` metadata helper/check; Phase 5 validator unchanged; locked downgrade |
| ORM | `backend/src/mnemonic_api/models.py` | private `ClientOperation` model and WorkEvent Phase 6 check declaration matching database invariants without changing metadata-v1 |
| public schemas | `backend/src/mnemonic_api/schemas.py` | optional request IDs, conditional actors, request-only progress metadata validator; historical read type unchanged |
| errors | `backend/src/mnemonic_api/errors.py` | stable conflict/unavailable/secret-echo errors |
| runtime/config | `backend/src/mnemonic_api/config.py`, `database.py`, `.env.example`, `compose.yaml`, config/database tests | wait default 10/range 1..10, deployment wiring, explicit `READ COMMITTED`, transaction-local timeout/reset behavior |
| receipt service | new `backend/src/mnemonic_api/services/client_operations.py`, service exports | registry, canonicalization, reservation, replay, completion |
| routes/transactions | `backend/src/mnemonic_api/application.py` | enroll ten REST routes, retain route commit, set internal outcome |
| live sync | `backend/src/mnemonic_api/application.py`, `backend/src/mnemonic_api/live_sync.py` | publication policy consumes data-free mutation outcome |
| backend fixtures/tests | `backend/tests/conftest.py`, new `test_phase6_migration_postgres.py`, new `test_idempotent_mutations_postgres.py`, existing validation/domain/live-sync suites | cleanup, migration, concurrency, per-operation, security, regression coverage |
| MCP contract | `mcp/src/mnemonic_mcp/server.py`, `models.py`, `validation.py`, `api.py` | required inputs, forwarding, annotations, validation, stable guidance |
| MCP tests | `mcp/tests/test_tools.py`, `test_transport.py`, fixtures/snapshots | exact catalog/schema, one attempt, retry/error behavior |
| frontend API/types | `frontend/lib/api.ts`, `types.ts`, new `mutation-intent.ts`, new `mutation-responses.ts` (or equivalents) | body field, conflict-key registry/state machine, strict status/body/coherence decoders |
| proxy | `frontend/lib/proxy-policy.ts`, `frontend/app/api/mnemonic/[...path]/route.ts` | exact allowlists/UUID validation and ambiguous outcome handling |
| dashboard actions | `frontend/components/dashboard.tsx`, work editor/detail/event/relationship components | dashboard-level registry ownership, nine integrations, unresolved-intent blocking, current-state reconciliation |
| frontend tests | unit tests beside helpers/components; new `frontend/tests/e2e/phase6-idempotent-mutations.spec.ts` | decoder strictness, unmount lifecycle, policy, lost response, convergence |
| stack smoke | `scripts/check-stack.py` | retained IDs/arguments on all protected writes/cleanup, catalog assertions, lost result, replay-vs-new-key no-ops, durable counts; writable disposable-stack mode only |
| public docs | `docs/api-contract.md`, `architecture.md`, `operations.md`, `validation.md`, `agents.md`, `development.md`, `roadmap.md` | contract, lock/transaction model, retry/runbook/verification, phase status |
| plugin | all three `plugin/skills/*/SKILL.md`, both `plugin/reference/*.md` files as applicable, `plugin/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | immutable intent workflow; inner manifest `0.4.0`; marketplace metadata only as needed |
| examples | only examples that issue covered mutations | caller-generated stable UUID and unknown-outcome retry; no compatibility examples |

Implementation may split a focused helper or test file when it improves ownership. It must not
create parallel “v1/v2” route/service trees, legacy MCP aliases, or models for future gates and
verification.

## 17. Risks and mitigations

| Risk | Consequence | Required mitigation |
| --- | --- | --- |
| request canonicalization drifts | exact retries conflict or distinct requests collide | frozen per-operation vectors, explicit version, closed registry, review every schema change |
| response schema drifts | old snapshots fail validation | response version, migration or new API version, fail closed |
| receipt/project FK reverses locks | deadlock under graph/work concurrency | logical project UUID with no resource FK; deterministic lock tests |
| pending row commits or first INSERT lacks final digest | key is unusable or protection fails | pre-INSERT salt/digest, pending-only INSERT trigger, deferred current-row constraint trigger, fault/direct-SQL tests |
| metadata-v1 is rewritten | formerly legal history fails reads, validation, or restore | Phase 5 function/check unchanged; separate `NOT VALID` new-row constraint; populated upgrade and dump/restore test |
| downgrade guard races a writer | a just-committed receipt is dropped | writer quiescence; `ACCESS EXCLUSIVE` lock before check through drop; two-connection race test |
| receipt is deleted/expired | delayed retry executes twice | indefinite retention, immutable/delete trigger, guarded downgrade, runbook |
| response snapshot grows | table/backup bloat | 1 MiB bound, measured maxima, TOAST/capacity/backup monitoring |
| retry returns stale snapshot | UI/agent mistakes history for current state | explicit historical semantics and post-replay read/refetch |
| key reused across tools/targets | wrong action could execute | operation kind excluded from uniqueness but included in fingerprint; sanitized conflict |
| new UUID used after timeout | duplicate semantic mutation | client pending-intent workflow and prominent same-key guidance |
| same key with rebuilt payload | conflict despite caller believing it retried | freeze full payload/path once; never reconstruct under an old key |
| MCP loses key or complete arguments | receipt exists but exact replay is unrecoverable | stateless adapter guidance, secure caller-local pending call, stop/inspect/request direction; never synthesize a key |
| asserted exact retry returns key conflict | browser/caller state or contract is unsafe | retain blocked safety state, redacted incident guidance, no ordinary new-key flow |
| operation or capability leaks | credential/control data enters durable history/logs | UUID type, private table, salted SHA-256 input, explicit wire-to-domain projection, secret-echo/reserved-key checks, redaction tests |
| control field reaches a service model | ORM error or operation ID becomes a domain edit | closed projector registry, explicit checkpoint/patch exclusions, keyed regression tests |
| high same-key contention | connection/lock exhaustion | transaction-local receipt wait default/max 10 seconds, sanitized timeout, existing-log aggregation, no fallback, pool/load tests |
| asserted provenance is mistaken for scope/auth | bearer holder may overclaim identity or replay if it knows the exact request | provenance never selects scope; document shared-bearer boundary; add authenticated-principal replay authorization in a future auth phase |
| unkeyed REST remains retry-unsafe | direct caller assumes universal idempotency | explicit docs/OpenAPI language; canonical MCP/dashboard always key covered calls |
| malformed 2xx or UI unmount clears recovery | committed action cannot be safely replayed | strict per-operation runtime decoders and dashboard-level registry; clear only after definitive resolution |
| edit/cancel replaces ambiguous intent | related new UUID can duplicate a committed action | retain and lock dispatched unresolved intent; block related new intent until same-key resolution |
| dashboard reload loses frozen intent | user cannot safely reconstruct exact browser retry | explicit unrecoverable same-document limit, best-effort unload warning, no claim that a new UUID is safe |
| live invalidation lost after commit | another open dashboard remains stale | applied replay republishes the existing data-free invalidation; no-op replay does not; reconnect/refetch remains fallback |
| rollback removes receipt data | old retries execute after redeploy | retain `0013`, guarded downgrade, restore-tested backup |
| generic registry enrolls a capability response | expired/replaced token is replayed | static response allowlist, negative registry tests, claim/renew exclusion |

## 18. Explicitly deferred work

Phase 6 does not implement:

- project create/update idempotency or an installation-global operation scope;
- durable replay of claim/claim-and-recall capabilities;
- idempotent/time-anchored lease renewal;
- receipt list/get/delete/rebind APIs or operator mutation tools;
- TTL, garbage collection, partitioning, archival, response compression, or external receipt
  storage;
- a generic HTTP `Idempotency-Key` header;
- automatic MCP, proxy, or browser retry loops;
- cross-tab or reload persistence of sensitive pending browser payloads;
- multi-user authenticated principals or per-user replay authorization;
- semantic duplicate-work detection;
- placeholder gate/verification tables, tools, or response models before Phases 7 and 11;
- compatibility aliases for old prerelease MCP schemas; or
- byte-for-byte HTTP response/header replay.

These can be planned from observed needs without weakening the durable contract shipped here.
Future gate and verification mutations should enroll in the same registry only after selecting
their truthful scope, non-capability response, canonical vector, event atomicity, and replay guards.

## 19. Definition of done

Phase 6 is complete only when all items below are true.

### Persistence and transaction safety

- [x] `0013` upgrades empty and populated `0012_pending_deferred_statuses` databases and
      dump/restores legacy
      nested/case-varied metadata without changing existing content.
- [x] Receipt uniqueness is exactly `(project_id, client_operation_id)`; provenance has no ledger
      column or key scope, and no resource foreign key exists.
- [x] The first pending INSERT contains the final 32-byte salt and salted SHA-256 digest.
- [x] Direct completed INSERT, committed pending state, completed update/delete, and protected-field
      mutation are rejected by the database.
- [x] The Phase 5 metadata-v1 function/check remain unchanged; a separate unvalidated Phase 6
      constraint rejects the reserved key only on new rows.
- [x] One keyed success atomically commits domain rows, authoritative events, and typed receipt.
- [x] Failures before commit leave no receipt or partial domain/event state.
- [x] Backup/restore preserves replay; downgrade quiesces writers, locks before checking, refuses a
      nonempty ledger, and holds `ACCESS EXCLUSIVE` through drop.

### API semantics

- [x] Exactly ten REST mutations accept the optional top-level UUID and conditional actors are
      enforced.
- [x] Progress requests use the new request-only reserved-metadata validator while historical
      `WorkEventRead` retains the Phase 5 validation contract.
- [x] Every keyed wire model is projected to a domain-only request; checkpoint construction and
      work patching cannot consume the control field.
- [x] Exact same-key retry returns the original status and parsed JSON before current domain guards.
- [x] Every semantic mismatch, including changed provenance, returns sanitized
      `client_operation_conflict` with no effect.
- [x] All successful natural no-ops bind and replay their original false flag.
- [x] Unkeyed REST and excluded project/claim/renew behavior are explicitly tested and documented.
- [x] No public receipt API, replay wrapper/header, server-generated key, or compatibility path
      exists.

### Concurrency and operation coverage

- [x] Deterministic concurrent identical calls yield one execution and one replay.
- [x] Owner rollback lets a waiter execute; owner commit makes a waiter replay.
- [x] Same-key waits never exceed the configured maximum of 10 seconds, never fallback-execute, and
      release saturated pool capacity after a sanitized unavailable result.
- [x] The same UUID in another project is independent; changed client/session under one project/UUID
      conflicts and performs no work.
- [x] Different-key graph/work races preserve the documented lock order without deadlock.
- [x] Each of the ten REST operations passes the first/replay/mismatch/failure/no-duplicate/current-state
      matrix.
- [x] Delete/remove/release replay works after the source state disappears or is replaced.
- [x] Canonical v1 and response v1 vectors are frozen for every registry entry.

### Client surfaces

- [x] Among mutating MCP tools, exactly nine require the UUID, forward it exactly, and advertise
      idempotency; read-only tools retain truthful hints and excluded mutations remain false.
- [x] The MCP catalog remains 22, makes one outbound attempt per invocation, and documents that
      losing either key or exact arguments makes server receipt recovery unavailable.
- [x] All nine dashboard mutations use a dashboard-owned frozen same-document registry and
      exact-body retry; unresolved dispatched intent survives component unmount and blocks every
      intersecting conflict key, including checkpoint versus completion.
- [x] An asserted exact-key conflict enters a retained safety state and cannot fall into ordinary
      edit/new-key flow.
- [x] Strict per-operation runtime decoders clear browser recovery only after the expected status,
      exact shape, UUID/time types, and frozen path/result coherence; malformed, missing,
      wrong-typed, extra, or inconsistent data retains it.
- [x] Reload/process loss is explicitly unrecoverable; no browser store or unsafe new-key claim is
      added.
- [x] Proxy allowlists accept the field only on those nine routes and retain lease-route denial.
- [x] Lost/malformed-response Playwright and explicitly authorized writable stack-smoke cases on
      disposable stacks prove exact request reuse, original-result recovery, one durable effect,
      safe blocking, healing invalidation, and current-state convergence.
- [x] The inner plugin manifest is `0.4.0` and fresh/sequential install validation passes without
      aliases.

### Security, side effects, and operations

- [x] Receipts contain no raw request, bearer, lease token, claim ID, or automatic history copy.
- [x] Fingerprints use salted SHA-256 with explicit limited claims; no HMAC primitive or key
      semantics are implemented.
- [x] Responses, errors, existing-log outcome records, traces/pre-existing telemetry, WebSockets,
      timelines, and contexts pass redaction tests.
- [x] Phase 6 adds no metrics subsystem or identifying metric-label instrumentation.
- [x] Replays create no duplicate domain row, event, version/activity change, lease action, cache
      write, or unauthorized current-state change.
- [x] A keyed or unkeyed first applied covered execution publishes once, its applied replay may
      republish one data-free healing invalidation, and covered original/replayed no-ops plus
      failures publish none; excluded writes retain their shipped fallback.
- [x] Storage, latency, contention, backup, and restore measurements meet recorded budgets.
- [x] Operations documentation treats receipt loss/corruption as an incident and never recommends
      purge/re-execution.

### Quality gate

- [x] Backend tests and Ruff pass with the PostgreSQL suite unskipped.
- [x] MCP tests pass in its separate frozen environment.
- [x] Frontend unit tests, typecheck, production build, and isolated Playwright stack pass.
- [x] Migration fresh/legacy-populated upgrade/dump-restore/locked unused-downgrade/refused-
      downgrade/two-connection race paths pass.
- [x] The updated writable stack smoke passes on a disposable stack, retains keys/arguments for
      all protected writes and cleanup, and proves committed-response loss, exact replay, new-key
      no-op distinctions, and unchanged durable counts.
- [x] API, architecture, operations, validation, agent, plugin, and roadmap docs agree on coverage,
      exclusions, retention, historical response semantics, and retry guidance.
- [x] No Phase 7/11 placeholder models and no prerelease back-compat shims were added.

## 20. Cold adversarial review disposition

A fresh-context adversarial reviewer inspected the first complete draft without editing it. This
plan incorporates every blocker and high-severity finding plus the actionable medium/low findings:

- salt and the final digest are computed before the first INSERT, while canonical bytes survive
  just long enough for a stored-salt conflict comparison;
- the Phase 5 metadata-v1 contract is never reinterpreted; a separate request validator and
  unvalidated new-row check preserve legacy reads and restore;
- uniqueness is project/operation UUID, with provenance in the fingerprint only;
- wire/domain projection, explicit bounded database waits, writer-locked downgrade, healing replay
  invalidation, strict browser response decoders, and non-discardable ambiguous intents are all
  load-bearing requirements;
- the design uses salted SHA-256, domain-order normalization for initial relationships, existing
  bounded logs rather than a new metrics subsystem, and candid MCP/browser loss boundaries.

The reviewer suggested a transaction-scoped advisory lock and completed-only receipt as a smaller
alternative. This plan deliberately retains the pending ledger: full UUID uniqueness avoids hashed
lock collisions, and the deferred database invariant prevents a protected domain commit from
silently omitting its completed receipt.

After correction, the same reviewer performed a read-only remediation audit and found no remaining
blocker or high-severity issue. Its residual medium finding (unkeyed covered live publication) and
low finding (exact versus proper-substring secret tests) are resolved in Sections 3.10, 6.6, 11.5,
and 11.6. This disposition records planning decisions, not implementation evidence; every item
remains subject to the Section 19 release gates.
