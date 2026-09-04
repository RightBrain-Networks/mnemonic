"""Deterministic OpenAPI snapshot shared with strict downstream consumers."""

import json
import sys
from pathlib import Path
from typing import Any

from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "docs" / "openapi.json"

MCP_COMPONENT_OVERRIDES = {
    "CheckpointPage": "Page_CheckpointRead_",
    "Project": "ProjectRead",
    "ProjectPage": "Page_ProjectRead_",
    "RelationshipPage": "Page_AdjacentRelationshipRead_",
    "WorkCompletion": "WorkCompletionRead",
    "WorkDeletionResult": "WorkDeletionRead",
    "WorkPage": "Page_Union_WorkSearchHit__HierarchySummary__",
}

FRONTEND_PROPERTY_SETS = {
    "frontend/lib/completion-evidence.ts:decodeArtifactReference": (
        "ArtifactReferenceRead"
    ),
    "frontend/lib/completion-evidence.ts:decodeCheckpointPointer": "CheckpointPointer",
    "frontend/lib/completion-evidence.ts:decodeCompletionEvidencePage": (
        "CompletionEvidencePage"
    ),
    "frontend/lib/completion-evidence.ts:decodeCompletionEvidencePage:item": (
        "CompletionEvidenceEpisodeRead"
    ),
    "frontend/lib/completion-evidence.ts:decodeCompletionEvidencePayload": (
        "CompletionEvidencePayloadRead"
    ),
    "frontend/lib/completion-evidence.ts:decodeVerificationResult:command": (
        "CommandVerificationRead"
    ),
    "frontend/lib/completion-evidence.ts:decodeVerificationResult:observation": (
        "ObservationVerificationRead"
    ),
    "frontend/lib/human-gates.ts:GATE_FIELDS": "HumanGateRead",
    "frontend/lib/human-gates.ts:decodeAncestor": "WorkIdentityPointer",
    "frontend/lib/human-gates.ts:decodeCheckpointPointer": "CheckpointPointer",
    "frontend/lib/human-gates.ts:decodeCursorPage:attention": "HumanAttentionPage",
    "frontend/lib/human-gates.ts:decodeCursorPage:gates": "HumanGatePage",
    "frontend/lib/human-gates.ts:decodeHumanAttentionPage:item": "HumanAttentionItem",
    "frontend/lib/human-gates.ts:decodeLease": "LeasePublic",
    "frontend/lib/human-gates.ts:decodeReadiness": "Readiness",
    "frontend/lib/human-gates.ts:decodeRevision": "HumanGateContextRevision",
    "frontend/lib/human-gates.ts:decodeWorkSummary": "WorkSummary",
    "frontend/lib/mutation-responses.ts:decodeCheckpoint": "CheckpointRead",
    "frontend/lib/mutation-responses.ts:decodeMutationResult:complete_work": (
        "WorkCompletionRead"
    ),
    "frontend/lib/mutation-responses.ts:decodeRelationship": "RelationshipEdgeRead",
    "frontend/lib/work-events.ts:EVENT_FIELDS": "WorkEventRead",
    "frontend/lib/work-events.ts:decodeWorkEventPage": "WorkEventPage",
    "frontend/lib/duplicate-handling.ts:decodeCanonicalWorkProjection": (
        "CanonicalWorkProjection"
    ),
    "frontend/lib/duplicate-handling.ts:decodeMergeReviewRevision": "MergeReviewRevision",
    "frontend/lib/duplicate-handling.ts:decodeWorkContext": "WorkContext",
    "frontend/lib/duplicate-handling.ts:decodeWorkItemDetail": "WorkItemDetailRead",
    "frontend/lib/duplicate-handling.ts:decodeWorkSearchPage:item": "WorkSearchHit",
    "frontend/lib/duplicate-handling.ts:decodeWorkSearchPage": (
        "Page_Union_WorkSearchHit__HierarchySummary__"
    ),
    "frontend/lib/duplicate-suggestions.ts:decodeDuplicateCandidateSummary": (
        "DuplicateCandidateSummary"
    ),
    "frontend/lib/duplicate-suggestions.ts:decodeDuplicateSuggestion:item": (
        "DuplicateSuggestion"
    ),
    "frontend/lib/duplicate-suggestions.ts:decodeDuplicateSuggestionPage": (
        "DuplicateSuggestionPage"
    ),
    "frontend/lib/hierarchy-presentation.ts:decodeHierarchyPage": (
        "Page_Union_WorkSearchHit__HierarchySummary__"
    ),
    "frontend/lib/hierarchy-presentation.ts:decodeHierarchyPage:item": "HierarchySummary",
    "frontend/lib/hierarchy-presentation.ts:decodeHierarchyPresentation": (
        "HierarchyPresentation"
    ),
}

CONSUMER_METADATA = {
    "mcp": {
        "default_component_rule": "CanonicalResponse class name equals component name",
        "component_overrides": MCP_COMPONENT_OVERRIDES,
        "compare": ["properties", "required"],
    },
    "frontend": {
        "property_sets": FRONTEND_PROPERTY_SETS,
        "compare": ["properties", "required"],
    },
}


def openapi_snapshot() -> dict[str, Any]:
    """Generate the public schema without connecting to PostgreSQL."""
    settings = Settings(
        database_url="postgresql://openapi.invalid/mnemonic",
        api_key="openapi-snapshot-key-is-long-enough",
    )
    engine = build_engine(settings)
    app = create_app(settings, engine=engine)
    try:
        document = app.openapi()
    finally:
        engine.dispose()
    document["x-mnemonic-schema-consumers"] = CONSUMER_METADATA

    components = document["components"]["schemas"]
    targets = {
        *MCP_COMPONENT_OVERRIDES.values(),
        *FRONTEND_PROPERTY_SETS.values(),
    }
    missing = sorted(targets - components.keys())
    assert not missing, f"Consumer metadata names missing components: {missing}"
    return document


def rendered_openapi_snapshot() -> str:
    return json.dumps(
        openapi_snapshot(),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def test_committed_openapi_snapshot_is_current() -> None:
    assert SNAPSHOT_PATH.read_text(encoding="utf-8") == rendered_openapi_snapshot(), (
        "docs/openapi.json is stale; regenerate it with "
        "'cd backend && uv run python tests/test_openapi_snapshot.py --write'."
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit("usage: python tests/test_openapi_snapshot.py --write")
    SNAPSHOT_PATH.write_text(rendered_openapi_snapshot(), encoding="utf-8")
    print(SNAPSHOT_PATH.relative_to(PROJECT_ROOT))
