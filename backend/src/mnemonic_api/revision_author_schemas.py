"""Immutable revision authorship with a separately timed view of session work."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from mnemonic_api.schemas import APIModel


class RevisionAuthorWork(APIModel):
    work_item_id: UUID
    title: str
    holder_client: str
    purpose: Literal["implementation", "code_review"]
    expires_at: datetime


class RevisionAuthorContext(APIModel):
    reference: str
    author_kind: Literal["human", "agent"]
    agent_session_id: str | None
    model_id: str | None
    authored_at: datetime
    work_observed_at: datetime
    active_work_items: list[RevisionAuthorWork]
    active_work_items_truncated: bool
