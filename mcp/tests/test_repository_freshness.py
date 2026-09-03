"""Phase 10 repository-scope transport and canonicality contract."""

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from mnemonic_mcp.models import (
    CheckpointInput,
    CheckpointPointer,
    CheckpointRead,
    WorkContext,
)
from mnemonic_mcp.server import _checkpoint_matches_request

MCP_SOURCE = Path(__file__).resolve().parents[1] / "src" / "mnemonic_mcp"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCOPE_CORPUS = json.loads(
    (
        REPOSITORY_ROOT
        / "tests"
        / "fixtures"
        / "repository-freshness-scope-v1.json"
    ).read_text(encoding="utf-8")
)


def generated_scope(case: dict[str, object]) -> list[str]:
    count = int(case["count"])
    entry_bytes = int(case["entry_bytes"])
    prefix_width = int(case["prefix_width"])
    fill = str(case["fill"])
    extra_bytes_on_first = int(case["extra_bytes_on_first"])
    paths = []
    for index in range(count):
        prefix = f"{index:0{prefix_width}d}"
        target_bytes = entry_bytes + (extra_bytes_on_first if index == 0 else 0)
        paths.append(prefix + fill * (target_bytes - len(prefix)))
    return paths


def checkpoint_input(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "prompt": "Inspect the scoped implementation.",
        "source_client": "claude-code",
        "source_session_id": "phase-10-session",
    }
    value.update(overrides)
    return value


def test_omitted_and_explicit_empty_scope_have_one_sparse_canonical_form():
    omitted = CheckpointInput.model_validate(checkpoint_input())
    explicit = CheckpointInput.model_validate(checkpoint_input(affected_paths=[]))

    assert omitted.affected_paths == []
    assert explicit.affected_paths == []
    assert omitted.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "affected_paths" not in omitted.model_dump(mode="json")


def test_nonempty_scope_preserves_exact_order_case_and_binds_coherence(checkpoint):
    paths = ["src/Mnemonic.py", "tests/**", "-generated"]
    request = CheckpointInput.model_validate(
        checkpoint_input(verified_against="A832BC1", affected_paths=paths)
    )
    response = CheckpointRead.model_validate(
        {
            **checkpoint,
            **request.model_dump(mode="json"),
            "verified_against": "a832bc1",
        }
    )

    assert request.model_dump(mode="json")["affected_paths"] == paths
    assert response.model_dump(mode="json")["affected_paths"] == paths
    assert _checkpoint_matches_request(
        response,
        request,
        response.work_item_id,
        response.kind,
    )
    reordered = CheckpointInput.model_validate(
        checkpoint_input(verified_against="A832BC1", affected_paths=list(reversed(paths)))
    )
    assert not _checkpoint_matches_request(
        response,
        reordered,
        response.work_item_id,
        response.kind,
    )


def test_full_response_accepts_absence_but_rejects_explicit_empty(checkpoint):
    historical = CheckpointRead.model_validate(checkpoint)
    assert historical.affected_paths == []
    assert historical.model_dump(mode="json") == checkpoint

    with pytest.raises(ValidationError, match="noncanonical"):
        CheckpointRead.model_validate({**checkpoint, "affected_paths": []})


def test_nested_context_rejects_explicit_empty_checkpoint_response(work_context):
    malformed = deepcopy(work_context)
    malformed["initial_checkpoint"]["affected_paths"] = []

    with pytest.raises(ValidationError, match="noncanonical"):
        WorkContext.model_validate(malformed)


def test_checkpoint_pointer_remains_scope_free(checkpoint):
    pointer_payload = {
        name: value
        for name, value in checkpoint.items()
        if name not in {"prompt", "source_metadata", "source_session_url"}
    }
    pointer = CheckpointPointer.model_validate(
        {**pointer_payload, "affected_paths": ["src/**"]}
    )

    assert "affected_paths" not in pointer.model_dump(mode="json")
    assert "affected_paths" not in CheckpointPointer.model_json_schema()["properties"]


