"""PDR questions retain exact retry intents and expose canonical decision provenance."""

import copy
import hashlib
import json

import httpx
import pytest
from conftest import OTHER_WORK_ID, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_tools import CONTENT, DECISION_UUID, DOMAIN_ID, arguments, decision, domain
from test_tools import adapter, protected_tool_arguments, structured

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME
from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.help_schema import resolve
from mnemonic_mcp.models import HumanGateRead


def metadata(**changes):
    return {
        "title": "Choose a rollout policy", "related_decision_ids": [],
        "related_work_item_ids": [WORK_ID], "related_artifact_ids": [],
        "depends_on": [], "supersedes": [], "supersession_details": [], **changes,
    }


def product_decision(content="Which rollout policy should this work use?", **changes):
    return {
        "document_id": DECISION_UUID, "domain_id": DOMAIN_ID,
        "decision_id": "PDR-00001", "reference": "PDR-00001-0", "revision": 0,
        "state": "proposed", "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": metadata(), **changes,
    }


async def test_question_metadata_is_sparse_and_exact_across_retry(settings, human_gate):
    edits = {
        "title": "Keep deploys manual", "related_decision_ids": ["ADR-00194"],
        "related_work_item_ids": [OTHER_WORK_ID], "related_artifact_ids": [],
        "depends_on": [{"decision_id": "ADR-00194", "revision": 2,
                        "rationale": "Manual approval is the current release constraint."}],
    }
    response = {
        **human_gate, "decision": product_decision(metadata=metadata(
            **{**edits, "related_work_item_ids": [OTHER_WORK_ID, WORK_ID]},
        )),
    }
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["decision_metadata"] == edits
        requests.append(request.content)
        return httpx.Response(201, json=response)

    arguments = {**protected_tool_arguments()["request_human_input"], "decision_metadata": edits}
    server = adapter(settings, handler)
    for _ in range(2):
        assert structured(await server.call_tool("request_human_input", arguments)) == response
    assert requests[0] == requests[1]


@pytest.mark.parametrize("edits", [{}, {"related_decision_ids": []}])
async def test_empty_metadata_and_explicit_clear_are_not_omitted(settings, human_gate, edits):
    response = {**human_gate, "decision": product_decision()}

    def handler(request):
        assert json.loads(request.content)["decision_metadata"] == edits
        return httpx.Response(201, json=response)

    result = await adapter(settings, handler).call_tool("request_human_input", {
        **protected_tool_arguments()["request_human_input"], "decision_metadata": edits,
    })
    assert structured(result) == response


async def test_metadata_response_accepts_canonical_deduplicated_links(settings, human_gate):
    response = {**human_gate, "decision": product_decision(metadata=metadata(
        related_decision_ids=["ADR-00194"], related_artifact_ids=[OTHER_WORK_ID],
    ))}
    result = await adapter(settings, lambda _: httpx.Response(201, json=response)).call_tool(
        "request_human_input", {
            **protected_tool_arguments()["request_human_input"], "decision_metadata": {
                "related_decision_ids": ["ADR-00194", "ADR-00194"],
                "related_artifact_ids": [OTHER_WORK_ID, OTHER_WORK_ID],
                "related_work_item_ids": [WORK_ID, WORK_ID],
            },
        },
    )
    assert structured(result) == response


async def test_historical_question_receipt_keeps_absent_metadata_and_decision(settings, human_gate):
    def handler(request):
        assert "decision_metadata" not in json.loads(request.content)
        return httpx.Response(201, json=human_gate)

    result = await adapter(settings, handler).call_tool(
        "request_human_input", protected_tool_arguments()["request_human_input"],
    )
    assert structured(result) == human_gate
    assert "decision" not in structured(result)


@pytest.mark.parametrize("edits", [
    None, {"title": None}, {"title": " "}, {"title": "a\x00b"},
    {"related_decision_ids": None}, {"related_decision_ids": ["ADR-00194-0"]},
    {"related_work_item_ids": ["not-a-uuid"]}, {"state": "accepted"},
    {"depends_on": [{"decision_id": "ADR-00194", "rationale": "No revision"}]},
    {"related_artifact_ids": [OTHER_WORK_ID] * 101},
])
async def test_invalid_product_metadata_never_reaches_api(settings, edits):
    def handler(_request):
        pytest.fail("Invalid PDR metadata must not reach the API")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("request_human_input", {
            **protected_tool_arguments()["request_human_input"], "decision_metadata": edits,
        })


@pytest.mark.parametrize("changes", [
    {"revision": 2, "reference": "PDR-00001-2"}, {"reference": "ADR-00001-0"},
    {"decision_id": "ADR-00001", "reference": "ADR-00001-0"},
    {"state": "accepted"}, {"sha256": "invalid"}, {"sha256": "a" * 64}, {"metadata": {}},
    {"metadata": metadata(related_work_item_ids=[])},
    {"metadata": metadata(title="Unexpected title")},
])
async def test_question_rejects_incoherent_product_decision_response(
    settings, human_gate, changes,
):
    response = {**human_gate, "decision": product_decision(**changes)}
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json=response)

    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await adapter(settings, handler).call_tool("request_human_input", {
            **protected_tool_arguments()["request_human_input"],
            "decision_metadata": {"title": "Choose a rollout policy"},
        })
    assert len(seen) == 1


