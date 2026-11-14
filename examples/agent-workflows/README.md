# Optional project workflow starters

These modules are examples for a project owner to review, select, and copy into
repository-owned agent guidance. They are not loaded by Mnemonic, the plugin,
portable skill export, or client connection setup. Select only modules that fit
the project; adapt the copied text and maintain it with the repository. Updates
require an explicit owner decision to review and copy changes again.

| Module | Optional team preference |
| --- | --- |
| [Status reporting](status-reporting.md) | Use bounded updates for recurring summaries. |
| [Decision records](decision-records.md) | Record durable design choices and evidence. |
| [Repository handoffs](repository-handoffs.md) | Include useful repository verification in handoffs. |
| [Review methodology](review-methodology.md) | Focus independent reviews on behavior and evidence. |

Start each adopted module with the shared orientation flow: `session_init()` when
missing, project selection, then `get_project(project_id, settings_only=true)`.
Current command help defines API requirements; project settings define runtime
policy. These modules neither change those contracts nor grant permission to act.
