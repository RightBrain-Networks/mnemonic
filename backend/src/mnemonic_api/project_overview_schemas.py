"""Complete, metadata-only project aggregates for agent orientation."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt

from mnemonic_api.schemas import (
    APIModel,
    LeaseMinutes,
    PositiveRevision,
    ReviewThreshold,
    Timestamps,
)

Count = Annotated[StrictInt, Field(ge=0)]


class ProjectSettingsValues(APIModel):
    project_id: UUID
    revision: PositiveRevision
    lease_default_minutes: LeaseMinutes
    lease_minimum_minutes: LeaseMinutes
    lease_maximum_minutes: LeaseMinutes
    code_review_required_min_priority: ReviewThreshold
    code_review_optional_min_priority: ReviewThreshold
    allow_remediation_code_reviews: StrictBool


class ProjectStateCounts(APIModel):
    pending: Count
    active: Count
    to_review: Count = Field(alias="to-review")
    dropped: Count
    blocked: Count
    waiting: Count
    duplicate: Count
    deadlocked: Count
    deferred: Count
    done: Count
    wont_do: Count = Field(alias="wont-do")
    promoted: Count


class ProjectActiveWorkItem(Timestamps):
    id: UUID
    title: str
    session_id: str
    tags: list[str]


class ProjectWorkItemsSummary(APIModel):
    total: Count
    counts_by_state: ProjectStateCounts
    active: list[ProjectActiveWorkItem]
    counts_by_tag: dict[str, ProjectStateCounts]


class ProjectNeedsAttentionSummary(APIModel):
    open_count: Count


class ProjectKnowledgeDomainSummary(APIModel):
    id: UUID
    name: str
    prefix: str
    decision_document_count: Count


class ProjectOverviewRead(APIModel):
    project_id: UUID
    settings: ProjectSettingsValues
    work_items_summary: ProjectWorkItemsSummary
    needs_attention: ProjectNeedsAttentionSummary
    knowledge_domains: list[ProjectKnowledgeDomainSummary]
