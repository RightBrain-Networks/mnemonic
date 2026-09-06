"""Generate supported maximum context fields without retaining a multi-megabyte JSON blob."""

from copy import deepcopy
from uuid import UUID


def maximum_external_context(context, adjacent, event, gate, resolved_gate):
    result = deepcopy(context)
    work_id = result["work_item"]["id"]
    project_id = result["work_item"]["project_id"]
    refs = [
        {
            "url": "https://example.com/" + str(i) + "a" * 1979,
            "kind": "tracked-by",
            "state": "unknown",
            "label": "😀" * 120,
            "state_observed_at": "2026-09-05T14:20:00.123456Z",
        }
        for i in range(10)
    ]
    title, summary = "a" + "\x01" * 199, "a" + "\x01" * 999
    result["work_item"].update(title=title, summary=summary, external_references=refs)
    result["canonical"]["canonical_work_item"]["title"] = title
    checkpoint = deepcopy(result["initial_checkpoint"])
    checkpoint.update(
        prompt="a" + "\x01" * 99999,
        source_metadata={"payload": "a" + "\x01" * 2728},
        source_client="a" + "\x01" * 79,
        source_session_id="a" + "\x01" * 199,
        source_model="a" + "\x01" * 119,
        repository_branch="a" + "\x01" * 199,
        verified_against="a" * 40,
        affected_paths=[f"{i:02d}" + "a" * 254 for i in range(64)],
        source_session_url="https://example.com/" + "😀" * 1980,
        tags=[f"{i:02d}" + "\x01" * 48 for i in range(20)],
    )
    result["initial_checkpoint"] = deepcopy(checkpoint)
    result["current_context"] = {**deepcopy(checkpoint), "id": str(UUID(int=1000))}
    result["current_context_is_initial"] = False
    result["recent_checkpoints"] = [
        {**deepcopy(checkpoint), "id": str(UUID(int=1001 + i))} for i in range(20)
    ]
    result["checkpoint_total"] = 22
    result["merge_review_revision"]["context_checkpoint_id"] = result["current_context"]["id"]
    for index, direction in enumerate(("incoming", "outgoing", "undirected")):
        result[direction + "_relationships"] = []
        for i in range(100):
            other_id = str(UUID(int=2000 + index * 100 + i))
            edge = deepcopy(adjacent)
            source, target = (other_id, work_id) if direction == "incoming" else (work_id, other_id)
            if direction == "undirected":
                source, target = sorted((source, target))
            edge.update(direction=direction, relative_to_work_item_id=work_id)
            edge["relationship"].update(
                id=str(UUID(int=3000 + index * 100 + i)),
                project_id=project_id,
                relationship_type="related" if index == 2 else "blocks",
                source_work_item_id=source,
                target_work_item_id=target,
                created_by_client="a" + "\x01" * 79,
                created_by_session_id="a" + "\x01" * 199,
                created_by_model="a" + "\x01" * 119,
            )
            edge["counterpart"].update(id=other_id, title=title, external_references=deepcopy(refs))
            edge["counterpart"]["readiness"]["canonical_work_item_id"] = other_id
            result[direction + "_relationships"].append(edge)
    result["relationship_counts"] = {
        "incoming": 100,
        "outgoing": 100,
        "undirected": 100,
        "total": 300,
    }
    result["duplicate_merge_eligibility"].update(
        incident_blocks_count=200, has_unresolved_gate=True
    )
    result["readiness"].update(
        unresolved_blocker_count=100,
        is_blocked=True,
        is_ready=False,
        unresolved_gate_count=20,
        is_gated=True,
        display_state="waiting",
    )
    result["recent_events"] = [
        {
            **deepcopy(event),
            "id": i + 1,
            "event_type": "work_updated",
            "body": None,
            "metadata": {
                "work_version": 3,
                "changes": {
                    "title": {"before": title, "after": title},
                    "summary": {"before": summary, "after": summary},
                    "priority": {"before": 100, "after": 100},
                    "status": {"before": "pending", "after": "pending"},
                    "external_references": {"before": deepcopy(refs), "after": deepcopy(refs)},
                },
            },
        }
        for i in range(20)
    ]
    result["event_total"] = 20
    result["omitted_event_count"] = 0
    result["merge_review_revision"]["work_event_count"] = 20
    for resolved, name, template in (
        (False, "unresolved_gates", gate),
        (True, "recent_resolved_gates", resolved_gate),
    ):
        result[name] = []
        for i in range(20):
            item = deepcopy(template)
            item.update(
                id=str(UUID(int=4000 + int(resolved) * 100 + i)),
                question="a" + "\x01" * 3999,
                resolution="a" + "\x01" * 3999 if resolved else None,
            )
            for revision_name in (
                "requested_context_revision",
                "current_context_revision",
                "resolved_context_revision",
            ):
                if item[revision_name] is not None:
                    item[revision_name]["context_checkpoint_id"] = result["current_context"]["id"]
            result[name].append(item)
    result["unresolved_gate_total"] = result["resolved_gate_total"] = 20
    return result
