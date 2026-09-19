# Dashboard tasks

A **task** is a unit of work assigned to a single agent session. Tasks have two types:

- **Work item**: an implementation objective handed off by an earlier agent session or entered by a person on the dashboard.
- **Code review**: a child task scoped to adversarially reviewing a completed work item. Each review retains its own identity and links to its parent work item.

The home route (`/`) is the dashboard. Its two widgets show Active and Pending counts for work items and code reviews in the selected project. Full-width cards below combine both types of Active task, with a label, summary and View link. Counts include the entire project; cards are paginated. Live activity and lease expiry refresh the view.

The collapsible **Tasks** sidebar menu contains **Work items** (`/work-items`) and **Code reviews** (`/code-reviews`). Work item filters use implementation lifecycle status, so a completed parent remains Done throughout its review. Reviews have their own queue: Pending means a requested review waiting for an agent, Active means its review lease is active, and human dispositions and completed/superseded episodes remain discoverable through the other filters. A recommendation asking whether a review is needed is not itself a code review task; it remains attached to the originating work item.

Code reviews use the same resizable queue and detail panes as Work items, including keyboard selection and a full-screen detail pane on small screens. Summary cards offer the shared **Defer** split button and **Copy recall pointer**. Status decisions target the exact review episode and preserve the completed parent. Completed and superseded reviews retain immutable history; their recall pointers request context only. Pending reviews copy the configured recall and warm review prompts with the exact review ID.

Saved `/?work=…` links redirect to the work item surface. New task links pin the project and parent work ID; review links also pin the review ID, so retained review history can be opened directly.

The gear beside Live Updates opens the **Application settings** drawer. **Hide Nemo logo** defaults to enabled and hides the sidebar robot and tagline. This preference applies across projects in the current browser and survives reloads. It is independent of project settings and the theme preference. The drawer supports Escape, backdrop dismissal and focus restoration.

## API and release

Release 0.66.0 introduced safe-read `GET /projects/{project_id}/tasks`, with `kind`, `status`, `task_id`, `limit` and `offset` filters. Release 0.67.0 adds `work_version` to fence stale card decisions and nullable `review_state` to distinguish an authored review result from a human disposition of an open episode. Counts and pages use a coherent database snapshot and omit deleted work and duplicate aliases. Each review is a separate task; its implementation parent is never counted as active merely because its review is active. Responses expose public lease metadata only, plus the next active task lease expiry across the project so paginated views refresh on time.

Work item search and child browsing accept `status_scope=work_item`; unified search accepts it under `filters.work_items`. This opt-in filter uses implementation status. Existing API callers retain their effective-status behavior by default. No database migration or deployment configuration change is required. The MCP catalog and mutation/receipt counts are unchanged.
