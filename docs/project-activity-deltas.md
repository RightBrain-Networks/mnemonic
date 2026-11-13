# Project activity deltas

`query_activity` is a read-only MCP command backed by
`POST /api/v1/projects/{project_id}/activity/query`. It returns bounded, compact
project changes without requiring an agent to fetch the whole project.
The existing `get_activity` command and contiguous journal remain unchanged.

The command help is the agent-facing reference:
`help(topic="query_activity usage")`, then `help(topic="query_activity details")`.
Follow numbered continuation links for the full contract. Parameter shapes and
constraints are available through field help and `query_activity schema`.
Skills only point to that reference.

## Queries

```json
{
  "project_id": "<project UUID>",
  "since": "2026-11-02T00:00:00-05:00",
  "until": "2026-11-05T00:00:00-05:00",
  "facets": ["work_items"],
  "filters": {"work_items": {"statuses": ["pending"]}},
  "limit": 20
}
```

Date bounds require a timezone and describe `[since, until)`. They filter the
retained source event time (`occurred_at`); `recorded_at` is journal capture time.
Neither claims exact transaction commit time. Unlike a current `updated_at`
search, a subsequent edit does not erase the earlier change from its interval.

For ADRs use `facets=["decisions"]` and
`filters={"decisions":{"domain_prefixes":["ADR"]}}`.
Exact domain IDs and decision states are also supported. Work and artifact tag
filters use catalog names with `tag_mode="or"` or `"and"`. Work tag filters also
match intrinsic `remediation` and `dir` labels. Results keep assigned labels in
`current.tags` and intrinsic labels in `current.system_tags`.

The eight facets are work items, artifacts, decisions, project metadata,
settings, knowledge domains, tags, and transcripts. Omission includes all.
`view="resources"` groups changes by resource; `view="events"` returns individual
records. `limit` defaults to 20 and is at most 100. Aggregate counts distinguish
change records from distinct resources and include facet breakdowns.

## Polling and pagination

1. Use `start="now"` to intentionally skip retained history and capture a
   checkpoint, or make a historical query first.
2. Poll with `after=<checkpoint>` and the desired facets and filters.
3. When `has_more=true`, repeat the query with `cursor=next_cursor`. Keep the
   original bounds, checkpoint, filters and view; only `limit` may change.
4. Accept all pages before saving the final `checkpoint`. Open-ended queries
   return it; a report with explicit `until` returns `checkpoint=null` even on
   its final page, avoiding acknowledgment of unseen later activity. The
   intermediate continuation cursor is not a polling checkpoint.

Checkpoints bind facets, filters and view; start a new query when changing that
scope. Use closed date windows for reports and checkpoint polling for continuous
tracking. Pages also have a 32,768-byte bound, so follow `next_cursor`
even when fewer than `limit` items are returned.

The first page captures an upper sequence. New changes belong to the next poll.
An empty filtered batch still advances through the captured stream. Retried
reads can redeliver records; events are identified by stream and sequence.
After a restored stream changes identity, explicitly resnapshot and rebootstrap;
never hide a gap by silently skipping to now. Legacy `get_activity` cursors and
the new query cursors are not interchangeable.

## Meaning and coverage

Status, tag and decision-domain filters use **current** resource state at each
page read. They do not reconstruct historical queue membership. The upper
sequence fixes the recorded event set, but current filter membership can change
between pages. Omit mutable-state filters for a stable historical event set.
Work status follows `search_work`: Pending means stored pending without a lease;
Active/Dropped reflect lease expiry and review states follow current review
disposition. Pending does not guarantee ready-to-claim; gates and dependencies
still need checking. Lease renewals and heartbeats are intentionally omitted; time-driven lease
expiry does not itself imply a new stored change record.

Summaries describe operations and changed field names deterministically. They
are not relevance rankings, semantic analysis, correctness checks, or a complete
net difference between historical database snapshots. Small before/after scalars,
identity and revision references allow an agent to choose which resources need
further reading. `before_state` and `after_state` come from the earliest and latest
selected journal records; they are not a reconstructed whole-resource diff.
Grouped source timestamps are the minimum/maximum in the window, while result
ordering follows `last_sequence`. Full bodies, prompt text, checkpoint prose, artifact bytes and
extracted content, and transcript content are excluded. Sensitive artifact
discovery does not grant content access.

Settings use separate change-stream identities for settings families and
individual prompts; some IDs are synthetic. The title identifies the family or
prompt. Retrieve settings and prompt text by project and prompt key, rather than
treating these stream IDs as generic resource lookup IDs.

The journal supplements Mnemonic's mutable operational tables. A domain mutation
and its activity record commit atomically; application reads still use existing
tables rather than replaying activity to reconstruct state. Historical imports
reuse retained evidence only. File-backed prompt history starts at its first
observed hash. Edits before that baseline, or changes reverted between reads,
may be missed; prompt timestamps identify observation rather than the original
external edit time. Overwritten scalar values cannot be recovered;
the response's coverage must accompany any claim about historical completeness.
Read current work and readiness before taking action. Activity does not authorize
execution or prove that external implementation, testing or deployment occurred.
