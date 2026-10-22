"""Frozen rendering and attribution rules for migration 0056 and its records.

Do not change these functions in a later release. Introduce a new version when
the product-decision document format or client attribution rules change.
"""

HUMAN_CLIENTS = frozenset({"dashboard", "human", "manual", "browser"})


def product_decision_title(summary: str) -> str:
    return ("Product decision: " + " ".join(summary.split()))[:500]


def product_decision_content(question: str, resolution: str | None = None) -> str:
    if resolution is None:
        return question
    return "## Question\n\n" + question + "\n\n## Product decision\n\n" + resolution


def product_decision_author(
    client: str,
    session_id: str,
    model_id: str | None,
    *,
    resolved: bool = False,
) -> tuple[str, str | None, str | None]:
    if resolved or client in HUMAN_CLIENTS:
        return "human", None, None
    return "agent", session_id, model_id
