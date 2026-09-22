"""Sanitized input rejections, distinct from failed or uncertain execution."""

from mcp.server.fastmcp.exceptions import ToolError

type ValidationDetails = tuple[dict[str, set[str]], set[str]]


class InputValidationError(ToolError):
    def __init__(self, message: str, *, details: ValidationDetails | None = None) -> None:
        super().__init__(message)
        self.details = details
