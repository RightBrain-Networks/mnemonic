# Mnemonic validation record

## Code reviews implementation

Application/API/MCP/dashboard 0.13.0, plugin 0.14.0 and migration
`0024_code_reviews` add first-class reviews and bounded remediation ancestry.
The [implementation validation](code-reviews-validation.md) records the current
regression, capacity, restore, browser and independent cold-review evidence.
These are isolated implementation checks, not a production deployment.

## External records implementation — 2026-09-05

Application/API/MCP/dashboard `0.10.0`, plugin `0.12.0`, and migration
`0022_external_references` add authored references and explicit caller-supplied
comparison. The [implementation validation](external-records-implementation-validation.md)
records the full suites, three independent adversarial code reviews and their
fixes, and a populated supported backup/restore rehearsal. The
[performance evidence](external-records-performance-and-recovery-evidence.md)
records real-model fallback, context/transport sizes, and browser measurements.
These used synthetic data in disposable environments; no live cutover was run.

## Legacy eventless completions accepted by 0019 — 2026-09-05

This checkpoint records a production defect found and fixed on the deployed
stack at `/srv/mnemonic`. It claims no new release, backup, or cutover.

- **The deployed API crash-looped on start-up**, because it runs
  `alembic upgrade head` before `uvicorn` and migration `0019` refused the
  database. `mcp`, `web`, and `backup` all wait on `api: service_healthy` and
  so never started; PostgreSQL stayed healthy throughout and no data was lost.
- **The cause was two work items completed on 2026-08-31, before `0010`
  introduced the event timeline.** Each holds only a `context` checkpoint and a
  single backfilled `work_created` event, so `0010` -- which derives
  `work_completed` strictly from completion checkpoints -- correctly invented
  no completion for them. The `0019` preflight nevertheless required every
  `done` item to own a `work_completed` event, an invariant no constraint or
  trigger in the chain establishes. Running the four preflight conditions
  separately against the live database showed exactly one violated, with 0
  violations on the other three.
- **A copy of the production database was migrated to `0019` with the fix.**
  Restored from a 1,683,767-byte custom-format dump at head
  `0018_repository_freshness`, it reached `0019` and preserved all 123 work
  items with unchanged statuses (92 `done`, 15 `pending`, 8 `deferred`, 8
  `wont-do`), all 264 checkpoints, and all 557 events. The two legacy
  completions carry `completion_generation` 0; the other 90 completions
  received real negative generations. The same copy at head `0018` reproduced
  the original refusal at the identical line.
- **The API served both legacy items from the migrated copy.** Their
  completion-evidence reads returned 200 with a null current pointer and no
  episodes, where the unfixed route answered 503; a normally completed item
  still resolved its exact pointer and episode.
- **The complete backend suite passed 1,107 tests against PostgreSQL 17;
  backend Ruff and whole-source `ty` passed.** That count includes the eight
  new legacy-shape migration tests. Both new gates were confirmed to fail
  against the unfixed code before the fix was restored: the migration gate
  reported `0019 preflight rejected 0018 history:
  done_work_without_completion_event`, and the route gate reported 503.

## Phase 11 structured completion evidence implementation — 2026-09-04

This checkpoint covers application/API/MCP/dashboard `0.6.0`, plugin
`0.10.0`, and Alembic head `0019_structured_completion_evidence`, rebased
through `origin/main` at `9f5e57b`. It records observed repository-release
evidence; it does not claim a production backup, deployment approval, or
live-fleet cutover.

- **The complete backend suite passed 1,072 tests against PostgreSQL 17;
  backend Ruff and whole-source `ty` passed.** Seven upstream deprecation and
  schema-comparison warnings were non-failing. The suite covers the strict
  completion contract, atomic evidence persistence and replay, event-backed
  history, migration/backfill/downgrade behavior, direct-SQL guards, catalog
  auditing, backup/restore policy, bounded representations, and every earlier
  phase. An initial post-rebase run found one newly added shared-fixture
  consumer that still replaced each case's `expected_version` with `1`; the
  consumer was fixed, the current focused 84-test slice passed, and this
  complete clean rerun followed.
- **A separate production-shaped PostgreSQL 17 rehearsal passed from
  `0018_repository_freshness` through `0019`, backup, restore, and exact
  replay.** It compared 16 Phase 10 row/sequence sets before upgrade and 171
  restored Phase 11 row/sequence sets afterward. Read-only audits passed on
  the source 0018 database, the restored 0018 database, pre-enablement 0019,
  populated 0019, and restored 0019. Historical Phase 10 create/completion
  receipts and the new evidence-bearing completion receipt replayed exactly;
  a whole-schema sentinel was absent after restore.
- **Matched PostgreSQL 17 tools produced and parsed both rehearsal archives.**
  The pre-0019 archive was 205,623 bytes with 192 table-of-contents entries and
  SHA-256
  `0f7269e54e85213117aa9440abf32ba6b79b54414b73be1ebcff545c504d1f37`;
  the post-0019 archive was 320,695 bytes with 273 entries and SHA-256
  `290b1e0605df20c250f388126ca2d1eb204b37b32a67f4d108a24920125eaa6c`.
  A separate archive/downgrade proof reproduced all six expected catalog
  hashes, exact owner-only application-object ACLs, and absence of
  `PUBLIC EXECUTE` through
  downgrade/re-upgrade.
- **The complete MCP suite passed 548 tests; MCP Ruff and `ty` passed.** The
  adapter exposes exactly 28 tools and 11 protected writes. It keeps the new
  evidence history operation read-only, preserves the typed FastMCP dual
  representation, enforces bounded HTTP/stdio framing and output, and agrees
  with REST on the strict `complete_work.expected_version` range
  `1..2,147,483,646`.
- **The frontend passed all 252 unit tests, TypeScript checking, and the
  production build.** The host used Node v22.22.3 and emitted the expected
  engine warning because CI is pinned to Node 24. The final isolated
  Playwright stack ran 103 executions: 99 passed, four narrow/mobile cases
  skipped by design, and none failed, in 6.3 minutes.
- **Plugin and repository-freshness packaging checks passed.** The MCP/plugin
  contract gate passed 51 tests and the disposable helper behavior suite
  passed 71. A separate authentic Claude CLI smoke passed, and fresh
  `0.10.0` plus sequential
  `0.6.1 -> 0.7.0 -> 0.8.0 -> 0.9.0 -> 0.10.0` installations were byte- and
  mode-identical across all 11 payload files. The current host's real Git 2.43
  was rejected before repository access as required. The authentic macOS Bash
  3.2/newer-Git CI lane then passed on implementation commit
  `6b7ba816ed7e199dd1f2355dab8575b7bbe7d289`.
- **The deployed nginx identity-coding harness passed.** Stock nginx accepted
  the shared policy syntax; an ABI-matched Brotli module produced the positive
  `br` control; and evidence success/error responses, including the 1 MiB
  ingress and 3 MiB history boundaries, remained byte-exact identity bodies.
- **Cold review found, fixed, and closed every release blocker.** Early
  contract and documentation reviews closed request-version drift, raw
  survivor-projection wording, archived object-ACL scope, and proof
  attribution. A final context-free whole-diff review then found missing MCP
  response maxima, a missing browser work-version maximum, and a live runbook
  that could migrate without a fresh post-quiescence recovery point. Its
  migration specialist subsequently caught the scheduled backup loop still
  running across DDL. Exact-max/max-plus-one reader regressions now pass, and a
  runbook regression fixes the order as stop writers/backup loop, take and
  verify a one-shot archive, audit 0018, migrate, smoke, take/verify the 0019
  archive, then restart backup with an explicit health wait before writers. The
  independent contract and
  migration closure reviews returned `CLEAN`; the whole-diff reviewer returned
  **ACCEPT** with no remaining high-confidence issue.
- **Final static and supply-chain gates passed:** repository-wide
  pre-commit/gitleaks, backend/MCP/operational-script Ruff, backend/MCP `ty`,
  TypeScript, shell syntax, production build, and `git diff --check`.
- **Required pull-request CI passed on the reviewed implementation commit.**
  PR #32 Actions run `33919885049` passed Gitleaks, Ruff, `ty`, backend tests,
  MCP tests, frontend checks, the authentic macOS repository-freshness
  runtime, and the aggregate `Required checks` job against
  `6b7ba816ed7e199dd1f2355dab8575b7bbe7d289`.

The database, browser, plugin-install, helper, nginx, and rehearsal fixtures
were uniquely scoped and disposable. Their synthetic databases, archives,
credentials, containers, networks, volumes, and temporary installations were
removed. No production data was read or changed.

## Keyboard hints redrawn in the keyboard's own groups — 2026-09-04

