## Metadata

- **Status:** proposed
- **Decision date:** pending
- **Deciders:** Repository owner (pending acceptance; direction directed by the owner across the 2026-08-29 sessions, including the requirement that each artifact be a complete hand-off prompt rather than a ticket)
- **Scope:** the agent-assistant layer only — `.claude/hooks/memory-recall.py`, `.claude/hooks/memory-recall-hook.sh`, `.claude/hooks/memory-recall-bootstrap.sh`, the `UserPromptSubmit` registration in `.claude/settings.json`, the per-project store layout under `~/.claude/projects/<slug>/`, `~/.local/bin/claude-memory-backup.sh` (to be widened), `.claude/CLAUDE.md` standing orders, `.claude/skills/llm-handoff-writing/`. No primary-application, test, or CI surface.
- **Supersedes:** None
- **Superseded by:** None
- **Related:** the MEMORY.md generated-index standing order, the gotcha-to-chip rule, and the task-chip provenance stamp (`.claude/CLAUDE.md`); the destroyed-memory recovery-channels order (memory `a-destroyed-memory-file-has-two-recovery-channels`); `.claude/skills/llm-handoff-writing/` (the artifact format this extends); `.claude/skills/issue-triage/` (the sweep pattern clause 7 extends); `/home/jamie/.config/systemd/user/claude-memory-backup.service` → `~/.local/bin/claude-memory-backup.sh` (the backup clause 9 widens)

## Context

Sessions here regularly surface follow-ups — out-of-scope defects, suspicious seams, improvements, un-investigated leads — inside their output prose. Prose is where they die: the owner reads past them, the session ends, and a later session re-derives the work or never finds it. The owner asked whether the agent memory subsystem could carry these findings without minting a GitHub issue for each one.

Every existing channel fails this alone:

- **Output prose** is the default and the problem. Nothing records the finding at all.
- **GitHub issues** are the sanctioned tracker, but the new-issue approval gate is deliberate and the owner has refused an issue per finding. Issues also arrive ticket-shaped: a cold session gets a title and a paragraph, not a working context.
- **Task chips** are nearly the right artifact — a self-contained prompt carrying the provenance stamp, with no approval gate, because the chip IS the proposal. But chips live in the client: there is no cross-session queue view, and an un-clicked chip's durability past session archival is not documented anywhere a session reads.
- **The memory store** (`~/.claude/projects/<slug>/memory/`) is durable and discoverable: a nightly NAS mirror with per-day version retention, a transcript-corpus second channel, semantic search over every file, and a `UserPromptSubmit` hook that injects high-scoring candidates automatically. But it holds keep-forever lessons. A work item has a lifecycle — open, possibly stale, done — that the store's culture deliberately lacks, and a completed work item has no sanctioned way to leave.

What exists to build on is substantial. `memory-recall.py` already provides, in one parameterized file: a frontmatter parser tolerant of the client's `metadata:` folding; a generated-index discipline (`regen`) with adoption, divergence, and census reporting; an incremental (mtime, size, sha1) SQLite embedding cache rebuilt in timeout-surviving chunks; cosine ranking over every file in a store; and a pointer-only, fail-open prompt hook. Its store is resolved by directory (`resolve_memory_dir`, `FF_MEMORY_DIR` override), and the embedding cache already lives in a sibling directory (`memory-index/` beside `memory/`), so a second store beside the first follows an existing pattern rather than inventing one. The hook's registration comment names the exact failure shape this ADR generalizes from lessons to findings: sessions re-derive facts an existing memory already records because recall depended on a session *thinking* to search.

The owner's controlling refinement: each stored artifact must be a **complete hand-off prompt** — a pre-baked brief a cold agent can pick up and execute — following the `llm-handoff-writing` skill: all necessary context, citations to durable records, the landmines the previous session uncovered, and verification steps. Generic bug-tracker tickets are explicitly refused as the artifact shape.

