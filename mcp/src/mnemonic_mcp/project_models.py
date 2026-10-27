"""Complete project aggregates, independently validated at the MCP boundary."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr, model_validator

from .phase12_models import Phase12Wire, ProjectSettingsValues, UTCDateTime
from .tag_models import TagName

Count = Annotated[StrictInt, Field(ge=0)]


class ProjectStateCounts(Phase12Wire):
    model_config = ConfigDict(serialize_by_alias=True)

    deadlocked: Count
    pending: Count
    active: Count
    to_review: Count = Field(alias="to-review")
    dropped: Count
    blocked: Count
    waiting: Count
    duplicate: Count
    deferred: Count
    done: Count
    wont_do: Count = Field(alias="wont-do")
    promoted: Count


class ProjectActiveWorkItem(Phase12Wire):
    id: UUID
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    created_at: UTCDateTime
    updated_at: UTCDateTime
    session_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    tags: list[TagName]

    @model_validator(mode="after")
    def distinct_tags(self) -> Self:
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Active work tags must be distinct.")
        return self


class ProjectWorkItemsSummary(Phase12Wire):
    total: Count
    counts_by_state: ProjectStateCounts
    active: list[ProjectActiveWorkItem]
    counts_by_tag: dict[TagName, ProjectStateCounts]

    @model_validator(mode="after")
    def complete_counts(self) -> Self:
        counts = self.counts_by_state.model_dump()
        if sum(counts.values()) != self.total:
            raise ValueError("State counts must cover every work item exactly once.")
        if self.counts_by_state.active != len(self.active):
            raise ValueError("Active work details must cover every active work item.")
        if len({item.id for item in self.active}) != len(self.active):
            raise ValueError("Active work identities must be distinct.")
        for tag, tag_counts in self.counts_by_tag.items():
            if any(value > counts[state] for state, value in tag_counts.model_dump().items()):
                raise ValueError("Tag counts cannot exceed project state counts.")
            if tag_counts.active != sum(tag in item.tags for item in self.active):
                raise ValueError("Active tag counts must match active work details.")
        if any(tag not in self.counts_by_tag for item in self.active for tag in item.tags):
            raise ValueError("Active work tags must belong to the project catalog.")
        return self


class ProjectNeedsAttentionSummary(Phase12Wire):
    open_count: Count


class ProjectKnowledgeDomainSummary(Phase12Wire):
    id: UUID
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    prefix: Annotated[StrictStr, Field(pattern=r"^[A-Z]{1,5}$")]
    decision_document_count: Count


class ProjectOverviewRead(Phase12Wire):
    project_id: UUID
    settings: ProjectSettingsValues
    work_items_summary: ProjectWorkItemsSummary
    needs_attention: ProjectNeedsAttentionSummary
    knowledge_domains: list[ProjectKnowledgeDomainSummary]

    @model_validator(mode="after")
    def coherent_project(self) -> Self:
        if self.settings.project_id != self.project_id:
            raise ValueError("Settings must belong to the requested project.")
        for values in (
            [domain.id for domain in self.knowledge_domains],
            [domain.prefix for domain in self.knowledge_domains],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Knowledge domain identities and prefixes must be distinct.")
        return self
