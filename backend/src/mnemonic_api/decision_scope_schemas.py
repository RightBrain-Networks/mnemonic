"""Report citations can reuse an immutable implementation scope without rewriting it."""

from datetime import datetime
from uuid import UUID

from mnemonic_api.schemas import APIModel


class ScopeCompletionCitation(APIModel):
    report_id: UUID
    work_item_id: UUID
    actor_client: str
    actor_session_id: str | None
    actor_model: str | None
    created_at: datetime
