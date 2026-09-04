"""Sanitize request validation failures before they leave the process.

Pydantic's raw errors quote the offending input and name every key the caller
sent, including free-form metadata keys. The public form keeps three things:
the error family (or ``validation_error``), a location rebuilt from a fixed
vocabulary of known field names with anything else replaced by ``field``, and
a fixed message per family.
"""

from collections.abc import Iterable, Mapping

PUBLIC_LOCATION_REPLACEMENT = "field"
PUBLIC_LOCATION_SEGMENTS = frozenset(
    """
    body query path header cookie project_id work_item_id relationship_id
    name description slug q semantic status tag source_client source_session_id
    view sort limit offset min_priority parent_work_item_id direction type order
    event_type recent_limit recent_event_limit title summary priority expected_version
    initial_checkpoint initial_relationships checkpoint kind prompt source_model
    source_session_url repository_branch verified_against affected_paths tags source_metadata
    migration_origin legacy_record_id relationship_type source_work_item_id
    target_work_item_id other_work_item_id context_checkpoint_id created_by_client
    created_by_session_id created_by_model holder_client holder_session_id
    claim_request_id client_operation_id lease_token actor actor_client actor_session_id
    actor_model metadata gate_id gate_type question resolution
    requested_by_client requested_by_session_id requested_by_model
    resolved_by_client resolved_by_session_id resolved_by_model
    reviewed_context_revision current_context_revision
    requested_work_version requested_context_checkpoint_id
    requested_relationship_event_count resolved_context_revision
    resolved_work_version resolved_context_checkpoint_id
    resolved_relationship_event_count relationship_event_count work_version cursor
    focus_gate_id recall_pointer_template initial_prompt exclude_work_item_id
    completion_evidence verification_results artifact_references verification_type
    outcome command exit_code observed_at observed_at_commit artifact_type label reference
    """.split()
)

PUBLIC_ERROR_MESSAGES = {
    "assertion_error": "Value is invalid.",
    "bool_parsing": "Input should be a valid boolean.",
    "bool_type": "Input should be a valid boolean.",
    "datetime_parsing": "Input should be a valid datetime.",
    "datetime_type": "Input should be a valid datetime.",
    "dict_type": "Input should be an object.",
    "extra_forbidden": "Extra inputs are not permitted.",
    "finite_number": "Input should be a finite number.",
    "float_parsing": "Input should be a valid number.",
    "float_type": "Input should be a valid number.",
    "greater_than_equal": "Input is below the allowed minimum.",
    "int_parsing": "Input should be a valid integer.",
    "int_type": "Input should be a valid integer.",
    "json_invalid": "Request body contains invalid JSON.",
    "less_than_equal": "Input exceeds the allowed maximum.",
    "list_type": "Input should be a list.",
    "literal_error": "Input has an unsupported value.",
    "missing": "Field required.",
    "model_attributes_type": "Input should be an object.",
    "string_pattern_mismatch": "String format is invalid.",
    "string_too_long": "String is too long.",
    "string_too_short": "String is too short.",
    "string_type": "Input should be a valid string.",
    "too_long": "Collection contains too many items.",
    "too_short": "Collection contains too few items.",
    "uuid_parsing": "Input should be a valid UUID.",
    "uuid_type": "Input should be a valid UUID.",
    "value_error": "Value is invalid.",
}
_UNKNOWN_TYPE = "validation_error"
_UNKNOWN_MESSAGE = "Request validation failed."


def public_validation_errors(
    errors: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    return [_public_error(error) for error in errors]


def _public_error(error: Mapping[str, object]) -> dict[str, object]:
    error_type = _public_type(error.get("type"))
    return {
        "type": error_type,
        "loc": _public_location(error.get("loc")),
        "msg": PUBLIC_ERROR_MESSAGES.get(error_type, _UNKNOWN_MESSAGE),
    }


def _public_type(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in PUBLIC_ERROR_MESSAGES else _UNKNOWN_TYPE


def _public_location(raw: object) -> list[str | int]:
    parts = raw if isinstance(raw, (list, tuple)) else ()
    return [_public_segment(part) for part in parts]


def _public_segment(part: object) -> str | int:
    if isinstance(part, int) and not isinstance(part, bool):
        return part  # a list index is a position, not a caller-chosen name
    if isinstance(part, str) and part in PUBLIC_LOCATION_SEGMENTS:
        return part
    return PUBLIC_LOCATION_REPLACEMENT
