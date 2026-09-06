"""Catalog-level assertions for duplicate-merge enforcement objects."""

from hashlib import sha256

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.postgres


EXPECTED_TRIGGERS = {
    ("checkpoints", "duplicate_alias_checkpoint_guard"): (
        31,
        False,
        False,
        "mnemonic_reject_alias_owned_fact",
    ),
    ("work_duplicate_merges", "duplicate_merge_completeness_guard"): (
        5,
        True,
        True,
        "mnemonic_require_duplicate_merge_evidence",
    ),
    ("work_duplicate_merges", "duplicate_merge_insert_guard"): (
        7,
        False,
        False,
        "mnemonic_guard_duplicate_merge_insert",
    ),
    ("work_duplicate_merges", "duplicate_merges_immutable"): (
        27,
        False,
        False,
        "mnemonic_reject_duplicate_merge_mutation",
    ),
    ("work_events", "duplicate_work_event_guard"): (
        7,
        False,
        False,
        "mnemonic_guard_duplicate_work_event",
    ),
    ("work_gates", "duplicate_alias_gate_guard"): (
        31,
        False,
        False,
        "mnemonic_reject_alias_owned_fact",
    ),
    ("work_items", "duplicate_alias_work_mutation_guard"): (
        27,
        False,
        False,
        "mnemonic_reject_alias_work_mutation",
    ),
    ("work_leases", "duplicate_alias_lease_guard"): (
        31,
        False,
        False,
        "mnemonic_reject_alias_owned_fact",
    ),
    ("work_relationships", "duplicate_relationship_completeness_guard"): (
        5,
        True,
        True,
        "mnemonic_require_duplicate_relationship_merge",
    ),
    ("work_relationships", "duplicate_relationship_mutation_guard"): (
        31,
        False,
        False,
        "mnemonic_guard_duplicate_relationship_mutation",
    ),
}

EXPECTED_FUNCTIONS = {
    "mnemonic_duplicate_component_state",
    "mnemonic_duplicate_merge_is_complete",
    "mnemonic_guard_duplicate_merge_insert",
    "mnemonic_guard_duplicate_relationship_mutation",
    "mnemonic_guard_duplicate_work_event",
    "mnemonic_reject_alias_owned_fact",
    "mnemonic_reject_alias_work_mutation",
    "mnemonic_reject_duplicate_merge_mutation",
    "mnemonic_require_duplicate_merge_evidence",
    "mnemonic_require_duplicate_relationship_merge",
    "mnemonic_work_merged_metadata_v1_is_valid",
}

EXPECTED_FUNCTION_HASHES = {
    "mnemonic_duplicate_component_state": (
        "2c6b0e84d7e8aaa4871c07452c127375909a6f6fb5c3ec3efc0f74969b014300"
    ),
    "mnemonic_duplicate_merge_is_complete": (
        "cc23396ca93869246bf00654b13cbe21926febd36fed4c7e8e8fa50e659afc8d"
    ),
    "mnemonic_guard_duplicate_merge_insert": (
        "de50dcc3f22c7ab09129059e4a4aa4530859e2f74884649737f6f400b9f15c7b"
    ),
    "mnemonic_guard_duplicate_relationship_mutation": (
        "b3775c38f3c330b1c2c381c1d13c5798645153b4fb2ed077b661f6479f522fcd"
    ),
    "mnemonic_guard_duplicate_work_event": (
        "54b7d67967e7fa7586f3cca083085f1c048ed481fa64c64501a018763ae4fda7"
    ),
    "mnemonic_reject_alias_owned_fact": (
        "d36264a00249a23115037be221d3e5e8d33826ccad0d06f1ccf790a1b1a4945a"
    ),
    "mnemonic_reject_alias_work_mutation": (
        "54f0df7f081b143a60845f6f318f99b619424ba95af9ef8a011aebdba6371d4f"
    ),
    "mnemonic_reject_duplicate_merge_mutation": (
        "f0102a4b843fef1c29b0bc807cd667513e71003ec3414ed4c36c63261ead221f"
    ),
    "mnemonic_require_duplicate_merge_evidence": (
        "cf9d628d446fb94ba7869ec8c41900907833f3b878672ccced999fc534965702"
    ),
    "mnemonic_require_duplicate_relationship_merge": (
        "c1da86c5fbd980f7e4e992aef39b79f796f275798b23499c71a82de3c3e1f614"
    ),
    "mnemonic_work_merged_metadata_v1_is_valid": (
        "853f17348fdaa08b7d63f83c5f6c2ecfd8fbf1f7a3e07e32cfc1ea030710b2e7"
    ),
}


