# Repository freshness assessment

Shared by the `mnemonic-save`, `mnemonic-search`, and `mnemonic-recall`
skills. This reference governs the local, advisory Git comparison performed by
`bin/mnemonic-repository-freshness`. The API, MCP server, and browser never
inspect a checkout or accept an assessment result.

## Declaration contract

`affected_paths` is an ordered caller declaration of version-control paths on
which a full checkpoint's assertions depend. It is not a generated diff and is
not server-verified. Missing or empty scope means unknown scope. It never means
the whole repository or no changes. The literal `**` declares all eligible
repository paths.

Non-empty scope requires `verified_against`, a caller-asserted hexadecimal
commit object name that the author actually inspected. Reading `HEAD` alone is
not inspection. `repository_branch` is optional display provenance; branch
names are mutable and are never passed to or resolved by the helper.

Version 1 accepts 1 through 64 unique patterns, at most 512 ASCII bytes each
and 16,384 bytes in aggregate. Components contain only
`A-Z a-z 0-9 . _ @ + = , ~ - *`, separated by `/`. A single `*` spans bytes
inside one component. `**` is valid only as a complete component and spans
complete components. Empty, `.` and `..` components and all whitespace,
non-ASCII, backslash, `?`, quotes, brackets, braces, colon, bang, caret, shell
syntax, Git magic, and other consecutive-star forms are invalid. A literal
names one file or gitlink; write `directory/**` for a directory dependency.
Order, spelling, and case are preserved.

Eligible paths are tracked entries in the baseline, captured current tree, or
index, plus nonignored untracked regular worktree paths. Each pattern must
match independently. Ignored untracked content, submodule interiors, generated
or runtime state, external systems, and symlink targets are not proven.

## Fixed local invocation

The user must explicitly select the workspace before assessment. Repository
URL is display context, not repository identity. Invoke from that selected and
locally trusted workspace with separately quoted argv and no dynamic root, URL,
branch, ref, configuration, command, or output path:

```text
"${CLAUDE_PLUGIN_ROOT}/bin/mnemonic-repository-freshness" \
  --baseline a832bc1 \
  --path 'src/**' \
  --path 'tests/test_api.py'
```

Do not use `eval`, a generated shell command, command substitution from stored
data, or shell expansion. The client must enforce a 15-second
whole-process-group deadline. Timeout or signal is caller-side `timed_out`;
exit 64 or 70, stderr, partial or extra output, an oversized body, or a body/exit
mismatch is `malformed_helper_result`.

The runtime floor is Bash 3.2 and Git 2.45.0. Older Git is rejected before any
repository read because no-lazy-fetch behavior is required. The helper uses
only Bash and a trusted-host Git executable. It performs no fetch, network
operation, configured process, hook, filter, pager, credential interaction,
repository mutation, configuration change, or temporary-file write. It rejects
an effective `core.worktree`, then pins every later Git process to the discovered
absolute Git-directory/worktree pair. Local and worktree config includes are
honored when detecting an effective fsmonitor. Graft lookup is pinned to
`/dev/null`, replacement objects remain disabled, every inherited `GIT_*`
variable is removed, and `GLOBIGNORE` is neutralized.

Before its first index-aware Git command, the helper requires the discovered
Git directory to be listable and rejects any `sharedindex.*` entry with
`split_index_unsupported`. Git 2.45 unconditionally refreshes the shared
index file's modification time when it reads a split index, even with optional
locks disabled. Supporting that layout is therefore incompatible with the
Bash/Git-only, no-temp, no-repository-write contract. A stale shared-index
artifact is rejected conservatively as well.

Every Git stderr stream is drained without retaining its bytes. Any diagnostic,
including one paired with exit zero, fails closed; raw Git diagnostics never
enter the protocol. NUL-delimited records and abbreviated-object lines are read
in bounded chunks. Over-cap records are either represented conservatively as
truncated evidence when their scope match is certain or make the assessment
indeterminate. Repository/worktree config values are streamed the same way;
an over-cap `core.autocrlf` or `core.fsmonitor` value is necessarily non-false
and becomes its content-specific conservative blocker.

For each scoped stage-zero regular file, content is read only through
`git hash-object --no-filters --stdin` without `-w`. Executable state is
obtained separately from a `git diff --no-index --raw` comparison of
`/dev/null` with that exact absolute path. This narrow metadata call uses
`lstat` to produce Git's canonical owner-execute mode and forces
`--no-ext-diff`, `--no-textconv`, `--no-renames`, `--no-relative`, and
`-O/dev/null`. Raw no-index output bypasses patch/content generation,
attributes, diff drivers, and clean/process filters. The helper never uses
`git diff-files`, `git status`, or Bash `-x` for worktree mode.

## Exact protocol

Valid stdout is ASCII, no larger than 32 KiB, has one terminal newline, and
uses this exact order:

```text
protocol=mnemonic-repository-freshness-v1
state=unchanged|changed|indeterminate
reason=<registered_reason>
baseline_oid=<full_hex_or_dash>
head_oid=<full_hex_or_dash>
pattern_count=<unsigned_decimal>
matched_pattern_count=<unsigned_decimal>
displayed_path_count=<0_to_100>
paths_truncated=0|1
detail=<reason_defined_detail>
path_byte_q=<ascii_byte_escaped_path>
disclaimer=git-state-only-not-semantic-proof
```

