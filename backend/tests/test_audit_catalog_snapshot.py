"""The operational audit's guard catalog must not depend on the plan PostgreSQL picks.

These run without a database on purpose. The invariant they pin - that a catalog
name two rows share resolves to the same digest whichever row a scan yields last -
is what stops `scripts/audit_project_activity.py` reporting a spurious
`catalog_foreign_key_triggers_drift` against its own frozen fixture.
"""

import runpy

from .conftest import BACKEND_DIR

SCHEMA = "mnemonic_test_stub"

# A self-referencing foreign key gives one relation two internal triggers that
# share a constraint and a tgtype, so this name owns two rows that differ only in
# the referential action procedure.
SHARED = "code_review_remediations.fk_code_review_remediations_parent.17"
UNIQUE = "work_items.fk_work_items_remediation.17"
CHECK_ROW = '["O", true, true, "code_review_remediations", "\\"RI_FKey_check_upd\\"()", "\\\\x"]'
NOACTION_ROW = (
    '["O", true, true, "code_review_remediations", "\\"RI_FKey_noaction_upd\\"()", "\\\\x"]'
)


def _audit_module() -> dict:
    return runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))


class _StubConnection:
    """Answers the calls catalog_snapshot makes, from a canned row table.

    It models a session for the render-affecting settings the audit pins, so the
    pin's read-back sees the values it just wrote. Every other statement is still
    rejected, which is what keeps these tests a statement about the catalog
    queries alone.
    """

    def __init__(self, rows: dict[str, list[tuple[str, str]]], statements: dict[str, str]) -> None:
        self._rows = rows
        self._statements = statements
        self._settings: dict[str, str] = {}

    def scalar(self, statement: object, parameters: dict | None = None) -> str:
        if "current_setting" in str(statement):
            return self._settings[parameters["name"]]
        return SCHEMA

    def execute(self, statement: object, parameters: dict) -> list[tuple[str, str]]:
        if "set_config" in str(statement):
            self._settings[parameters["name"]] = parameters["value"]
            return []
        for category, sql in self._statements.items():
            if str(statement) == sql:
                return list(self._rows[category])
        raise AssertionError(f"catalog_snapshot ran a statement it does not own: {statement}")


def _rows(statements: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    rows = {category: [(f"{category}.only", f"{category} definition")] for category in statements}
    rows["foreign_key_triggers"] = [
        (SHARED, NOACTION_ROW),
        (UNIQUE, CHECK_ROW),
        (SHARED, CHECK_ROW),
    ]
    return rows


def test_catalog_snapshot_is_independent_of_the_order_rows_arrive_in() -> None:
    """Nothing orders the catalog queries, so last-row-wins would be a coin flip."""
    audit = _audit_module()
    statements = audit["CATALOG_STATEMENTS"]
    rows = _rows(statements)

    forward = audit["catalog_snapshot"](_StubConnection(rows, statements))
    flipped = {category: list(reversed(value)) for category, value in rows.items()}
    backward = audit["catalog_snapshot"](_StubConnection(flipped, statements))

    assert forward == backward
    assert set(forward) == set(statements)


def test_catalog_snapshot_digests_every_row_a_shared_name_owns() -> None:
    """Folding must cover both rows, not silently discard whichever scanned first."""
    audit = _audit_module()
    statements = audit["CATALOG_STATEMENTS"]
    captured = audit["catalog_snapshot"](_StubConnection(_rows(statements), statements))

    assert captured["foreign_key_triggers"][SHARED] == audit["_digest"](
        "\n".join(sorted([CHECK_ROW, NOACTION_ROW])), SCHEMA
    )


def test_catalog_snapshot_leaves_a_name_only_one_row_owns_byte_identical() -> None:
    """8,791 of the 8,793 frozen digests are single-row names and must not move."""
    audit = _audit_module()
    statements = audit["CATALOG_STATEMENTS"]
    captured = audit["catalog_snapshot"](_StubConnection(_rows(statements), statements))

    assert captured["foreign_key_triggers"][UNIQUE] == audit["_digest"](CHECK_ROW, SCHEMA)
    assert captured["functions"]["functions.only"] == audit["_digest"](
        "functions definition", SCHEMA
    )
