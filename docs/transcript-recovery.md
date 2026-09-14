# Recover incorrect historical transcript paths

Application/API/MCP/dashboard **0.52.1**, plugin **0.30.1**, and migration
`0038_transcript_recovery` add an operator-only recovery workflow. No MCP tool,
REST mutation, or browser mutation is added. The worker still copies and indexes
through RabbitMQ; recovery does not copy files inline or bypass active leases.

`source_path` is the original agent assertion and never changes. A separate
append-only `transcript_recoveries` journal retains the approved replacement path,
original assertion, project at approval, reason, evidence, operation UUID,
expected generation/snapshot, approved SHA-256/size, and resulting capture identity.
The transcript points to its current approval. Recovery history is included in
project backups and follows the transcript when its work moves projects.
The dashboard continues showing the originally reported path; inspect the private
prepared request or journal for the approved capture path.

## Approve exact matches, not guesses

An incorrect path can produce `transcript_io_error` even when a file exists in a
different Claude worktree directory. A temporary task `.output` symlink outside
the configured roots produces `transcript_path_not_allowed`. Neither error alone
proves that the transcript was deleted or that mount permissions need widening.

First identify the exact transcript and current owning project. Independently
verify the native file belongs to that session/subagent. A unique exact session
filename match or a client-reported link to the actual native file can support
an operator's approval; they are not automatic worker heuristics. Record how the
identity was verified. If several files could match, the source is a directory,
or no file can be established, leave the record unchanged and report the gap.
Never flatten a workflow directory into one session or infer missing content.

Supply the **actual regular file target** beneath an existing allowed root.
The helper and worker refuse symlink traversal, nonregular files, paths outside
the allowlist, oversized content, and unstable reads. Do not add `/tmp` to the
allowlist to accommodate temporary output links, or create aliases/preseed raw
storage to make the old assertion appear valid.

## Prepare once, then apply

Run in the upgraded shared worker environment with its database connection,
source mounts, and configured size limits. Store the inputs in a private operator
directory (`0700`) with files owned by the worker identity and mode `0600`.
The directory is an explicit temporary administrative bind, not a new source
root. Do not place these files in Git: they contain private paths and frozen
operation arguments, though no transcript bodies or credentials.

Create a JSON array of at most 1,000 explicit mappings:

```json
[
  {
    "transcript_id": "<exact-transcript-uuid>",
    "project_id": "<current-project-uuid>",
    "original_source_path": "/approved/old-location/session.jsonl",
    "replacement_path": "/approved/verified-worktree/session.jsonl",
    "reason": "Correct the historical worktree path assertion",
    "evidence": "Operator verified the unique native session identity at this target"
  }
]
```

The example placeholders must be replaced with the actual IDs and evidence. After
mounting the private directory as `/recovery`, invoke:

```sh
python /app/scripts/recover_transcript_paths.py prepare \
  --mappings /recovery/mappings.json --output /recovery/prepared.json
python /app/scripts/recover_transcript_paths.py apply \
  --request /recovery/prepared.json
python /app/scripts/migrate_transcript_copies.py --wait --timeout 1200 --verify
```

Preparation is database-read-only. It checks current ownership, original path,
generation, active-lease and pause guards, then streams each eligible source's
hash without retaining or printing its contents. It creates a **new** private
intent file with fresh operation UUIDs and the exact expected bytes. It refuses
to overwrite an existing intent or follow symlinked manifest paths. Deferred
entries retain only transcript IDs and safe error codes in the prepared output.

Application checks each exact receipt before fresh-state guards. A repeated
operation with changed arguments is rejected. A fresh approval atomically
appends the journal, rotates generation and capture identity to invalidate old
workers, resets copy claims, and creates the normal PostgreSQL outbox job. It
preserves any previously readable normalized text. Copy and indexing completion
remain separate; an accepted approval is not evidence that either finished.

If an apply response is lost, **keep the prepared file unchanged and apply it
again**. Do not regenerate UUIDs, rehash the source, or edit the arguments for an
uncertain retry. Earlier successful entries replay even if later entries failed,
the file vanished, the project moved, or processing was subsequently paused.

The worker verifies the approved hash and size before publishing or adopting a
retained snapshot. Changed bytes produce `transcript_recovery_content_changed`;
the wrong file is never published as the approved copy. Investigate the change
and prepare a new, separately approved intent only after the earlier outcome is
known. The old journal remains immutable. Rebuilds retain the approval and reuse
successful copies; a fresh enrollment establishes a separate capture identity.

The helper exits `0` when all requested actions were accepted, `1` for deferred
or refused entries, `2` for invalid inputs/environment, and `3` for a database or
transient service error with a potentially unknown apply outcome. A sanitized
503 can hide a committed transaction; it is never reported as a definite refusal.
The helper stops the batch and instructs an exact retry. It never prints SQL exceptions,
request arguments, or transcript bodies. The backfill verifier can still exit
nonzero for unrelated missing/ambiguous sources or unsupported formats; report
those separately from successful approved recoveries.

## Coordinated upgrade and recovery checks

Build the new images first. Stop API, MCP, dashboard, and shared worker writers;
take a verified PostgreSQL backup plus the private artifact, prompt, transcript,
and configuration backups. Upgrade to `0038_transcript_recovery`, then start all
four upgraded application services and check health before applying recoveries.
RabbitMQ, PostgreSQL, and Tika can remain running. Do not run older application
processes against the new schema. See [the job migration runbook](transcript-jobs.md)
for quiescing, backup verification, and coordinated recovery boundaries.

Verify retained raw SHA-256/size and final indexing state for each approval.
Compare the full error inventory before and after, retaining unambiguous counts
for recovered, deferred, unmatched, ambiguous, and unsupported-format records.
Run both integrity audits; distinguish pre-existing findings from new ones.
Do not rewrite unrelated historical evidence to make an audit green.

The journal cannot be removed through a lossy downgrade. Fix forward, or restore
the coordinated pre-upgrade database/files/configuration and old images. Project
archives are schema-specific: a pre-0038 archive requires its matching release,
not a compatibility loader into the new schema.