This entry covers the owner-reviewed follow-up to
[Arrow key hints drawn in a keyboard's inverted T](#arrow-key-hints-drawn-in-a-keyboards-inverted-t--2026-09-04),
which kept the inverted T but repeated the arrows as `↑↓` and `←→` beside each
label (dashboard `0.5.0`, no application/API/MCP, plugin, migration, or
proxy-allowlist change). Those pairs are gone: the placeholder now draws each cap
exactly once, in the groups a keyboard gives them, and the copy carries the
directions instead.

The arrows keep their named-area grid — `". up ."` over `"left down right"` on
three fixed 22px columns — and the digit pair moves in beneath them, both key
groups centering in the first column of a two-column grid, so the narrower pair
lines up with the cluster's own axis rather than with its left cap. The three
labels share the second column and therefore start on one left edge whatever the
caps beside them measure, with the two arrow labels set as one block centered
against the cluster rather than a line per cap row. Because the T puts down in
the bottom row with left and right, no label can be read off the row it sits
against: they read "select work item (up/down)", "cycle states (left/right)", and
"select a project". The cluster is `aria-hidden`, since that copy names its keys;
the digit caps stay readable, since their label does not. Every figure below was
observed in the session that recorded it, on a local Node v22.22.3 checkout of
the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 227 passing, 0 failing.** The count is
  unchanged because `tests/empty-pane-keys.test.mjs`, added by the entry above,
  is rewritten rather than extended: one cap per arrow inside one `aria-hidden`
  cluster, the exact grid areas and three equal columns, both key groups
  centering in one column, the digit caps left readable, each label naming its
  own directions, and the arrow labels set as one centered block.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 99 executions, 95
  passing, 4 skipped by design, 0 failing, 5.6 minutes** on a uniquely named
  disposable Compose project; teardown left no container, volume, or network of
  its own. The Escape case in `tests/e2e/work-library-surface.spec.ts` measures
  the rendered block in the browser: up sits entirely above the bottom row and
  shares a left edge with down within a pixel, left and right sit either side of
  down without overlapping it, all three bottom caps share one top edge, the
  digit pair's center matches the cluster's within a pixel below it, and the
  three labels share one left edge clear of the caps. It also reads back the
  three label strings and the digit group's `1–0`. The dark-theme contrast
  fixture carries the new markup and the audit passes on it.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Arrow key hints drawn in a keyboard's inverted T — 2026-09-04

This entry covers reshaping the arrow hints in the quiet detail placeholder
recorded in
[Digit-key project selection and the pointer copy key](#digit-key-project-selection-and-the-pointer-copy-key--2026-09-04)
from two flat pairs into the inverted T the four keys occupy on a keyboard
(dashboard `0.5.0`, no application/API/MCP, plugin, migration, or
proxy-allowlist change). The cluster is a named-area grid — `". up ."`
over `"left down right"` on three fixed 22px columns — so up stays centered above
down whatever the labels beside it measure, and the placeholder emits each arrow
cap exactly once.

Folding two hint rows into one picture moves the down arrow out of the row its
own label sat on: read by row alone, the bottom row now reads as "cycle states",
which is wrong for down. Each label therefore carries its own `↑↓` or `←→` pair
in front of it rather than relying on the row it sits against, and the cluster is
`aria-hidden`, so a screen reader still hears the two hints it heard before
instead of four loose glyphs. The digit hint keeps its own caps on a line below.
Every figure below was observed in the session that recorded it, on a local Node
v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 227 passing, 0 failing.** The run adds
  `tests/empty-pane-keys.test.mjs`, which reads the placeholder and
  `app/globals.css` together so the caps and the grid that seats them cannot
  drift: one cap per arrow inside one `aria-hidden` cluster, the exact grid areas
  and the three equal columns, each cap's `grid-area`, both labels carrying their
  own pair, and the digit hint keeping its caps and its own spacing below.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 99 executions, 95
  passing, 4 skipped by design, 0 failing, 5.5 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` container,
  volume, or network of its own. The Escape case in
  `tests/e2e/work-library-surface.spec.ts` now also measures the rendered cluster
  in the browser: up sits entirely above the bottom row, its box shares a left
  edge with down within a pixel, left and right sit either side of down without
  overlapping it, and all three bottom caps share one top edge. The dark-theme
  contrast fixture carries the placeholder's new markup, and the audit passes
  with the labels' pairs inheriting the hint ink rather than a fainter one.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Hero project name set smaller than its title — 2026-09-04

This entry covers reducing the hero project name added in
[Hero heading names the selected project](#hero-heading-names-the-selected-project--2026-09-04)
to 60% of the view title beside it (dashboard `0.5.0`, no application/API/MCP,
plugin, migration, or proxy-allowlist change). The size is `.6em` rather than a
pixel value, so one declaration tracks all three sizes the heading already
resolves to: the fluid `clamp(32px, 3.1vw, 46px)` and the 38px and 33px
breakpoint overrides.

The heading tracks in absolute pixels (`-1.8px`, and `-1.2px` below 800px) tuned
for its own display size. Inherited unchanged into text at 60% of that size, the
same value is -.076em rather than the title's -.045em, and renders visibly
crowded; the name now restates that ratio relatively as `letter-spacing:
-.045em` so shrinking the text preserves the existing tracking rather than
tightening it. The colon keeps the full title size, as the period it replaced
did. Every figure below was observed in the session that recorded it, on a local
Node v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 223 passing, 0 failing.** The run adds one
  case to `tests/hero-heading.test.mjs` covering the relative size and the
  restated tracking ratio.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 99 executions, 95
  passing, 4 skipped by design, 0 failing, 5.5 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` container,
  volume, or network of its own. The existing hero case in
  `tests/e2e/phase1-work-checkpoints.spec.ts` now also reads both resolved pixel
  sizes back out of the browser and asserts their ratio is 0.6, and that the
  name's tracking is -.045 of its own size rather than the title's pixels.
- **Rendered sizes observed on the desktop viewport: 23.808px for the name
  against 39.68px for the title.** Both figures come from `getComputedStyle` on
  the running stack, so the ratio is measured after the clamp resolves.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Hero heading names the selected project — 2026-09-04

This entry covers replacing the Work library hero's trailing period with a colon
and the selected project's name (dashboard `0.5.0`, no application/API/MCP,
plugin, migration, or proxy-allowlist change). The colon keeps the accent the
period carried, so both the light and dark accent rules had to stop matching any
bare child span of the heading and name `.heading-mark` instead; the project name
is a sibling `.heading-subject` span that inherits the heading's ink. Only the
library passes a subject, so "Project settings." and "Needs Attention." are
unchanged, and the library falls back to "Work library." with no project selected.

The name is set in IBM Plex Sans Italic, vendored as two new WOFF2 subsets from
the same Google Fonts `ibmplexsans` v23 distribution and unicode-range split the
roman faces already use. It is the family's drawn italic — `fontTools` reports
subfamily `Italic`, the OS/2 and head italic bits set, and an italic angle of
-11.31°, over the same `wght` 100–700 axis — not a slant, and
`font-synthesis-style: none` keeps a browser from substituting a faux oblique if
the face ever fails to load. Because a project name is user-supplied and the
heading had no wrap guard, the subject also carries `overflow-wrap: anywhere`.
Every figure below was observed in the session that recorded it, on a local Node
v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 222 passing, 0 failing.** The run adds
  `tests/hero-heading.test.mjs` (the library alone passing a subject; the exact
  heading markup with its colon-or-period mark; no accent rule in either theme
  still matching a bare child span and none coloring the subject; the subject's
  italic, 80% opacity, disabled synthesis, and wrap guard; the italic faces
  matching the roman faces' subset split, weight axis, and count; and every
  declared IBM Plex Sans file existing, carrying the `wOF2` signature, and
  hashing differently from the others so a roman file cannot ship under an
  italic name).
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 99 executions, 95
  passing, 4 skipped by design, 0 failing, 5.4 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` container,
  volume, or network of its own. The run adds one case to
  `tests/e2e/phase1-work-checkpoints.spec.ts`: the hero reading
  `Work library: <project name>` after the picker changes projects, the mark
  painting the eyebrow's accent while the subject paints the heading's own ink,
  the subject computing to italic IBM Plex Sans at `0.8` opacity with
  `font-synthesis-style: none`, and an italic IBM Plex Sans face reaching
  `loaded` in `document.fonts` — which, with synthesis off, is what proves the
  vendored file was actually fetched to paint that text. The dark-theme contrast
  fixture now carries the hero's real markup.
- **Measured contrast of the project name at 80% opacity: 9.46:1 in the light
  theme and 6.15:1 in the dark theme**, against 17.45:1 and 9.02:1 for the same
  ink at full opacity. The dark figure sits below the 7.21:1 floor
  `tests/e2e/dark-theme-contrast.spec.ts` enforces, and that audit does not catch
  it: `auditTextContrast` reads each element's computed `color` and only skips an
  element at `opacity: 0`, so it measured the undimmed 9.02:1 and passed. The
  80% opacity was specified for this heading and is applied as specified; raising
  the dark subject's base ink to about `#c9c3ca` would return it to the band if
  that trade is not wanted.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Digit-key project selection and the pointer copy key — 2026-09-04

This entry covers replacing the function-key project shortcuts recorded in
[Work library keyboard bindings](#work-library-keyboard-bindings--2026-09-04)
with the digits, and naming the whole keyboard map in the quiet detail
placeholder, and binding `c` to the recall-pointer copy (dashboard `0.5.0`, no
application/API/MCP, plugin, migration, or proxy-allowlist change). The function row was rejected on reach rather than
implementation: a bare function key loses F1, F5, F11, and F12 to the browser and
the whole row to the macOS media-key default, while both modified forms are
worse — Alt+F*n* loses F4 to the window manager everywhere and five more keys to
GNOME and KDE, and Ctrl+F*n* loses F4 and F5 to the browser and F1 through F8 to
macOS keyboard navigation. An unmodified digit is reserved by nothing, at the
cost of ten slots rather than twelve. `frontend/lib/project-shortcuts.ts` now maps
1 through 9 and then 0 onto the picker's first ten projects, the option text
carries project names alone, and the placeholder lists all three bindings in the
`<kbd>` glyphs the queue hint already used. Because a digit is something a person
types — unlike an arrow or a function key — the shortcut had to start refusing a
typing target; that guard and the open-dialog check are now one shared module,
`frontend/lib/keyboard-shortcuts.ts`, which the queue map and the `/` search
shortcut both use.

`c` copies the open record's recall pointer through the same `copyRecallPointer`
the record's own button calls, so the value, the notice, and the copied state
cannot drift from it. It is the queue's key rather than the pane's: with focus
inside `.work-detail-pane` it is left alone, since the pane carries its own copy
button, and with nothing open it does nothing at all. It is deliberately absent
from the placeholder hint stack, which renders only when no record is open —
exactly when `c` has nothing to copy. A Playwright probe of the three `c`
variants settled how the key arrives: `press("C")` reports `key=C` with
`shiftKey` false, which is what Caps Lock produces on a real keyboard, while
`Shift+c` reports `key=c` with `shiftKey` true. A single-character key is
therefore compared lowered so Caps Lock still copies, and the modifier guard
above still refuses a real Shift. Every figure below was observed in the session
that recorded it, on a local Node v22.22.3 checkout of the topic branch (CI uses
Node 24).

- **Frontend unit tests (`npm test`): 216 passing, 0 failing.** The rewritten
  `tests/project-shortcuts.test.mjs` covers the ten-slot range with 0 as the
  tenth, non-integer and out-of-range rejections, a round trip from every bound
  index back through its key, and `""`, `" "`, `"10"`, `"01"`, `"!"`, `"a"`,
  `"F1"`, `"ArrowLeft"`, `"Escape"`, and a full-width `"０"` all resolving to
  nothing.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 97 executions, 93
  passing, 4 skipped by design, 0 failing, 5.8 minutes** on a uniquely named
  disposable Compose project, run after the rebase onto the cross-dissolve below so
  the figures describe the branch as it merges; teardown left no `mnemonic-e2e-*`
  container, volume, or network of its own. The project case presses digits,
  asserts the options carry no key prefix, and adds the guard that matters for a
  digit: a number typed into the search field stays in the field and switches
  nothing. A new case reads the real clipboard rather than a notice: `c` copies the
  open record's pointer with the card button's copied state, copies again for the
  uppercase letter Caps Lock reports, and leaves the clipboard untouched with
  nothing open, with focus inside the pane, with Shift held, and when typed into
  the search field. Because writing the clipboard from an `evaluate` has no user
  activation behind it, each "nothing happened" check compares the clipboard before
  the press to after rather than seeding a sentinel. The
  Escape case additionally asserts the uncovered placeholder lists all three
  hints in order.
- **Placeholder rendering:** captured at 1440×900 in both themes from a
  disposable stack; the three hints stack as one block with the second and third
  7px under the first, and the `<kbd>` glyphs keep their existing light and dark
  treatment. `tests/e2e/dark-theme-contrast.spec.ts` carries the two new lines in
  its static fixture, so its contrast sweep covers them.
- **Gitleaks (`pre-commit run --all-files`): passed.**

## Cross-dissolve retiming to 400ms — 2026-09-04

This entry covers shortening the work library's lifecycle-filter cross-dissolve from
500ms to 400ms on owner review that it ran a little long (dashboard `0.5.0`, no
application/API/MCP, plugin, migration, or proxy-allowlist change). The change is the
one `--pane-crossfade-duration` in `app/globals.css`; both halves of both panes read
it, so nothing else moved and the two circ easings are unchanged. Every figure below
was observed in the session that recorded it, on a local Node v22.22.3 checkout of the
topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 217 passing, 0 failing**, including the
  stylesheet assertion in `tests/pane-crossfade.test.mjs` now reading 400ms on the
  group, old, and new halves of both panes.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Playwright suite on a disposable stack: 95 executions, 91 passing, 4 skipped by
  design, 0 failing, 5.4 minutes** on a uniquely named `mnemonic-e2e-` Compose project
  built from this frontend; teardown left no `mnemonic-e2e-` project, container, or
  volume belonging to this run. The cross-dissolve case reads `0.4s` on every captured
  half from one `span` constant, so the next retiming is one edit there too.
- **Held-frame inspection (Chromium, 1500x950):** with the transition's animations
  paused and stepped through 0, 80, 160, 200, 280, 360, and 400ms, each pane's outgoing
  capture reads 1.000, 0.402, 0.201, 0.135, 0.046, 0.005, 0.000 and its incoming 0.000,
  0.020, 0.084, 0.135, 0.288, 0.565, 1.000. The curve is the one the previous entry
  recorded, compressed: the halves still cross at 0.135, now 100ms sooner.
- **Gitleaks (`pre-commit` hook): passed** on the commit.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Lifecycle-filter cross-dissolve — 2026-09-04

This entry covers replacing the abrupt swap of both work-library columns on a
lifecycle-filter change with a cross-dissolve (dashboard `0.5.0`, no
application/API/MCP, plugin, migration, or proxy-allowlist change). Before it,
the queue and the detail pane cut straight to their new contents. The filter
change now runs inside one view transition: `usePaneCrossfade` renames the queue,
and the detail pane when the change retires the open record, then applies the
state change inside `document.startViewTransition`, so the browser eases each
outgoing capture out on easings.net `easeOutCirc` while the live pane eases in on
`easeInCirc`. The filter buttons, the empty state's Clear filters, and the
horizontal-arrow shortcut all reach it through the same `filterByStatus`. `--pane-crossfade-duration` in `app/globals.css` is the single
adjustable span for both halves. Everything the filter did not rename is captured
as the root, which the stylesheet holds still so the clicked button answers at
once and the theme selector keeps its own root crossfade. A first attempt
overlaid a cloned DOM snapshot instead; it duplicated every element in the pane
for the length of the fade and broke seven existing specs on strict-mode locator
matches, which is why the shipped change carries no clone at all. Every figure
below was observed in the session that recorded it, on a local Node v22.22.3
checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 217 passing, 0 failing.** The run adds
  `tests/pane-crossfade.test.mjs`, which reads `app/globals.css` so the stylesheet
  and `lib/pane-crossfade.ts` cannot drift: one 500ms duration on the group, old,
  and new halves of both panes; `cubic-bezier(0.55, 0, 1, 0.45)` on the incoming
  half and `cubic-bezier(0, 0.55, 0.45, 1)` on the outgoing one; the scoped rule
  that holds the root still without disarming the theme's own root fade; and which
  panes each `statusFilterTransition` result renames.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Playwright suite on a disposable stack: 95 executions, 91 passing, 4 skipped
  by design, 0 failing, 5.4 minutes** on a uniquely named `mnemonic-e2e-` Compose
  project built from this branch. The run adds the desktop case in
  `tests/e2e/work-library-surface.spec.ts` that opens a pending record, clicks
  Deferred, and reads the running animations in the same task: both panes captured
  for `0.5s` on the two circ curves, no root half animating, no second copy of the
  detail title in the DOM, and no `view-transition-name` or `data-pane-crossfade`
  left behind once it settles; then a filter change driven by the horizontal-arrow
  shortcut with nothing open, which captures the queue alone; then a filter click
  under `prefers-reduced-motion: reduce`, which starts no transition at all. The
  narrow project skips it for the same reason it skips the deselection case: the
  full-screen sheet covers the filter row while a record is open. Every other spec
  passed unchanged.
- **Held-frame inspection (Chromium, 1500x950):** with the transition's animations
  paused and stepped through 0, 100, 200, 250, 350, 450, and 500ms, each pane's
  outgoing capture reads 1.000, 0.402, 0.201, 0.135, 0.046, 0.005, 0.000 and its
  incoming 0.000, 0.020, 0.084, 0.135, 0.288, 0.565, 1.000; the queue and the detail
  pane measured identically. The halves cross at 0.135 rather than at half weight,
  because `easeInCirc` in against `easeOutCirc` out is a dip by construction: the
  surface passes through the page ground mid-transition instead of holding constant
  weight. Held frames at each step show the sidebar, header, filter row, and the
  clicked button unfaded throughout.
- **Gitleaks (`pre-commit` hook): passed** on the commit.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Work library keyboard bindings — 2026-09-04

This entry covers three keyboard bindings added beside the queue's existing
vertical arrows (dashboard `0.5.0`, no application/API/MCP, plugin, migration,
or proxy-allowlist change). The horizontal arrows walk the lifecycle filter row
as a ring; Escape drops the open selection through the same rule the pane's Back
button follows and stays silent with nothing open; F1 through F12 select the
workspace picker's first twelve projects, each option naming its own key because
a native `<option>` carries text and nothing else. The pure parts live in
`cycleStatusFilter` and the shared `statusFilterOrder` in
`frontend/lib/work-queue.ts`, which the filter row now renders from, and in the
new `frontend/lib/project-shortcuts.ts`. Every figure below was observed in the
session that recorded it, on a local Node v22.22.3 checkout of the topic branch
(CI uses Node 24).

- **Frontend unit tests (`npm test`): 212 passing, 0 failing.** The run adds
  `tests/project-shortcuts.test.mjs` (the bound range and its integer and
  out-of-range rejections, per-option labels inside and past the range, every
  bound key resolving to its index while `F0`, `F13`, `f1`, and non-function keys
  resolve to nothing) and four cases to `tests/work-queue.test.mjs` (the filter
  row carrying each labelled filter exactly once, a forward and backward step from
  every filter, both ends wrapping, and an unknown filter landing on the first).
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 93 executions, 90
  passing, 3 skipped by design, 0 failing, 5.2 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` container,
  volume, or network of its own. The run adds three cases to
  `tests/e2e/work-library-surface.spec.ts`: Escape closing a clicked selection and
  then an arrow-key selection with the address cleared each time and a second
  press changing nothing; the horizontal arrows walking all eight filters,
  wrapping at both ends, persisting the filter they land on, and leaving both a
  focused search field and the focused surface divider to their own use of the
  same keys (desktop only, since the stacked layout has no divider); and the
  function keys switching to a project created for the test and back again from a
  picker whose first twelve options each carry their key while the rest carry
  none. The function-key case reads its fixture's position from the rendered
  picker rather than assuming one, because earlier specs seed projects of their
  own.
- **Observed and fixed during the run:** the function-key handler first refused
  while a background project refresh was in flight, which the live-sync `projects`
  scope triggers, so a press could be silently dropped for the duration of a
  refetch. The handler now answers from the list already on screen. A pane closed
  below 900px is `display:none`, where a role locator stops resolving, so the new
  Escape assertions address it by class.
- **Gitleaks (`pre-commit run --all-files`): passed.**

## Lifecycle-filter deselection — 2026-09-04

This entry covers dropping the open work-item selection when the work library's
lifecycle filter changes (dashboard `0.5.0`, no application/API/MCP, plugin,
migration, or proxy-allowlist change). Before it, a record reached under Pending
stayed in the detail pane after Deferred, Done, or any other filter was clicked,
so the pane showed a record the queue no longer listed. The rule now lives in one
pure helper, `statusFilterTransition` in `frontend/lib/work-queue.ts`, which the
filter buttons and the empty state's Clear filters both consult; reselecting the
filter already in force is not a change, and an unsaved edit or checkpoint draft
holds the change behind the same confirmation that closing the pane uses. Every
figure below was observed in the session that recorded it, on a local Node
v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 205 passing, 0 failing.** The run adds three
  cases to `tests/work-queue.test.mjs`: every ordered pair of the eight distinct
  filters deselects while a record is open, reselecting the current filter is
  `unchanged`, and a change with nothing open only refilters the queue.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 87 executions, 85
  passing, 2 skipped by design, 0 failing, 5.0 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` project, container,
  or volume. The run adds the desktop case in
  `tests/e2e/work-library-surface.spec.ts` that opens a pending record, switches to
  Deferred and observes the closed pane, the "Pick a work item." placeholder, and
  the cleared `?work=`; reselects Pending and confirms clicking the filter already
  in force keeps the selection and the address; then selects under All, empties the
  queue with a nonmatching search, and confirms Clear filters returns to Pending and
  drops the selection. The narrow project skips it because the full-screen sheet
  covers the filter row while a record is open, which is the second skip alongside
  the pre-existing narrow divider case. Every other spec passed unchanged, including
  each one that switches a lifecycle filter after closing the detail.
- **Gitleaks (`pre-commit` hook): passed** on the commit.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Brand mark for the favicon and sidebar — 2026-09-03

This entry covers replacing the dashboard's orange badge with the robot-head
artwork now tracked at `images/mnemonic_logo.svg` (dashboard `0.5.0`, no
application/API/MCP, plugin, migration, or proxy-allowlist change). The artwork
is drawn three times: the source asset, the favicon Next.js serves from
`frontend/app/icon.svg`, and the mark inlined into `Logo()` in
`frontend/components/dashboard.tsx`. Every figure below was observed in the
session that recorded it, on a local Node v22.22.3 checkout of the topic branch
(CI uses Node 24).

- **Frontend unit tests (`npm test`): 202 passing, 0 failing.** The run adds
  `tests/brand-mark.test.mjs`, which parses all three copies and compares each
  one's drawing primitives and resolved paint against the source asset,
  class-resolved through the exporter's `<style>` block and tokenized so wrapped
  `d` attributes compare by command and number rather than by text. Each of its
  five assertions was mutation-checked: a drifted fill, a moved path
  coordinate, a replaced white stroke, a non-square favicon box, and a distorted
  sidebar aspect ratio each failed exactly one test, and the unmutated tree
  passed all five.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass**, with `/icon.svg` still emitted as a route.
- **Isolated Playwright stack (`npm run test:e2e:stack`): 85 executions, 84
  passing, 1 skipped by design, 0 failing, 4.8 minutes** on a uniquely named
  disposable Compose project; teardown left no `mnemonic-e2e-*` container or
  volume. The single skip is the narrow project's divider case in
  `tests/e2e/work-library-surface.spec.ts`, which the stacked layout has no
  divider for. `tests/e2e/dark-theme-contrast.spec.ts` passed unchanged: the
  three edited rules dropped `.logo-mark` from per-theme tinting, which the new
  fixed-color mark no longer reads, and left every `.brand-period` color as it
  was.
- **The running stack served the favicon and the mark.** Against that same
  disposable web container, `GET /icon.svg` returned 200 with a body whose
  SHA-256 matched `frontend/app/icon.svg` exactly
  (`36f1ff230c950e656c38cae05808eca65eca3cd5afa6dbdd1906a8414626fe3f`); the
  dashboard document carried `<link rel="icon" ... type="image/svg+xml">` and a
  server-rendered `class="logo-mark"` SVG at `viewBox="0 0 916 863.9"` with both
  brand fills present.
- **Gitleaks (`pre-commit` hook): passed** on the commit.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result is
claimed for this change, and it does not alter any production, cutover, or
permanence gate.

## Phase 10 repository freshness implementation — 2026-09-03

This checkpoint covers application/API/MCP/dashboard `0.5.0`, plugin `0.9.0`,
and Alembic head `0018_repository_freshness`, rebased through
`origin/main` at `a0cc7fc`. It records observed repository-release evidence;
it does not claim a production backup, deployment approval, or live-fleet
cutover.

- **The complete backend suite passed 650 tests against PostgreSQL 17; backend
  Ruff and whole-source `ty` passed.** The suite includes fresh and populated
  migration, cross-layer grammar, sparse response, all 13 receipt kinds,
  idempotency, authorization, alias/root, concurrency, OpenAPI, and aggregate
  audit coverage. Seven upstream deprecation warnings were non-failing.
- **A separate production-shaped database rehearsal passed.** Its populated
  0017 fixture held seven work items, ten checkpoints across all kinds, 17
  events, two relationships, a merge, gate, lease, two embeddings, and every
  completed receipt kind. Upgrade to 0018 took 0.58 seconds and preserved every
  prior row count and digest; historical scope stayed empty, old create/add/
  complete receipts replayed without a changed digest, and the audit reported
  zero findings. The focused migration/recovery batch passed 62 tests in 4.72
  seconds, including both downgrade races.
- **Downgrade and recovery boundaries behaved exactly as documented.** A
  pre-use 0018-to-0017 downgrade and re-upgrade preserved all prior digests.
  After three scoped create/add/complete writes and exact replays, downgrade
  refused without losing the column, scope, or any of 16 receipts. A deliberately
  removed constraint produced one audit blocker; restoring the reviewed
  constraint fixed forward in place with all scope intact. A real strict 0.4.0
  `CheckpointRead` rejected `affected_paths` as `extra_forbidden`.
- **Matched PostgreSQL 17 backup tools produced and restored both sides of the
  migration.** The pre-0018 custom archive was 208,456 bytes with 191 table-of-
  contents entries and SHA-256
  `ef01dd9460bc284b9df3fe3b42c2fffc6a4cab8a70ef56e2e96394dde9fd3d8a`.
  The post-0018 archive was 212,763 bytes with 192 entries and SHA-256
  `5a29a0b1a817c209eea18445cd4e8129bbefe7379315ba8029ed076ae9494e84`;
  all 11 protected-table digests, 16 canonical receipts, and three scoped
  checkpoints matched after restore. Whole rollback to the earlier archive
  served the exact 0.4.0 code successfully while making the documented
  post-backup data-loss boundary concrete.
- **The complete MCP suite passed 348 tests; MCP Ruff and `ty` passed.** The
  adapter remains repository-blind, exposes exactly 27 tools and 11 protected
  writes, keeps compact pointers scope-free, and strictly accepts only the
  sparse canonical full-checkpoint contract.
- **The frontend passed all 197 unit tests, TypeScript checking, and a Node 24
  production build.** The final isolated Playwright stack passed 84 executions
  with one intentional narrow-layout divider skip and zero failures in 4.8
  minutes. It covered declared scope create/display/append/complete/refresh and
  historical omission at desktop and narrow widths. The first full run exposed
  an accessible-name collision between the affected-path hint and baseline
  field; a dedicated label plus regression fixed it before the clean rerun.
- **The repository helper ran 72 default tests: 71 passed and only its opt-in
  authentic-runtime case skipped; that case then passed with real Bash 3.2.57
  and Git 2.45.4.** Real Git 2.44.4 was rejected before repository access. The
  first hosted PR run then exposed two supported-platform drift cases: Git
  2.55 diagnosed expansion of an active sparse index during the unmerged scan,
  and macOS rejected the exhaustive invalid-UTF-8 filename fixture with
  `EILSEQ` before helper execution. Follow-up adversarial isolation proved the
  Git expansion could also write a loose tree object despite optional locks
  being disabled. The delivered fix makes unmerged enumeration sparse-aware
  with `--sparse`; it does not suppress the diagnostic or relax fail-closed
  stderr handling. Active and config-disabled on-disk sparse fixtures now prove
  both accepted indeterminate taxonomies and byte-for-byte repository
  preservation. The filename test falls back to a valid UTF-8 control/
  punctuation/multibyte corpus only on `EILSEQ`, while Linux retains exhaustive
  bytes `01` through `FF`. Complete discovery subsequently passed all 72 tests
  locally with authentic Bash 3.2.57 and Git 2.55.0. The matrix covers object
  topology, every change lane, per-pattern matching, hostile environment/
  config/filter sentinels, normalization and index blockers, races, exact
  protocol/exit behavior, byte quoting and caps, and absence of configured
  process, network, or repository mutation. A required `macos-15` authentic
  runtime job is part of the aggregate CI gate.
- **Plugin source and installed packaging passed validation.** A fresh isolated
  0.9.0 install was byte-identical, with the helper at Git mode `100755`; a
  separate `0.6.1 -> 0.7.0 -> 0.8.0 -> 0.9.0` update left only the current
  version active. Installed clean and dirty helper smokes returned
  `unchanged`/exit 0 and `changed`/exit 10.
- **A fresh production-image stack passed read-only and authorized writable
  checks.** All five services became healthy at head 0018. The checker verified
  REST 0.5.0, authentication/origin/proxy/font boundaries, the exact tool/write
  catalogs, scoped receipt recovery, gates, ready work, hierarchy, merge/alias
  invariants, and cleanup. Its database retained exactly the two merge members
  and one immutable witness, with five other synthetic records soft-deleted.
  A 203,206-byte, 192-entry custom backup validated with SHA-256
  `e44feb6815d6e23970fd9f9736d46fecf86330be2d784ff9134dacc9adb1e2a2`;
  service logs contained no error marker.
- **Final static and supply-chain gates passed:** both plugin manifests,
  workflow YAML/runtime-job assertions, operational-script Ruff,
  `git diff --check`, and `pre-commit run --all-files` including gitleaks.
  Cold implementation review rejected unsafe intermediate helper mechanisms,
  verified their replacements under authentic Git 2.45, and returned
  **ACCEPT** on the final rebased tree with no unresolved blocker.

The E2E, stack, migration, restore, plugin-install, and helper fixtures were
uniquely scoped and disposable. Their containers, networks, volumes, databases,
archives, credentials, and temporary installations were removed. The existing
Mnemonic stack stayed healthy and no production data was read or changed.

## Adjustable work-surface split — 2026-09-03

This entry covers the draggable divider between the work queue and the detail
pane (dashboard `0.5.0`, no application/API/MCP, plugin, migration, or
proxy-allowlist change). The queue's share of the surface is stored in
`localStorage` under `mnemonic.work-split`, clamped to 20–70%, and applied as a
CSS variable that the grid bounds so neither column drops below its readable
minimum. Every figure below was observed in the session that recorded it, on a
local Node v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 180 passing, 0 failing.** The run adds
  `tests/work-split.test.mjs` (bounds and rounding, stored-preference parsing,
  pointer-to-share mapping, keyboard steps) and the preference-key and parser
  assertions in `tests/dashboard-preferences.test.mjs`.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass.**
- **Isolated Playwright stack (`npm run test:e2e:stack`): 81 executions, 80 passing,
  1 skipped by design, 0 failing, 4.6 minutes** on a uniquely named disposable
  Compose project (40 on desktop Chromium, 1 on the focused Firefox motion
  project, 39 passing plus the skip on narrow Chromium); teardown left no
  `mnemonic-e2e-*` container, volume, or network. The run
  includes the new desktop divider test in
  `tests/e2e/work-library-surface.spec.ts` (drag, stored share, reflow without
  page overflow, reload persistence, keyboard steps to both bounds, double-click
  reset); the narrow project skips it because the stacked layout has no divider.
- **Gitleaks (`pre-commit run --all-files`): passed.**

## Work library two-column surface — 2026-09-03

This entry covers dashboard `0.5.0` over unchanged application/API/MCP `0.4.0`,
plugin `0.8.0`, and Alembic head `0017_duplicate_suggestion_title_key`. The
change replaces the work-context modal with the two-column work surface: a
lazily paged queue beside a detail pane with Context, History, Graph,
Questions, and Activity tabs, inline edit in the Context tab, and the merge
panel inside the Graph tab. It touches no backend, MCP, plugin, migration, or
proxy-allowlist code and is validated with the frontend checks only. Every
figure below was observed in the session that recorded it, on a local Node
v22.22.3 checkout of the topic branch (CI uses Node 24).

- **Frontend unit tests (`npm test`): 175 passing, 0 failing.** The run
  includes the new `tests/work-queue.test.mjs` (page merging, loaded offsets,
  forced More-filters state, result-count labels, arrow-key selection, and
  list-scroll arithmetic) and `tests/work-detail-tabs.test.mjs` (tab counts
  and alert state) alongside every existing suite; the proxy-policy tests are
  unchanged.
- **TypeScript checking (`npm run typecheck`) and the production build
  (`npm run build`): both pass** with the final tree.
- **Isolated Playwright stack (`npm run test:e2e:stack`): 79 executions, 79
  passing, 0 failing, 4.5 minutes** on a uniquely named disposable Compose
  project: 39 on desktop Chromium, 1 on the focused Firefox motion project,
  and 39 on narrow Chromium. Teardown left no `mnemonic-e2e-*` container,
  volume, or network. The run covered `tests/e2e/work-library-surface.spec.ts`
  in both Chromium projects, every phase spec migrated to the
  `tests/e2e/surface.ts` helpers, and the updated dark-theme contrast fixture.
  Two earlier full runs on the same tree surfaced and fixed, in order: two
  pre-existing dark-theme contrast gaps (blocked/waiting operational badges and
  `button.text-link` chrome), an unmounted merge recovery block after a lost
  merge response, and two live-sync races that superseded an in-flight exact
  or reconciling context load; the first duplicate-suggestion request on a
  fully seeded project takes about six seconds while new embeddings are built,
  so that browser expectation now waits up to thirty seconds.
- **Metadata alignment: confirmed.** `frontend/package.json` and both root
  `package-lock.json` version fields read `0.5.0`; application/API/MCP remain
  `0.4.0`, the plugin `0.8.0`; the browser registry remains exactly eleven
  mutations and no proxy route was added.
- **Gitleaks (`pre-commit run --all-files`): passed.**
- **Wide-layout follow-up (same day): 79 executions, 79 passing, 0 failing,
  4.6 minutes, clean teardown.** After review on a 2000px-wide monitor the
  library view's content column stopped inheriting the 1320px cap that the old
  single-column list used (`.page-content-library`, capped at 2200px); the
  full isolated stack was rerun on that tree, and `npm test` (175 passing) and
  `npm run typecheck` were repeated.

No backend, MCP, migration, plugin, stack-check, or adversarial-review result
is claimed for this change, and it does not alter any production, cutover,
backup/restore, or permanence gate recorded below.

## Phase 9 Advisory implementation checkpoint — 2026-09-02

This checkpoint covers application/API/MCP/dashboard `0.4.0`, plugin `0.8.0`,
and Alembic head `0017_duplicate_suggestion_title_key`. It records the
separately versioned Advisory duplicate-suggestion implementation; the Core
checkpoint remains below. Only observed results are stated.

- **The complete backend suite passed 525 tests against PostgreSQL 17; backend
  Ruff and whole-source `ty` passed.** The suite includes the committed OpenAPI
  `0.4.0` freshness guard, suggestion selection, resource, deadline, and cache
  regressions, and the shared ordinary-search/suggestion inference gate.
- **The focused Advisory migration, audit, and schema-parity batch passed seven
  PostgreSQL tests.** A read-only aggregate audit also completed with zero
  blockers on an isolated schema at head 0017. This is repository and
  fresh-schema evidence, not a populated-production preflight or restore
  rehearsal.
- **A separate populated Core database passed the aggregate audit at exactly
  head 0016 with zero blocking or violation counts.** Its disposable fixture
  contained one project, three work items, and one complete authoritative
  merge. The exact database was dropped afterward; this is not a target-system
  audit or backup/restore evidence.
- **The complete MCP suite passed 288 tests; MCP Ruff and `ty` passed.** The
  catalog contains exactly 27 tools and 11 protected writes;
  `suggest_duplicate_work` is a strict `safe_read` with a bounded hard timeout
  and value-free failure handling.
- **Under Node 24, the frontend passed all 152 unit tests, TypeScript checking,
  and the production build.** The final production image also built
  successfully. The explicit Check Existing action remains separate from
  Create, and suggestions remain memory-only.
- **The final isolated Node 24 browser stack passed all 57 Playwright
  executions in 2.6 minutes:** 28 in desktop Chromium, one in the focused
  Firefox motion project, and 28 in narrow Chromium. The total includes four
  Advisory executions and eight Core duplicate executions. Teardown left no
  containers, network, or volumes for the uniquely named stack.
- **The isolated production-shaped checker passed both its read-only and
  authorized writable checks.** Its intentional durable effect was exactly one
  authoritative merge over two synthetic work items. The post-check aggregate
  audit reported head 0017, `authoritative_merges=1`, `pending_receipts=0`, and
  zero blocking or violation counts. The exact checker stack and volume were
  then destroyed; this was disposable evidence, not a production merge.
- **The plugin passed fresh and sequential installation drills.** A fresh
  `0.8.0` install was byte-identical to its source, and a separate
  `0.6.1 -> 0.7.0 -> 0.8.0` update left only 0.8.0 active with three skills,
  two shared references, and no compatibility skill tree.
- **A cold adversarial review found two release-blocking issues in concurrency
  and normalization handling.** Both were fixed and covered by regressions
  before the complete suites and final acceptance stack passed; the closure
  review found no remaining blocker.
- **`pre-commit run --all-files` passed**, including the required hardcoded
  secret scan.
- **The application, MCP, frontend, and plugin metadata align at the Advisory
  boundary.** Application packages and the dashboard are `0.4.0`; the plugin
  is `0.8.0`; OpenAPI is `0.4.0`; the migration head is 0017; and the catalogs
  contain 27 MCP tools, 11 protected writes, 13 REST receipt kinds, and 11
  browser mutations.

No production database, populated-production audit, cutover, backup/restore
rehearsal, frozen numeric performance fixture, recovery-point proof, or
product/operator permanence signoff is recorded here. The disposable writable
check does not fill any of those gates. They remain explicit prerequisites
before production traffic is enabled.

## Phase 9 Core implementation checkpoint — 2026-09-02

This checkpoint covers application/API/MCP/dashboard `0.3.0`, plugin `0.7.0`,
and Alembic head `0016_duplicate_handling`. Core contains authoritative
duplicate merging and canonical aliases. It does not contain or validate the
separate Advisory suggestion release. Only observed results are stated here.

- **The complete backend suite passed 458 tests against PostgreSQL 17; backend
  Ruff and whole-source `ty` passed.** The committed OpenAPI freshness test
  passed with `docs/openapi.json` regenerated at application version `0.3.0`.
  The focused client-operation suite passed 95 tests, including the thirteenth
  merge receipt and preserved response-v1 contracts. A separate non-PostgreSQL
  selection earlier passed 189 tests with 31 database skips and 220 deselected;
  that run is supporting evidence only, not the database release result.
- **The focused Core migration batch passed 13 PostgreSQL tests.** It covers
  fresh/populated `0015 -> 0016` upgrade, preservation and zero inferred
  merges/witnesses, schema/catalog parity, immutable ledger/evidence/event
  constraints, and direct-database guard behavior. Migration 0016 has no
  downgrade.
- **The aggregate duplicate audit passed manually on a fresh PostgreSQL 17
  schema at head 0016 with zero blocking findings.** This proves that invocation
  and fresh-schema catalog only; it is not a populated production preflight,
  continuous audit history, or backup/restore rehearsal.
- **The complete MCP suite passed 241 tests; MCP Ruff and `ty` passed.** The
  contract contains exactly 26 tools and 11 protected writes, with
  `merge_work`, canonical/group search, alias context, strict merge results,
  same-key recovery, and no suggestion tool.
- **The frontend passed `npm ci`, all 142 unit tests, TypeScript checking, and
  the production build.** Its contract contains exactly 11 protected browser
  mutations, including the two-work-key frozen merge intent. The local install
  reported the existing Node 22 versus required Node 24 engine warning; the
  build itself completed successfully, and the disposable production image
  rebuilt and passed with Node 24.
- **The complete isolated browser stack passed all 53 Playwright executions in
  desktop Chromium, narrow Chromium, and the focused Firefox motion project.**
  Eight of those executions exercise the four Core duplicate scenarios at both
  dashboard viewports: exact lost-response replay, immutable aliases and
  regrouping, active-lease/capability denial, context drift, and unambiguous
  source/destination identity under hostile Unicode titles.
- **Both plugin manifests passed strict validation.** A disposable fresh
  `0.7.0` install and a separate sequential `0.6.1 -> 0.7.0` marketplace/cache
  update both resolved the Core plugin version. The isolated configuration and
  marketplace source contained no compatibility skill tree.
- **`pre-commit run --all-files` passed**, including the required hardcoded
  secret scan.
- **The application, MCP, frontend, and plugin metadata align at the Core
  boundary.** Application packages and the dashboard are `0.3.0`; the plugin is
  `0.7.0`; OpenAPI is `0.3.0`; and the migration head is 0016. Historical
  receipt-bearing Phase 1–8 response shapes remain separate from the new detail,
  context, search, and merge projections.

A writable Core stack check, frozen performance fixture, populated audit,
pre/post-0016 backup restore rehearsal, and product/operator permanence signoff
are not recorded here. No production cutover, production merge, recovery-point
proof, or Advisory shipment is claimed. Those remain operational gates; the
historical sections below are retained as evidence for their named revisions
and do not fill these Core gaps.

## Complexity ceiling and type-check gate widening — 2026-09-02

This record covers removing the `C901` per-file exceptions from
`backend/pyproject.toml` and widening `ty` from `src/mnemonic_api/application`
to the whole backend `src` tree. Only observed results are stated.

- **The full backend suite passed 426 tests with seven warnings** against the
  disposable PostgreSQL 17 container, both as a baseline before the change and
  again after it. That includes the committed OpenAPI freshness test:
  `docs/openapi.json` is byte-identical to the regenerated document, so the
  REST contract did not change. The 93 client-operation unit tests, including
  the frozen canonical/digest and response-v1 vectors, and the validation
  suite also passed in isolation.
- **Backend Ruff passed with the `C901` ceiling of 10 and no
  `per-file-ignores`.** `enforce_event_contract` fell from 47 to 2 by
  delegating to actor, origin, body, reference, relationship-projection, and
  metadata-family checks, the largest of which scores 7;
  `_response_matches_operation` from 20 to 3 through one coherence function per
  operation kind in a table checked against the registry at import;
  `reject_client_operation_secret_echo` from 18 to 6 by sharing one echo walker
  with the response check; `reserve_client_operation` from 16 to 4 by
  separating the receipt round trip, the same-request check, and the
  completed-receipt replay; and the readiness matrix test from 11 to 7. The
  highest remaining score across `src` and `tests` is 10.
- **`ty 0.0.77` reported zero diagnostics for `uv run ty check src`,** down
  from 34. The fixes are annotations and typing-only call forms: `NoReturn` on
  the raising helpers, `Result.tuples()` ahead of `dict()`, `scalar_one()` for
  `clock_timestamp()`, a `rows_affected` helper for DML row counts, a declared
  `dict[str, JsonValue]` for released-lease metadata, and `WorkItem.status`
  typed with the same five-value `Literal` its check constraint guards. The
  schema parity test confirms the ORM metadata did not change.
- The MCP and dashboard suites were not rerun. Neither imports the backend
  package, and their shared input, the OpenAPI snapshot, did not change.

## Application package decomposition — 2026-09-02

This record covers the split of `backend/src/mnemonic_api/application.py` into
the `mnemonic_api.application` package. Only observed results are stated.

- **The full backend suite passed 426 tests with seven warnings** against the
  disposable PostgreSQL container. That includes the committed OpenAPI
  freshness test: `docs/openapi.json` is byte-identical to the regenerated
  document, so the REST contract did not change.
- **Backend Ruff passed with the new `C901` complexity ceiling of 10.** The
  highest-scoring function in the package scores 5. The pre-existing exceptions
  named in `backend/pyproject.toml` (`schemas.py`,
  `services/client_operations.py`, and `tests/`) were left as they were.
- **`ty 0.0.77` reported zero diagnostics** for `src/mnemonic_api/application`
  and `src/mnemonic_api/database.py`. An unscoped run over `src` still reports
  the pre-existing diagnostics in the service modules; those are not gated.
- The MCP and dashboard suites were not rerun. Neither imports the backend
  package, and their shared input, the OpenAPI snapshot, did not change.

## Plugin 0.6.1 and post-review MCP/docs validation — 2026-09-02

This current record covers the integrated post-review corrective checks reported
by each owning agent. The statements below name only results actually observed;
unrun release drills remain explicit.

- **The full backend suite passed 426 tests with seven warnings, and backend
  Ruff passed.** Alembic reported `0015_gate_review_fixes (head)`. The suite
  includes
  `backend/tests/test_schema_parity_postgres.py::test_migrated_schema_matches_orm_metadata`;
  the committed OpenAPI freshness test also passed.
- **The full MCP suite passed 208 tests in its frozen environment.** It includes
  the committed-OpenAPI property/required-set comparison, nested
  `requested_context_revision`, backend-owned drift-field typing, protected gate
  request coherence, reachable attention/history scope injections, ready-page
  waiting refusal, value-free query/cursor logging, and plugin inventory at
  manifest `0.6.1`. A separate current-schema audit covered all 54 strict MCP
  response models and found zero property/required-set deltas.
- **The frontend passed 123 unit tests, TypeScript checking, the production
  build, and all 44 isolated Playwright executions across desktop and narrow
  Chromium in 1.6 minutes.** The unit suite includes the committed OpenAPI
  guard. Corrective regressions directly cover debounced request-driving
  filters, attention empty/error recovery, Show every question, nonzero
  unresolved/resolved omission messages, attention and 53-gate history
  pagination, current-cursor live refetch, and sibling draft/focus preservation.
- **Repository Ruff passed** for `mcp/src/mnemonic_mcp`, `mcp/tests`, and
  `scripts/check-stack.py`; the stack checker also compiled. All three skills
  passed the skill-creator quick validator.
- **Four copyable JSON bodies passed their live backend request models:** the
  canonical work body, dual parent/discovery work body, human-gate request, and
  reviewed-revision resolution. Relative Markdown links across the changed
  root/docs/plugin files resolved, and `git diff --check` passed.
- A disposable fresh/sequential Claude plugin cache install was not performed in
  this corrective run. Manifest `0.6.1` is cache-visible, but the fresh `0.6.1`
  and `0.6.0 -> 0.6.1` installation drill remains a release gate.
- No post-correction 12,000-item hierarchy measurement was recorded. The 0014
  JIT-heavy measurements and budgets retained below are historical baselines,
  not evidence for the current JIT-disabled, page-first query.

## Historical Phases 7–8 integrated baseline — 2026-09-01

This section preserves observations from the pre-remediation revision 0014
implementation. It is not validation of migration 0015 or the corrected
hierarchy query shape. Fence, downgrade, schema, and performance claims in this
historical section are superseded by the current corrective record above.

- **The full backend suite passed 396 tests against PostgreSQL 17 with three
  warnings, and backend Ruff passed.** The suite includes the `0013 -> 0014`
  migration, gate persistence/service/receipt behavior, readiness and lifecycle
  enforcement, attention/history/context reads, event coherence, and hierarchy
  presentation. The focused Phase 6/7–8 migration batch passed 11 tests. That
  revision did not contain an ORM/migration parity test; the corrective record
  above names the regression that now enforces it.
- **The full MCP suite passed 206 tests, and repository Ruff passed for MCP.**
  That revision's contract had exactly 25 tools and exactly ten protected writes;
  `request_human_input`, `list_human_attention`, and `list_work_gates` were
  present, while no MCP resolution tool exists.
- **The frontend passed 107 unit tests, TypeScript checking, and the production
  build.** Those pre-remediation unit tests exercised the ten browser mutation
  intents, gate decoders/revision helpers, proxy policy, and hierarchy query and
  guard helpers; they did not render the attention/detail components. Current
  interactive evidence is recorded above.
- **Plugin manifest and disposable installation checks passed.** Both a fresh
  `0.5.0` install and sequential `0.4.0 -> 0.5.0` update resolved the shipped
  skill/reference bytes without a compatibility copy.
- **The complete isolated Playwright stack passed 40 executions across desktop
  and narrow Chromium.** This includes committed-response-loss/exact replay,
  a B-to-C work/checkpoint/relationship drift rejection while the outer queue
  projection remains stale at B, branch-local all-descendant filtering, and
  collapsed passive-expiry refresh. The disposable E2E API enabled human-gate
  requests explicitly. That historical request fence was removed by the
  post-review corrective release.
- **The isolated production-shaped five-service stack passed its read-only and
  authorized writable checks.** The checker exercised one lost gate-request
  response, waiting/readiness/claim exclusion, exact dashboard resolution
  replay and one activity advance, 25 later ordinary events without paired
  decision eviction, exact planned/discovered hierarchy aggregates, project
  isolation, and cleanup. Post-cleanup PostgreSQL state was revision 0014,
  five of five synthetic work items hidden, one of one gate resolved, exactly
  two gate events and two completed gate receipts, no pending receipts, and no
  visible unresolved gate. A 143-entry custom archive contained gates, their
  attention sequence, events, and receipts. Across 54 dynamic gate/operation
  identifiers and 41,339 log characters, the API, MCP, web, backup, and
  PostgreSQL logs contained no identifier, gate text, answer, bearer, operation
  field, claim-request field, or lease-token field from the audit set.

### Historical pre-remediation release evidence — 2026-09-01

The following checks used synthetic data in the isolated PostgreSQL 17 test
service. Every temporary database and schema was dropped after the measurement;
no application or production data was read. These are historical observations
for revision 0014, not current release gates, production SLOs, or enforced
input/graph-size limits.

#### Locked downgrade and writer race

- The focused command
  `TEST_DATABASE_URL=<isolated PostgreSQL URL> uv run pytest -q
  tests/test_phase78_migration_postgres.py` passed **11 tests in 1.78 seconds**
  with one upstream Starlette warning. Backend Ruff also passed. Two new
  deterministic tests exercise the migration's actual PostgreSQL locks rather
  than timing assumptions.
- A new canonical Phase 6 replay regression builds a typed append-event
  response and its real salted request fingerprint at revision
  `0013_idempotent_mutations`, completes the receipt through the same
  pending-to-completed database contract, and establishes the byte baseline
  through the actual REST event route. The response remained byte-identical
  after `0013 -> 0014` and again after downgrade/re-upgrade; both passes left
  exactly two work events, one receipt, no gates, and the work activity
  timestamp unchanged.

- In the writer-first order, a keyed request transaction reserved and completed
  its receipt, locked the focal work, and inserted the gate and request event.
  Downgrade was observed waiting for `ACCESS EXCLUSIVE` on
  `client_operations`; after the writer committed, downgrade refused, left the
  database at `0014_human_gates`, and retained both the gate and completed
  receipt.
- In the downgrade-first order, the migration was paused after its empty-data
  check while holding all four required `ACCESS EXCLUSIVE` locks. An unkeyed
  gate writer was observed waiting for `ROW SHARE` on `work_items`; downgrade
  completed to `0013_idempotent_mutations`, the writer then failed with SQLSTATE
  `42P01` instead of committing into a dropped schema, and re-upgrade restored
  `0014_human_gates`. The pre-existing work remained and the gate table was
  empty. Neither order deadlocked or lost a committed gate/receipt.

#### Custom backup, isolated restore, and exact replay

- A disposable source database was migrated from empty state to
  `0014_human_gates`. Through the real REST service it created one work item and
  **100 keyed gates**, resolved 99, and retained one unresolved gate. The
  fixture therefore contained 199 gate events and 199 completed gate-operation
  receipts, with no pending receipt. One resolved gate used the maximum
  4,000-character question and resolution plus maximum-length provenance.
- A PostgreSQL 17 custom archive was taken and its catalog explicitly checked
  for `work_gates`, the attention identity-sequence state, `work_events`, and
  `client_operations`. Restoring it into a separate empty database preserved
  the full source digest, revision, 100/99/1 gate counts, maximum attention
  sequence and sequence value of 100, all seven gate-table indexes, and all six
  cross-table guard triggers.
- With new gate creation disabled in the restored application, exact receipt
  replays of a resolved gate request, its resolution, and the still-unresolved
  request returned byte-identical response bodies. The complete redacted
  durable digest and counts were unchanged. Ready-work total remained zero,
  text-free attention total remained one, and a fresh claim returned
  `409 work_gated`.
- The archive grew from **136,195 bytes to 199,844 bytes** for this fixture,
  an observed increase of 63,649 bytes. The populated archive took 143.949 ms
  to write and 184.033 ms to restore. Allocated relation growth was 163,840
  bytes for gates, 221,184 for events, and 458,752 for receipts. The maximum-
  text gate plus its two events and two receipts occupied 34,154 row bytes:
  9,002 gate, 9,264 events, and 15,888 receipts.
- In-process API plus local PostgreSQL request latency across 100 gates was
  p50 **16.824 ms**, p95 **25.997 ms**, and p99 **37.633 ms**. Resolution
  latency across 99 gates was p50 **17.487 ms**, p95 **20.318 ms**, and p99
  **28.449 ms**. These figures include validation, persistence, response-model
  rendering, and local transport; they do not establish a network deployment
  latency budget.

#### Representative hierarchy plans

A random schema migrated to `0014_human_gates` held 12,000 work items and
12,000 checkpoints: 120 roots, 11,880 parent-child edges, maximum depth 50,
50-child broad branches, 321 discovery edges, 223 blockers, 282 active and 283
expired leases, and 393 gates split 197 unresolved/196 resolved. A deep-only
tag forced qualification through a deep descendant. Each production service
case received one warm-up and seven timed calls; its exact captured SQL then ran
under PostgreSQL 17.10 with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` and the
production five-second statement timeout.

| Hierarchy case | Returned / total | Service p50 / p95 | Plan / execution | Shared hits | Recursive rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Root first page | 20 / 120 | 1062.382 / 1072.874 ms | 2.043 / 1064.345 ms | 72,267 | 12,000 |
| Root later page, offset 100 | 20 / 120 | 1073.723 / 1100.170 ms | 1.759 / 1071.097 ms | 72,267 | 12,000 |
| Root deep-tag filter | 1 / 1 | 1143.205 / 1162.721 ms | 1.889 / 1111.282 ms | 71,669 | 12,000 |
| Broad child first page | 20 / 50 | 86.911 / 88.450 ms | 3.542 / 90.604 ms | 3,482 | 99 |
| Broad child later page, offset 40 | 10 / 50 | 84.275 / 88.174 ms | 1.703 / 84.037 ms | 3,323 | 99 |
| Deep child tag filter | 1 / 1 | 19.843 / 25.311 ms | 1.843 / 15.995 ms | 3,134 | 99 |

All hierarchy plans had zero shared reads/writes and zero temporary blocks;
sorts stayed in 25–55 KB. They used the unresolved-gate, checkpoint, lease,
work-item, and relationship indexes, with expected sequential scans for
full-project root aggregation. Recursive traversal itself took about
15.6 ms. PostgreSQL JIT consumed roughly 994–1,040 ms of root execution and
67–73 ms of broad-child execution, while the selective deep-child plan did not
trigger JIT. This identifies JIT configuration/query cost, not a missing index,
as the first measured optimization target. No case timed out, spilled, or
returned duplicate rows.

#### High-degree focused human review

A second random schema migrated to head created 501 work items, 500 current
`related` edges and their canonical events, and one unresolved human gate. An
ordinary context response reported all 500 in `relationship_counts` but returned
its normal 50-edge slice: 58,704 response bytes with p50/p95 16.280/17.155 ms.
The valid focused-gate review returned **all 500 edges** in one statement:
485,206 bytes with p50/p95 51.224/51.994 ms. The exact focused SQL received one
EXPLAIN warm-up and three measured `ANALYZE, BUFFERS` runs; median planning was
2.512 ms, median execution 37.820 ms, and the final plan used 16,138 shared
buffer hits with zero reads, writes, dirtied blocks, temporary blocks, or JIT.
It stayed within the five-second statement timeout.

The focused review deliberately has no enforced edge-count maximum, because it
must return every relationship fact bound to the unresolved gate review. The
500-edge fixture is therefore an observed capacity point, not a cap or proof
for arbitrarily high degree; response size and latency grow with focal degree.

#### Ready-work, fresh-claim, and attention density

A third random schema on PostgreSQL 17.10 held 5,000 Pending work items,
1,000 unresolved gates (20 percent density), 500 active leases, 500 retained
expired leases, and 100 work items having both an active lease and a gate.
The expected ready union was therefore 3,600 items. The fixture was vacuum-
analyzed before measurement and every connection used the production
five-second statement timeout. In-process REST timings below used one warm-up
and seven measured calls per page. Exact SQL captured from each endpoint then
received one EXPLAIN warm-up and three measured
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` bundles.

| Read case | Items / total | Service p50 / p95 | Plan / execution | Shared hits | Response bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ready first page | 50 / 3,600 | 33.289 / 34.545 ms | 0.549 / 28.277 ms | 34,035 | 11,296 |
| Ready later page, offset 3,500 | 50 / 3,600 | 33.021 / 33.907 ms | 0.515 / 28.014 ms | 34,036 | 11,299 |
| Attention first page | 50 / 1,000 | 28.063 / 36.747 ms | 1.875 / 5.448 ms | 2,442 | 118,329 |
| Attention after sequence 500 | 50 / 1,000 | 28.759 / 37.800 ms | 1.785 / 5.010 ms | 2,420 | 109,018 |

The ready bundle contained two statements and used 15.408/15.146 ms of JIT in
the first/later final measured runs. The attention bundle contained eleven
statements and used no JIT. All four final bundles had zero shared reads,
writes, or dirtied blocks and zero temporary reads or writes. The later
attention cursor was obtained by traversing ten 50-item pages, rather than by
inventing a sequence token.

Fresh-claim behavior was then sampled on 100 distinct gated targets and 100
distinct otherwise eligible targets. Every gated request returned
`409 work_gated`; every eligible request returned 200 and acquired its lease.
Gated service latency was p50 **10.550 ms** and p95 **15.871 ms**; eligible
latency was p50 **11.839 ms** and p95 **16.507 ms**. Each captured request had
four read statements. After one warm-up and across three measured EXPLAIN
bundles, gated median planning/execution was 0.587/0.212 ms and eligible was
0.476/0.184 ms. Both final bundles used 20 shared hits with zero shared reads,
writes, dirtied or temporary blocks and no JIT. To avoid duplicating durable
effects, EXPLAIN replay covered only the captured `SELECT`/`WITH` statements;
the successful lease/event DML is evidenced by the 100 real service calls, not
re-executed under EXPLAIN.

These are small local samples and observed capacity points. In particular,
seven-call p95 values are descriptive only; they are not production network
SLOs.

#### Passive active-descendant lease expiry

The same production-shaped schema added one 500-child branch with 100 active
descendant leases. One lease expired three seconds after the initial read and
the other 99 expired fifteen minutes later. The first hierarchy response took
109.070 ms, reported 100 active descendants, and returned the earliest boundary.
A read started 100.077 ms after that boundary; seven post-boundary reads were
p50 **108.456 ms** and p95 **113.398 ms**, reported 99 active descendants, and
advanced the returned boundary to the later expiry. Time passage performed zero
database writes and no server background polling; the corrected count appeared
on the next read.

The authoritative browser scheduler command
`node --test tests/lease-refresh.test.mjs` passed **5/5 tests in 71.435 ms**.
Under fake time it selects the earliest valid displayed expiry, fires exactly
at that boundary, retries an unchanged already-due boundary every 65 seconds,
and stops after cancellation. That bounds a persistently stale mounted
scheduler to about 0.923 retry callbacks per minute after its first boundary
callback. The dashboard refreshes its list, attention total, and open context;
each expanded hierarchy branch schedules from its returned earliest child
boundary. Rate therefore scales with mounted views/expanded branches and has
no server-global enforced maximum. The full 40-execution Playwright result
above includes the active-lease browser expiry path. These observations are
behavior and rate evidence, not a browser/network latency SLO.

#### Prerelease capacity acceptance budgets

The Phase 3 hierarchy baseline used 2,000 work items and reported 12.28 ms root
pagination and 1.48 ms child expansion before full-branch presentation facts
existed. The Phase 7-8 fixture deliberately increased the project sixfold,
required every returned row and aggregate to share one statement snapshot, and
added gates, discovery, blockers, lease overlap, depth 50, and deep-only
qualification. Using that historical baseline and the production-shaped measurements
above,
the following local budgets were accepted for the old revision 0014 query
shape. The corrected implementation disables JIT and pages before enriching
member facts. No post-correction 12,000-item measurement is recorded here, so
this table is not a current release gate or a promise for arbitrary hardware,
graph size, focal degree, or network latency.

| Capacity case | Local prerelease budget | Observed worst named value | Result |
| --- | --- | ---: | --- |
| Gate request/resolution, 100-gate fixture | p95 <= 50 ms and p99 <= 100 ms | 25.997 / 37.633 ms | pass |
| Maximum-text gate plus two events/receipts | <= 64 KiB row bytes | 34,154 bytes | pass |
| Ready and attention first/later pages, 5,000 work / 20% gate density | p95 <= 100 ms, no temporary spill | 37.800 ms, zero temp blocks | pass |
| Fresh gated/eligible claims | p95 <= 50 ms with exact 409/200 outcomes | 16.507 ms | pass |
| 12,000-work hierarchy root pages/deep filter | p95 <= 1.5 s, one statement, no spill, under timeout | 1.163 s, zero temp blocks | pass |
| Broad/deep hierarchy child pages | p95 <= 150 ms, no spill | 88.450 / 25.311 ms | pass |
| Focused 500-edge human review | service p95 <= 100 ms, SQL execution <= 75 ms, no spill | 51.994 / 37.820 ms | pass |
| Passive 500-child expiry correction | first post-boundary read <= 250 ms and advances exact count/boundary | 113.398 ms | pass |
| 100-gate archive growth and restore | archive growth <= 128 KiB; dump and restore each <= 1 s | 63,649 bytes; 143.949 / 184.033 ms | pass |

The historical root result exposed JIT as its dominant cost: the measured root
statements spent roughly one second compiling, while recursive traversal was
about 15.6 ms. The current JIT-disabled, page-first query requires fresh
representative measurements before a release budget can be claimed.

#### Extracted Phase 6 process against revision 0014

No historical Phase 6 container image was available or run. A safe disposable
source/process drill instead archived immutable commit
`7f2a3215853873d19cdffe5c7b096bce4e4403d0`, verified that its backend source
had no `work_gates` or human-gate references, and launched that extracted
source in a separate operating-system Python process using the locked backend
environment. Its TestClient targeted a random PostgreSQL 17.10 schema already
at `0014_human_gates` with a five-second statement timeout.

The gate-aware service created two gated Pending targets: one without a lease
and one with a retained expired lease. Its ready total was zero. The old process
stale-listed both as ready, proving the documented read hazard, but its fresh
claim, expired-lease replacement claim, completion, and deletion attempts each
returned `503 database_unavailable`. After all attempts, the schema remained
at `0014_human_gates` with two gates, two gate events, zero lease rows for the
fresh target, the one original expired lease still retained, both work items
Pending at version 1 and undeleted, and their two original checkpoints. Thus
the database backstops failed closed without a partial domain change.

This was a real separate old-source application process, but not a historical
container-image/package/startup drill. Coordinated image inventory, routing
drain, and zero old database connections remain mandatory at deployment; this
source-level evidence does not replace them.

The final root validation also passed backend and MCP Ruff, frontend typecheck
and production build, production Compose rendering, checker compile/lint,
repository whitespace checks, and relative Markdown-link validation. A fresh
adversarial review of the implementation and the added gate-idempotency,
readiness/lifecycle, hierarchy, writable-stack, and browser evidence found no
remaining blocker or high-severity issue. The ready/claim, attention,
hierarchy, focused-context, passive-expiry, backup/restore, and old-process
observations above cover the accepted plan's prerelease capacity and
compatibility gates without creating production SLOs. Historical Phase 6
counts below remain accurate for that release and are not rewritten as current
Phases 7-8 totals.

## Sidebar artwork and edge SVG serving — 2026-09-01

Checks observed while replacing the sidebar's drawn page stack with the robot
SVG and moving static SVG delivery to the host nginx.

- **73 frontend unit tests, TypeScript checking, and the production build
  passed.** The isolated Node 24 Playwright stack passed all 26 desktop and
  narrow-viewport executions, including a new check that the sidebar image
  resolves to a decoded asset rather than a broken `<img>`.
- **The installed configuration passed `nginx -t` on nginx 1.24.0 and was
  reloaded.** Against the live host: `/img/robot.svg` returned 200 from disk
  with `cache-control: max-age=604800` and gzip encoding, carrying HSTS and all
  six response headers `next.config.ts` sets. `/icon.svg`, which Next.js
  generates outside `public/`, still returned 200 from the dashboard through
  the fallback, and an absent `.svg` returned the dashboard's 404.
- **The routing guards were unchanged by the new location.** `/` returned 200,
  `/mcp` without a bearer token returned 401, `/mcp/foo.svg` returned 404 from
  the `^~` prefix rather than the SVG regex, a traversal sequence resolved
  outside the root and returned 404, and port 80 still returned 308.

These checks also found a pre-existing configuration fault unrelated to the
change: `.env` had never set `MNEMONIC_TLS_HOST`, so `compose.tls.yaml` supplied
its `mnemonic.example.com` placeholder to all three allowlists and the dashboard
answered 403 to its own `/api/mnemonic/*` requests over the public hostname. The
page itself loaded, which is why routing checks passed. Setting the real
hostname and recreating the stack returned 200 with the projects listed, and an
untrusted origin still returned 403.

Not checked: any client outside the address allowlist. No stored prompts were
read or modified.

## Phase 6 final integrated validation — 2026-09-01

Validated against the final integrated source after the semantic rebase. The
Alembic order is `0011_project_settings` -> `0012_pending_deferred_statuses` ->
`0013_idempotent_mutations`; the protected surface is ten REST operations, nine
MCP tools, and nine browser intents.

- **The full backend suite passed 314 tests against PostgreSQL 17, and the full
  backend Ruff check was clean.** The run produced three known warnings.
  Focused migration validation passed four tests, and the deterministic
  receipt-race, deferral, and readiness batches also passed.
- **The full MCP suite passed 186 tests in its separate environment.** The
  protected surface remains exactly nine MCP mutation tools within the exact
  22-tool catalog.
- **The frontend passed 96 unit tests, TypeScript checking, and the production
  build.** The complete isolated Playwright stack then passed 36/36 executions
  in 1.1 minutes across desktop and narrow Chromium. Its disposable stack was
  cleaned after the run.
- **Both plugin manifests validated, and real disposable installation drills
  passed.** A fresh `0.4.0` install and a sequential `0.3.0 -> 0.4.0` upgrade
  both completed successfully.
- **A disposable five-service production stack became healthy.** The read-only
  checker passed health, authentication, dashboard proxy/origin policy, the
  exact 22-tool catalog, all nine protected MCP schemas and annotations, and
  REST-backed project listing.
- **The authorized checker passed its complete canonical lifecycle.** It
  exercised create, search, recall, checkpoints, events, the resource and
  prompt, dashboard editing, stale-version rejection, claim replay, renewal and
  release, pointer/capability isolation, ready-work behavior, event history,
  graph behavior, and cleanup. A post-check ledger query found 25 completed
  receipts across all nine MCP-covered operation kinds, with zero pending rows
  and no completed row missing a response status.
- **A dedicated custom-archive replacement drill preserved idempotent deferral
  replay.** After known create and defer operations, writers were stopped and a
  custom archive was restored through whole-`public`-schema replacement. The
  restored database retained revision `0013_idempotent_mutations`, removed a
  post-backup sentinel, retained the completed defer receipt, and returned the
  same HTTP status and a byte-identical body for an exact same-key deferral
  replay without a second transition.
- **Application-service log review found no runtime failure or operation-key/ID
  leak.** The aggregate PostgreSQL log's only failure was one operator-caused
  diagnostic query against the nonexistent `mutation_receipts` name; it was not
  an application runtime query or failure.

The post-implementation cold adversarial code review found and drove fixes for
two high-severity recovery gaps. Request-only MCP metadata validation had been
reused for historical progress-event reads, so nested legacy
`Client_Operation_ID` metadata could no longer be recalled; request and
historical validators are now separate, with a regression through both event
listing and recall. The browser mutation registry also lacked a client-side
deadline spanning both `fetch` and response-body decoding, so a stalled request
could remain permanently in flight. Every attempt now has a 20-second deadline,
five seconds above the ordinary proxy timeout, with one abort signal kept active
through strict decoding; hung-fetch and hung-body tests prove transition to
unresolved followed by exact UUID/method/path/body retry. Read-only remediation
reviews found no remaining blocker or high-severity issue.

## Phase 6 pre-integration validation checkpoint — 2026-09-01

This checkpoint records genuinely observed results from the Phase 6 branch
before it was semantically rebased onto `0012_pending_deferred_statuses` and
before `defer_work` became the tenth REST and ninth browser operation. Its old
migration name, operation counts, and test counts are retained as historical
performance, contention, restore, and integration evidence only; they are not
the final integrated release result above.

Validated against the then-current pre-integration Phase 6 source in the
isolated Linux worktree with
Python 3.13.15, PostgreSQL 17, the separate locked backend/MCP environments,
and Node 24.20.0 for the frontend unit and type gates. This record contains
only checks and measurements observed during this implementation session.

- **208 backend tests passed against the disposable PostgreSQL service with no
  skips, and the full backend Ruff check passed.** The three warnings were the
  existing upstream Starlette TestClient deprecation and SQLAlchemy reflection
  warnings for the PostgreSQL `NOT VALID` constraint options. The suite covers
  all nine operation kinds, frozen canonical vectors, exact replay,
  cross-project/key conflicts, natural no-ops, rollback, response validation
  and secret rejection, outcome-aware live invalidation, pool saturation, and
  migration/model parity.
- **133 MCP tests passed in its separate environment, and repository Ruff
  passed for MCP plus `scripts/check-stack.py`.** The exact catalog remains 22
  tools. Exactly nine mutation tools require a UUID and advertise
  `idempotentHint=true`; protected transport/malformed-response failures make
  one outbound attempt and retain exact-retry guidance, while excluded writes
  retain their separate contracts.
- **88 frontend unit tests and TypeScript checking passed under Node 24.20.0.**
  The then-current production build also passed in the Node 24 image. The tests
  cover all eight browser mutation intents, exact serialized-body reuse, strict
  response decoding, conflict-key blocking, retained safety conflicts, proxy
  route/body/secret policy, and the no-persistence boundary.
- **The complete isolated Playwright stack passed 28/28 executions in 57.7
  seconds: 14 desktop and 14 narrow Chromium.** The Phase 6 scenarios commit
  before a synthetic lost or malformed response, retry the exact method, path,
  body, and UUID, and prove one durable create/event/relationship/delete
  effect. They also cover natural true-versus-fresh-key-false results, blocked
  ambiguous UI, modal-accessible recovery, healing invalidation, newer-state
  reconciliation, deletion disappearance, and absence of retry material from
  browser storage and rendered content.
- **Migration validation passed for populated `0011_project_settings` to
  `0012_idempotent_mutations`, fresh head creation, empty-ledger downgrade and
  re-upgrade, and completed-ledger downgrade refusal.** Historical nested and
  case-varied `client_operation_id` metadata remained byte-semantically
  unchanged under the separate `NOT VALID` Phase 6 check, and the Phase 5
  metadata function remained unchanged. Two deterministic, no-sleep
  two-connection tests observed the actual PostgreSQL locks: a writer-first
  downgrade waited then refused without losing its completed receipt, while a
  downgrade-first path held `ACCESS EXCLUSIVE` after the empty check through
  drop and forced the blocked writer to fail with SQLSTATE `42P01`.
- **The receipt contention and pool-recovery drill passed four focused
  PostgreSQL tests in 2.79 seconds.** Same-key owner commit produced one replay,
  owner rollback transferred ownership to the waiter, a one-second bounded
  timeout never fallback-executed, and two bounded waiters against a
  pool-size-three/no-overflow engine released all capacity for an unrelated
  query and exact retry.
- **A 1,721-receipt durability/performance fixture completed across four
  representative response shapes.** It held 420 append-event receipts, 1,200
  absent relationship-removal no-ops, 100 larger create-work snapshots, and
  one update-work recovery receipt. Another 400 exact replays of the last
  append key added no receipt. Full in-process API plus local PostgreSQL fresh
  append latency was p50 11.947 ms, p95 21.153 ms, and p99 27.159 ms; replay
  was p50 7.800 ms, p95 11.505 ms, and p99 15.858 ms. Eight workers completed
  1,200 different-key durable no-ops over 64 project-lock partitions in 9.658
  seconds, or 124.3 requests/second.
- **The unique receipt lookup used `uq_client_operations_scope` as a one-row
  index scan.** The observed plan took 0.054 ms to plan and 0.033 ms to execute
  with three shared-buffer hits and no reads. After `VACUUM (ANALYZE)`, 1,721
  rows used 1,515,520 bytes of heap, 1,556,480 bytes of table/TOAST storage,
  204,800 bytes of indexes, and 1,761,280 bytes total: approximately 1,023.4
  physical bytes per receipt. Serialized response snapshots averaged 498.5
  bytes and had p95/max 1,380 bytes.
- **A real custom-format dump and isolated restore preserved retry knowledge.**
  The 413,715-byte archive took 0.172 seconds to dump and 0.307 seconds to
  restore. The restored revision was exactly `0012_idempotent_mutations`;
  project/work/checkpoint/event/relationship/lease/receipt aggregates and the
  dedicated target version matched the source. A real post-restore PATCH with
  the retained UUID and exact body returned the original typed JSON while the
  entire before/after aggregate tuple remained unchanged.
- **A PostgreSQL 17 old-archive-over-new-target replacement drill passed.** A
  real populated `0011_project_settings` custom archive was restored over a
  migrated `0012_idempotent_mutations` target containing a completed private
  receipt. Immediately after restore, `alembic_version` was exactly
  `0011_project_settings`, `to_regclass('public.client_operations')` was null,
  and the historical nested value
  `{"outer":[{"Client_Operation_ID":"historically-legal"}]}` remained
  semantically exact. Migrating that restored database to Phase 6 recreated an
  empty receipt ledger, preserved the legacy value, and installed the reserved
  metadata constraint as deliberately `NOT VALID`. This specifically proves an
  older archive cannot leave future schema objects or receipt data behind.
- **Plugin manifest, inventory, and installation validation passed.** Both JSON
  manifests parsed strictly; the inner version is `0.4.0`; the package
  contains exactly three skills and two shared references. A fresh isolated
  `0.4.0` installation and a sequential `0.3.0 -> 0.4.0` update both
  installed the expected bytes and valid shared links without compatibility
  copies.
- **The pre-integration disposable production-image stack passed.** All five services
  became healthy with `0012_idempotent_mutations` matching the image, running
  API, and database. The read-only checker passed both sections; the authorized
  checker passed all three sections, the 22/22 catalog and 9/9 protected schema
  gates, and a five-item MCP-to-REST-to-PostgreSQL/dashboard-proxy lifecycle.
  Its retained state represented all nine operation kinds: 31/31 receipts were
  completed, zero were pending, all seven work rows were soft-deleted, and no
  relationship remained. All five recognizable misplaced operation-ID headers
  were rejected value-free with no durable state. A bodyless dashboard-proxy
  relationship DELETE was rejected while the edge, both endpoint timelines,
  and receipt count remained unchanged.
- **That pre-integration stack log audit inspected 379 aggregate lines with zero
  tracebacks, severe runtime entries, credential-value hits, operation-ID hits,
  or known body-content hits.** The first smoke cycles exposed only stale
  checker expectations: a keyed stale edit needed its required actor, and keyed
  secret echoes now correctly return `client_operation_secret_echo` before the
  Phase 5 event-only guard. Both checker fixtures were corrected and the full
  writable lifecycle reran successfully.
- **The pre-integration static gates passed.** Both manifests and every repository-local
  Markdown target parse or exist, the checker CLI imports, and
  `git diff --check` is clean. Every disposable benchmark database/schema,
  dump, browser stack, production stack, volume, network, temporary credential
  file, Playwright artifact, and checker artifact was removed. The existing
  `mnemonic` and `mnemonic-test` stacks were not mutated by disposable
  validation.

The performance figures are one warm local tmpfs run, not an SLO or production
capacity claim. They use in-process TestClient rather than network/TLS/proxy,
moderate payloads and four of nine response shapes rather than maximum-size
responses, and durable relationship no-ops rather than applied writes for the
parallel throughput sample. Index bytes are relation-wide, the dump size
includes the entire fixture database, and the exercise did not benchmark a
production-sized migration lock or sustained ten-second contention.

## Phase 4 ready-work and Phase 5 event validation — 2026-09-01

Validated in the local Linux workspace with the locked environments, isolated
PostgreSQL 17 data, and Node 24. This record includes only checks and
measurements observed during this implementation session.

- **140 backend tests passed against disposable PostgreSQL 17, and the full
  Ruff check passed.** The populated migration exercise through
  `0009_ready_work_indexes` and `0010_work_events` passed upgrade, exact
  backfill, ORM model parity, and downgrade checks.
- **122 MCP tests passed.** The package-local environment does not include Ruff;
  MCP and checker lint were covered by the full repository Ruff run above.
- **51 frontend unit tests, TypeScript checking, and the production build
  passed.** The isolated Node 24 Playwright stack passed all 16 desktop and
  narrow-viewport executions.
- **A cold adversarial review found no remaining P0/P1/P2 flaws after fixes.**
  It drove regression fixes for deferred release-marker bypasses, exact retained
  holder values, Unicode tag normalization, strict event references and
  endpoint binding, ready-page lifecycle semantics, dashboard refresh/paging
  recovery, and attacker-controlled validation-location reflection. Every
  affected backend, MCP, frontend, Playwright, restore, and full-stack gate was
  rerun against the final source.
- **Plugin validation and installation drills passed.** The marketplace and
  plugin manifests validated; real disposable installs succeeded for sequential
  `0.1.0 -> 0.2.0 -> 0.3.0` upgrades and fresh `0.2.0` and `0.3.0` installs.
- **The populated `0009 -> 0010` migration completed in 7.35 seconds.** It
  backfilled exactly 52,000 immutable events over 10,000 work items: 10,000
  `work_created`, 20,000 `checkpoint_added`, 4,000 `dependency_added`, 16,000
  `relationship_added`, and 2,000 `work_claimed`. Initial event storage was
  26 MB. Inserting a further 100,000 progress events took 7.16 seconds, leaving
  152,000 table rows and a busiest per-work history of 100,305 events in that
  multi-work migration fixture.
- **Every required ready-work plan passed on the final canonical query.** The
  exact corpus held 10 projects, 10,000 open work items, 30,000 checkpoints,
  10,000 relationships including 2,000 blockers and 1,000 direct-parent edges,
  and 2,000 leases split evenly between active and expired. The default query
  returned the requested project's total 750 plus 30 rows in 3.182 ms with
  4,691 shared-buffer hits. A selective mixed-case tag exercised PostgreSQL
  normalization on both operands and returned total/page 7 in 2.820 ms with
  556 hits using `ix_checkpoints_normalized_tags_gin`. The direct-parent filter
  returned total 100 plus 30 rows in 3.023 ms with 2,691 hits using
  `uq_work_relationships_one_parent`. Offset 500 returned total 750 plus 30 rows
  in 5.921 ms with 4,691 hits. Each query was warmed once; all four reported
  zero shared reads/writes/dirtied blocks and zero temporary I/O. They used
  `ix_work_items_ready_order`, bounded blocker endpoint/source indexes, lease
  primary-key probes, and page-only checkpoint-count probes, with no sequential
  scan, full lease/graph scan, external sort, or spill.
- **Event paging remained bounded in a separate intentionally one-hot fixture.**
  All 152,000 table rows belonged to the queried work item. The list route
  returned the exact per-work total plus a 30-row page in 23.084 ms; bounded
  context returned the same total plus 10 timeline rows in 15.760 ms. Both
  bounded page selections used `ix_work_events_timeline` with no temporary
  spill; their separate exact totals necessarily traversed all matching rows.
- **Both event orders and deep offsets were measured through the exact list
  service statement.** A separate 100,001-event history contained one
  `work_created`, 90,000 `progress`, and 10,000 `work_updated` rows.
  Oldest-first pages at offsets 0, 50,000, and 99,901 ran in 16.664, 21.139, and
  26.451 ms with 2,132, 4,611, and 7,186 shared-buffer hits. Newest-first pages
  at those offsets ran in 16.394, 21.602, and 26.490 ms with 2,131, 3,976, and
  5,716 hits. Every 100-row page used `ix_work_events_timeline`; increasing
  index rows and buffers show the documented offset degradation. All page
  sorts remained in memory at 39–46 kB.
- **Selective event filters used the dedicated indexes.** A 10,000-row
  `work_updated` filter returned 100 rows plus its exact total in 3.426 ms and
  200 shared-buffer hits, using `ix_work_events_timeline_type` for both the
  page and an index-only count. The one-row `work_created` filter used
  `uq_work_events_work_created`, ran in 0.188 ms, and hit 7 buffers. The common
  90,000-row `progress` filter ran in 21.509 ms with 2,572 hits; its page used
  the general timeline index and PostgreSQL rationally chose a sequential
  exact-count aggregate for that majority. The history flag used
  `uq_work_events_work_created` in every plan. No variant spilled to temporary
  storage. The disposable database was dropped afterward.
- **A real custom-format backup and isolated restore drill passed.** Dump took
  0.62 seconds and restore took 7.78 seconds. Source and restored databases
  matched at 152,000 events, maximum event ID 152,000, deterministic checksum
  `e89ae2688fd6393045e7e46f115e3d6b`, and sequence state. The restore retained
  all 11 indexes, the event checks, the immutability function, and all three
  work-event triggers. A post-review repeat against the hardened final schema
  dumped in 0.18 seconds and restored in 0.13 seconds. Its source and restore
  matched at 13 events, maximum ID 13, checksum
  `2844251f5ce317c3128c02beef907911`, sequence state, all 11 indexes, all four
  event/release guards, and the final release-marker function fingerprint.
  Restored event update and delete each failed with SQLSTATE `55000`. Both
  drills removed their disposable databases and archives.

- **The production-image full-stack check passed** against a uniquely named,
  disposable Compose project. All services became healthy, and
  `scripts/check-stack.py` verified authentication, dashboard origin and host
  protection, the exact 22-tool MCP catalog, and the complete authorized Phase
  4/5 ready-work and immutable-event lifecycle through MCP, REST, PostgreSQL,
  and the dashboard proxy. Cleanup succeeded. A 23,486-character scan of API,
  MCP, and web logs contained no bearer value/header, synthetic request body,
  accepted progress-event body, traceback, or unhandled exception. The
  disposable containers, network, and volume were removed afterward.

## Deprecated hand-off surface removal — 2026-08-31

Validated in the local Linux workspace with the locked environments and an
isolated PostgreSQL 17 test stack, after removing the deprecated hand-off tools,
REST routes, resource, and prompt.

- **120 backend tests passed against disposable PostgreSQL 17**, none skipped.
  A control run without `TEST_DATABASE_URL` reported 77 passed and 51 skipped,
  confirming the PostgreSQL tests actually executed. Backend Ruff passed; the
  only warning was the existing upstream Starlette TestClient deprecation.
  Coverage that previously reached canonical behavior through a deprecated route
  was retargeted rather than deleted: weighted full-text ranking, literal and
  wildcard query safety, `search_vector` re-derivation after an edit, combined
  filters with pagination, concurrent-writer version conflict, and the shared
  schema validation cases.
- **86 MCP tests passed**, and MCP Ruff passed. The suite now verifies the exact
  19-tool canonical catalog. The eight deprecated tools were removed, so the
  count fell from the 87 recorded for Phase 3 above.
- **39 frontend unit tests passed under Node 24.** TypeScript checking and the
  Next.js production build passed. Six tests covering deprecated-only helpers
  were removed with those helpers.
- **The production-image full-stack check passed** after `docker compose up
  --build`. `scripts/check-stack.py` read-only mode verified service security and
  the exact 19-tool catalog.
- **Live MCP surface measured against the running stack.** `tools/list` returned
  19 tools with no `*_handoff*` name; the model-visible tool surface fell from
  27,627 to 21,860 bytes. The `handoffs` resource template and the
  `resume_handoff` prompt are absent; the `work-items` resource and `resume_work`
  prompt still resolve. All eight `/projects/{project_id}/handoffs*` REST routes
  return `404` while the canonical routes return `200`.
- **Recall duplication eliminated.** Across the eight live work items, bounded
  recall returned 191,411 bytes with 89,985 bytes (47.0%) of byte-identical
  checkpoint duplication before the change, and 111,357 bytes with 0 duplicated
  bytes after it.
- **Search compaction measured.** The default agent-facing `view=minimal`
  returned 315 bytes per item against 1,824 bytes for the unchanged `view=full`
  dashboard shape.
- **`INSTRUCTIONS` is 581 characters** and leads with the trigger condition.
- **Migration provenance verified against the live database, not from code.**
  All seven checkpoints carrying `migration_origin = 'legacy-handoff-snapshot'`
  retain their `legacy_record_id`, and both fields still surface through the
  canonical context route. The Alembic head remains `0008_work_relationships`
  and no file under `backend/alembic/versions/` was modified.

The "27-tool canonical/compatibility catalog" recorded for Phase 3 below was
observed before this removal and is left as written.

## Phase 3 typed work-relationship validation — 2026-08-31

The complete three-phase program was validated in the local Linux workspace
with the repository's locked environments and isolated PostgreSQL 17 stacks:

- **128 backend tests passed against disposable PostgreSQL 17**. They covered
  migration/model parity through `0008_work_relationships`, all five
  project-local edge types, database constraints, direction and provenance,
  normalization/idempotency, one-parent enforcement, sequential and concurrent
  cycle prevention, blocker readiness and lease overlap, atomic linked creation,
  hierarchy filtering, bounded relationship context, live synchronization, and
  legacy tag compatibility. Backend Ruff passed; the only warning was the
  existing upstream Starlette TestClient deprecation.
- **87 MCP tests passed**, and MCP Ruff passed. HTTP and stdio tests exercised
  the exact 27-tool canonical/compatibility catalog, strict schemas,
  pointer-only counterpart data, typed graph errors, local validation
  sanitization, malformed-envelope log redaction, and claim-response recovery.
- **45 frontend unit tests passed under Node 24**. TypeScript checking and the
  Next.js production build passed. The tests cover hierarchy/search helpers,
  relationship direction and conflict language, stable per-tab provenance,
  strict proxy routes, capability rejection, and empty-stream DELETE handling.
- **10 Playwright test executions passed** across desktop and narrow Chromium.
  They exercised the Phase 1 and 2 scenarios plus collapsed root paging, lazy
  child loading, ancestry breadcrumbs, open descendants below every terminal
  parent status, active-plus-blocked display, relationship add/remove and
  parent conflict behavior, keyboard/dialog use, and narrow-layout containment.
- **The production-image full-stack check passed** against the separately named
  `mnemonic-phase3-validation` Compose project. All five containers became
  healthy at Alembic `0008_work_relationships`. The checker verified service
  security and the exact 27-tool catalog, then exercised create/search/recall,
  immutable checkpoints, stale edits, claim/replay/renew, blocker eligibility,
  atomic child/discovery creation, hierarchy browse, completion, default-open
  filtering, compatibility aliases, graph-first cleanup, and soft deletion
  through MCP → REST → PostgreSQL and the dashboard proxy.
- **All six planned query shapes were inspected with
  `EXPLAIN (ANALYZE, BUFFERS)`** on 2,000 work items, 6,000 checkpoints,
  1,800 hierarchy edges, and 100 blocker edges. Observed execution times were
  0.10 ms for indexed browse, 22.46 ms for complete lexical fallback search,
  0.05 ms for latest checkpoint, 0.08 ms for indexed blocker count, 12.28 ms
  for subtree-aware root pagination, and 1.48 ms for child expansion.
- **A real post-upgrade custom-format backup/restore drill passed**. The archive
  passed `pg_restore --list` and had SHA-256
  `4a04521677d690e70f54e02912162e7f536f3a4058608c8dee80910da567e5b6`.
  Source and restored databases matched at migration head, one project, 2,003
  work items, 6,005 checkpoints, and 1,900 relationships (100 `blocks` and
  1,800 `parent-child`). The restored checkpoint immutability trigger and all
  three relationship indexes were present.
- The disposable API/MCP/web log audit covered 13,288 characters and found no
  API key, bearer header, lease-token field/query, claim request ID, traceback,
  or unhandled exception. All three bundled skills passed the skill-creator
  validator; the examples parsed, documentation links resolved, the full-stack
  checker passed Ruff/compile/help/catalog checks, and the final diff had no
  whitespace or patch artifacts.

The comprehensive Phase 1–3 review also fixed expired claim-request reuse,
mixed-case migrated tag lookup, project-wide graph live synchronization and
open-detail reconciliation, a deletion guard that bypassed project-leading
relationship indexes, context projection work performed before its bound,
possible MCP SDK validation-value logging, non-strict canonical project
responses, a missing typed discovery-context error mapping, bodyless browser
relationship deletion, whitespace-only search mode drift, lifecycle-filtered
hierarchy fallback, and stale/superseded UI reload reporting and recovery. The
disposable E2E and production containers, networks, volumes, restored database,
backup archives, and temporary settings were removed. The user's existing
Mnemonic stack was not modified.

## Phase 2 atomic work-lease validation — 2026-08-31

The Phase 2 work-lease cutover was validated in the local Linux workspace with
the repository's locked environments:

- **118 backend tests passed against disposable PostgreSQL 17**. They covered
  migration/model parity, exact claim replay, expiry takeover, renewal and
  release, holder/session/request isolation, terminal-transition lease
  consumption, lock ordering, concurrent claims, optional checkpoint lease
  validation, query-capability rejection, and redaction. Backend and
  repository-wide Python Ruff checks passed; the suite emitted only its existing
  upstream Starlette TestClient deprecation warning.
- **68 MCP tests passed**. They covered the four typed lease tools, the exact
  23-tool HTTP and stdio catalogs, secret input schemas and representations,
  safe error mapping, and recovery of an unknown claim response without an
  unsafe retry.
- **34 frontend unit tests passed under Node 24**; TypeScript checking and a
  Next.js production build succeeded. The tests cover lifecycle/lease display,
  recursive proxy denial of capability-bearing inputs, typed conflict handling,
  and refresh at the lease-expiry boundary.
- **8 Playwright scenarios passed** across desktop and narrow Chromium. In
  addition to the Phase 1 work/checkpoint and live-update flows, they exercised
  active-lease visibility, expiry refresh, and an external claim arriving during
  a dashboard edit. The UI did not expose claim, renew, release, or token
  controls.
- **The production-image full-stack check passed** against a separately scoped
  disposable Compose project. The API, MCP server, dashboard, backup service,
  and PostgreSQL 17 became healthy; Alembic reported `0007_work_leases`, the
  removed hand-off tables were absent, and the real MCP HTTP catalog contained
  exactly 23 tools. The check exercised canonical work/checkpoint behavior plus
  exact claim replay, renew, cross-project token isolation, leased completion,
  and open-work filtering.
- **A real post-upgrade custom-format backup/restore drill passed**. The archive
  was validated by `pg_restore --list` and had SHA-256
  `f0ef414228a6e64de01583e25b3eaa2c025443bb66e2902bc270a6235c9fa437`.
  Source and restored databases matched at migration head, table counts, lease
  count, capability-token shape, removed-table absence, and deterministic
  canonical-data checksum (`798a8de29db3b8e5eff4c40d54f0b8b4`). A restored
  API rejected replay of an expired request with `claim_request_expired`, then
  allowed takeover and kept ordinary context responses capability-free.
- The updated `mnemonic-recall` skill passed the skill-creator validator. A
  separate final scope audit found no Phase 3 persistence, tools, or UI.

The Playwright, production-stack, restore-API, and test-database resources were
uniquely scoped. Their disposable containers, networks, volumes, restored
database, backup archive, and temporary configuration were removed after the
checks. The user's existing Mnemonic stack was not modified by validation.

## Phase 1 work/checkpoint validation — 2026-08-31

The canonical Phase 1 work-item/checkpoint cutover was validated in the local
Linux workspace with the repository's locked environments:

- **101 backend tests passed against disposable PostgreSQL 17**. Backend Ruff
  and Python compile checks also passed.
- **50 MCP tests passed**. MCP Ruff checks and the bundled skill validations
  also passed.
- **21 frontend unit tests passed**; TypeScript checking and a Next.js
  production build succeeded.
- **2 Playwright acceptance scenarios passed** against a disposable stack: one
  in desktop Chromium and one at the narrow Chromium viewport. They exercised
  work-item grouping, immutable checkpoint history, canonical recall pointers,
  work-only edits, completion, and deletion.
- **The production-image full-stack check passed** against a separately scoped
  disposable Compose project. It exercised the exact 19-tool MCP catalog and a
  canonical create/search/recall/checkpoint/update/complete/delete lifecycle
  through MCP, REST, PostgreSQL, and the dashboard proxy, then verified the
  deprecated aliases, resource, and prompt resolve the same canonical records.
- **A real custom-format backup/restore drill passed on the Phase 1 schema**.
  The production backup and restore scripts both validated the archive with
  `pg_restore --list`; an isolated restored database had the same deterministic
  canonical-data checksum as its source
  (`842b39ac85894777721e2b7f28f70588`). Canonical and deprecated API reads
  preserved the representative work item, both exact checkpoint bodies,
  Unicode, provenance, JSON metadata, IDs, timestamps, lifecycle, and version.
  The restored checkpoint immutability trigger rejected a direct update.
- The migration and running API both reported Alembic head
  `0005_work_graph_backfill`.

The Playwright wrapper used a uniquely scoped Compose project with disposable
PostgreSQL storage. Its success-path cleanup removed the containers and network;
no E2E containers remained after the run. The production-stack and restore
checks used a different narrowly named Compose project and isolated restore
database; their containers, volume, network, and temporary archive were removed
after validation.

## Hand-off progress validation — 2026-08-31

The comment and completion-summary change was validated in the local Linux
workspace with the repository's locked environments:

- **92 API tests passed against disposable PostgreSQL 17**, including Alembic
  model parity, exact comment text/provenance, comment full-text search,
  cross-project isolation, atomic completion, stale-version duplicate prevention,
  lifecycle filtering, and comment-aware embedding invalidation.
- **38 MCP tests passed**, including all ten typed tools, comment pagination and
  writes, completion receipts, timeline-bearing resources/prompts, Streamable
  HTTP, and a real stdio subprocess handshake.
- **13 dashboard tests passed**; TypeScript checking and a Next.js production
  build also succeeded. The tests cover the allowlisted comment/completion proxy
  routes alongside the existing origin and host protections.
- Backend lint, changed-MCP-file lint, and the updated full-stack check script's
  lint and format checks passed.

The disposable PostgreSQL container and network were removed after the run. The
API environment emitted its existing upstream Starlette TestClient deprecation
warning; no test failed.

## Prior MVP validation — 2026-08-30

Validated on 2026-08-30 (America/New_York) using Docker Desktop's Linux engine
on Windows. The production images were built from the repository dependency
lockfiles and run with the shipped Compose configuration.

## Automated checks

- **77 API tests passed** against real PostgreSQL 17, using the API's exact
  locked environment. Includes migrations/schema consistency, weighted GIN
  full-text search, stemming, literal identifiers and paths, safe query escaping,
  validation, authentication, project isolation, lifecycle, pagination, soft
  deletion, and simultaneous writer conflicts.
- **33 MCP tests passed**, including typed tools, HTTP error mapping, bearer and
  Host/Origin protection, SDK Streamable HTTP initialization/calls, and a real
  stdio subprocess handshake.
- **6 dashboard security tests passed**. TypeScript validation and the Next.js
  production Docker build succeeded. Package installation reported no known npm
  advisories at the time of this run.
- All three distributable skills passed the skill-creator validator. A separate
  scenario review checked duplicate handling, unavailable session IDs, stale
  provenance, and authorization boundaries.
- Python lint checks passed for the backend and operator/check scripts.

The API test environment reports one upstream Starlette TestClient deprecation
warning; it does not affect these results or the serving application.

## Running application checks

The live check script passed against the production containers, including
MCP → REST → PostgreSQL writes, compact search, exact recall, resource and prompt
retrieval, dashboard proxy edits, conflicting versions, lifecycle filtering,
cross-project rejection, and deletion. A separate real Docker stdio client
initialized successfully, discovered all seven tools available at that revision,
and listed projects through the API. Container restarts preserved the database contents.

In-browser verification covered:

- First-project creation and project-ID copying.
- Project switching, open/completed filters, and search by a stored tag.
- Full prompt viewing and exact clipboard preservation, including Unicode,
  trailing spaces, and newlines before and after an edit.
- Immutable originating session display.
- An external edit arriving while a browser draft was open: the stale save was
  rejected, the draft stayed intact, and explicit reconciliation preserved both
  the browser's title change and the other session's summary change.
- Canceling deletion and confirming deletion of a synthetic record.
- Usable narrow and desktop layouts, with no horizontal desktop overflow.

Temporary verification projects and records were removed from the application
after testing. Normal startup does not insert demonstration data.

## Backup and restore drill

A real custom-format backup containing two test projects and five test hand-offs
was restored into a new isolated database in the disposable PostgreSQL test
container. Every stored field matched the archive: prompt text, provenance,
metadata, tags, lifecycle, versions, and timestamps. Restored API recall/search,
full-text search, both GIN indexes, and soft-delete isolation worked.

Five invalid restore attempts (missing confirmation, path traversal, absolute
path, wrong extension, and missing file) failed without changing the empty
test database. A damaged archive with a readable table of contents failed during
data restoration; the single transaction rolled back and preserved all prior
test data. The isolated database and copied test files were then removed.

## Boundaries not claimed as validated

The actual Claude Code and OpenCode applications were not configured globally
or launched. Their configuration examples were checked against official docs,
and the underlying MCP transports were exercised with the official SDK.
ChatGPT cloud access, OAuth, public hosting, multi-user authorization, semantic
embedding recall, automatic capture hooks, and an off-machine backup destination
are outside this MVP. See operations guidance before any remote deployment.
