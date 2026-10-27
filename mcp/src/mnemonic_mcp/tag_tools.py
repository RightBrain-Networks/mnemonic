"""Read-only discovery of the human-managed project tag catalog."""

from typing import cast
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .api import MnemonicAPI, TransportEffect
from .response_validation import response_matches
from .tag_models import ProjectTagList


def register_tag_tools(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    async def list_tags(project_id: UUID) -> ProjectTagList:
        """Read every available project tag, including description, color, menu_order and system identity. Before creating work, query this catalog and read the descriptions to select appropriate tags. Supply selected normalized names in create_work.tags, or explicit null when none apply. Tags can only be created, edited, deleted or reordered by humans in dashboard Project settings > Tags; there are no MCP tag-management writes. Review episodes carry code-review in the dashboard; use list_code_reviews for agent discovery. Assigning code-review to work does not create a review or change lease purpose."""
        return cast(ProjectTagList, await api.request(
            "GET", f"projects/{project_id}/tags", response_model=ProjectTagList,
            effect=TransportEffect.SAFE_READ,
            response_validator=response_matches(
                ProjectTagList, lambda page: page.project_id == project_id,
            ),
        ))
