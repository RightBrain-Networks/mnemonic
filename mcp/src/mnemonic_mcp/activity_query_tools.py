"""Safe bounded activity queries; independent of the legacy contiguous feed."""

from typing import Annotated, Literal, cast
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from .activity_query_cursors import query_positions_match
from .activity_query_models import (
    ActivityFacets,
    ActivityFilters,
    ActivityLimit,
    ActivityToken,
    ActivityView,
    QueryActivityPage,
    QueryActivityRequest,
    activity_query_matches,
)
from .api import MnemonicAPI, TransportEffect
from .input_errors import InputValidationError
from .response_validation import response_matches
from .search_exploration import SearchDate


def register_activity_query_tool(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    async def query_activity(
        project_id: UUID,
        since: Annotated[SearchDate | None, Field(description=(
            "Inclusive source event-time bound; include a timezone. Cannot accompany after/start."
        ))] = None,
        until: Annotated[SearchDate | None, Field(description=(
            "Exclusive source event-time bound; include a timezone. Cannot accompany after/start."
        ))] = None,
        after: Annotated[ActivityToken | None, Field(description=(
            "Exact polling checkpoint; preserve facets/filters/view. Do not use a legacy cursor."
        ))] = None,
        cursor: Annotated[ActivityToken | None, Field(description=(
            "Continuation: repeat original bounds/after, facets, filters and view; limit may change."
        ))] = None,
        start: Literal["now"] | None = None,
        facets: ActivityFacets | None = None,
        filters: ActivityFilters | None = None,
        view: ActivityView = "resources",
        limit: ActivityLimit = 20,
    ) -> QueryActivityPage:
        """Summarize bounded project changes, grouped by resource by default. Use timezone-aware since/until for [since,until), or after for a saved polling checkpoint; start=now deliberately skips history. Filter facets and current work status/tags or decision domains/states. Read counts and coverage; follow next_cursor with unchanged query scope and save checkpoint only after accepting all pages. Selected scalar deltas and deterministic summaries exclude bodies. Historical imports are partial; current filters are reevaluated each page. This safe read grants no authority. Use help(topic='query_activity usage') and its complete-guidance link for examples, polling, filters and limitations."""
        fields: dict[str, object] = {
            "since": since, "until": until, "after": after, "cursor": cursor, "start": start,
            "view": view, "limit": limit,
        }
        if facets is not None:
            fields["facets"] = facets
        if filters is not None:
            fields["filters"] = filters
        try:
            request = QueryActivityRequest.model_validate(fields)
        except ValidationError:
            raise InputValidationError(
                "Invalid activity bounds, position, or filter scope. "
                "Use help(topic='query_activity usage') and field help."
            ) from None
        return cast(QueryActivityPage, await api.request(
            "POST", f"projects/{project_id}/activity/query",
            payload=request.model_dump(mode="json", exclude_none=True),
            response_model=QueryActivityPage, effect=TransportEffect.SAFE_READ,
            expected_status_code=200, strict_wire_response=True,
            bounded_identity_response=True, response_max_bytes=32768,
            response_validator=response_matches(
                QueryActivityPage, lambda page: (
                    activity_query_matches(page, project_id, request)
                    and query_positions_match(page, request)
                ),
            ),
        ))