def _normalized_function_hash(definition: str, schema: str) -> str:
    normalized = definition.replace(f'"{schema}".', "<schema>.")
    normalized = normalized.replace(f"{schema}.", "<schema>.")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return sha256(normalized.encode()).hexdigest()


def test_duplicate_merge_trigger_catalog_is_exact(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        rows = list(
            connection.execute(
                text(
                """
                SELECT relation.relname AS table_name,
                       trigger_row.tgname AS trigger_name,
                       trigger_row.tgtype,
                       trigger_row.tgdeferrable,
                       trigger_row.tginitdeferred,
                       trigger_row.tgenabled,
                       procedure.proname AS function_name
                FROM pg_trigger AS trigger_row
                JOIN pg_class AS relation
                  ON relation.oid = trigger_row.tgrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_proc AS procedure
                  ON procedure.oid = trigger_row.tgfoid
                WHERE namespace.nspname = current_schema()
                  AND NOT trigger_row.tgisinternal
                  AND trigger_row.tgname LIKE 'duplicate%'
                ORDER BY relation.relname, trigger_row.tgname
                """
                )
            ).mappings()
        )
        actual = {
            (row["table_name"], row["trigger_name"]): (
                row["tgtype"],
                row["tgdeferrable"],
                row["tginitdeferred"],
                row["function_name"],
            )
            for row in rows
        }
        enabled_states = {row["tgenabled"] for row in rows}
    assert actual == EXPECTED_TRIGGERS
    assert enabled_states == {"O"}


def test_duplicate_merge_function_definitions_are_frozen(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        rows = connection.execute(
            text(
                """
                SELECT procedure.proname AS function_name,
                       procedure.provolatile,
                       procedure.proparallel,
                       procedure.prosecdef,
                       procedure.proconfig,
                       pg_get_functiondef(procedure.oid) AS definition
                FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = current_schema()
                  AND procedure.proname = ANY(CAST(:names AS text[]))
                ORDER BY procedure.proname
                """
            ),
            {"names": sorted(EXPECTED_FUNCTIONS)},
        ).mappings()
        actual = {
            row["function_name"]: (
                row["provolatile"],
                row["proparallel"],
                row["prosecdef"],
                tuple(row["proconfig"] or ()),
                _normalized_function_hash(row["definition"], schema),
            )
            for row in rows
        }
    assert set(actual) == EXPECTED_FUNCTIONS
    assert all(not attributes[2] for attributes in actual.values())
    assert all(attributes[3] == ("search_path=pg_catalog",) for attributes in actual.values())
    assert {name: attributes[4] for name, attributes in actual.items()} == (
        EXPECTED_FUNCTION_HASHES
    )
    assert actual["mnemonic_work_merged_metadata_v1_is_valid"][:2] == ("i", "s")
    assert actual["mnemonic_duplicate_component_state"][:2] == ("s", "u")
    assert actual["mnemonic_duplicate_merge_is_complete"][:2] == ("s", "u")
    trigger_functions = EXPECTED_FUNCTIONS - {
        "mnemonic_work_merged_metadata_v1_is_valid",
        "mnemonic_duplicate_component_state",
        "mnemonic_duplicate_merge_is_complete",
    }
    assert all(actual[name][:2] == ("v", "u") for name in trigger_functions)
