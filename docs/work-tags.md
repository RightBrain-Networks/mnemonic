# Project tags

Each project owns a tag catalog. Humans manage its names, descriptions, colors,
and visual order in **Project settings > Tags** (`/settings/tags`). Names are
normalized to lowercase words separated by dashes. A tag definition is shared by
the project's work items and artifacts; changing its presentation changes dashboard labels.
Editing opens a compact form in that tag's row. Renaming or deleting a tag updates
current canonical work assignments and appends a metadata revision for each affected artifact. Frozen
duplicate audit records retain their recorded names, preserving their immutable history.
Color is supplementary: every tag also has a visible name and a description.

New projects also receive six editable starter tags copied from Fish Food's
production catalog on October 27, 2026: `bug-suspected`, `bug-proven`, `infra`,
`tests`, `ux-ui`, and `security`. Their initial descriptions, colors, and order
match that snapshot. These are independent project-owned definitions: humans can
edit or delete them, and later changes in Fish Food do not change other projects.

The `code-review`, `remediation`, and `dir` definitions are system-owned and cannot
be renamed, edited, or deleted. Their menu order can change with the rest of the
catalog. Review episode rows intrinsically carry `code-review`; their original completed
implementation work does not inherit that tag. Work-item reads intrinsically
carry `remediation` or `dir` when the corresponding lineage is present. Selecting or clearing an ordinary work tag
does not create a review, change eligibility, remove lineage, or acquire a lease.
The `dir` description is “Design Impact Review: investigate the impact a revised decision document”.

The work library displays tags above the status and sort controls, separated by a
subtle horizontal divider. **All** clears the tag
selection, **OR** matches any selected tag, and **AND** requires every selected
tag. Tag order affects this presentation only. Code review work appears in this
library and retains its existing review controls and purpose-bound leases.

## Agent contract

`list_tags(project_id)` is a safe read returning the complete catalog in menu
order. Each entry includes `id`, `project_id`, `name`, `description`, `color`,
`menu_order`, and `system`; the envelope includes `project_id`, `items`, and `total`. There are no
MCP tools to create, edit, delete, or reorder tag definitions.

Before creating work, agents must query the catalog, read the descriptions, and
select the names that fit the objective. Fresh `create_work` calls require a
top-level `tags` declaration: a nonempty list of unique existing normalized names,
or explicit null when none apply. Empty lists and fresh omission are rejected.
Historical receipt replay preserves the original omitted arguments unchanged.

`update_work.changes.tags` replaces current assignments; null clears them and
omission leaves them unchanged. Work reads expose nullable assigned `tags` and
separate intrinsic `system_tags` (`remediation` and `dir`). Review episode rows
on the dashboard carry `code-review`; agents continue to discover those episodes
with `list_code_reviews` and use the existing review tools and purpose-bound leases. Checkpoint tag fields remain readable for
historical receipts, but fresh checkpoints must leave them empty; current labels
are not immutable checkpoint metadata.

Unified search already exposes tag counts through `tag_counts`. It now filters
current assignments and intrinsic work-item system tags (`remediation` and `dir`) with
`filters.work_items.tags=["name", "another-name"]` and
`filters.work_items.tag_mode="or"` or `"and"`. The singular `tag` filter remains
available. `search_work` accepts the same plural names and operation directly.
Artifact filters independently accept `filters.artifacts.tags` and `tag_mode`.
Decision and transcript facets retain their own filters. A `code-review` work filter matches explicitly assigned
work labels; review episodes remain discoverable through `list_code_reviews`,
not a new unified-search record type. Manually assigning `code-review` does not
create an episode or make an ordinary implementation lease a review lease.

Artifacts use the same catalog and All/OR/AND filtering in the library. Humans
select tags when uploading or editing artifact details. Agents can pass `tags`
to upload, replacement, and metadata-update tools after reading the catalog.
Upload omission starts untagged; update/replacement omission preserves the current
assignment, and explicit null clears it. A nonempty list replaces the assignment.
Tag changes advance the artifact revision and retain previous assignments in
immutable history. They do not change the artifact bytes or grant sensitive
content access. Keep the exact tags declaration and revision on uncertain retries.

## Migration

Migration `0067_project_tags` deletes legacy tag definitions and checkpoint assignments and sets
existing mutable work assignments to null, then creates each project's three
system definitions. Review and decision lineage is retained, so intrinsic system
tags can still identify existing workflows. This intentionally discards legacy
tag data; it does not reset code review state or change MCP review operations.

Migration `0068_artifact_tags` initializes existing artifact assignments and their
historical revisions to null, updates the protected `dir` description, and adds
the starter set only to future projects. Existing custom definitions, colors,
order, and work assignments are preserved. Downgrade is blocked after artifact
tag assignments or related durable operations exist; existing starter definitions
remain project-owned and are never deleted by downgrade.