Two environmental facts were verified this session and are load-bearing. The nightly backup script (`~/.local/bin/claude-memory-backup.sh`, user unit `claude-memory-backup.timer`) globs `$SRC_ROOT/*/memory` only, so a sibling store is covered by nothing until the script is widened. And the hook's injection format is pointer-only by construction (score, name, description excerpt, path), which is exactly the delivery shape a long brief needs: the hook can surface a lead without ever flooding a prompt with a full brief body.

## Decision

We propose that out-of-scope findings are captured as cold-start hand-off briefs in a sibling store served by the memory-recall machinery:

1. **A second store, `handoffs/`, beside `memory/`** — `~/.claude/projects/<slug>/handoffs/`, one Markdown file per brief, same slug derivation and worktree-stripping as the memory store, so a worktree session shares its primary's briefs. The sibling-directory layout is the established pattern (`memory-index/` already sits beside `memory/`).

2. **The brief is the prompt.** The body is a complete hand-off per `llm-handoff-writing`: self-contained context, what and why, citations to durable records (the record governs; a brief's summary never outranks what it cites), the landmines the authoring session uncovered, and verification steps a completing session can execute. The task-chip provenance stamp block opens the body verbatim, because a brief spawned as a chip arrives as a first user turn byte-indistinguishable from owner input. Completeness means self-contained, never long: a small finding's brief is short. A ticket-shaped stub — title, link, no landmines, no verification steps — is a defect at birth, and the `llm-handoff-writing` skill gains a stored-brief section that says so and sets the minimum content.

3. **Frontmatter carries index and lifecycle state**, reusing the memory convention plus two keys: `name`, `description`, `index_title`, `index_note`, `index_rank` (the index renders from these exactly as `MEMORY.md` does), plus `status: open | promoted | done | wont-do` and `verified_against: <git SHA>`. The `description:` doubles as the retrieval key — written as the trigger or failure shape, since the embed text is `name + description + first ~1500 body chars` and the hook surfaces a pointer, never the body.

4. **The machinery is shared, parameterized by store.** `memory-recall.py` gains a store selector (flag or equivalent) so the same frontmatter parser, `regen` discipline, embedding-cache mechanics, and fail-open contract serve both stores. `regen` renders `HANDOFFS.md` in the brief store from `open` briefs' frontmatter, with the same divergence, dangling-link, census, and budget reporting. The embedding cache lives at `handoff-index/` beside the store, mirroring the `memory-index/` precedent. One process boundary, one venv — no second embedding stack.

5. **The hook covers both stores, pointer-only, open-only.** The `UserPromptSubmit` hook searches the brief store alongside the memory store and injects at most the capped handful of candidates, in the same labeled-lead shape (score, name, description excerpt, path), restricted to `status: open`. It MUST NOT inject full brief bodies into prompt context. The brief-lead threshold is not the lesson threshold: 0.72 was calibrated on the live 432-file lesson corpus (2026-08-29); a brief corpus does not yet exist, and the threshold is calibrated against the live store when populated rather than inherited unexamined.

6. **Completed work leaves the active store.** The completing session flips `status:` as part of wrap-up, and completed briefs are archived out of the active directory, because `regen`, search, and the hook all rank the whole directory and a done item must stop being a candidate. The mirror's deliberate no-`--delete` rule keeps every archived brief recoverable regardless.

7. **Freshness is a named obligation, not an assumption.** `verified_against:` records the SHA the brief's citations were checked against. A re-verification sweep — extending the `issue-triage` skill's verify-against-the-tree pass over open briefs — refreshes stale citations, retires dead work, and merges near-duplicates, using the semantic search as the dedup oracle. The same search-before-write oracle applies at capture: an authoring session searches before minting a near-duplicate brief. The sweep's cadence is owner-invoked at first, like triage itself. The verification labor lives in the sweep so a cold session can trust a brief without re-deriving it; the provenance stamp still governs authority for anything that has drifted since the last pass.

8. **Promotion to a GitHub issue stays owner-gated.** A brief is agent-authored and never self-promotes. When a finding grows weight — dependencies, claims, multiple sessions — it is promoted into an issue at owner approval during triage, and the brief's `status` flips to `promoted`.

9. **Backup parity lands with the store.** Widening `claude-memory-backup.sh` to mirror `*/handoffs` with the same rsync flags, per-day `--backup-dir` versioning, and deliberate no-`--delete` rule is part of this decision's definition of done, not follow-up work. The 2026-08-27 destroyed-memory incident — a session that assumed no backup existed and reconstructed a file from sources that had recorded wrong claims — is the precedent for what an uncovered store costs.

10. **A capture standing order joins the existing ones.** An out-of-scope finding worth a fresh cold session is never prose-only: it gets a brief in the same breath. Findings below that bar keep the existing memory-file-plus-chip path. Chips remain a delivery channel — a chip spawned from a brief carries the body as its prompt — and the store becomes the durable parking lot chips have lacked.

**Boundaries and exclusions.** The store never becomes a tracker: no claims, dependencies, assignees, or due dates — clause 8's promotion is the escape valve for heavy items. The tooling retrieves, renders, and verifies; it never authors — sessions author briefs, extending the memory doctrine line unchanged. The store lives outside the repo tree; docs hygiene already refuses tracker files there, and the per-project dot-directory is the sanctioned home for cross-session agent state. Briefs follow the memory store's content norms: household PII stays out, and `.untracked/` stays out of citations. Nothing in this ADR touches the primary application, its tests, or CI.

## Decision Drivers

- A finding must survive the session that found it and reach a future session without a human remembering it exists. (The buried-in-prose failure, owner-reported.)
- A cold session must act on a finding without re-deriving it: pre-baked, self-contained, landmines included. (Owner requirement.)
- Briefs must stay trustworthy as the tree drifts; a stale brief re-teaches wrong lessons, which is worse than no brief. (The 2026-08-27 reconstruction incident is the precedent.)
- The GitHub issue count stays low and the new-issue approval gate is untouched; issues remain the tracker for promoted work. (Owner constraint.)
- No second implementation of store, index, search, hook, or cache machinery. (`converging-on-patterns` doctrine; #632/#638 lineage.)
- Durable state on this box is backed up from day one. (The destroyed-memory lesson.)

## Alternatives Considered

### One GitHub issue per follow-up

- **Benefits:** The sanctioned home for coordination; search, labels, and comments for free; already the natural promotion target.
- **Costs and risks:** The owner explicitly refuses issue-per-finding volume; every issue needs approval; issues arrive ticket-shaped, so the cold-start requirement is unenforced.
- **Why not selected:** Fails the issue-count constraint and the cold-start driver. Issues remain where promoted work goes (clause 8).

### One long-lived tracking issue accumulating checklist items

- **Benefits:** A single queue; extending an existing issue needs no approval, so this dodges the gate honestly.
- **Costs and risks:** Checklist items are tickets again; there is no per-item semantic recall, so discovery depends on a session reading the right issue; ordering and triage are manual; comment threading buries landmines.
- **Why not selected:** Re-creates the prose problem inside an issue; fails the cold-start and discovery drivers.

### Briefs inside the memory store with a `kind:` marker

- **Benefits:** Zero new paths; the backup already covers `memory/`; the parser and hook work unchanged.
- **Costs and risks:** Lifecycle divergence: memory files are keep-forever lessons whose destruction is treated as an incident with recovery channels, while briefs are transient work items that must leave the active corpus. Done briefs would pollute semantic recall and the `MEMORY.md` budget.
- **Why not selected:** Initially favored for its cost; the owner's requirement that each artifact be a complete prompt widened the lifecycle divergence to decisive. A sibling store keeps each doctrine intact. (If the memory store ever gains a lifecycle mechanism of its own, see Revisit When.)

### A separate subsystem (new script, new store format, new hook)

- **Benefits:** Clean-slate lifecycle design; no coupling to the memory tooling's evolution.
- **Costs and risks:** Rebuilds the exact twin — frontmatter store, generated index, semantic search, fail-open hook, incremental cache — the defect class this repo documents as recurring (#632/#638: five separately-invented guards, no arbiter). `memory-recall.py` is already store-parameterized, so reuse costs a selector, not a subsystem.
- **Why not selected:** Fails the no-second-implementation driver outright.

### Task chips alone (status quo)

- **Benefits:** No new machinery; the provenance stamp and self-contained-prompt requirements are already mandated; no approval gate.
- **Costs and risks:** No durable queue view; no re-verification; no dedup; an un-clicked chip's durability past session archival is not documented.
- **Why not selected:** Fails the survival driver. Chips are kept as the delivery channel into the store (clause 10).

## Consequences

### Positive

- Findings become durable, ranked, dedup-able, and auto-surfaced to sessions touching the relevant area — the read side is fixed structurally, the way the memory hook already fixed it for lessons.
- A cold session starts from verified context and recorded landmines instead of re-deriving both.
- The issue count stays bounded and the approval gate intact; heavy work is promoted deliberately.
- The implementation delta is small: a store selector, a second regen target, a cache directory, the hook's second store, the backup widening, and two text additions (the skill section, the standing order).

### Negative

- Brief authorship is expensive — recon plus verified citations — so the same-breath rule must gate on the bar (clause 10), or the store fills with stubs.
- A periodic sweep becomes owed labor; skipping it converts the store from an asset into a liability.
- Two stores, two indexes, and a widened backup must stay coherent; the shared-code selector is the control, but the coherence is a standing obligation.
- Occasional pointer-only brief leads will surface in prompts that touch related areas; this is accepted, capped, labeled noise, tunable by threshold and reversible by filtering briefs out of the hook.

### Risks and Mitigations

- **Risk:** A stale brief re-teaches a wrong lesson — the same failure shape as the 2026-08-27 reconstruction incident, where a rebuilt memory re-asserted claims the original had flagged as wrong. **Mitigation:** `verified_against:` plus the sweep's re-verification (clause 7); the provenance stamp keeps the authority rules inside every brief; the verification labor is placed where the cold session does not pay it.
- **Risk:** Authorship regresses to ticket-shaped stubs. **Mitigation:** The `llm-handoff-writing` stored-brief section sets minimum content (context, citations, landmines, verification steps); the sweep audits for stubs and returns them for rewrite or retirement.
- **Risk:** The store accretes tracker features — claims, dependencies, due dates — and becomes a second tracker beside GitHub. **Mitigation:** The explicit exclusion in Decision; clause 8's owner-gated promotion is the sanctioned route for heavy items.
- **Risk:** The store ships without backup coverage, silently. **Mitigation:** Clause 9 makes the widening part of the same change that introduces the store. Residual exposure after that change is accepted: the backup script's zero-stores alarm (exit 78) fires on the memory glob only, so a future regression that drops the handoffs glob would not page anyone — the sweep's census is the observation point.
- **Risk:** Capture discipline fails silently — findings still die in prose because no session writes the brief. **Mitigation:** Convention-level enforcement only, same as the existing gotcha-to-chip rule: the standing order, plus the sweep's census making the queue's size visible. A hook cannot detect an unwritten brief; this exposure is accepted explicitly.
- **Risk:** The two stores' machinery drifts apart over time — a divergent spelling of a parser or index rule. **Mitigation:** One file, one parser, one selector; divergence requires forking the shared code, which is the same tell as any twin in this repo.

## Revisit When

- Two consecutive sweeps find open briefs stale on arrival — their `verified_against` SHAs, older than the sweep interval, dominating the queue. Sweep cadence or freshness automation is then reconsidered.
- The open-brief count grows monotonically across three consecutive sweeps, exceeding the sweep's re-verification capacity. The promotion bar, a cap, or capture-bar tuning is reconsidered.
- The client ships durable, server-side task queues or persistent chips, making a file store redundant as the queue layer.
- Brief-lead injections are reported as noise by the owner in practice; filtering or dropping brief injection is reconsidered.
- The memory store gains a lifecycle mechanism of its own, dissolving the rationale for the two-store split.
- The mirror is ever observed missing handoffs content after clause 9 has landed; the backup alarm surface is then extended to the second glob.
