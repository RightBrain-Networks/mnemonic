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
keys. The dashboard's small refresh adapters also notify remaining legacy panels.
Each migrated resource has one cache owner; the legacy counters are consumed only
by unmigrated panels. Background refetch retains loaded data (including an empty
list). Do not use cross-project placeholder data. Work queues retain decoded wire
pages and server `next_offset`, including byte-budget-short pages and coverage.
On refresh they reload the prior raw loaded prefix, rather than assuming a fixed
page size or counting deduplicated display rows. Canonical pickers retain their
250ms debounce and hide results immediately when the request scope changes.

Migration 6 owns selected work context/history/relationships/reviews/evidence and
protected-write reconciliation. Its imperative exact-context, placement and
fresh completion-policy reads remain explicit for now; MutationIntentRegistry
continues to own frozen writes and uncertain outcomes. Placement probes use the
same Query catalog with an explicit fresh fetch. Migration 7 owns artifact and
transcript libraries, prompts, backups, attention/report/review detail panels and
other secondary reads. Runtime configuration bootstraps the app before Query;
activity/WebSocket cursors and raw upload/download streams are transport state,
not cached resource pages. No production rollout is implied by this migration.
