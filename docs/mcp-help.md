# MCP command help

The `help` tool answers locally with one plain-text page, without a project ID,
API request, lease, or operation receipt. It is safe to use before claiming work
and during a cold review: it reads command definitions, never work context.

Pass an optional `topic` string. Each successive word selects a child page:

| MCP call | Result |
| --- | --- |
| `help({})` | All registered commands, grouped by task, plus navigation syntax |
| `help({"topic":"complete_work"})` | Purpose, required arguments, optional argument names, and important fresh-call requirements |
| `help({"topic":"complete_work usage"})` | Workflow and retry guidance |
| `help({"topic":"search details"})` | First bounded page of complete search guidance; follow its Next link |
| `help({"topic":"search details 2"})` | Second complete-guidance page |
| `help({"topic":"search filters work_items semantic"})` | Semantic preconditions, latency and response fields to inspect |
| `help({"topic":"complete_work checkpoint"})` | Checkpoint fields and author provenance |
| `help({"topic":"complete_work completion_evidence artifact_references"})` | Required fields and rules for each artifact reference |
| `help({"topic":"complete_work completion_evidence verification_results command"})` | The command verification variant and conditional exit-code requirements |
| `help({"topic":"complete_work schema"})` | The exact full registered input JSON Schema |
| `help({"topic":"complete_work completion_evidence schema"})` | Only that field's schema and its transitively referenced definitions |
| `help({"topic":"claim_work force"})` | Lost-token recovery, checks for another active session, and exact force-claim retries |

Overview pages list immediate fields rather than recursively expanding them.
Array pages describe their items; discriminated unions list selectable variants.
Every field page links back to its parent and offers its own schema. Dotted field
paths and `[]` suffixes also work. Unknown topics return the nearest known page
or the root navigation hint without echoing the unknown input. Topics are limited
to 400 characters and 12 field levels.

Every registered tool description fits within 2,048 characters. Search preconditions
and sensitive-content approval requirements appear in the advertised description;
longer contracts remain available through `details`, with explicit numbered
continuations. These pages retain the full guidance without loading unrelated
command schemas. Field pages show schema descriptions and contextual prose,
including semantic prerequisites, cost and coverage.

Names, required fields, types, enum choices, bounds, and explicit schemas come
from the registered tool contract. Short authored notes explain workflow rules
that are not expressed by schema alone. For example, fresh closeouts require a
report and explicit transcript assertions even though their historical receipt
replays remain parseable without them. Help never grants execution authority.

Ordinary help pages stay small; full schema retrieval is explicit and can be
large. The response has one text content block and no duplicate structured
payload. No schema rules, annotations, defaults, or long patterns are removed
from an explicitly requested schema.

Rejected inputs receive at most three field repairs, a count of additional invalid
fields, and short navigation instructions. Repairs use only reviewed field/error
names, static guidance, and server-owned constraints. Caller values, unknown
field names, and upstream error prose remain withheld. For example, misplaced
`complete_work` actor fields are redirected to `checkpoint.source_client` and
`checkpoint.source_session_id`; a missing checkpoint names its required fields
and points to `help({"topic":"complete_work checkpoint"})`.

The same behavior applies to local validation and API 422 input rejections over
HTTP and stdio. Conflict and uncertain-outcome handling stays unchanged; a help
link is not permission to change an operation UUID or any frozen retry argument.

`claim_work` and `claim_and_recall` accept `force=true` to replace an active
lease when compaction loses its token. Confirm that no other active session is
working on the item before using a new claim request ID. Both usage pages and
the `force` field page explain that the previous token is invalidated and that
force does not bypass eligibility or human gates. Ordinary token recovery still
uses an exact replay when the original claim arguments are available.