def test_resolved_product_decision_pins_answer_revision(resolved_human_gate):
    response = {**resolved_human_gate, "decision": product_decision(
        content="## Question\n\n" + resolved_human_gate["question"]
        + "\n\n## Product decision\n\n" + resolved_human_gate["resolution"],
        revision=1, reference="PDR-00001-1", state="accepted",
    )}
    assert HumanGateRead.model_validate(response).decision.revision == 1
    response["decision"]["revision"] = 0
    response["decision"]["reference"] = "PDR-00001-0"
    with pytest.raises(ValueError, match="Product decision must match"):
        HumanGateRead.model_validate(response)


@pytest.mark.parametrize("origin_project, status, related_work", [
    (PROJECT_ID, "unresolved", [WORK_ID]),
    (OTHER_WORK_ID, "resolved", []),
])
async def test_decision_get_retains_human_question_identity(
    settings, human_gate, origin_project, status, related_work,
):
    response = decision(
        prefix="PDR", decision_id="PDR-00001", reference="PDR-00001-0",
        state="proposed", related_work_item_ids=related_work, human_gate={
            "gate_id": human_gate["id"], "work_item_id": WORK_ID,
            "work_project_id": origin_project,
            "status": status, "question_version": 1,
        }, content=CONTENT, total_lines=2, line_start=1, line_end=2,
    )
    result = await adapter(settings, lambda _: stream_json(response)).call_tool(
        "decision_get", {"project_id": PROJECT_ID, "decision_id": "PDR-00001-0"},
    )
    assert structured(result) == response


def test_resolved_historical_product_record_can_retain_moved_origin(resolved_human_gate):
    response = {**resolved_human_gate, "decision": product_decision(
        content="## Question\n\n" + resolved_human_gate["question"]
        + "\n\n## Product decision\n\n" + resolved_human_gate["resolution"],
        revision=1, reference="PDR-00001-1", state="accepted",
        metadata=metadata(related_work_item_ids=[]), originating_work_project_id=OTHER_WORK_ID,
    )}
    parsed = HumanGateRead.model_validate(response)
    assert str(parsed.decision.originating_work_project_id) == OTHER_WORK_ID
    assert parsed.decision.metadata.related_work_item_ids == []
    response["decision"]["originating_work_project_id"] = PROJECT_ID
    with pytest.raises(ValueError, match="resolved historical questions"):
        HumanGateRead.model_validate(response)


def test_unresolved_product_record_cannot_use_moved_origin_exception(human_gate):
    response = {**human_gate, "decision": product_decision(
        metadata=metadata(related_work_item_ids=[]), originating_work_project_id=OTHER_WORK_ID,
    )}
    with pytest.raises(ValueError, match="resolved historical questions"):
        HumanGateRead.model_validate(response)


async def test_domains_identify_designated_product_domain(settings):
    response = {"items": [domain(is_product_decision_domain=True)],
                "total": 1, "limit": 50, "offset": 0}
    result = await adapter(settings, lambda _: stream_json(response)).call_tool(
        "list_knowledge_domains", {"project_id": PROJECT_ID},
    )
    assert structured(result) == response


async def test_managed_pdr_put_rejection_is_definitive_and_does_not_retry(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(409, json={"detail": {
            "code": "decision_managed_by_human_gate", "message": "private database detail",
            "context": {},
        }})

    with pytest.raises(ToolError, match="only the human dashboard") as caught:
        await adapter(settings, handler).call_tool("decision_put", {
            **arguments(), "decision_id": "PDR-00001", "expected_revision": 0,
        })
    assert "request_human_input" in str(caught.value)
    assert "private database detail" not in str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME not in str(caught.value)
    assert len(calls) == 1


async def test_help_exposes_pdr_metadata_and_preserves_human_resolution_boundary(settings):
    def handler(_request):
        pytest.fail("Help must remain local")

    catalog = {tool.name: tool for tool in await adapter(settings, handler).list_tools()}
    schema = json.loads(render_help("request_human_input decision_metadata schema", catalog))
    schema = resolve(schema, schema)
    assert set(schema["properties"]) == set(metadata())
    assert "state" not in schema["properties"]
    help_text = render_help("request_human_input details", catalog)
    assert "Question version N identifies decision revision N-1" in help_text
    assert "decision_put" in render_help("request_human_input usage", catalog)
    assert "managed pdrs" in render_help("decision_put usage", catalog).lower()


def test_metadata_snapshot_is_not_mutated_by_response_validation(human_gate):
    response = {**human_gate, "decision": product_decision()}
    before = copy.deepcopy(response)
    HumanGateRead.model_validate(response)
    assert response == before
