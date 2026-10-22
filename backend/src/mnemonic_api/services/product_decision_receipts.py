"""PDR receipt coherence retains sparse metadata intents without inferred edits."""

from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.schemas import HumanGateRead, HumanGateRequestCreate


def requested_metadata_matches(request: HumanGateRequestCreate, result: HumanGateRead) -> bool:
    if request.decision_metadata is MISSING:
        return True
    if result.decision is None:
        return False
    actual = result.decision.metadata.model_dump(mode="json")
    requested = request.decision_metadata.model_dump(mode="json", exclude_unset=True)
    return all(_field_matches(key, value, actual, str(result.work_item_id))
               for key, value in requested.items())


def _field_matches(key: str, value: object, actual: dict, work_item_id: str) -> bool:
    if key == "related_work_item_ids" and isinstance(value, list):
        return set(actual[key]) == set(value) | {work_item_id}
    if key == "supersedes" and isinstance(value, list):
        detailed = {detail["decision_id"] for detail in actual["supersession_details"]}
        return set(actual[key]) == set(value) | detailed
    if key in {"related_decision_ids", "related_artifact_ids"} and isinstance(value, list):
        return set(actual[key]) == set(value)
    if isinstance(value, list):
        return sorted(actual[key], key=str) == sorted(value, key=str)
    return actual[key] == value
