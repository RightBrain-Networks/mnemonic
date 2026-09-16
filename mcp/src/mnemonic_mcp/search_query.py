"""Resolve the two public spellings of a dedicated content-search query."""

from mcp.server.fastmcp.exceptions import ToolError
from pydantic.experimental.missing_sentinel import MISSING


def content_search_query(query: str | MISSING, q: str | MISSING) -> str:
    if query is MISSING and q is MISSING:
        raise ToolError(
            "Mnemonic rejected the input. Check: query (missing). "
            "Supply query or its q alias."
        )
    if query is not MISSING and q is not MISSING:
        raise ToolError(
            "Mnemonic rejected the input. Supply exactly one of query or q, not both."
        )
    if query is not MISSING:
        return query
    assert q is not MISSING
    return q
