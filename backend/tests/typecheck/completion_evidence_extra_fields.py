"""Static-only fixtures: completion-evidence models must remain closed to extra fields."""

from typing import cast
from uuid import UUID

from mnemonic_api.schemas import (
    ArtifactReferenceInput,
    CommandVerificationInput,
    CompletionCheckpointPointer,
    CompletionEvidenceEpisodeRead,
    CompletionEvidenceInput,
    CompletionEvidencePage,
    CompletionEvidencePayloadRead,
    ObservationVerificationInput,
)

WORK_ITEM_ID = UUID("20000000-0000-0000-0000-000000000001")


CommandVerificationInput(
    verification_type="command",
    name="Backend typecheck",
    outcome="passed",
    summary="The focused check passed.",
    command="uv run ty check src",
    exit_code=0,
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

ObservationVerificationInput(
    verification_type="observation",
    name="Backend review",
    outcome="passed",
    summary="The completion evidence was reviewed.",
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

ArtifactReferenceInput(
    artifact_type="commit",
    label="Reviewed commit",
    reference="abcdef1",
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

CompletionEvidenceInput(
    verification_results=[],
    artifact_references=[],
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

CompletionEvidencePayloadRead(
    verification_results=[],
    artifact_references=[],
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

CompletionEvidenceEpisodeRead(
    completion_event_id="1",
    completion_checkpoint=cast(CompletionCheckpointPointer, object()),
    verification_results=[],
    artifact_references=[],
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)

CompletionEvidencePage(
    work_item_id=WORK_ITEM_ID,
    work_version=1,
    lifecycle_status="done",
    is_duplicate=False,
    canonical_work_item_id=WORK_ITEM_ID,
    current_completion_checkpoint_id=None,
    as_of_completion_event_id=None,
    items=[],
    total=0,
    structured_completion_total=0,
    limit=10,
    next_cursor=None,
    unexpected_completion_evidence_field=True,  # ty: ignore[unknown-argument]
)
