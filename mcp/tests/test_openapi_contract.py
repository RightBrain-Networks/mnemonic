"""Pin strict MCP response models to the committed API schema."""

import inspect
import json
from pathlib import Path

import mnemonic_mcp.models as response_models

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_strict_response_models_match_openapi_properties_and_required_sets():
    document = json.loads(
        (REPOSITORY_ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")
    )
    components = document["components"]["schemas"]
    metadata = document["x-mnemonic-schema-consumers"]["mcp"]
    assert metadata["compare"] == ["properties", "required"]
    overrides = metadata["component_overrides"]

    model_classes = {
        name: model
        for name, model in inspect.getmembers(response_models, inspect.isclass)
        if model is not response_models.CanonicalResponse
        and model.__module__ == response_models.__name__
        and issubclass(model, response_models.CanonicalResponse)
    }
    assert model_classes

    for name, model in sorted(model_classes.items()):
        component_name = overrides.get(name, name)
        assert component_name in components, (name, component_name)
        model_schema = model.model_json_schema()
        component_schema = components[component_name]
        assert set(model_schema.get("properties", {})) == set(
            component_schema.get("properties", {})
        ), name
        assert set(model_schema.get("required", [])) == set(
            component_schema.get("required", [])
        ), name


def test_duplicate_suggestion_request_matches_openapi_shape():
    document = json.loads(
        (REPOSITORY_ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")
    )
    component = document["components"]["schemas"]["DuplicateSuggestionRequest"]
    model_schema = response_models.DuplicateSuggestionRequest.model_json_schema()

    assert set(model_schema["properties"]) == set(component["properties"])
    assert set(model_schema["required"]) == set(component["required"])
    for name in model_schema["properties"]:
        assert model_schema["properties"][name] == component["properties"][name]


def test_phase12_models_match_published_openapi_shape():
    """New focused models participate in the same independent shape inventory."""
    from mnemonic_mcp import phase12_models

    document = json.loads((REPOSITORY_ROOT / "docs/openapi.json").read_text())
    components = document["components"]["schemas"]
    for name in (
        "JobCompletionReportInput", "JobCompletionReportRead", "JobCompletionReportDetailRead",
        "HumanDismissalRead", "SourceWorkState", "JobCompletionReportEnvelope",
        "JobCompletionReportDetailEnvelope", "JobCompletionReportPage", "ProjectActivityRead",
        "ProjectActivityPage", "ProjectSettingsRead",
    ):
        schema = getattr(phase12_models, name).model_json_schema()
        assert name in components, name
        assert set(schema.get("properties", {})) == set(components[name].get("properties", {})), name
        assert set(schema.get("required", [])) == set(components[name].get("required", [])), name
