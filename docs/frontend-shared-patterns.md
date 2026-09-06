# Shared frontend boundaries

These modules consolidate the client-side P1 opportunities in the
[shared-pattern audit PR](https://github.com/RightBrain-Networks/mnemonic/pull/33).
They do not replace domain review rules or the receipt executor.

## Wire codecs

General structure belongs to the neutral `revision-codecs`, `work-codecs`,
`readiness-codecs`, `checkpoint-codecs`, and `relationship-codecs` modules in
`frontend/lib/`. Feature decoders, the browser proxy, and mutation-response
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
