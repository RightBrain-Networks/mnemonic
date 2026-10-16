# Shared frontend boundaries

These modules consolidate the client-side P1 opportunities in the
[shared-pattern audit PR](https://github.com/RightBrain-Networks/mnemonic/pull/33).
They do not replace domain review rules or the receipt executor.

The [FastAPI browser API foundation](browser-api.md) documents the explicit JSON
route inventory, browser admission boundary, runtime configuration, and live-sync
alias used by the static Vite dashboard. FastAPI is the sole browser transport
owner; nginx forwards only those explicit namespaces. See [static delivery](frontend-delivery.md).

## Wire codecs

General structure belongs to the neutral `revision-codecs`, `work-codecs`,
`readiness-codecs`, `checkpoint-codecs`, and `relationship-codecs` modules in
`frontend/lib/`. Feature decoders and mutation-response
validation compose those exports instead of importing common structures from
one another. Primitive checks and JSON equality stay in `wire-guards`.

Keep exact field sets, integer bounds, parsed UUID equivalence, and
caller-specific errors. Completion evidence layers completion-kind, timestamp,
and tag restrictions over general checkpoint pointers; a general pointer is
not automatically a valid evidence pointer. When moving a decoder, update the
OpenAPI consumer catalog and regenerate `docs/openapi.json`.

## Canonical-work search

`useCanonicalWorkSearch` owns the trimmed query, debounce, cancellation,
scope-keyed loading/error/results state, strict canonical-page decoding, and
case-insensitive self-exclusion. Both relationship and merge pickers use it.
Consumers provide project, excluded work, query, enabled state, and result
limit; they retain selection, relationship direction, eligibility, and explicit
merge review.

Changing or disabling a search immediately hides the old scope's state. Cleanup
aborts the previous request, and late callbacks cannot publish its results or
errors.

## Mutation scope and recovery

`selectMutationScope` owns dispatched-state selection and conjunctive scope
matching. Registry methods and the subscribed `useMutationScope` hook use the
same predicates. Prepared requests do not block; in-flight, unresolved, and
safety-conflict requests do. Conflict keys retain their operation-specific
meaning, including both merge endpoints and the work/gate pair on gate writes.
Gate draft UI watches its own gate key without freezing unrelated answer drafts.

`selectMutationRecovery` partitions dispatched intents exactly once: create
dialog, delete dialog, visible merge panel, opened work pane, then global
recovery. Pass ownership targets only for surfaces that can present usable
recovery controls. A merge panel owns its source's exact slot, not every merge
that happens to touch its work ID. Hidden panels must yield ownership to a
visible fallback.

`MutationRecoveryPanel` shares the warning and retry control, with optional
domain-specific wording for the single-merge presentation. Only unresolved
requests offer retry; safety conflicts remain blocked for inspection. Rendering
receives immutable public summaries, never request bodies or operation IDs.
The registry remains responsible for the frozen body/UUID, conflict checks at
dispatch, unload warnings, and byte-identical retries.

## Human-facing Markdown

`frontend/lib/markdown.ts` converts Markdown to HTML using the vendored
markdown-it browser build. `MarkdownContent` is the shared rendering boundary
for completion-report summaries/FYIs and human questions in both the Needs
Attention queue and work context. Shared `.markdown-content` styles keep lists,
code, and tables readable in light/dark themes and narrow layouts.

Raw HTML is escaped, unsafe link schemes are rejected by the parser, and image
embeds are disabled. Only this configured converter supplies the component's
HTML sink. Stored text, receipt bytes, report bounds, and API shapes are unchanged.
Report summaries/FYIs retain their single-paragraph input contract; questions
can use block Markdown. See the vendor README for upstream research, licenses,
integrity records, and upgrade instructions.

Synthetic browser examples, including deliberately escaped HTML and rejected
links: [summary formatting](images/markdown-summaries-light.png),
[questions in dark mode](images/markdown-attention-dark.png), and
[questions on a narrow screen](images/markdown-attention-narrow.png).

## Regression coverage

- `revision-codecs.test.mjs` and the existing domain/proxy/receipt suites cover
  structural acceptance, rejection, equality, and stricter evidence composition.
- `mutation-scope.test.mjs` drives all registry states and checks scope parity,
  both merge endpoints, gate/work conflicts, own-slot exclusion, and exclusive
  recovery placement.
- The Phase 9 browser suite covers delayed/cleared searches in both pickers and
  exact merge-response recovery. The existing receipt and human-gate browser
  suites cover navigation restrictions, modal recovery, and frozen retries.
- `markdown.test.mjs` covers formatting, escaping, links, image suppression,
  per-message isolation, and vendor integrity. `markdown-messages.spec.ts`
  exercises actual report/question writes, dashboard rendering, narrow/dark
  layouts, and browser DOM safety.

## Core server reads with TanStack Query (migration 5)

The migration-wide acceptance matrix and operator release/rollback procedure are
in [frontend migration release](frontend-migration-release.md). The ownership
rules below describe the completed migrations 5–7, including their deliberate
streaming and bootstrap exceptions.

`main.tsx` creates one QueryClient for the application lifetime, outside route
transitions. `lib/query-keys.ts` is the request identity boundary; project IDs,
filters, offsets (or infinite-query page parameters), canonical scope and search
terms belong in keys. Never include refresh counters, drafts or selections.
`core-queries.ts`, `task-queries.ts` and `work-queries.ts` build typed options and
run the existing strict wire decoders before publishing data. Each read consumes
Query's AbortSignal; changing scope detaches its observer and cancels the obsolete
request when no other consumer still needs it. Disabled work/search observers use
a separate disabled key so they do not keep an obsolete request alive or display
its data, errors or spinner.

The client uses a 30-second stale time and five-minute inactive cache retention.
Automatic Query retries, focus refetch and reconnect refetch are disabled.
WebSocket notifications and cursor-based activity catch-up remain the freshness
owners, including focus and reconnect; bootstrap still precedes project-scoped
surface reads.
Lexical/task/settings/catalog failures retain the existing bounded-backoff read
recovery. Semantic work searches never gain automatic failure retries. The
existing duplicate-inference admission policy and mutation executor are unchanged.

`invalidateCoreReads` maps the existing live/activity/lease notifications and
confirmed mutation callbacks to project-scoped work, task, settings and count
keys. The dashboard's refresh adapters invalidate resource and secondary domains
directly. Its work-list revision counter captures motion only; it does not
dispatch reads or form part of a cache key. Background refetch retains loaded data (including an empty
list). Do not use cross-project placeholder data. Work queues retain decoded wire
pages and server `next_offset`, including byte-budget-short pages and coverage.
On refresh they reload the prior raw loaded prefix, rather than assuming a fixed
page size or counting deduplicated display rows. Canonical pickers retain their
250ms debounce and hide results immediately when the request scope changes.

Migration 6 places selected work context, detail, checkpoint history, events,
relationships, children, gates, reviews, completion evidence, report provenance,
originating-report detail and work-scoped report policy in Query. Request keys carry project/work identity and each
resource's filters, cursor, offset or context limits. The selected context
observer is disabled: the existing placement and navigation coordinator explicitly
dispatches fresh reads through shared Query options after applying its request
generation and draft guards. It cancels the exact prior key before reconciliation
so an older in-flight read cannot satisfy a post-write request. Placement probes
use the same Query catalog with an explicit fresh fetch.
Fresh context and policy helpers also verify that their own transport ran without
cancellation. Query's cancellation fallback may retain cached data for display;
that retained data cannot satisfy placement verification or mutation preflight.

Query owns decoded read-only server snapshots. The editor's `editBase`, authored
report prompt revision and pinned review drafts remain separate UI state;
background metadata refresh cannot silently replace their reviewed versions.
Context observer identity and request bookkeeping only describe the view's current
selection and guarded navigation; they are not another resource cache.
`JobReportEditor` shares work-scoped settings reads while requiring an explicit
action to accept a changed report revision. Completion and review preflight
reads still request fresh data through Query before dispatch. MutationIntentRegistry
continues to own frozen writes and uncertain outcomes. Work-view report provenance
retains its snapshot sequence, exact continuation, ordering and identity checks
across infinite pages; refetch rebuilds the loaded chain from a fresh head.
Originating-report keys retain both the viewed work scope and the original report
project, preserving exact source identity after moves, merges and deletion.
Their decoded report snapshot stays visible during background refresh and failures.
Closing a selected report or gate history detaches its observer and cancels any
read no longer used by another observer. Migration 7 also places artifact and
transcript libraries, prompts, backups, standalone attention/report/review panels and
explicit duplicate comparisons in Query. Runtime configuration bootstraps the app before Query;
activity/WebSocket cursors and raw upload/download streams are transport state,
not cached resource pages. No production rollout is implied by this migration.

### Confirmed work mutations and Query reconciliation

`MutationIntentRegistry.subscribeConfirmed` publishes a decoded result after an
initial success or an explicit retry succeeds. The app connects that stream once
with `connectWorkMutationQueries`; its cleanup removes the subscription. Existing
`subscribeRecovered` callbacks remain exclusive to explicit retries, preserving
their navigation, draft and modal recovery behavior. Query never executes a
protected write or grants retry authority. Timeouts, transport ambiguity,
malformed success payloads, definitive rejections and safety conflicts do not
publish confirmations. Frozen bodies, operation IDs, conflict keys and explicit
retry behavior remain owned by the mutation registry.

`work-mutation-queries.ts` exhaustively maps every protected work mutation to
cached reads. In this table, **work reads** means work queues, canonical pickers,
task pages, selected-work detail and the attention list/count. **Closeout reads** adds
the report list/count. Selected-work detail includes all cached context, checkpoint,
event, relationship, review, completion-evidence and work-view report pages, including filtered
and paginated variants.

| Mutation | Reads invalidated | Scope evidence |
| --- | --- | --- |
| `create_work` | Work reads | Request project and decoded new work/initial relationships |
| `update_work` (edits, status, review request/decision) | Closeout reads | Frozen work key and decoded work |
| `defer_work` | Work reads | Frozen work key and decoded work |
| `add_checkpoint` | Work reads | Request project and frozen work key |
| `append_event` | Work reads | Frozen work key and decoded event |
| `complete_work` | Closeout reads | Frozen work key and decoded completed work |
| `add_relationship` | Work reads | Both frozen endpoint keys and decoded relationship authority |
| `remove_relationship` | Work reads | Both frozen endpoint keys and decoded relationship authority |
| `move_work` | Closeout reads | Frozen source/target keys plus decoded source/target projects and moved work |
| `merge_work` | Closeout reads | Both frozen work keys and both decoded work endpoints |
| `delete_work` | Closeout reads | Frozen work key and decoded deleted-work project |
| `resolve_human_input` | Work reads | Frozen work/gate keys and decoded gate project |
| `respond_to_work_follow_up` | Work reads | Frozen work key and decoded follow-up project |
| `dismiss_job_completion_report` | Selected-work detail and report list/count | Frozen report key and decoded dismissal project |
| `create_job_completion_report_follow_up` | Closeout reads | Frozen source/report keys and decoded new work |

Scopes are derived from the dispatched intent and decoded result, independently
of the currently selected project. Cross-project relationships and moves refresh
both sides. Originating reports also match their original authority project, even
when their viewed follow-up work now belongs to another project. Detail invalidation deliberately includes other work in each affected
project: relationship pointers, hierarchy and readiness can change when an
adjacent work item changes. Work-scoped report policy refreshes with these detail
reads; core project settings, unrelated projects and the project catalog remain
cached. Inactive reads become stale; active reads refresh with their
existing strict decoders and cancellation. Older active history requests are
cancelled before confirmation refresh. The guarded context observer is disabled,
so invalidation marks it stale while the explicit placement coordinator retains
ownership of reconciliation and navigation. No optimistic result enters the
cache, and unsuccessful writes do not clear or refresh cached data.

### Resource and secondary read ownership (migration 7)

The complete read inventory is below. Options factories own request identity,
transport cancellation and decoding; components own selection, cursor navigation,
drafts, disclosure and motion. Do not reintroduce a second fetched-page state or
effect-driven cache beside Query. Metadata uses the default 30-second freshness
and five-minute inactive retention unless the domain specifies otherwise.
Nothing persists Query bodies in browser storage.

| Read family | Query owner | Freshness and scope |
| --- | --- | --- |
| Project catalog, settings and navigation badges | `core-queries.ts` | Project scope; activity/bootstrap and live invalidation |
| Tasks, work queues and canonical pickers | `task-queries.ts`, `work-queries.ts` | Full filters and server pagination; existing bounded read admission and debounce |
| Work context, checkpoints, children and policy | `work-detail-queries.ts` | Work/project identity; guarded fresh reconciliation remains imperative through Query |
| Events, gates, reviews, recommendations and completion evidence | `work-panel-queries.ts` | Exact offsets/cursors and validated infinite-page chains |
| Work report provenance and report detail | `work-report-queries.ts` | Viewed work and original report authority project; shared with Summaries |
| Artifact availability, library/search, pickers, related and work-linked files | `artifact-queries.ts` | Shared availability; project/filter/sort/offset/limit keys; visible metadata discovery polling |
| Artifact metadata and bounded semantic passages | `artifact-queries.ts` | Artifact identity plus revision, extracted-text hash and passage locator for evidence; passage bodies have zero inactive retention |
| Transcript library/search, detail, text, conversation, health and settings | `transcript-queries.ts` | Project and complete search/pagination identity; text digest and normalized revision/segment locator pins |
| Prompt catalog/detail, action rendering and priority rubric | `secondary-queries.ts` | Project/prompt identity; renders include exact work/review scope, use explicit fresh reads and zero inactive retention; accepted draft revisions stay separate |
| Backup catalog and job status | `secondary-queries.ts` | Project/job identity; visible catalog polling, one-second active-job polling, terminal/error stop |
| Needs Attention and Summaries | `secondary-queries.ts` | Work filter/cursor or validated report cursor chain; shared invalidation with navigation counts |
| Review library | `task-queries.ts`, `work-panel-queries.ts` | Shared task list and exact work/review detail options |
| Explicit duplicate comparison | `duplicate-suggestion-queries.ts` | Frozen submitted draft/external records and project; disabled observer, explicit fresh action, zero inactive retention |

Artifact semantic inference never gains interval, focus, reconnect or invalidation
retries. A resource invalidation marks semantic results stale and asks for an
explicit new search. Metadata discovery continues while the visible library is
open because resource worker jobs do not publish work activity. Returning to a
visible artifact library refreshes ordinary metadata without invalidating or
repeating semantic inference. Sorting and paging preserve the displayed range
until the replacement page arrives, including when revisiting a cached key. Transcript list
and health discovery likewise poll visibly every 30 seconds, including empty
lists; selected metadata stops its own job polling at terminal states. Query
intervals stop while hidden. These are metadata reads, not job submissions.
Content replacement, deletion, unavailable storage and changed revision pins
must hide stale evidence and dispose associated preview transfers.

Confirmed artifact writes reconcile metadata through `reconcileArtifactMutation`
and invalidate the affected resource domain. Transcript settings/import/rebuild
invalidate transcript reads only after decoded success. Prompt and rubric saves
refresh their alternate views and affected settings/policy caches without adopting
new editor baselines. Backup completion refreshes the archive catalog. Core
settings notifications cover prompts, rubric and backup metadata; attention and
report notifications cover both lists and badges. Protected work confirmations
use the exhaustive mapping above, including explicit recovered outcomes. Unknown
outcomes never create cache success or resubmit an operation. Clipboard prompt rendering
uses a fresh Query transport for each action and rejects cancelled or retained
cache results; failures never substitute older instructions.

The remaining direct read transports are deliberate: runtime configuration loads
before the Query provider; WebSocket and project-activity readers own revision and
cursor delivery; raw artifact previews/downloads, transcript downloads and backup
archives own streaming bytes and browser object URLs. Raw file previews can reach
1 GiB and therefore stay outside the cache. Upload/restore and protected mutations
retain their existing request, byte-limit, cancellation, receipt and uncertainty
owners. Local preferences, clocks, form drafts and route state are not server reads.