def test_scope_schema_exposes_entry_grammar_and_bounds():
    scope = CheckpointInput.model_json_schema()["properties"]["affected_paths"]
    assert scope["maxItems"] == 64
    assert scope["items"] == {
        "maxLength": 512,
        "minLength": 1,
        "pattern": "^[A-Za-z0-9._@+=,~*/-]+$",
        "type": "string",
    }


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "src/**",
        "tests/test_*.py",
        "*",
        "**",
        "-leading",
        "a*b*c",
        "A-Z/0_9.@+=,~-",
    ],
)
def test_valid_scope_grammar(path):
    model = CheckpointInput.model_validate(
        checkpoint_input(verified_against="abcdef0", affected_paths=[path])
    )
    assert model.affected_paths == [path]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute",
        "trailing/",
        "repeated//component",
        ".",
        "..",
        "src/./file",
        "src/../file",
        "C:/drive",
        "\\\\server\\share",
        "has space",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "question?",
        "bracket[a]",
        "brace{a}",
        "quote'",
        'quote"',
        "dollar$",
        "back`tick",
        ":(glob)src/**",
        "!excluded",
        "^excluded",
        "a**b",
        "***",
        "nul\x00byte",
        "line\nbreak",
    ],
)
def test_invalid_scope_grammar(path):
    with pytest.raises(ValidationError):
        CheckpointInput.model_validate(
            checkpoint_input(verified_against="abcdef0", affected_paths=[path])
        )


def test_scope_bounds_and_exact_duplicates():
    valid_512 = "a" * 512
    assert CheckpointInput.model_validate(
        checkpoint_input(verified_against="abcdef0", affected_paths=[valid_512])
    ).affected_paths == [valid_512]

    invalid_scopes = [
        ["a" * 513],
        ["src/**", "src/**"],
        [f"p{index}" for index in range(65)],
        [f"{index:02d}" + "a" * 510 for index in range(33)],
    ]
    for scope in invalid_scopes:
        with pytest.raises(ValidationError):
            CheckpointInput.model_validate(
                checkpoint_input(verified_against="abcdef0", affected_paths=scope)
            )

    exactly_16_kib = [f"{index:02d}" + "a" * 510 for index in range(32)]
    assert len(CheckpointInput.model_validate(
        checkpoint_input(verified_against="abcdef0", affected_paths=exactly_16_kib)
    ).affected_paths) == 32


def test_nonempty_scope_requires_declared_commit(checkpoint):
    with pytest.raises(ValidationError, match="verified_against"):
        CheckpointInput.model_validate(checkpoint_input(affected_paths=["src/**"]))
    with pytest.raises(ValidationError, match="verified_against"):
        CheckpointRead.model_validate({**checkpoint, "affected_paths": ["src/**"]})


def test_shared_repository_scope_corpus_matches_mcp_validator():
    assert SCOPE_CORPUS["version"] == "repository-freshness-scope-v1"
    base = checkpoint_input(verified_against="abcdef1")
    for path in SCOPE_CORPUS["valid_paths"]:
        parsed = CheckpointInput.model_validate({**base, "affected_paths": [path]})
        assert parsed.affected_paths == [path]
    for path in SCOPE_CORPUS["invalid_paths"]:
        with pytest.raises(ValidationError):
            CheckpointInput.model_validate({**base, "affected_paths": [path]})

    for case in SCOPE_CORPUS["generated_scopes"]:
        paths = generated_scope(case)
        assert sum(len(path.encode("ascii")) for path in paths) == int(
            case["expected_total_bytes"]
        )
        if case["valid"]:
            assert CheckpointInput.model_validate(
                {**base, "affected_paths": paths}
            ).affected_paths == paths
        else:
            with pytest.raises(ValidationError):
                CheckpointInput.model_validate({**base, "affected_paths": paths})
    for case in SCOPE_CORPUS["literal_scopes"]:
        with pytest.raises(ValidationError):
            CheckpointInput.model_validate(
                {**base, "affected_paths": case["paths"]}
            )

    supported = set(SCOPE_CORPUS["component_characters"])
    for codepoint in range(128):
        character = chr(codepoint)
        path = f"a{character}b"
        if character in supported or character == "/":
            assert CheckpointInput.model_validate(
                {**base, "affected_paths": [path]}
            ).affected_paths == [path]
        else:
            with pytest.raises(ValidationError):
                CheckpointInput.model_validate({**base, "affected_paths": [path]})

    with pytest.raises(ValidationError, match="verified_against"):
        CheckpointInput.model_validate(
            checkpoint_input(
                affected_paths=[SCOPE_CORPUS["requires_baseline_path"]]
            )
        )


def test_mcp_scope_transport_does_not_gain_local_repository_dependencies():
    forbidden_roots = {"dulwich", "git", "gitpython", "pathlib", "subprocess"}
    for filename in ("api.py", "models.py", "server.py"):
        tree = ast.parse((MCP_SOURCE / filename).read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert imported_roots.isdisjoint(forbidden_roots), filename
