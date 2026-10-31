"""Verify automatic change reassignment using immutable successor ancestry."""

from uuid import UUID

from mcp.server.fastmcp.exceptions import ToolError

from .api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from .decision_change_models import DecisionChangeRead
from .knowledge_models import DecisionPut, DecisionPutResult


async def verify_put_successor(
    api: MnemonicAPI, project_id: UUID, intent: DecisionPut, result: DecisionPutResult,
) -> None:
    from .knowledge_tools import _read

    declaration = intent.change
    saved = result.decision.change
    if (not result.created or declaration is None or declaration.change_id is None
            or saved is None or saved.change_id == declaration.change_id):
        return
    # The write validator permits a changed ID only for substantive declarations.
    # Ancestry is immutable, so this remains valid for an old permanent receipt.
    current = saved.change_id
    visited: set[UUID] = set()
    try:
        while current is not None and current not in visited:
            if current == declaration.change_id:
                return
            visited.add(current)
            row = await _read(
                api, f"projects/{project_id}/knowledge/changes/{current}", DecisionChangeRead,
                lambda item, identity=current: (item.project_id, item.id) == (project_id, identity), {},
            )
            current = row.parent_change_id
    except ToolError:
        # A failed read cannot turn a potentially committed write into a rejection.
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None
    raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
