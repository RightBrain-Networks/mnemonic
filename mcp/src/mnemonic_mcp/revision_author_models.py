"""Revision authorship and independently observed current session work."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator


class RevisionAuthorWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_item_id: UUID
    title: str
    holder_client: str
    purpose: Literal["implementation", "code_review"]
    expires_at: datetime


class RevisionAuthorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str
    author_kind: Literal["human", "agent"]
    agent_session_id: str | None
    model_id: str | None
    authored_at: datetime
    work_observed_at: datetime
    active_work_items: list[RevisionAuthorWork]
    active_work_items_truncated: StrictBool

    @model_validator(mode="after")
    def coherent_work(self) -> Self:
        if len(self.active_work_items) > 10 or len({
            item.work_item_id for item in self.active_work_items
        }) != len(self.active_work_items):
            raise ValueError("Author work context must be bounded and unique")
        if any(item.expires_at <= self.work_observed_at for item in self.active_work_items):
            raise ValueError("Author work lease was not active at observation")
        if self.author_kind == "human" and (
            self.agent_session_id or self.model_id or self.active_work_items
        ):
            raise ValueError("Human author cannot have agent session holdings")
        return self