All `detail` lines precede all path lines. Their counts must agree with the
reason and `displayed_path_count`. A path preserves only
`A-Z a-z 0-9 . _ / @ + = , ~ -`; every other filename byte is uppercase
`\xHH`. It is display-only and must never be decoded, evaluated, or reused as
an argument. Paths are lane-ordered (committed, staged, unmerged, unstaged,
then untracked), deduplicated by first occurrence, capped at 100, and safe to
show only in necessary tool/model context.

Raw path candidates longer than 8,192 bytes are not retained and set
`paths_truncated=1`. A repeated anchored committed, staged, or unmerged
difference status can therefore be `changed` with zero displayed paths.
Over-cap worktree or untracked observations remain positive but cannot alone
become stable changed evidence because their identity was not retained.

Exit 0 is a valid `unchanged` body, 10 a valid `changed` body, and 20 a valid
`indeterminate` body. Exit 64 is invalid invocation and exit 70 internal
failure; neither has a trustworthy body.

## Outcomes and reasons

- `unchanged` / `no_relevant_change_observed`: two bracketed, complete
  sweeps saw no relevant eligible Git change. Say only "no relevant eligible
  Git change observed." This is not semantic freshness, correctness, safety,
  or authority.
- `changed` / `relevant_change_observed`: a sound committed, staged,
  unmerged, raw filter-free worktree, or nonignored-untracked observation
  repeated across both sweeps. Reinspect current source before relying on the
  checkpoint; do not claim every statement is stale.
- `indeterminate`: explain the registered reason and inspect manually or ask
  for repository direction when a material choice remains. Never collapse
  missing output, missing scope, or a failed command into unchanged.

Caller-only reasons are `no_scope`, `no_baseline`, `repository_unbound`,
`timed_out`, and `malformed_helper_result`.

Helper reasons are `unsupported_bash_version`, `unsupported_git_version`,
`invalid_declaration`, `not_a_worktree`, `bare_repository`, `unborn_head`,
`baseline_missing`, `baseline_ambiguous`, `baseline_not_commit`,
`baseline_not_ancestor`, `split_index_unsupported`, `pattern_unmatched`,
`exact_directory_requires_recursive_glob`,
`assume_unchanged_scope_unsupported`, `skip_worktree_scope_unsupported`,
`fsmonitor_scope_unsupported`, `submodule_scope_unsupported`,
`external_filter_scope_unsupported`, `normalization_scope_unsupported`,
`symlink_scope_unsupported`, `sparse_checkout_unsupported`,
`core_filemode_scope_unsupported`, `git_failed`,
`state_changed_during_check`, `relevant_change_observed`, and
`no_relevant_change_observed`.

Pattern reasons carry ascending unique `pattern_index:<zero_based_decimal>`
details. `git_failed` carries one `lane:` detail. Concurrent movement carries
one `anchor:head|index|worktree` detail. A stable committed, index, untracked,
or filter-free raw difference may still establish `changed`; unsupported
conditions are blockers to a complete zero result.

## Limits and authority

Raw regular-file bytes are compared without filters. If attributes or
`core.autocrlf` make raw equality insufficient, the result is indeterminate;
every explicit scoped `filter`, `text`, `eol`, `crlf`, `ident`, or
`working-tree-encoding` declaration is conservative, including a negative
declaration. This avoids Git porcelain's ambiguity between literal values
named `unset`/`unspecified` and its sentinel states. Sparse checkout/index,
split indexes, assume-unchanged, skip-worktree, configured fsmonitor,
`core.fileMode=false`, stable symlink scope (including a regularized 120000
entry under `core.symlinks=false`), and submodule scope similarly block a zero
claim. Missing or modified skip-worktree files are not promoted to raw
worktree-change evidence. Nonregular untracked entries are ineligible, and an
untracked symlink is a symlink blocker rather than change evidence. Because
disabling a configured fsmonitor is required to
prevent its hook or daemon from executing and that disabling can hide
per-entry valid bits, any enabled or path-valued `core.fsmonitor` is
conservatively treated as the same scoped zero blocker. The helper does not
claim to observe dormant persisted valid bits while fsmonitor is disabled;
those bits cannot suppress its filter-free raw hash of every scoped stage-zero
regular file. The comparison is two best-effort observations, not an atomic
snapshot, build result, runtime check, content manifest, or semantic proof.
Abbreviated-object resolution is streamed, drains producer output, and retains
only the first candidate plus a count capped at two.

Repository roots, remotes, raw Git errors, configuration, and credentials must
not enter the protocol, logs, checkpoints, or telemetry. Actual path output is
privacy-sensitive because tool output enters model context. Show the bounded
quoted list only when useful and never copy it automatically into durable
work, events, or metadata.

The checkout, its Git metadata, repository entry count/config size, and other
local processes are inside the local trust boundary. Input, deliberately
retained evidence, and protocol output are capped, but Git/Bash enumeration is
not independent of repository size: Bash 3.2 glob completion materializes the
split-artifact match vector before discarding it. The caller must apply the
whole-process-group deadline. The two sweeps detect many ordinary concurrent
changes, but a Bash/Git-only helper cannot atomically gate a concurrent
split-index conversion or make descriptor-relative `openat2`/`O_NOFOLLOW`
reads. A malicious local process can therefore race the shared-index gate or a
regular path between the helper's checks and open. Expanding the threat model
to hostile local filesystem races requires a native helper or read-only
filesystem sandbox; it cannot be promised by this Bash 3.2 implementation.

Assessment is advisory. It grants no execution authority, lease, readiness,
gate answer, merge direction, mutation permission, or server-side state. Read
[authority-and-provenance.md](${CLAUDE_PLUGIN_ROOT}/reference/authority-and-provenance.md)
before using a checkpoint to continue work.
