"""Add immutable, completion-atomic structured evidence.

Revision ID: 0019_structured_completion_evidence
Revises: 0018_repository_freshness
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision: str = "0019_structured_completion_evidence"
down_revision: str | None = "0018_repository_freshness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_ID_MAX = 9223372036854775806
_REWRITE_TRIGGERS = (
    ("work_items", "duplicate_alias_work_mutation_guard"),
    ("checkpoints", "checkpoints_immutable"),
    ("checkpoints", "duplicate_alias_checkpoint_guard"),
    ("work_events", "events_immutable"),
)
_PHASE11_CATALOG_SHA256 = {
    "relations": "8b8a389da0f398be9e5ab62cefa36828695813f64e1a8d4c12714fcc4c77a1bb",
    "columns": "661ec98d3d0ceab6a33651bca9a78630ff6698cb9633dfa2a22fc43f6b162973",
    "constraints": "949e2f283ebe8c755fef57429e0be77a10e0fb759e6c35495bf6050aaecbecd5",
    "indexes": "e42d2073ac878f00f54c33bb6eebeeb74bd71b1be400c8f480730c8a8e22ae09",
    "triggers": "d373c87879d6d720758656059da6303e46fcd9af78f4de61cf16f5311e233fce",
    "functions": "fe5c30629aa6664b58e76daf5ceb9236ce04fc4b76d4e21238200d80925b7a4a",
}
_PHASE11_FUNCTION_NAMES = (
    "mnemonic_completion_artifact_reference_v1_is_valid",
    "mnemonic_completion_evidence_text_bytes_v1",
    "mnemonic_completion_episode_is_sealed",
    "mnemonic_guard_completion_generation",
    "mnemonic_require_completion_state_episode",
    "mnemonic_guard_completion_pending_exit",
    "mnemonic_guard_completion_unsealed_deletion",
    "mnemonic_guard_completion_episode_departure",
    "mnemonic_require_completion_generation_reopen",
    "mnemonic_guard_completion_checkpoint_insert",
    "mnemonic_require_completion_checkpoint_episode",
    "mnemonic_guard_completion_evidence_insert",
    "mnemonic_require_completion_evidence_episode",
    "mnemonic_guard_completion_lifecycle_event_insert",
    "mnemonic_require_completion_reopen_event_episode",
    "mnemonic_reject_completion_evidence_mutation",
    "mnemonic_reject_completion_evidence_truncate",
    "mnemonic_reject_phase11_history_truncate",
)
_PHASE11_TRIGGER_NAMES = (
    "completion_generation_guard",
    "completion_state_episode_guard",
    "completion_pending_exit_guard",
    "completion_unsealed_deletion_guard",
    "completion_episode_departure_guard",
    "completion_generation_reopen_guard",
    "completion_lifecycle_event_insert_guard",
    "completion_reopen_event_episode_guard",
    "completion_checkpoint_insert_guard",
    "completion_checkpoint_episode_guard",
    "verification_results_insert_guard",
    "verification_results_episode_guard",
    "artifact_references_insert_guard",
    "artifact_references_episode_guard",
    "verification_results_immutable",
    "artifact_references_immutable",
    "verification_results_truncate_guard",
    "artifact_references_truncate_guard",
    "work_events_phase11_truncate_guard",
    "client_operations_phase11_truncate_guard",
)
_PHASE11_INDEX_NAMES = (
    "uq_checkpoints_completion_generation",
    "uq_work_events_reopen_generation",
    "ix_work_events_completion_evidence_history",
    "ix_work_events_live_completion_version_order",
    "ix_client_operations_completion_checkpoint_receipt",
    "ix_client_operations_completion_receipt_correspondence",
    "ix_verification_results_completion_checkpoint_id_id",
    "ix_artifact_references_completion_checkpoint_id_id",
    "uq_verification_results_episode_position",
    "uq_artifact_references_episode_position",
    "uq_verification_results_work_item_id_id",
    "uq_artifact_references_work_item_id_id",
    "uq_artifact_references_episode_reference",
    "pk_verification_results",
    "pk_artifact_references",
)
_PHASE11_CONSTRAINT_IDENTITIES = (
    ("work_items", "ck_work_items_completion_generation_range"),
    ("checkpoints", "ck_checkpoints_completion_generation_kind"),
    ("work_events", "ck_work_events_reopen_generation_kind"),
    ("work_items", "completion_state_episode_guard"),
    ("work_items", "completion_generation_reopen_guard"),
    ("work_events", "completion_reopen_event_episode_guard"),
    ("checkpoints", "completion_checkpoint_episode_guard"),
)
# PostgreSQL stores parsed expression trees, and pg_dump/pg_restore reparses
# their SQL representation.  That supported round trip changes the exact
# spelling of 18 CHECK constraints and two partial indexes without changing
# their semantics.  Keep both whole-catalog representations explicit: do not
# weaken this fail-closed boundary with expression normalization.
_PHASE10_SURVIVOR_CATALOG_SHA256S = frozenset(
    {
        # Built directly by the migration chain.
        "5171e0e22b9b6f838277725146ad81ccdcb747a82244fba3dd2aa42bb3cfa8fe",
        # Restored from the shipped PostgreSQL custom-format backup.
        "95ac5cede92f756a2132379f9fb38f97148b7c3dd2c817a1844b8ad1facc45fe",
    }
)


def _quoted_current_schema() -> str:
    schema = op.get_bind().scalar(sa.text("SELECT pg_catalog.current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError(
            "0019_structured_completion_evidence requires a current PostgreSQL schema"
        )
    return op.get_bind().dialect.identifier_preparer.quote_identifier(schema)


def _sql_string_literal(value: str) -> str:
    """Quote a trusted generated value as a PostgreSQL string literal."""

    return "'" + value.replace("'", "''") + "'"


def _lock_upgrade_relations(schema: str) -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for relation in ("client_operations", "work_items", "checkpoints", "work_events"):
        op.execute(f"LOCK TABLE {schema}.{relation} IN ACCESS EXCLUSIVE MODE")


def _legacy_eventless_completion(schema: str, work_alias: str) -> str:
    """SQL for a completion that predates the event timeline entirely.

    Migration 0010 introduced ``work_events`` and derived ``work_completed``
    strictly from ``completion`` checkpoints, deliberately reconstructing only
    provable facts rather than inventing a completion it could not witness.  A
    work item completed before 0010 therefore arrives here as ``done`` with no
    completion checkpoint and no completion event -- no episode at all, and
    nothing 0018 ever promised otherwise: no constraint or trigger in the chain
    ties ``done`` to a completion event.

    Phase 11 carries such an item forward unchanged at generation 0, the same
    value a never-completed item holds, and every episode rule skips it.  A
    ``done`` item that does own a completion checkpoint is a different matter
    entirely: a missing or duplicated event for it is a real integrity fault,
    and ``completion_checkpoint_event_pairing`` below still fails closed on it.

    Generation 0 does not itself mark the absence of an episode -- work completed
    once and never reopened sits there too -- so read the episode, never the
    number.  What does follow is that these items cannot leave ``done``:
    ``completion_episode_departure_guard`` permits a departure only from a
    sealed episode.  They stay exactly as history recorded them.
    """

    return f"""
        NOT EXISTS (
            SELECT 1
            FROM {schema}.checkpoints AS legacy_checkpoint
            WHERE legacy_checkpoint.work_item_id = {work_alias}.id
              AND legacy_checkpoint.kind = 'completion'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM {schema}.work_events AS legacy_event
            WHERE legacy_event.work_item_id = {work_alias}.id
              AND legacy_event.event_type = 'work_completed'
        )
    """


def _preflight_conditions(schema: str) -> tuple[tuple[str, str], ...]:
    """Every 0018 history condition 0019 refuses to migrate, each named.

    One condition per entry, never a disjunction: an operator reading the
    failure has to learn which rows to look at, and a single message covering
    several unrelated conditions tells them only that something, somewhere, is
    wrong.  ``test_legacy_shape_migration_postgres.py`` seeds each condition
    and asserts its own name comes back.
    """

    return (
        (
            "completion_checkpoint_event_pairing",
            f"""
            SELECT 1
            FROM {schema}.checkpoints AS checkpoint
            LEFT JOIN {schema}.work_events AS event
              ON event.work_item_id = checkpoint.work_item_id
             AND event.checkpoint_id = checkpoint.id
             AND event.event_type = 'work_completed'
            WHERE checkpoint.kind = 'completion'
            GROUP BY checkpoint.work_item_id, checkpoint.id
            HAVING pg_catalog.count(event.id) <> 1
            """,
        ),
        (
            "completion_event_without_checkpoint",
            f"""
            SELECT 1
            FROM {schema}.work_events AS event
            LEFT JOIN {schema}.checkpoints AS checkpoint
              ON checkpoint.work_item_id = event.work_item_id
             AND checkpoint.id = event.checkpoint_id
             AND checkpoint.kind = 'completion'
            WHERE event.event_type = 'work_completed'
              AND checkpoint.id IS NULL
            """,
        ),
        (
            "done_work_without_completion_event",
            f"""
            SELECT 1
            FROM {schema}.work_items AS work
            WHERE work.status = 'done'
              AND NOT EXISTS (
                  SELECT 1
                  FROM {schema}.work_events AS event
                  WHERE event.work_item_id = work.id
                    AND event.event_type = 'work_completed'
              )
              AND NOT ({_legacy_eventless_completion(schema, "work")})
            """,
        ),
        (
            "completion_event_id_out_of_range",
            f"""
            SELECT 1
            FROM {schema}.work_events
            WHERE event_type IN ('work_completed', 'work_reopened')
              AND (id < 1 OR id > {_EVENT_ID_MAX})
            """,
        ),
        (
            # The caller holds client_operations ACCESS EXCLUSIVE, so this exact
            # non-null expression-key check cannot race the unique index created
            # below.
            "duplicate_completion_receipt_correspondence",
            f"""
            SELECT 1
            FROM {schema}.client_operations AS operation
            WHERE operation.operation_kind = 'complete_work'
              AND operation.state = 'completed'
              AND operation.response_body #>> '{{checkpoint,id}}' IS NOT NULL
              AND operation.response_body #>> '{{work_item,id}}' IS NOT NULL
            GROUP BY operation.response_body #>> '{{checkpoint,id}}',
                     operation.response_body #>> '{{work_item,id}}'
            HAVING pg_catalog.count(*) > 1
            """,
        ),
        (
            "live_completion_version_ordering",
            f"""
            WITH live_completion AS (
                {_completion_version_order(schema)}
            )
            SELECT 1 FROM live_completion
            WHERE work_version IS NULL
               OR (prior_id IS NOT NULL AND work_version <= prior_version)
            """,
        ),
        (
            "live_completion_precedes_backfill",
            f"""
            SELECT 1
            FROM {schema}.work_events AS live
            JOIN {schema}.work_events AS backfill
              ON backfill.work_item_id = live.work_item_id
             AND backfill.event_type = 'work_completed'
             AND backfill.origin = 'backfill'
            WHERE live.event_type = 'work_completed'
              AND live.origin = 'live'
              AND live.id <= backfill.id
            """,
        ),
        (
            "completion_version_exceeds_work_version",
            f"""
            WITH current_done AS (
                {_current_done_completion_version(schema)}
            )
            SELECT 1 FROM current_done
            WHERE completion_version IS NOT NULL
              AND completion_version > version
            """,
        ),
    )


def _bounded_work_version(expression: str) -> str:
    """Read a positive, representable ``work_version`` out of event metadata."""

    return f"""
        CASE
            WHEN pg_catalog.jsonb_typeof({expression} -> 'work_version') = 'number'
             AND {expression} ->> 'work_version' ~ '^[1-9][0-9]*$'
             AND pg_catalog.length({expression} ->> 'work_version') <= 19
            THEN CASE
                WHEN ({expression} ->> 'work_version')::numeric
                         <= 9223372036854775807
                THEN ({expression} ->> 'work_version')::bigint
            END
        END
    """


def _completion_version_order(schema: str) -> str:
    version = _bounded_work_version("event.metadata")
    return f"""
        SELECT event.id,
               event.work_item_id,
               {version} AS work_version,
               pg_catalog.lag(event.id) OVER (
                   PARTITION BY event.work_item_id ORDER BY event.id
               ) AS prior_id,
               pg_catalog.lag({version}) OVER (
                   PARTITION BY event.work_item_id ORDER BY event.id
               ) AS prior_version
        FROM {schema}.work_events AS event
        WHERE event.event_type = 'work_completed'
          AND event.origin = 'live'
    """


def _current_done_completion_version(schema: str) -> str:
    version = _bounded_work_version("event.metadata")
    return f"""
        SELECT DISTINCT ON (event.work_item_id)
               event.work_item_id,
               work.version,
               CASE
                   WHEN event.origin = 'live' THEN {version}
               END AS completion_version
        FROM {schema}.work_events AS event
        JOIN {schema}.work_items AS work ON work.id = event.work_item_id
        WHERE event.event_type = 'work_completed'
          AND work.status = 'done'
        ORDER BY event.work_item_id, event.id DESC
    """


def _require_clean_0018_history(schema: str) -> None:
    bind = op.get_bind()
    for name, condition in _preflight_conditions(schema):
        if bind.scalar(sa.text(f"SELECT EXISTS ({condition})")):
            raise RuntimeError(f"0019 preflight rejected 0018 history: {name}")


def _trigger_snapshot(schema: str) -> dict[tuple[str, str], tuple[str, str]]:
    bind = op.get_bind()
    snapshot: dict[tuple[str, str], tuple[str, str]] = {}
    for table, trigger in _REWRITE_TRIGGERS:
        row = bind.execute(
            sa.text(
                """
                SELECT trigger.tgenabled,
                       pg_catalog.pg_get_triggerdef(trigger.oid, true)
                FROM pg_catalog.pg_trigger AS trigger
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = trigger.tgrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = pg_catalog.current_schema()
                  AND relation.relname = :table
                  AND trigger.tgname = :trigger
                  AND NOT trigger.tgisinternal
                """
            ),
            {"table": table, "trigger": trigger},
        ).one_or_none()
        if row is None or row[0] != "O":
            raise RuntimeError(f"0019 requires enabled trigger {table}.{trigger}")
        snapshot[(table, trigger)] = (row[0], row[1])
    return snapshot


def _backfill_generations(schema: str) -> None:
    before = _trigger_snapshot(schema)
    for table, trigger in _REWRITE_TRIGGERS:
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE TRIGGER {trigger}")
    try:
        op.execute(
            f"""
            UPDATE {schema}.checkpoints AS checkpoint
            SET completion_generation = -event.id
            FROM {schema}.work_events AS event
            WHERE checkpoint.kind = 'completion'
              AND event.work_item_id = checkpoint.work_item_id
              AND event.checkpoint_id = checkpoint.id
              AND event.event_type = 'work_completed'
            """
        )
        op.execute(
            f"""
            UPDATE {schema}.work_items AS work
            SET completion_generation = CASE
                WHEN work.status = 'done' THEN COALESCE(
                    (
                        SELECT -event.id
                        FROM {schema}.work_events AS event
                        WHERE event.work_item_id = work.id
                          AND event.event_type = 'work_completed'
                        ORDER BY event.id DESC
                        LIMIT 1
                    ),
                    -- A completion predating the event timeline owns no
                    -- episode, so it carries generation 0 like work that was
                    -- never completed.  The preflight has already proved this
                    -- is the legacy shape and not a missing event.
                    0
                )
                ELSE 0
            END
            """
        )
        op.execute(
            f"""
            UPDATE {schema}.work_events
            SET reopen_generation = -id
            WHERE event_type = 'work_reopened'
            """
        )
    finally:
        for table, trigger in reversed(_REWRITE_TRIGGERS):
            op.execute(f"ALTER TABLE {schema}.{table} ENABLE TRIGGER {trigger}")
    if _trigger_snapshot(schema) != before:
        raise RuntimeError("0019 did not restore the controlled rewrite triggers exactly")


def _add_generation_columns(schema: str) -> None:
    op.add_column(
        "work_items",
        sa.Column("completion_generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("checkpoints", sa.Column("completion_generation", sa.BigInteger()))
    op.add_column("work_events", sa.Column("reopen_generation", sa.BigInteger()))
    _backfill_generations(schema)
    op.create_check_constraint(
        op.f("ck_work_items_completion_generation_range"),
        "work_items",
        f"completion_generation >= -{_EVENT_ID_MAX}",
    )
    op.create_check_constraint(
        op.f("ck_checkpoints_completion_generation_kind"),
        "checkpoints",
        "(kind = 'completion' AND completion_generation IS NOT NULL) OR "
        "(kind <> 'completion' AND completion_generation IS NULL)",
    )
    op.create_index(
        "uq_checkpoints_completion_generation",
        "checkpoints",
        ["work_item_id", "completion_generation"],
        unique=True,
        postgresql_where=sa.text("kind = 'completion'"),
    )
    op.create_check_constraint(
        op.f("ck_work_events_reopen_generation_kind"),
        "work_events",
        "(event_type = 'work_reopened' AND reopen_generation IS NOT NULL "
        "AND reopen_generation <> 0) OR "
        "(event_type <> 'work_reopened' AND reopen_generation IS NULL)",
    )
    op.create_index(
        "uq_work_events_reopen_generation",
        "work_events",
        ["work_item_id", "reopen_generation"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_reopened'"),
    )
    op.create_index(
        "ix_work_events_completion_evidence_history",
        "work_events",
        ["project_id", "work_item_id", sa.text("id DESC")],
        postgresql_where=sa.text("event_type = 'work_completed'"),
    )
    op.create_index(
        "ix_work_events_live_completion_version_order",
        "work_events",
        ["work_item_id", sa.text("id DESC")],
        postgresql_where=sa.text("event_type = 'work_completed' AND origin = 'live'"),
    )
    op.create_index(
        "ix_client_operations_completion_checkpoint_receipt",
        "client_operations",
        ["project_id", sa.text("(response_body #>> '{checkpoint,id}')")],
        postgresql_where=sa.text("operation_kind = 'complete_work' AND state = 'completed'"),
    )
    op.create_index(
        "ix_client_operations_completion_receipt_correspondence",
        "client_operations",
        [
            sa.text("(response_body #>> '{checkpoint,id}')"),
            sa.text("(response_body #>> '{work_item,id}')"),
        ],
        unique=True,
        postgresql_where=sa.text("operation_kind = 'complete_work' AND state = 'completed'"),
    )


def _create_artifact_validator(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_completion_artifact_reference_v1_is_valid(
            artifact_type_value text,
            reference_value text
        ) RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            component text;
            authority text;
            host text;
            host_address inet;
            host_components text[];
            host_prefix text;
            host_suffix text;
            hostname_label text;
            path_component text;
            port_text text;
            port_value integer;
            closing_bracket integer;
            double_colon_position integer;
            explicit_group_count integer;
            maximum_zero_run integer;
            omitted_zero_count integer;
            path_value text;
            zero_run integer;
            whitespace_characters text;
        BEGIN
            whitespace_characters :=
                pg_catalog.chr(9) || pg_catalog.chr(10) ||
                pg_catalog.chr(11) || pg_catalog.chr(12) ||
                pg_catalog.chr(13) || pg_catalog.chr(28) ||
                pg_catalog.chr(29) || pg_catalog.chr(30) ||
                pg_catalog.chr(31) || pg_catalog.chr(32) ||
                pg_catalog.chr(133) || pg_catalog.chr(160) ||
                pg_catalog.chr(5760) || pg_catalog.chr(8192) ||
                pg_catalog.chr(8193) || pg_catalog.chr(8194) ||
                pg_catalog.chr(8195) || pg_catalog.chr(8196) ||
                pg_catalog.chr(8197) || pg_catalog.chr(8198) ||
                pg_catalog.chr(8199) || pg_catalog.chr(8200) ||
                pg_catalog.chr(8201) || pg_catalog.chr(8202) ||
                pg_catalog.chr(8232) || pg_catalog.chr(8233) ||
                pg_catalog.chr(8239) || pg_catalog.chr(8287) ||
                pg_catalog.chr(12288);
            IF artifact_type_value = 'commit' THEN
                RETURN reference_value COLLATE "C" ~ '^[0-9a-f]{{7,64}}$';
            ELSIF artifact_type_value = 'branch' THEN
                RETURN pg_catalog.length(reference_value) BETWEEN 1 AND 200
                   AND pg_catalog.octet_length(reference_value) <= 800
                   AND {schema}.mnemonic_has_non_whitespace(reference_value)
                   AND pg_catalog.strpos(
                           whitespace_characters, pg_catalog.left(reference_value, 1)
                       ) = 0
                   AND pg_catalog.strpos(
                           whitespace_characters, pg_catalog.right(reference_value, 1)
                       ) = 0;
            ELSIF artifact_type_value = 'repository_path' THEN
                IF pg_catalog.octet_length(reference_value) NOT BETWEEN 1 AND 512
                   OR reference_value COLLATE "C" !~ '^[A-Za-z0-9._@+=,~/-]+$' THEN
                    RETURN false;
                END IF;
                FOREACH component IN ARRAY pg_catalog.string_to_array(reference_value, '/') LOOP
                    IF component IN ('', '.', '..') THEN
                        RETURN false;
                    END IF;
                END LOOP;
                RETURN true;
            ELSIF artifact_type_value IN (
                'pull_request', 'test_run', 'external_issue', 'build_artifact'
            ) THEN
                IF pg_catalog.octet_length(reference_value) NOT BETWEEN 1 AND 2000
                   OR reference_value COLLATE "C" !~ '^[\\x21-\\x7e]+$'
                   OR reference_value COLLATE "C" !~ '^https://[^/?#]+/.*$'
                   OR reference_value ~ '[?#\\\\]' THEN
                    RETURN false;
                END IF;
                authority := pg_catalog.split_part(
                    pg_catalog.substr(reference_value, 9), '/', 1
                );
                IF authority = '' OR pg_catalog.strpos(authority, '@') > 0 THEN
                    RETURN false;
                END IF;
                port_text := NULL;
                IF pg_catalog.left(authority, 1) = '[' THEN
                    closing_bracket := pg_catalog.strpos(authority, ']');
                    IF closing_bracket <= 2 THEN
                        RETURN false;
                    END IF;
                    host := pg_catalog.substr(authority, 2, closing_bracket - 2);
                    IF closing_bracket < pg_catalog.length(authority) THEN
                        IF pg_catalog.substr(authority, closing_bracket + 1, 1) <> ':' THEN
                            RETURN false;
                        END IF;
                        port_text := pg_catalog.substr(authority, closing_bracket + 2);
                    END IF;
                    IF host COLLATE "C" !~ '^[0-9a-f:]+$' THEN
                        RETURN false;
                    END IF;
                    BEGIN
                        host_address := host::pg_catalog.inet;
                    EXCEPTION WHEN invalid_text_representation THEN
                        RETURN false;
                    END;
                    IF pg_catalog.family(host_address) <> 6 THEN
                        RETURN false;
                    END IF;
                    double_colon_position := pg_catalog.strpos(host, '::');
                    IF pg_catalog.strpos(host, ':::') > 0
                       OR (
                           double_colon_position > 0
                           AND pg_catalog.strpos(
                                   pg_catalog.substr(host, double_colon_position + 2),
                                   '::'
                               ) > 0
                       ) THEN
                        RETURN false;
                    END IF;
                    IF double_colon_position = 0 THEN
                        host_components := pg_catalog.string_to_array(host, ':');
                        IF pg_catalog.cardinality(host_components) <> 8 THEN
                            RETURN false;
                        END IF;
                        zero_run := 0;
                        maximum_zero_run := 0;
                        FOREACH component IN ARRAY host_components LOOP
                            IF component COLLATE "C" !~
                               '^(0|[1-9a-f][0-9a-f]{{0,3}})$' THEN
                                RETURN false;
                            END IF;
                            IF component = '0' THEN
                                zero_run := zero_run + 1;
                                IF zero_run > maximum_zero_run THEN
                                    maximum_zero_run := zero_run;
                                END IF;
                            ELSE
                                zero_run := 0;
                            END IF;
                        END LOOP;
                        IF maximum_zero_run >= 2 THEN
                            RETURN false;
                        END IF;
                    ELSE
                        host_prefix := pg_catalog.left(host, double_colon_position - 1);
                        host_suffix := pg_catalog.substr(host, double_colon_position + 2);
                        host_components := CASE
                            WHEN host_prefix = '' THEN ARRAY[]::text[]
                            ELSE pg_catalog.string_to_array(host_prefix, ':')
                        END;
                        explicit_group_count := pg_catalog.cardinality(host_components);
                        zero_run := 0;
                        maximum_zero_run := 0;
                        FOREACH component IN ARRAY host_components LOOP
                            IF component COLLATE "C" !~
                               '^(0|[1-9a-f][0-9a-f]{{0,3}})$' THEN
                                RETURN false;
                            END IF;
                            IF component = '0' THEN
                                zero_run := zero_run + 1;
                                IF zero_run > maximum_zero_run THEN
                                    maximum_zero_run := zero_run;
                                END IF;
                            ELSE
                                zero_run := 0;
                            END IF;
                        END LOOP;
                        IF zero_run > 0 THEN
                            RETURN false;
                        END IF;

                        host_components := CASE
                            WHEN host_suffix = '' THEN ARRAY[]::text[]
                            ELSE pg_catalog.string_to_array(host_suffix, ':')
                        END;
                        explicit_group_count := explicit_group_count
                            + pg_catalog.cardinality(host_components);
                        omitted_zero_count := 8 - explicit_group_count;
                        IF omitted_zero_count < 2
                           OR maximum_zero_run >= omitted_zero_count THEN
                            RETURN false;
                        END IF;
                        zero_run := 0;
                        maximum_zero_run := 0;
                        FOREACH component IN ARRAY host_components LOOP
                            IF component COLLATE "C" !~
                               '^(0|[1-9a-f][0-9a-f]{{0,3}})$' THEN
                                RETURN false;
                            END IF;
                            IF component = '0' THEN
                                zero_run := zero_run + 1;
                                IF zero_run > maximum_zero_run THEN
                                    maximum_zero_run := zero_run;
                                END IF;
                            ELSE
                                zero_run := 0;
                            END IF;
                        END LOOP;
                        IF pg_catalog.cardinality(host_components) > 0
                           AND host_components[1] = '0' THEN
                            RETURN false;
                        END IF;
                        IF maximum_zero_run > omitted_zero_count THEN
                            RETURN false;
                        END IF;
                    END IF;
                ELSE
                    IF pg_catalog.strpos(authority, ':') > 0 THEN
                        host := pg_catalog.split_part(authority, ':', 1);
                        port_text := pg_catalog.substr(authority, pg_catalog.length(host) + 2);
                    ELSE
                        host := authority;
                    END IF;
                    IF host COLLATE "C" ~ '^[0-9.]+$' THEN
                        BEGIN
                            host_address := host::pg_catalog.inet;
                        EXCEPTION WHEN invalid_text_representation THEN
                            RETURN false;
                        END;
                        IF pg_catalog.family(host_address) <> 4
                           OR pg_catalog.host(host_address) <> host THEN
                            RETURN false;
                        END IF;
                    ELSE
                        IF pg_catalog.length(host) > 253 THEN
                            RETURN false;
                        END IF;
                        FOREACH hostname_label IN ARRAY
                            pg_catalog.string_to_array(host, '.') LOOP
                            IF pg_catalog.length(hostname_label) NOT BETWEEN 1 AND 63
                               OR hostname_label COLLATE "C" !~
                                  '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$' THEN
                                RETURN false;
                            END IF;
                        END LOOP;
                    END IF;
                END IF;
                IF port_text IS NOT NULL THEN
                    IF port_text COLLATE "C" !~ '^(0|[1-9][0-9]{{0,4}})$' THEN
                        RETURN false;
                    END IF;
                    port_value := port_text::integer;
                    IF port_value > 65535 OR port_value = 443 THEN
                        RETURN false;
                    END IF;
                END IF;
                path_value := pg_catalog.substr(
                    reference_value,
                    9 + pg_catalog.length(authority)
                );
                IF path_value COLLATE "C" !~
                       '^/(?:[A-Za-z0-9._~!$&''()*+,;=:@/-]|%[0-9A-F]{{2}})*$' THEN
                    RETURN false;
                END IF;
                FOREACH path_component IN ARRAY pg_catalog.string_to_array(
                    path_value,
                    '/'
                ) LOOP
                    IF pg_catalog.lower(path_component) IN (
                        '.', '..', '%2e', '.%2e', '%2e.', '%2e%2e'
                    ) THEN
                        RETURN false;
                    END IF;
                END LOOP;
                RETURN true;
            END IF;
            RETURN false;
        END
        $function$;
        """
    )


def _create_evidence_tables() -> None:
    op.create_table(
        "verification_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completion_checkpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("verification_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("command", sa.Text()),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True, precision=6)),
        sa.Column("observed_at_commit", sa.String(length=64)),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verification_type::text = ANY "
            "(ARRAY['command'::text, 'observation'::text])",
            name=op.f("ck_verification_results_verification_type_valid"),
        ),
        sa.CheckConstraint(
            "outcome::text = ANY "
            "(ARRAY['passed'::text, 'failed'::text, 'inconclusive'::text, "
            "'skipped'::text])",
            name=op.f("ck_verification_results_outcome_valid"),
        ),
        sa.CheckConstraint(
            "(verification_type = 'command' AND outcome = 'passed' "
            "AND command IS NOT NULL AND exit_code = 0) OR "
            "(verification_type = 'command' AND outcome = 'failed' "
            "AND command IS NOT NULL AND exit_code IS NOT NULL AND exit_code <> 0) OR "
            "(verification_type = 'command' AND outcome = 'inconclusive' "
            "AND command IS NOT NULL AND exit_code IS NULL) OR "
            "(verification_type = 'observation' AND command IS NULL AND exit_code IS NULL)",
            name=op.f("ck_verification_results_result_matrix_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(name) AND length(name) <= 200 "
            "AND octet_length(name) <= 800",
            name=op.f("ck_verification_results_name_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(summary) AND length(summary) <= 4000 "
            "AND octet_length(summary) <= 16000",
            name=op.f("ck_verification_results_summary_valid"),
        ),
        sa.CheckConstraint(
            "command IS NULL OR (mnemonic_has_non_whitespace(command) "
            "AND length(command) <= 4096 AND octet_length(command) <= 16384)",
            name=op.f("ck_verification_results_command_valid"),
        ),
        sa.CheckConstraint(
            "observed_at_commit IS NULL OR observed_at_commit ~ '^[0-9a-f]{7,64}$'",
            name=op.f("ck_verification_results_observed_at_commit_valid"),
        ),
        sa.CheckConstraint(
            "observed_at IS NULL OR (isfinite(observed_at) AND observed_at >= "
            "TIMESTAMPTZ '0001-01-01 00:00:00+00' AND observed_at < "
            "TIMESTAMPTZ '10000-01-01 00:00:00+00')",
            name=op.f("ck_verification_results_observed_at_range"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 19",
            name=op.f("ck_verification_results_position_range"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_verification_results_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_verification_results_completion_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_verification_results"),
        sa.UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "position",
            name="uq_verification_results_episode_position",
        ),
        sa.UniqueConstraint("work_item_id", "id", name="uq_verification_results_work_item_id_id"),
    )


def _create_episode_validators(schema: str) -> None:
    validator_sql = f"""
        CREATE FUNCTION {schema}.mnemonic_completion_evidence_text_bytes_v1(
            work_id uuid,
            checkpoint_id uuid
        ) RETURNS bigint
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT
                COALESCE((
                    SELECT pg_catalog.sum(
                        pg_catalog.octet_length(result.verification_type)
                        + pg_catalog.octet_length(result.name)
                        + pg_catalog.octet_length(result.outcome)
                        + pg_catalog.octet_length(result.summary)
                        + CASE WHEN result.command IS NULL THEN 0
                               ELSE pg_catalog.octet_length(result.command) END
                        + CASE WHEN result.observed_at IS NULL THEN 0 ELSE 32 END
                        + CASE WHEN result.observed_at_commit IS NULL THEN 0
                               ELSE pg_catalog.octet_length(result.observed_at_commit) END
                    )
                    FROM {schema}.verification_results AS result
                    WHERE result.work_item_id = work_id
                      AND result.completion_checkpoint_id = checkpoint_id
                ), 0)::bigint
                + COALESCE((
                    SELECT pg_catalog.sum(
                        pg_catalog.octet_length(artifact.artifact_type)
                        + pg_catalog.octet_length(artifact.label)
                        + pg_catalog.octet_length(artifact.reference)
                    )
                    FROM {schema}.artifact_references AS artifact
                    WHERE artifact.work_item_id = work_id
                      AND artifact.completion_checkpoint_id = checkpoint_id
                ), 0)::bigint
        $function$;

        CREATE FUNCTION {schema}.mnemonic_completion_episode_is_sealed(
            requested_work_id uuid,
            requested_generation bigint
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            work_row record;
            checkpoint_row record;
            completion_row record;
            reopen_row record;
            successor_row record;
            result_count bigint;
            result_distinct bigint;
            result_min integer;
            result_max integer;
            artifact_count bigint;
            artifact_distinct bigint;
            artifact_min integer;
            artifact_max integer;
            prior_live_version bigint;
            completion_version bigint;
            reopen_version bigint;
            successor_version bigint;
        BEGIN
            SELECT work.id, work.project_id, work.status, work.version,
                   work.completion_generation
            INTO STRICT work_row
            FROM {schema}.work_items AS work
            WHERE work.id = requested_work_id;
            IF requested_generation IS NULL THEN
                RETURN false;
            END IF;

            SELECT checkpoint.id, checkpoint.work_item_id, checkpoint.kind,
                   checkpoint.migration_origin, checkpoint.created_at,
                   checkpoint.completion_generation
            INTO STRICT checkpoint_row
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = requested_work_id
              AND checkpoint.kind = 'completion'
              AND checkpoint.completion_generation = requested_generation;
            SELECT event.id, event.project_id, event.work_item_id, event.checkpoint_id,
                   event.origin, event.created_at, event.metadata
            INTO STRICT completion_row
            FROM {schema}.work_events AS event
            WHERE event.work_item_id = requested_work_id
              AND event.checkpoint_id = checkpoint_row.id
              AND event.event_type = 'work_completed';
            IF completion_row.project_id IS DISTINCT FROM work_row.project_id
               OR completion_row.created_at IS DISTINCT FROM checkpoint_row.created_at
               OR completion_row.id NOT BETWEEN 1 AND {_EVENT_ID_MAX} THEN
                RETURN false;
            END IF;

            IF requested_generation < 0 THEN
                IF requested_generation::numeric
                       IS DISTINCT FROM -completion_row.id::numeric THEN
                    RETURN false;
                END IF;
            ELSE
                IF checkpoint_row.migration_origin IS NOT NULL
                   OR completion_row.origin IS DISTINCT FROM 'live'
                   OR work_row.completion_generation IS NULL
                   OR requested_generation > work_row.completion_generation
                   OR COALESCE((
                       SELECT CASE
                                  WHEN pg_catalog.count(legacy_event.id) = 1
                                  THEN pg_catalog.min(legacy_event.id)
                                       >= completion_row.id
                                  ELSE true
                              END
                       FROM (
                           SELECT candidate.id
                           FROM {schema}.checkpoints AS candidate
                           WHERE candidate.work_item_id = requested_work_id
                             AND candidate.kind = 'completion'
                             AND candidate.completion_generation < 0
                           ORDER BY candidate.completion_generation ASC
                           LIMIT 1
                       ) AS legacy_checkpoint
                       LEFT JOIN {schema}.work_events AS legacy_event
                         ON legacy_event.work_item_id = requested_work_id
                        AND legacy_event.checkpoint_id = legacy_checkpoint.id
                        AND legacy_event.event_type = 'work_completed'
                       HAVING pg_catalog.count(legacy_checkpoint.id) = 1
                   ), false)
                   OR COALESCE((
                       SELECT CASE
                                  WHEN pg_catalog.count(other_event.id) = 1
                                  THEN pg_catalog.min(other_event.id)
                                       >= completion_row.id
                                  ELSE true
                              END
                       FROM (
                           SELECT candidate.id, candidate.completion_generation
                           FROM {schema}.checkpoints AS candidate
                           WHERE candidate.work_item_id = requested_work_id
                             AND candidate.kind = 'completion'
                             AND candidate.completion_generation >= 0
                             AND candidate.completion_generation
                                   < requested_generation
                           ORDER BY candidate.completion_generation DESC
                           LIMIT 1
                       ) AS other_checkpoint
                       LEFT JOIN {schema}.work_events AS other_event
                         ON other_event.work_item_id = requested_work_id
                        AND other_event.checkpoint_id = other_checkpoint.id
                        AND other_event.event_type = 'work_completed'
                       HAVING pg_catalog.count(other_checkpoint.id) = 1
                   ), false)
                   OR COALESCE((
                       SELECT CASE
                                  WHEN pg_catalog.count(other_event.id) = 1
                                  THEN pg_catalog.min(other_event.id)
                                       <= completion_row.id
                                  ELSE true
                              END
                       FROM (
                           SELECT candidate.id, candidate.completion_generation
                           FROM {schema}.checkpoints AS candidate
                           WHERE candidate.work_item_id = requested_work_id
                             AND candidate.kind = 'completion'
                             AND candidate.completion_generation
                                   > requested_generation
                           ORDER BY candidate.completion_generation ASC
                           LIMIT 1
                       ) AS other_checkpoint
                       LEFT JOIN {schema}.work_events AS other_event
                         ON other_event.work_item_id = requested_work_id
                        AND other_event.checkpoint_id = other_checkpoint.id
                        AND other_event.event_type = 'work_completed'
                       HAVING pg_catalog.count(other_checkpoint.id) = 1
                   ), false) THEN
                    RETURN false;
                END IF;
                IF pg_catalog.jsonb_typeof(
                       completion_row.metadata -> 'work_version'
                   ) IS DISTINCT FROM 'number'
                   OR (
                       completion_row.metadata ->> 'work_version'
                           ~ '^[1-9][0-9]*$'
                   ) IS DISTINCT FROM true
                   OR pg_catalog.length(
                       completion_row.metadata ->> 'work_version'
                   ) > 19
                   OR (completion_row.metadata ->> 'work_version')::numeric
                        > 9223372036854775807 THEN
                    RETURN false;
                END IF;
                completion_version := (
                    completion_row.metadata ->> 'work_version'
                )::bigint;
                SELECT CASE
                           WHEN pg_catalog.jsonb_typeof(
                                    event.metadata -> 'work_version'
                                ) = 'number'
                            AND event.metadata ->> 'work_version'
                                    ~ '^[1-9][0-9]*$'
                            AND pg_catalog.length(
                                    event.metadata ->> 'work_version'
                                ) <= 19
                            AND (event.metadata ->> 'work_version')::numeric
                                    <= 9223372036854775807
                           THEN (event.metadata ->> 'work_version')::bigint
                       END
                INTO prior_live_version
                FROM {schema}.work_events AS event
                WHERE event.work_item_id = requested_work_id
                  AND event.event_type = 'work_completed'
                  AND event.origin = 'live'
                  AND event.id < completion_row.id
                ORDER BY event.id DESC
                LIMIT 1;
                IF FOUND AND prior_live_version IS NULL THEN
                    RETURN false;
                END IF;
                IF prior_live_version IS NOT NULL
                   AND completion_version <= prior_live_version THEN
                    RETURN false;
                END IF;

                IF requested_generation > 0 THEN
                    SELECT event.id, event.project_id, event.origin, event.created_at,
                           event.metadata, event.reopen_generation
                    INTO STRICT reopen_row
                    FROM {schema}.work_events AS event
                    WHERE event.work_item_id = requested_work_id
                      AND event.event_type = 'work_reopened'
                      AND event.reopen_generation = requested_generation;
                    IF reopen_row.project_id IS DISTINCT FROM work_row.project_id
                       OR reopen_row.origin IS DISTINCT FROM 'live'
                       OR reopen_row.id NOT BETWEEN 1 AND {_EVENT_ID_MAX}
                       OR (
                           reopen_row.metadata ->> 'from_status'
                               IN ('done', 'deferred', 'wont-do', 'promoted')
                       ) IS DISTINCT FROM true
                       OR reopen_row.metadata ->> 'to_status'
                            IS DISTINCT FROM 'pending'
                       OR reopen_row.metadata -> 'changes' -> 'status' ->> 'before'
                            IS DISTINCT FROM reopen_row.metadata ->> 'from_status'
                       OR reopen_row.metadata -> 'changes' -> 'status' ->> 'after'
                            IS DISTINCT FROM 'pending'
                       OR pg_catalog.jsonb_typeof(
                           reopen_row.metadata -> 'work_version'
                       ) IS DISTINCT FROM 'number'
                       OR (
                           reopen_row.metadata ->> 'work_version'
                               ~ '^[1-9][0-9]*$'
                       ) IS DISTINCT FROM true
                       OR pg_catalog.length(
                           reopen_row.metadata ->> 'work_version'
                       ) > 19
                       OR (reopen_row.metadata ->> 'work_version')::numeric
                            > 9223372036854775807 THEN
                        RETURN false;
                    END IF;
                    reopen_version := (reopen_row.metadata ->> 'work_version')::bigint;
                    IF completion_version <= reopen_version THEN
                        RETURN false;
                    END IF;
                END IF;

                IF requested_generation < work_row.completion_generation THEN
                    SELECT event.id, event.project_id, event.origin, event.metadata,
                           event.reopen_generation
                    INTO STRICT successor_row
                    FROM {schema}.work_events AS event
                    WHERE event.work_item_id = requested_work_id
                      AND event.event_type = 'work_reopened'
                      AND event.reopen_generation = requested_generation + 1;
                    IF successor_row.project_id IS DISTINCT FROM work_row.project_id
                       OR successor_row.origin IS DISTINCT FROM 'live'
                       OR successor_row.id NOT BETWEEN 1 AND {_EVENT_ID_MAX}
                       OR successor_row.metadata ->> 'from_status'
                            IS DISTINCT FROM 'done'
                       OR successor_row.metadata ->> 'to_status'
                            IS DISTINCT FROM 'pending'
                       OR successor_row.metadata -> 'changes' -> 'status' ->> 'before'
                            IS DISTINCT FROM 'done'
                       OR successor_row.metadata -> 'changes' -> 'status' ->> 'after'
                            IS DISTINCT FROM 'pending'
                       OR pg_catalog.jsonb_typeof(
                           successor_row.metadata -> 'work_version'
                       ) IS DISTINCT FROM 'number'
                       OR (
                           successor_row.metadata ->> 'work_version'
                               ~ '^[1-9][0-9]*$'
                       ) IS DISTINCT FROM true
                       OR pg_catalog.length(
                           successor_row.metadata ->> 'work_version'
                       ) > 19
                       OR (successor_row.metadata ->> 'work_version')::numeric
                            > 9223372036854775807 THEN
                        RETURN false;
                    END IF;
                    successor_version := (
                        successor_row.metadata ->> 'work_version'
                    )::bigint;
                    IF completion_version >= successor_version THEN
                        RETURN false;
                    END IF;
                ELSIF work_row.status IS NOT DISTINCT FROM 'done' THEN
                    IF completion_version > work_row.version THEN
                        RETURN false;
                    END IF;
                ELSE
                    RETURN false;
                END IF;
            END IF;

            SELECT pg_catalog.count(*), pg_catalog.count(DISTINCT position),
                   pg_catalog.min(position), pg_catalog.max(position)
            INTO result_count, result_distinct, result_min, result_max
            FROM {schema}.verification_results
            WHERE work_item_id = requested_work_id
              AND completion_checkpoint_id = checkpoint_row.id;
            SELECT pg_catalog.count(*), pg_catalog.count(DISTINCT position),
                   pg_catalog.min(position), pg_catalog.max(position)
            INTO artifact_count, artifact_distinct, artifact_min, artifact_max
            FROM {schema}.artifact_references
            WHERE work_item_id = requested_work_id
              AND completion_checkpoint_id = checkpoint_row.id;
            IF result_count + artifact_count > 20
               OR (result_count > 0 AND (
                   result_min <> 0 OR result_max <> result_count - 1
                   OR result_distinct <> result_count
               ))
               OR (artifact_count > 0 AND (
                   artifact_min <> 0 OR artifact_max <> artifact_count - 1
                   OR artifact_distinct <> artifact_count
               ))
               OR {schema}.mnemonic_completion_evidence_text_bytes_v1(
                      requested_work_id, checkpoint_row.id
                  ) > 32768
               OR EXISTS (
                   SELECT 1
                   FROM {schema}.verification_results AS result
                   WHERE result.work_item_id = requested_work_id
                     AND result.completion_checkpoint_id = checkpoint_row.id
                     AND (
                         result.project_id IS DISTINCT FROM work_row.project_id
                         OR result.created_at IS DISTINCT FROM checkpoint_row.created_at
                     )
               )
               OR EXISTS (
                   SELECT 1
                   FROM {schema}.artifact_references AS artifact
                   WHERE artifact.work_item_id = requested_work_id
                     AND artifact.completion_checkpoint_id = checkpoint_row.id
                     AND (
                         artifact.project_id IS DISTINCT FROM work_row.project_id
                         OR artifact.created_at IS DISTINCT FROM checkpoint_row.created_at
                     )
               )
               OR EXISTS (
                   SELECT 1
                   FROM {schema}.artifact_references AS first_artifact
                   JOIN {schema}.artifact_references AS second_artifact
                     ON second_artifact.work_item_id = first_artifact.work_item_id
                    AND second_artifact.completion_checkpoint_id
                        = first_artifact.completion_checkpoint_id
                    AND second_artifact.artifact_type = first_artifact.artifact_type
                    AND second_artifact.reference = first_artifact.reference
                    AND second_artifact.id <> first_artifact.id
                   WHERE first_artifact.work_item_id = requested_work_id
                     AND first_artifact.completion_checkpoint_id = checkpoint_row.id
               ) THEN
                RETURN false;
            END IF;
            RETURN true;
        EXCEPTION
            WHEN no_data_found OR too_many_rows
                 OR numeric_value_out_of_range OR invalid_text_representation THEN
                RETURN false;
        END
        $function$;
        """
    op.create_table(
        "artifact_references",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completion_checkpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("reference", sa.Text(collation="C"), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_type::text = ANY "
            "(ARRAY['commit'::text, 'pull_request'::text, 'branch'::text, "
            "'test_run'::text, 'repository_path'::text, 'external_issue'::text, "
            "'build_artifact'::text])",
            name=op.f("ck_artifact_references_artifact_type_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(label) AND length(label) <= 200 "
            "AND octet_length(label) <= 800",
            name=op.f("ck_artifact_references_label_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_completion_artifact_reference_v1_is_valid(artifact_type, reference)",
            name=op.f("ck_artifact_references_reference_valid"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 19",
            name=op.f("ck_artifact_references_position_range"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_artifact_references_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_artifact_references_completion_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_references"),
        sa.UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "position",
            name="uq_artifact_references_episode_position",
        ),
        sa.UniqueConstraint("work_item_id", "id", name="uq_artifact_references_work_item_id_id"),
        sa.UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "artifact_type",
            "reference",
            name="uq_artifact_references_episode_reference",
        ),
    )
    op.create_index(
        "ix_verification_results_completion_checkpoint_id_id",
        "verification_results",
        ["completion_checkpoint_id", "id"],
    )
    op.create_index(
        "ix_artifact_references_completion_checkpoint_id_id",
        "artifact_references",
        ["completion_checkpoint_id", "id"],
    )
    op.execute(validator_sql)


def _create_work_lifecycle_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_completion_generation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion generation guard is misconfigured';
            END IF;
            IF TG_OP = 'INSERT' THEN
                NEW.completion_generation := 0;
                RETURN NEW;
            END IF;
            IF NEW.completion_generation IS DISTINCT FROM OLD.completion_generation THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion generation is database managed';
            END IF;
            IF NEW.version IS DISTINCT FROM OLD.version
               AND (OLD.version = 2147483647 OR NEW.version <> OLD.version + 1) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work version may only remain stable or advance by one';
            END IF;
            IF OLD.status <> 'pending' AND NEW.status = 'pending' THEN
                IF OLD.version = 2147483647 OR NEW.version <> OLD.version + 1
                   OR OLD.completion_generation >= {_EVENT_ID_MAX} THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'reopen must advance version and completion generation';
                END IF;
                NEW.completion_generation := CASE
                    WHEN OLD.completion_generation <= 0 THEN 1
                    ELSE OLD.completion_generation + 1
                END;
            ELSE
                NEW.completion_generation := OLD.completion_generation;
            END IF;
            IF NEW.completion_generation < 0 AND NEW.status <> 'done' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'negative completion generation belongs to a legacy done episode';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_generation_guard
        BEFORE INSERT OR UPDATE ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_generation();

        CREATE FUNCTION {schema}.mnemonic_guard_completion_episode_departure()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion departure guard is misconfigured';
            END IF;
            IF OLD.status = 'done' AND NEW.status <> 'done' THEN
                IF NEW.status <> 'pending'
                   OR NOT {schema}.mnemonic_completion_episode_is_sealed(
                       OLD.id, OLD.completion_generation
                   ) THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'done work may depart only from a sealed completion episode';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_episode_departure_guard
        BEFORE UPDATE OF status ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_episode_departure();

        CREATE FUNCTION {schema}.mnemonic_guard_completion_pending_exit()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            checkpoint_count bigint;
            event_count bigint;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion pending-exit guard is misconfigured';
            END IF;
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'pending' THEN
                SELECT pg_catalog.count(*),
                       pg_catalog.count(event.id)
                INTO checkpoint_count, event_count
                FROM {schema}.checkpoints AS checkpoint
                LEFT JOIN {schema}.work_events AS event
                  ON event.work_item_id = checkpoint.work_item_id
                 AND event.checkpoint_id = checkpoint.id
                 AND event.event_type = 'work_completed'
                WHERE checkpoint.work_item_id = OLD.id
                  AND checkpoint.kind = 'completion'
                  AND checkpoint.migration_origin IS NULL
                  AND checkpoint.completion_generation = OLD.completion_generation;
                IF NEW.status = 'done' THEN
                    IF OLD.completion_generation < 0
                       OR checkpoint_count <> 1 OR event_count <> 0
                       OR OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NOT NULL
                       OR OLD.version = 2147483647 OR NEW.version <> OLD.version + 1
                       OR EXISTS (
                           SELECT 1 FROM {schema}.work_events AS deleted
                           WHERE deleted.work_item_id = OLD.id
                             AND deleted.event_type = 'work_deleted'
                       )
                       OR EXISTS (
                           SELECT 1 FROM {schema}.work_duplicate_merges AS merge
                           WHERE merge.project_id = OLD.project_id
                             AND merge.source_work_item_id = OLD.id
                       ) THEN
                        RAISE EXCEPTION USING ERRCODE = '23514',
                            MESSAGE = 'pending completion requires one live current episode';
                    END IF;
                ELSIF NEW.status IN ('deferred', 'wont-do', 'promoted') THEN
                    IF checkpoint_count <> 0 THEN
                        RAISE EXCEPTION USING ERRCODE = '23514',
                            MESSAGE = 'pending work cannot abandon an unsealed completion';
                    END IF;
                END IF;
            ELSIF NEW.status = 'done' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'only pending work can become done';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_pending_exit_guard
        BEFORE UPDATE OF status ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_pending_exit();

        CREATE FUNCTION {schema}.mnemonic_guard_completion_unsealed_deletion()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion deletion guard is misconfigured';
            END IF;
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
               AND OLD.completion_generation >= 0
               AND EXISTS (
                   SELECT 1
                   FROM {schema}.checkpoints AS checkpoint
                   WHERE checkpoint.work_item_id = OLD.id
                     AND checkpoint.kind = 'completion'
                     AND checkpoint.migration_origin IS NULL
                     AND checkpoint.completion_generation
                         = OLD.completion_generation
                     AND NOT EXISTS (
                         SELECT 1 FROM {schema}.work_events AS event
                         WHERE event.work_item_id = checkpoint.work_item_id
                           AND event.checkpoint_id = checkpoint.id
                           AND event.event_type = 'work_completed'
                     )
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work cannot be deleted with an unsealed completion';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_unsealed_deletion_guard
        BEFORE UPDATE OF deleted_at ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_unsealed_deletion();

        CREATE FUNCTION {schema}.mnemonic_require_completion_state_episode()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            completion_version bigint;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion state guard is misconfigured';
            END IF;
            IF NOT {schema}.mnemonic_completion_episode_is_sealed(
                NEW.id, NEW.completion_generation
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'done work requires a sealed completion episode';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'pending' AND NEW.status = 'done' THEN
                SELECT (event.metadata ->> 'work_version')::bigint
                INTO completion_version
                FROM {schema}.checkpoints AS checkpoint
                JOIN {schema}.work_events AS event
                  ON event.work_item_id = checkpoint.work_item_id
                 AND event.checkpoint_id = checkpoint.id
                 AND event.event_type = 'work_completed'
                WHERE checkpoint.work_item_id = NEW.id
                  AND checkpoint.kind = 'completion'
                  AND checkpoint.completion_generation = NEW.completion_generation;
                IF completion_version IS DISTINCT FROM NEW.version THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'completion event version does not match transition';
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER completion_state_episode_guard
        AFTER INSERT OR UPDATE ON {schema}.work_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (NEW.status = 'done')
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_state_episode();

        CREATE FUNCTION {schema}.mnemonic_require_completion_generation_reopen()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            witness_count bigint;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_items")}::regclass
               OR TG_TABLE_NAME <> 'work_items' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion reopen guard is misconfigured';
            END IF;
            SELECT pg_catalog.count(*)
            INTO witness_count
            FROM {schema}.work_events AS event
            WHERE event.project_id = NEW.project_id
              AND event.work_item_id = NEW.id
              AND event.event_type = 'work_reopened'
              AND event.origin = 'live'
              AND event.reopen_generation = NEW.completion_generation
              AND event.created_at = NEW.updated_at
              AND event.metadata ->> 'from_status' = OLD.status
              AND event.metadata ->> 'to_status' = 'pending'
              AND event.metadata -> 'changes' -> 'status' ->> 'before' = OLD.status
              AND event.metadata -> 'changes' -> 'status' ->> 'after' = 'pending'
              AND event.metadata ->> 'work_version' = NEW.version::text;
            IF witness_count <> 1 THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'reopen transition requires its exact bound event';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER completion_generation_reopen_guard
        AFTER UPDATE ON {schema}.work_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (OLD.status <> 'pending' AND NEW.status = 'pending')
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_generation_reopen();
        """
    )


def _create_completion_fact_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_completion_checkpoint_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            work_row record;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.checkpoints")}::regclass
               OR TG_OP <> 'INSERT' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion checkpoint guard is misconfigured';
            END IF;
            IF NEW.kind <> 'completion' THEN
                RETURN NEW;
            END IF;
            IF NEW.completion_generation IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'checkpoint completion generation is database managed';
            END IF;
            SELECT work.id, work.project_id, work.status, work.deleted_at,
                   work.completion_generation
            INTO work_row
            FROM {schema}.work_items AS work
            WHERE work.id = NEW.work_item_id
            FOR UPDATE;
            IF NOT FOUND OR work_row.status <> 'pending'
               OR work_row.deleted_at IS NOT NULL
               OR work_row.completion_generation < 0
               OR NEW.migration_origin IS NOT NULL OR NEW.legacy_record_id IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_events AS deleted
                   WHERE deleted.work_item_id = NEW.work_item_id
                     AND deleted.event_type = 'work_deleted'
               )
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_duplicate_merges AS merge
                   WHERE merge.project_id = work_row.project_id
                     AND merge.source_work_item_id = NEW.work_item_id
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion checkpoints require live canonical pending work';
            END IF;
            NEW.completion_generation := work_row.completion_generation;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_checkpoint_insert_guard
        BEFORE INSERT ON {schema}.checkpoints
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_checkpoint_insert();

        CREATE FUNCTION {schema}.mnemonic_require_completion_checkpoint_episode()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            retained_generation bigint;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.checkpoints")}::regclass
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion checkpoint episode guard is misconfigured';
            END IF;
            SELECT completion_generation
            INTO retained_generation
            FROM {schema}.work_items
            WHERE id = NEW.work_item_id;
            IF NOT FOUND OR NEW.kind <> 'completion'
               OR retained_generation < NEW.completion_generation
               OR NOT {schema}.mnemonic_completion_episode_is_sealed(
                   NEW.work_item_id, NEW.completion_generation
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion checkpoint requires one sealed episode';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER completion_checkpoint_episode_guard
        AFTER INSERT OR UPDATE ON {schema}.checkpoints
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (NEW.kind = 'completion')
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_checkpoint_episode();

        CREATE FUNCTION {schema}.mnemonic_guard_completion_evidence_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            work_row record;
            checkpoint_row record;
        BEGIN
            IF TG_RELID NOT IN (
                   {_sql_string_literal(f"{schema}.verification_results")}::regclass,
                   {_sql_string_literal(f"{schema}.artifact_references")}::regclass
               )
               OR TG_TABLE_NAME NOT IN ('verification_results', 'artifact_references')
               OR TG_OP <> 'INSERT' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion evidence insert guard is misconfigured';
            END IF;
            SELECT work.id, work.project_id, work.status, work.deleted_at,
                   work.completion_generation
            INTO work_row
            FROM {schema}.work_items AS work
            WHERE work.id = NEW.work_item_id
              AND work.project_id = NEW.project_id
            FOR UPDATE;
            SELECT checkpoint.id, checkpoint.work_item_id, checkpoint.kind,
                   checkpoint.migration_origin, checkpoint.completion_generation,
                   checkpoint.created_at
            INTO checkpoint_row
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = NEW.work_item_id
              AND checkpoint.id = NEW.completion_checkpoint_id;
            IF work_row.id IS NULL OR checkpoint_row.id IS NULL
               OR work_row.status <> 'pending' OR work_row.deleted_at IS NOT NULL
               OR checkpoint_row.kind <> 'completion'
               OR checkpoint_row.migration_origin IS NOT NULL
               OR checkpoint_row.completion_generation < 0
               OR checkpoint_row.completion_generation
                    IS DISTINCT FROM work_row.completion_generation
               OR NEW.created_at IS DISTINCT FROM checkpoint_row.created_at
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_events AS deleted
                   WHERE deleted.work_item_id = NEW.work_item_id
                     AND deleted.event_type = 'work_deleted'
               )
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_duplicate_merges AS merge
                   WHERE merge.project_id = NEW.project_id
                     AND merge.source_work_item_id = NEW.work_item_id
               )
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_events AS completed
                   WHERE completed.work_item_id = NEW.work_item_id
                     AND completed.checkpoint_id = NEW.completion_checkpoint_id
                     AND completed.event_type = 'work_completed'
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'evidence can be inserted only in an open completion episode';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER verification_results_insert_guard
        BEFORE INSERT ON {schema}.verification_results
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_evidence_insert();

        CREATE TRIGGER artifact_references_insert_guard
        BEFORE INSERT ON {schema}.artifact_references
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_evidence_insert();

        CREATE FUNCTION {schema}.mnemonic_require_completion_evidence_episode()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            generation bigint;
        BEGIN
            IF TG_RELID NOT IN (
                   {_sql_string_literal(f"{schema}.verification_results")}::regclass,
                   {_sql_string_literal(f"{schema}.artifact_references")}::regclass
               )
               OR TG_TABLE_NAME NOT IN ('verification_results', 'artifact_references')
               OR TG_OP <> 'INSERT' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion evidence episode guard is misconfigured';
            END IF;
            SELECT completion_generation
            INTO generation
            FROM {schema}.checkpoints
            WHERE work_item_id = NEW.work_item_id
              AND id = NEW.completion_checkpoint_id
              AND kind = 'completion';
            IF NOT FOUND OR NOT {schema}.mnemonic_completion_episode_is_sealed(
                NEW.work_item_id, generation
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion evidence requires its sealed aggregate';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER verification_results_episode_guard
        AFTER INSERT ON {schema}.verification_results
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_evidence_episode();

        CREATE CONSTRAINT TRIGGER artifact_references_episode_guard
        AFTER INSERT ON {schema}.artifact_references
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_evidence_episode();

        CREATE FUNCTION {schema}.mnemonic_guard_completion_lifecycle_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            work_row record;
            checkpoint_row record;
            prior_completion_id bigint;
            prior_live_version bigint;
            event_version bigint;
            reopen_version bigint;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_events")}::regclass
               OR TG_OP <> 'INSERT' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion lifecycle event guard is misconfigured';
            END IF;
            IF NEW.reopen_generation IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'reopen generation is database managed';
            END IF;
            NEW.reopen_generation := NULL;
            IF NEW.event_type NOT IN ('work_reopened', 'work_completed') THEN
                RETURN NEW;
            END IF;
            SELECT work.id, work.project_id, work.status, work.version,
                   work.updated_at, work.deleted_at, work.completion_generation
            INTO work_row
            FROM {schema}.work_items AS work
            WHERE work.id = NEW.work_item_id
              AND work.project_id = NEW.project_id
            FOR UPDATE;
            IF NOT FOUND OR work_row.deleted_at IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_events AS deleted
                   WHERE deleted.work_item_id = NEW.work_item_id
                     AND deleted.event_type = 'work_deleted'
               )
               OR EXISTS (
                   SELECT 1 FROM {schema}.work_duplicate_merges AS merge
                   WHERE merge.project_id = NEW.project_id
                     AND merge.source_work_item_id = NEW.work_item_id
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'lifecycle event requires live canonical work';
            END IF;
            IF NEW.id NOT BETWEEN 1 AND {_EVENT_ID_MAX} THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'lifecycle event identity is outside the supported range';
            END IF;

            IF NEW.event_type = 'work_reopened' THEN
                IF work_row.status <> 'pending'
                   OR work_row.completion_generation <= 0
                   OR NEW.origin <> 'live'
                   OR NEW.created_at IS DISTINCT FROM work_row.updated_at
                   OR NEW.metadata ->> 'work_version' <> work_row.version::text
                   OR NEW.metadata ->> 'from_status'
                        NOT IN ('done', 'deferred', 'wont-do', 'promoted')
                   OR NEW.metadata ->> 'to_status' <> 'pending'
                   OR NEW.metadata -> 'changes' -> 'status' ->> 'before'
                        IS DISTINCT FROM NEW.metadata ->> 'from_status'
                   OR NEW.metadata -> 'changes' -> 'status' ->> 'after' <> 'pending' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'reopen event does not match its guarded transition';
                END IF;
                NEW.reopen_generation := work_row.completion_generation;
                RETURN NEW;
            END IF;

            SELECT checkpoint.id, checkpoint.work_item_id, checkpoint.kind,
                   checkpoint.migration_origin, checkpoint.completion_generation
            INTO checkpoint_row
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = NEW.work_item_id
              AND checkpoint.id = NEW.checkpoint_id;
            SELECT pg_catalog.max(event.id)
            INTO prior_completion_id
            FROM {schema}.work_events AS event
            WHERE event.work_item_id = NEW.work_item_id
              AND event.event_type = 'work_completed';
            IF checkpoint_row.id IS NULL OR work_row.status <> 'done'
               OR checkpoint_row.kind <> 'completion'
               OR checkpoint_row.migration_origin IS NOT NULL
               OR checkpoint_row.completion_generation < 0
               OR checkpoint_row.completion_generation
                    IS DISTINCT FROM work_row.completion_generation
               OR (prior_completion_id IS NOT NULL AND NEW.id <= prior_completion_id)
               OR NEW.origin <> 'live'
               OR NEW.metadata ->> 'work_version' !~ '^[1-9][0-9]*$' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion event does not match the current episode';
            END IF;
            event_version := (NEW.metadata ->> 'work_version')::bigint;
            SELECT CASE
                       WHEN event.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                       THEN (event.metadata ->> 'work_version')::bigint
                   END
            INTO prior_live_version
            FROM {schema}.work_events AS event
            WHERE event.work_item_id = NEW.work_item_id
              AND event.event_type = 'work_completed'
              AND event.origin = 'live'
            ORDER BY event.id DESC
            LIMIT 1;
            IF prior_live_version IS NOT NULL AND event_version <= prior_live_version THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'completion event version must advance retained history';
            END IF;
            IF checkpoint_row.completion_generation > 0 THEN
                SELECT CASE
                           WHEN event.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                           THEN (event.metadata ->> 'work_version')::bigint
                       END
                INTO reopen_version
                FROM {schema}.work_events AS event
                WHERE event.work_item_id = NEW.work_item_id
                  AND event.event_type = 'work_reopened'
                  AND event.origin = 'live'
                  AND event.reopen_generation = checkpoint_row.completion_generation;
                IF reopen_version IS NULL OR event_version <= reopen_version THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'completion version must advance its reopen event';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER completion_lifecycle_event_insert_guard
        BEFORE INSERT ON {schema}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_completion_lifecycle_event_insert();

        CREATE FUNCTION {schema}.mnemonic_require_completion_reopen_event_episode()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            work_row record;
        BEGIN
            IF TG_RELID <> {_sql_string_literal(f"{schema}.work_events")}::regclass
               OR TG_OP <> 'INSERT' OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion reopen event guard is misconfigured';
            END IF;
            SELECT project_id, completion_generation
            INTO work_row
            FROM {schema}.work_items
            WHERE id = NEW.work_item_id;
            IF work_row.project_id IS DISTINCT FROM NEW.project_id
               OR NEW.event_type <> 'work_reopened'
               OR NEW.origin <> 'live'
               OR NEW.reopen_generation IS NULL OR NEW.reopen_generation <= 0
               OR work_row.completion_generation < NEW.reopen_generation
               OR NOT EXISTS (
                   SELECT 1 FROM {schema}.work_events AS retained
                   WHERE retained.id = NEW.id
                     AND retained.work_item_id = NEW.work_item_id
                     AND retained.reopen_generation = NEW.reopen_generation
                     AND retained.event_type = 'work_reopened'
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'reopen event requires its exact retained generation';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER completion_reopen_event_episode_guard
        AFTER INSERT ON {schema}.work_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (NEW.event_type = 'work_reopened')
        EXECUTE FUNCTION {schema}.mnemonic_require_completion_reopen_event_episode();
        """
    )


def _create_immutability_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_completion_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID NOT IN (
                   {_sql_string_literal(f"{schema}.verification_results")}::regclass,
                   {_sql_string_literal(f"{schema}.artifact_references")}::regclass
               )
               OR TG_TABLE_NAME NOT IN ('verification_results', 'artifact_references')
               OR TG_OP NOT IN ('UPDATE', 'DELETE') OR TG_NARGS <> 1
               OR TG_ARGV[0] IS DISTINCT FROM TG_TABLE_NAME THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion evidence immutability guard is misconfigured';
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'completion evidence is immutable';
        END
        $function$;

        CREATE TRIGGER verification_results_immutable
        BEFORE UPDATE OR DELETE ON {schema}.verification_results
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_completion_evidence_mutation(
            'verification_results'
        );

        CREATE TRIGGER artifact_references_immutable
        BEFORE UPDATE OR DELETE ON {schema}.artifact_references
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_completion_evidence_mutation(
            'artifact_references'
        );

        CREATE FUNCTION {schema}.mnemonic_reject_completion_evidence_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID NOT IN (
                   {_sql_string_literal(f"{schema}.verification_results")}::regclass,
                   {_sql_string_literal(f"{schema}.artifact_references")}::regclass
               )
               OR TG_TABLE_NAME NOT IN ('verification_results', 'artifact_references')
               OR TG_OP <> 'TRUNCATE' OR TG_LEVEL <> 'STATEMENT' OR TG_NARGS <> 1
               OR TG_ARGV[0] IS DISTINCT FROM TG_TABLE_NAME THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'completion evidence truncate guard is misconfigured';
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'completion evidence history cannot be truncated';
        END
        $function$;

        CREATE TRIGGER verification_results_truncate_guard
        BEFORE TRUNCATE ON {schema}.verification_results
        FOR EACH STATEMENT
        EXECUTE FUNCTION {schema}.mnemonic_reject_completion_evidence_truncate(
            'verification_results'
        );

        CREATE TRIGGER artifact_references_truncate_guard
        BEFORE TRUNCATE ON {schema}.artifact_references
        FOR EACH STATEMENT
        EXECUTE FUNCTION {schema}.mnemonic_reject_completion_evidence_truncate(
            'artifact_references'
        );

        CREATE FUNCTION {schema}.mnemonic_reject_phase11_history_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID NOT IN (
                   {_sql_string_literal(f"{schema}.work_events")}::regclass,
                   {_sql_string_literal(f"{schema}.client_operations")}::regclass
               )
               OR TG_TABLE_NAME NOT IN ('work_events', 'client_operations')
               OR TG_OP <> 'TRUNCATE' OR TG_LEVEL <> 'STATEMENT' OR TG_NARGS <> 1
               OR TG_ARGV[0] IS DISTINCT FROM TG_TABLE_NAME THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'phase 11 history truncate guard is misconfigured';
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'authoritative event and receipt history cannot be truncated';
        END
        $function$;

        CREATE TRIGGER work_events_phase11_truncate_guard
        BEFORE TRUNCATE ON {schema}.work_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION {schema}.mnemonic_reject_phase11_history_truncate(
            'work_events'
        );

        CREATE TRIGGER client_operations_phase11_truncate_guard
        BEFORE TRUNCATE ON {schema}.client_operations
        FOR EACH STATEMENT
        EXECUTE FUNCTION {schema}.mnemonic_reject_phase11_history_truncate(
            'client_operations'
        );
        """
    )


def _normalize_phase11_privileges(schema: str) -> None:
    """Remove ambient default grants from every object introduced by this revision."""

    bind = op.get_bind()
    raw_schema = bind.scalar(sa.text("SELECT pg_catalog.current_schema()"))
    owner_name = bind.scalar(sa.text("SELECT CURRENT_USER"))
    if not isinstance(raw_schema, str) or not isinstance(owner_name, str):
        raise RuntimeError("Could not determine the Phase 11 privilege schema")
    quote = bind.dialect.identifier_preparer.quote_identifier

    table_rows = bind.execute(
        sa.text(
            """
            SELECT relation.relname, role.rolname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r', relation.relowner)
                )
            ) AS privilege
            LEFT JOIN pg_catalog.pg_roles AS role
              ON role.oid = privilege.grantee
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relname = ANY(:names)
              AND privilege.grantee <> relation.relowner
            GROUP BY relation.relname, privilege.grantee, role.rolname
            ORDER BY relation.relname, privilege.grantee
            """
        ),
        {
            "audit_schema": raw_schema,
            "names": ["verification_results", "artifact_references"],
        },
    ).all()
    for relation_name, role_name in table_rows:
        grantee = "PUBLIC" if role_name is None else quote(role_name)
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {schema}.{quote(relation_name)} FROM {grantee}"
        )
    for relation_name in ("verification_results", "artifact_references"):
        op.execute(
            f"GRANT ALL PRIVILEGES ON TABLE {schema}.{quote(relation_name)} "
            f"TO {quote(owner_name)}"
        )

    function_rows = bind.execute(
        sa.text(
            """
            SELECT procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   role.rolname
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f', procedure.proowner)
                )
            ) AS privilege
            LEFT JOIN pg_catalog.pg_roles AS role
              ON role.oid = privilege.grantee
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND procedure.proname = ANY(:names)
              AND privilege.grantee <> procedure.proowner
            GROUP BY procedure.oid, procedure.proname, privilege.grantee, role.rolname
            ORDER BY procedure.proname, privilege.grantee
            """
        ),
        {"audit_schema": raw_schema, "names": list(_PHASE11_FUNCTION_NAMES)},
    ).all()
    for function_name, arguments, role_name in function_rows:
        grantee = "PUBLIC" if role_name is None else quote(role_name)
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION "
            f"{schema}.{quote(function_name)}({arguments}) FROM {grantee}"
        )
    owned_functions = bind.execute(
        sa.text(
            """
            SELECT procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes)
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND procedure.proname = ANY(:names)
            ORDER BY procedure.proname,
                     pg_catalog.oidvectortypes(procedure.proargtypes)
            """
        ),
        {"audit_schema": raw_schema, "names": list(_PHASE11_FUNCTION_NAMES)},
    ).all()
    for function_name, arguments in owned_functions:
        op.execute(
            f"GRANT ALL PRIVILEGES ON FUNCTION "
            f"{schema}.{quote(function_name)}({arguments}) TO {quote(owner_name)}"
        )


def _advance_work_event_sequence(schema: str) -> None:
    bind = op.get_bind()
    maximum = bind.scalar(sa.text(f"SELECT pg_catalog.max(id) FROM {schema}.work_events"))
    if maximum is None:
        return
    if maximum >= 9223372036854775807:
        raise RuntimeError("0019 cannot advance an exhausted work event identity")
    sequence = bind.scalar(
        sa.text("SELECT pg_catalog.pg_get_serial_sequence(:relation, 'id')"),
        {"relation": f"{schema}.work_events"},
    )
    if not isinstance(sequence, str):
        raise RuntimeError("0019 requires the owned work_events identity sequence")
    bind.execute(
        sa.text(
            """
            SELECT pg_catalog.setval(
                CAST(:sequence AS regclass),
                GREATEST(
                    CAST(:maximum AS bigint),
                    COALESCE(
                        pg_catalog.pg_sequence_last_value(CAST(:sequence AS regclass)),
                        CAST(0 AS bigint)
                    )
                ),
                true
            )
            """
        ),
        {"sequence": sequence, "maximum": maximum},
    )


def _validate_upgraded_history(schema: str) -> None:
    legacy_eventless_completion = _legacy_eventless_completion(schema, "work")
    invalid = op.get_bind().scalar(
        sa.text(
            f"""
            WITH completion_events AS (
                SELECT event.id, event.project_id, event.work_item_id,
                       event.checkpoint_id, event.origin, event.created_at,
                       CASE
                           WHEN pg_catalog.jsonb_typeof(
                                    event.metadata -> 'work_version'
                                ) = 'number'
                            AND event.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                            AND pg_catalog.length(
                                    event.metadata ->> 'work_version'
                                ) <= 19
                            AND (event.metadata ->> 'work_version')::numeric
                                    <= 9223372036854775807
                           THEN (event.metadata ->> 'work_version')::bigint
                       END AS work_version
                FROM {schema}.work_events AS event
                WHERE event.event_type = 'work_completed'
            ), reopen_events AS (
                SELECT event.id, event.project_id, event.work_item_id,
                       event.origin, event.created_at, event.reopen_generation,
                       event.metadata,
                       CASE
                           WHEN pg_catalog.jsonb_typeof(
                                    event.metadata -> 'work_version'
                                ) = 'number'
                            AND event.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                            AND pg_catalog.length(
                                    event.metadata ->> 'work_version'
                                ) <= 19
                            AND (event.metadata ->> 'work_version')::numeric
                                    <= 9223372036854775807
                           THEN (event.metadata ->> 'work_version')::bigint
                       END AS work_version
                FROM {schema}.work_events AS event
                WHERE event.event_type = 'work_reopened'
            ), bound_completions AS (
                SELECT completion.*, checkpoint.completion_generation,
                       checkpoint.kind, checkpoint.migration_origin,
                       checkpoint.created_at AS checkpoint_created_at,
                       work.project_id AS retained_project_id,
                       work.status AS retained_status,
                       work.version AS retained_version,
                       work.completion_generation AS retained_generation
                FROM completion_events AS completion
                LEFT JOIN {schema}.checkpoints AS checkpoint
                  ON checkpoint.work_item_id = completion.work_item_id
                 AND checkpoint.id = completion.checkpoint_id
                LEFT JOIN {schema}.work_items AS work
                  ON work.id = completion.work_item_id
            ), ordered_live AS (
                SELECT completion.*,
                       pg_catalog.lag(completion.id) OVER (
                           PARTITION BY completion.work_item_id
                           ORDER BY completion.id
                       ) AS prior_id,
                       pg_catalog.lag(completion.work_version) OVER (
                           PARTITION BY completion.work_item_id
                           ORDER BY completion.id
                       ) AS prior_version
                FROM bound_completions AS completion
                WHERE completion.origin = 'live'
            )
            SELECT EXISTS (
                SELECT checkpoint.id
                FROM {schema}.checkpoints AS checkpoint
                LEFT JOIN completion_events AS completion
                  ON completion.work_item_id = checkpoint.work_item_id
                 AND completion.checkpoint_id = checkpoint.id
                WHERE checkpoint.kind = 'completion'
                GROUP BY checkpoint.id
                HAVING pg_catalog.count(completion.id) <> 1
            ) OR EXISTS (
                SELECT 1 FROM bound_completions AS completion
                WHERE completion.kind IS DISTINCT FROM 'completion'
                   OR completion.retained_project_id IS NULL
                   OR completion.project_id
                        IS DISTINCT FROM completion.retained_project_id
                   OR completion.created_at
                        IS DISTINCT FROM completion.checkpoint_created_at
                   OR completion.id NOT BETWEEN 1 AND {_EVENT_ID_MAX}
                   OR completion.completion_generation IS NULL
                   OR (
                       completion.completion_generation < 0
                       AND completion.completion_generation::numeric
                            IS DISTINCT FROM -completion.id::numeric
                   )
                   OR (
                       completion.completion_generation >= 0
                       AND (
                           completion.migration_origin IS NOT NULL
                           OR completion.origin <> 'live'
                           OR completion.completion_generation
                                > completion.retained_generation
                           OR completion.work_version IS NULL
                       )
                   )
            ) OR EXISTS (
                SELECT 1
                FROM {schema}.checkpoints AS checkpoint
                LEFT JOIN {schema}.work_items AS work
                  ON work.id = checkpoint.work_item_id
                WHERE work.id IS NULL
                   OR (checkpoint.kind = 'completion')
                        IS DISTINCT FROM (
                            checkpoint.completion_generation IS NOT NULL
                        )
                   OR (
                       checkpoint.completion_generation >= 0
                       AND checkpoint.completion_generation
                            > work.completion_generation
                   )
            ) OR EXISTS (
                SELECT 1 FROM {schema}.work_items AS work
                WHERE work.completion_generation < -{_EVENT_ID_MAX}
                   OR (
                       work.completion_generation < 0
                       AND work.status <> 'done'
                   )
                   OR (
                       work.status = 'done'
                       AND NOT EXISTS (
                           SELECT 1 FROM bound_completions AS completion
                           WHERE completion.work_item_id = work.id
                             AND completion.completion_generation
                                    = work.completion_generation
                       )
                       AND NOT ({legacy_eventless_completion})
                   )
                   OR (
                       work.status = 'done'
                       AND work.completion_generation < 0
                       AND work.completion_generation::numeric IS DISTINCT FROM -(
                           SELECT pg_catalog.max(completion.id)::numeric
                           FROM bound_completions AS completion
                           WHERE completion.work_item_id = work.id
                       )
                   )
                   OR (
                       work.status <> 'done'
                       AND EXISTS (
                           SELECT 1 FROM bound_completions AS completion
                           WHERE completion.work_item_id = work.id
                             AND completion.completion_generation
                                    = work.completion_generation
                       )
                   )
            ) OR EXISTS (
                SELECT 1
                FROM bound_completions AS completion
                JOIN bound_completions AS other
                  ON other.work_item_id = completion.work_item_id
                 AND other.id <> completion.id
                WHERE completion.completion_generation >= 0
                  AND (
                      (
                          other.completion_generation < 0
                          AND other.id >= completion.id
                      )
                      OR (
                          other.completion_generation >= 0
                          AND (
                              (
                                  other.completion_generation
                                      < completion.completion_generation
                                  AND other.id >= completion.id
                              )
                              OR (
                                  other.completion_generation
                                      > completion.completion_generation
                                  AND other.id <= completion.id
                              )
                          )
                      )
                  )
            ) OR EXISTS (
                SELECT 1
                FROM reopen_events AS reopen
                LEFT JOIN {schema}.work_items AS work
                  ON work.id = reopen.work_item_id
                WHERE work.id IS NULL
                   OR reopen.project_id IS DISTINCT FROM work.project_id
                   OR reopen.id NOT BETWEEN 1 AND {_EVENT_ID_MAX}
                   OR reopen.reopen_generation IS NULL
                   OR reopen.reopen_generation = 0
                   OR (
                       reopen.reopen_generation < 0
                       AND reopen.reopen_generation::numeric
                            IS DISTINCT FROM -reopen.id::numeric
                   )
                   OR (
                       reopen.reopen_generation > 0
                       AND (
                           reopen.origin <> 'live'
                           OR reopen.reopen_generation > work.completion_generation
                           OR reopen.work_version IS NULL
                           OR (
                               reopen.metadata ->> 'from_status'
                                   IN ('done', 'deferred', 'wont-do', 'promoted')
                           ) IS NOT TRUE
                           OR reopen.metadata ->> 'to_status'
                                IS DISTINCT FROM 'pending'
                           OR reopen.metadata -> 'changes' -> 'status' ->> 'before'
                                IS DISTINCT FROM reopen.metadata ->> 'from_status'
                           OR reopen.metadata -> 'changes' -> 'status' ->> 'after'
                                IS DISTINCT FROM 'pending'
                       )
                   )
            ) OR EXISTS (
                SELECT 1 FROM {schema}.work_events AS event
                WHERE event.event_type <> 'work_reopened'
                  AND event.reopen_generation IS NOT NULL
            ) OR EXISTS (
                SELECT work.id
                FROM {schema}.work_items AS work
                LEFT JOIN reopen_events AS reopen
                  ON reopen.work_item_id = work.id
                 AND reopen.reopen_generation > 0
                WHERE work.completion_generation > 0
                GROUP BY work.id, work.completion_generation
                HAVING pg_catalog.count(reopen.id) <> work.completion_generation
                    OR pg_catalog.min(reopen.reopen_generation) <> 1
                    OR pg_catalog.max(reopen.reopen_generation)
                        <> work.completion_generation
                    OR pg_catalog.count(DISTINCT reopen.reopen_generation)
                        <> work.completion_generation
            ) OR EXISTS (
                SELECT 1 FROM ordered_live AS completion
                WHERE completion.work_version IS NULL
                   OR (
                       completion.prior_id IS NOT NULL
                       AND completion.work_version <= completion.prior_version
                   )
            ) OR EXISTS (
                SELECT 1
                FROM bound_completions AS completion
                JOIN reopen_events AS reopen
                  ON reopen.work_item_id = completion.work_item_id
                 AND reopen.reopen_generation
                        = completion.completion_generation
                WHERE completion.completion_generation > 0
                  AND (
                      reopen.origin <> 'live'
                      OR reopen.work_version IS NULL
                      OR completion.work_version <= reopen.work_version
                  )
            ) OR EXISTS (
                SELECT 1
                FROM bound_completions AS completion
                LEFT JOIN reopen_events AS successor
                  ON successor.work_item_id = completion.work_item_id
                 AND successor.reopen_generation
                        = completion.completion_generation + 1
                WHERE completion.completion_generation >= 0
                  AND completion.completion_generation
                        < completion.retained_generation
                  AND (
                      successor.id IS NULL
                      OR successor.origin <> 'live'
                      OR successor.metadata ->> 'from_status'
                            IS DISTINCT FROM 'done'
                      OR successor.metadata ->> 'to_status'
                            IS DISTINCT FROM 'pending'
                      OR successor.metadata -> 'changes' -> 'status' ->> 'before'
                            IS DISTINCT FROM 'done'
                      OR successor.metadata -> 'changes' -> 'status' ->> 'after'
                            IS DISTINCT FROM 'pending'
                      OR successor.work_version IS NULL
                      OR completion.work_version >= successor.work_version
                  )
            ) OR EXISTS (
                SELECT 1 FROM bound_completions AS completion
                WHERE completion.completion_generation
                        = completion.retained_generation
                  AND completion.retained_status = 'done'
                  AND completion.origin = 'live'
                  AND completion.work_version > completion.retained_version
            ) OR EXISTS (
                SELECT 1
                FROM completion_events AS live
                JOIN completion_events AS backfill
                  ON backfill.work_item_id = live.work_item_id
                 AND backfill.origin = 'backfill'
                WHERE live.origin = 'live' AND live.id <= backfill.id
            )
            """
        )
    )
    if invalid:
        raise RuntimeError("0019 produced an invalid completion generation mapping")


def _require_work_event_identity_sequence(schema: str) -> None:
    bind = op.get_bind()
    sequence = bind.scalar(
        sa.text("SELECT pg_catalog.pg_get_serial_sequence(:relation, 'id')"),
        {"relation": f"{schema}.work_events"},
    )
    if not isinstance(sequence, str):
        raise RuntimeError("Cannot downgrade without the owned work_events identity sequence")
    sequence_parameters = bind.execute(
        sa.text(
            """
            SELECT seq.seqstart, seq.seqincrement, seq.seqmax,
                   seq.seqmin, seq.seqcycle, seq.seqcache
            FROM pg_catalog.pg_sequence AS seq
            WHERE seq.seqrelid = CAST(:sequence AS regclass)
            """
        ),
        {"sequence": sequence},
    ).one_or_none()
    sequence_state = bind.execute(
        sa.text(f"SELECT last_value, is_called FROM {sequence}")
    ).one_or_none()
    maximum = bind.scalar(sa.text(f"SELECT pg_catalog.max(id) FROM {schema}.work_events"))
    if sequence_parameters is None or sequence_state is None:
        raise RuntimeError("Cannot downgrade with an invalid work_events identity sequence")
    next_value = sequence_state.last_value + (
        sequence_parameters.seqincrement if sequence_state.is_called else 0
    )
    if (
        sequence_parameters.seqstart != 1
        or sequence_parameters.seqincrement != 1
        or sequence_parameters.seqmin != 1
        or sequence_parameters.seqmax != 9223372036854775807
        or sequence_parameters.seqcycle
        or sequence_parameters.seqcache != 1
        or next_value < 1
        or next_value > _EVENT_ID_MAX
        or (maximum is not None and next_value <= maximum)
    ):
        raise RuntimeError("Cannot downgrade with an invalid work_events identity sequence")


def upgrade() -> None:
    schema = _quoted_current_schema()
    if op.get_bind().scalar(sa.text("SELECT pg_catalog.getdatabaseencoding()")) != "UTF8":
        raise RuntimeError("0019_structured_completion_evidence requires UTF-8 encoding")
    _lock_upgrade_relations(schema)
    _require_clean_0018_history(schema)
    _add_generation_columns(schema)
    _create_artifact_validator(schema)
    _create_evidence_tables()
    _create_episode_validators(schema)
    _create_work_lifecycle_guards(schema)
    _create_completion_fact_guards(schema)
    _create_immutability_guards(schema)
    _normalize_phase11_privileges(schema)
    _advance_work_event_sequence(schema)
    _require_work_event_identity_sequence(schema)
    _validate_upgraded_history(schema)
    original_search_path = op.get_bind().scalar(
        sa.text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(original_search_path, str):
        raise RuntimeError("Could not preserve the upgrade search path")
    _require_intact_phase11_catalog(schema)
    op.get_bind().execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": original_search_path},
    )


def _phase11_catalog_digest(
    statement: str,
    *,
    schema: str,
    names: Sequence[str] | None = None,
    parameters: Mapping[str, object] | None = None,
    connection: Connection | None = None,
) -> str:
    bind = connection if connection is not None else op.get_bind()
    query_parameters: dict[str, object] = {
        "audit_schema": schema,
        **(parameters or {}),
    }
    if names is not None:
        query_parameters["names"] = list(names)
    rows = [
        [_normalize_catalog_value(value, schema) for value in row]
        for row in bind.execute(sa.text(statement), query_parameters)
    ]
    canonical = json.dumps(
        rows,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_catalog_value(value: object, schema: str) -> object:
    """Normalize only schema identifiers, never coincidental catalog substrings."""
    if isinstance(value, (list, tuple)):
        return [_normalize_catalog_value(item, schema) for item in value]
    if not isinstance(value, str):
        return value
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    normalized = value.replace(f"{quoted_schema}.", "<schema>.")
    normalized = normalized.replace(
        f"{quoted_schema.replace("'", "''")}.", "<schema>."
    )
    if re.fullmatch(r"[a-z_][a-z0-9_$]*", schema):
        normalized = re.sub(
            rf'(?<![A-Za-z0-9_$"]){re.escape(schema)}\.',
            "<schema>.",
            normalized,
        )
    if normalized.startswith("search_path="):
        entries = normalized.removeprefix("search_path=").split(",")
        normalized_entries = [
            "<schema>" if entry.strip() in {schema, quoted_schema} else entry.strip()
            for entry in entries
        ]
        normalized = "search_path=" + ",".join(normalized_entries)
    return normalized


def _phase10_survivor_catalog_digest(
    schema: str, *, connection: Connection | None = None
) -> str:
    """Hash every object that an eligible downgrade must leave at exact 0018."""

    bind = connection if connection is not None else op.get_bind()
    original_search_path = bind.scalar(
        sa.text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(original_search_path, str):
        raise RuntimeError("Could not preserve the survivor catalog search path")
    bind.execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": "pg_catalog"},
    )
    try:
        return _phase10_survivor_catalog_digest_on_safe_path(schema, connection=bind)
    finally:
        bind.execute(
            sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
            {"path": original_search_path},
        )


def _phase10_survivor_catalog_digest_on_safe_path(
    schema: str, *, connection: Connection
) -> str:
    """Hash the Phase 10 survivors after the wrapper fixes schema visibility."""

    return _phase11_catalog_digest(
        """
        WITH survivor(object_class, identity, attributes, definition, auxiliary) AS (
            SELECT 'relation', relation.relname,
                   pg_catalog.concat_ws('|', relation.relkind,
                       relation.relpersistence, relation.relispartition,
                       relation.relrowsecurity, relation.relforcerowsecurity,
                       relation.relreplident,
                       relation.relowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       ), COALESCE(relation.relacl::text, ''),
                       COALESCE(relation.reloptions::text, ''),
                       COALESCE(access_method.amname, ''),
                       COALESCE(tablespace.spcname, '')),
                   '', ''
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_am AS access_method
              ON access_method.oid = relation.relam
            LEFT JOIN pg_catalog.pg_tablespace AS tablespace
              ON tablespace.oid = relation.reltablespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm')
              AND relation.relname NOT IN (
                  'verification_results', 'artifact_references'
              )

            UNION ALL

            SELECT 'column', relation.relname || '.' || attribute.attname,
                   pg_catalog.concat_ws('|',
                       pg_catalog.format_type(
                           attribute.atttypid, attribute.atttypmod
                       ), attribute.attnotnull, attribute.attidentity,
                       attribute.attgenerated, COALESCE(attribute.attacl::text, ''),
                       COALESCE(collation_namespace.nspname, ''),
                       COALESCE(collation_value.collname, ''),
                       COALESCE(collation_value.collprovider::text, ''),
                       collation_value.collisdeterministic,
                       collation_value.collencoding,
                       COALESCE(collation_value.collcollate, ''),
                       COALESCE(collation_value.collctype, ''),
                       COALESCE(collation_value.colllocale, ''),
                       COALESCE(collation_value.collicurules, ''),
                       COALESCE(collation_value.collversion, '')),
                   COALESCE(pg_catalog.pg_get_expr(
                       default_value.adbin, default_value.adrelid, true
                   ), ''), ''
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = relation.oid
             AND default_value.adnum = attribute.attnum
            LEFT JOIN pg_catalog.pg_collation AS collation_value
              ON collation_value.oid = attribute.attcollation
            LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
              ON collation_namespace.oid = collation_value.collnamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm')
              AND relation.relname NOT IN (
                  'verification_results', 'artifact_references'
              )
              AND (relation.relname, attribute.attname) NOT IN (
                  ('work_items', 'completion_generation'),
                  ('checkpoints', 'completion_generation'),
                  ('work_events', 'reopen_generation')
              )

            UNION ALL

            SELECT 'constraint', relation.relname || '.' || constraint_value.conname,
                   pg_catalog.concat_ws('|', constraint_value.contype,
                       constraint_value.condeferrable,
                       constraint_value.condeferred,
                       constraint_value.convalidated,
                       constraint_value.connoinherit),
                   pg_catalog.pg_get_constraintdef(constraint_value.oid, true), ''
            FROM pg_catalog.pg_constraint AS constraint_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = constraint_value.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relname NOT IN (
                  'verification_results', 'artifact_references'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM ROWS FROM (
                      pg_catalog.unnest(
                          CAST(:phase11_constraint_relations AS text[])
                      ),
                      pg_catalog.unnest(
                          CAST(:phase11_constraint_names AS text[])
                      )
                  ) AS phase11(relation_name, constraint_name)
                  WHERE phase11.relation_name = relation.relname
                    AND phase11.constraint_name = constraint_value.conname
              )

            UNION ALL

            SELECT 'index', table_relation.relname || '.' || index_relation.relname,
                   pg_catalog.concat_ws('|', access_method.amname,
                       index_relation.relpersistence,
                       index_relation.relispartition,
                       index_relation.relowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       ), COALESCE(index_relation.reloptions::text, ''),
                       COALESCE(tablespace.spcname, ''),
                       index_value.indisunique, index_value.indisprimary,
                       index_value.indisexclusion, index_value.indimmediate,
                       index_value.indisvalid, index_value.indisready,
                       index_value.indislive, index_value.indisclustered,
                       index_value.indisreplident,
                       index_value.indnullsnotdistinct,
                       index_value.indnkeyatts, index_value.indnatts),
                   pg_catalog.pg_get_indexdef(index_relation.oid),
                   COALESCE(pg_catalog.pg_get_expr(
                       index_value.indpred, index_value.indrelid, true
                   ), '')
            FROM pg_catalog.pg_index AS index_value
            JOIN pg_catalog.pg_class AS index_relation
              ON index_relation.oid = index_value.indexrelid
            JOIN pg_catalog.pg_class AS table_relation
              ON table_relation.oid = index_value.indrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = index_relation.relnamespace
            JOIN pg_catalog.pg_am AS access_method
              ON access_method.oid = index_relation.relam
            LEFT JOIN pg_catalog.pg_tablespace AS tablespace
              ON tablespace.oid = index_relation.reltablespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND table_relation.relname NOT IN (
                  'verification_results', 'artifact_references'
              )
              AND index_relation.relname <> ALL(:phase11_indexes)

            UNION ALL

            SELECT 'trigger', relation.relname || '.' || CASE
                       WHEN trigger_value.tgisinternal THEN
                           'constraint:' || trigger_constraint.conname || ':'
                           || procedure.proname || ':' || trigger_value.tgtype::text
                       ELSE trigger_value.tgname
                   END,
                   pg_catalog.concat_ws('|', trigger_value.tgenabled,
                       trigger_value.tgtype, trigger_value.tgisinternal,
                       trigger_value.tgdeferrable,
                       trigger_value.tginitdeferred, trigger_value.tgnargs,
                       pg_catalog.encode(trigger_value.tgargs, 'hex'),
                       trigger_value.tgqual IS NOT NULL,
                       COALESCE(constraint_relation.relname, ''),
                       COALESCE(referenced_relation.relname, ''),
                       COALESCE(trigger_constraint.contype::text, ''),
                       COALESCE(trigger_constraint.condeferrable, false),
                       COALESCE(trigger_constraint.condeferred, false),
                       trigger_value.tgparentid = 0,
                       procedure_namespace.nspname
                           = CAST(:audit_schema AS text),
                       procedure_namespace.nspname = 'pg_catalog',
                       procedure.proname,
                       pg_catalog.oidvectortypes(procedure.proargtypes)),
                   CASE WHEN trigger_value.tgisinternal THEN ''
                        ELSE pg_catalog.pg_get_triggerdef(
                            trigger_value.oid, true
                        ) END,
                   ''
            FROM pg_catalog.pg_trigger AS trigger_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_value.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_proc AS procedure
              ON procedure.oid = trigger_value.tgfoid
            JOIN pg_catalog.pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            LEFT JOIN pg_catalog.pg_constraint AS trigger_constraint
              ON trigger_constraint.oid = trigger_value.tgconstraint
            LEFT JOIN pg_catalog.pg_class AS constraint_relation
              ON constraint_relation.oid = trigger_constraint.conrelid
            LEFT JOIN pg_catalog.pg_class AS referenced_relation
              ON referenced_relation.oid = trigger_constraint.confrelid
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relname NOT IN (
                  'verification_results', 'artifact_references'
              )
              AND (
                  (
                      NOT trigger_value.tgisinternal
                      AND trigger_value.tgname <> ALL(:phase11_triggers)
                  )
                  OR (
                      trigger_value.tgisinternal
                      AND COALESCE(constraint_relation.relname, '') NOT IN (
                          'verification_results', 'artifact_references'
                      )
                  )
              )

            UNION ALL

            SELECT 'sequence', sequence_relation.relname,
                   pg_catalog.concat_ws('|',
                       pg_catalog.format_type(sequence_value.seqtypid, NULL),
                       sequence_value.seqstart, sequence_value.seqincrement,
                       sequence_value.seqmax, sequence_value.seqmin,
                       sequence_value.seqcache, sequence_value.seqcycle,
                       sequence_relation.relowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       )),
                   COALESCE(owned_relation.relname, ''),
                   COALESCE(owned_attribute.attname, '')
            FROM pg_catalog.pg_sequence AS sequence_value
            JOIN pg_catalog.pg_class AS sequence_relation
              ON sequence_relation.oid = sequence_value.seqrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = sequence_relation.relnamespace
            LEFT JOIN pg_catalog.pg_depend AS dependency
              ON dependency.classid = 'pg_catalog.pg_class'::regclass
             AND dependency.objid = sequence_relation.oid
             AND dependency.deptype IN ('a', 'i')
            LEFT JOIN pg_catalog.pg_class AS owned_relation
              ON owned_relation.oid = dependency.refobjid
            LEFT JOIN pg_catalog.pg_attribute AS owned_attribute
              ON owned_attribute.attrelid = dependency.refobjid
             AND owned_attribute.attnum = dependency.refobjsubid
            WHERE namespace.nspname = CAST(:audit_schema AS text)

            UNION ALL

            SELECT 'function', procedure.proname || '('
                       || pg_catalog.oidvectortypes(procedure.proargtypes) || ')',
                   pg_catalog.concat_ws('|',
                       pg_catalog.format_type(procedure.prorettype, NULL),
                       procedure.proowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       ), COALESCE(procedure.proacl::text, ''),
                       procedure.prokind, procedure.pronargs,
                       procedure.pronargdefaults, procedure.proretset,
                       procedure.provolatile, procedure.proisstrict,
                       procedure.proparallel, procedure.prosecdef,
                       procedure.proleakproof, procedure.provariadic,
                       procedure.procost, procedure.prorows,
                       procedure.prosupport::pg_catalog.regproc::text,
                       COALESCE(procedure.proargnames::text, ''),
                       COALESCE(procedure.proargmodes::text, ''),
                       COALESCE(procedure.proallargtypes::text, ''),
                       COALESCE(procedure.proconfig::text, ''), language.lanname),
                   procedure.prosrc,
                   pg_catalog.concat_ws('|', procedure.probin,
                       COALESCE(procedure.prosqlbody::text, ''))
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            JOIN pg_catalog.pg_language AS language
              ON language.oid = procedure.prolang
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND procedure.proname <> ALL(:phase11_functions)
        )
        SELECT object_class, identity, attributes, definition, auxiliary
        FROM survivor
        ORDER BY object_class, identity, attributes, definition, auxiliary
        """,
        schema=schema,
        parameters={
            "phase11_constraint_relations": [
                relation for relation, _ in _PHASE11_CONSTRAINT_IDENTITIES
            ],
            "phase11_constraint_names": [
                constraint for _, constraint in _PHASE11_CONSTRAINT_IDENTITIES
            ],
            "phase11_indexes": list(_PHASE11_INDEX_NAMES),
            "phase11_triggers": list(_PHASE11_TRIGGER_NAMES),
            "phase11_functions": list(_PHASE11_FUNCTION_NAMES),
        },
        connection=connection,
    )


def _require_intact_phase11_catalog(quoted_schema: str) -> None:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT pg_catalog.current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("Cannot prove the Phase 11 catalog before downgrade")
    bind.execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": "pg_catalog"},
    )
    definitions: dict[str, tuple[str, Sequence[str] | None]] = {
        "relations": (
            """
                SELECT relation.relname, relation.relkind, relation.relpersistence,
                       relation.relispartition, relation.relrowsecurity,
                       relation.relforcerowsecurity, relation.relreplident,
                       relation.relowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       ),
                       (
                           SELECT pg_catalog.count(*) = 8
                              AND pg_catalog.bool_and(
                                      acl.grantee = relation.relowner
                                      AND acl.grantor = relation.relowner
                                      AND NOT acl.is_grantable
                                  )
                              AND pg_catalog.array_agg(
                                      acl.privilege_type
                                      ORDER BY acl.privilege_type
                                  ) = ARRAY[
                                      'DELETE', 'INSERT', 'MAINTAIN', 'REFERENCES',
                                      'SELECT', 'TRIGGER', 'TRUNCATE', 'UPDATE'
                                  ]::text[]
                           FROM pg_catalog.aclexplode(
                               COALESCE(
                                   relation.relacl,
                                   pg_catalog.acldefault('r', relation.relowner)
                               )
                           ) AS acl
                       ),
                       COALESCE(relation.reloptions, ARRAY[]::text[]),
                       access_method.amname,
                       COALESCE(tablespace.spcname, '')
                FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
                JOIN pg_catalog.pg_am AS access_method
                  ON access_method.oid = relation.relam
                LEFT JOIN pg_catalog.pg_tablespace AS tablespace
                  ON tablespace.oid = relation.reltablespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relname = ANY(:names)
            ORDER BY relation.relname
            """,
            ("artifact_references", "verification_results"),
        ),
        "columns": (
            """
                SELECT relation.relname, attribute.attname,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attnotnull,
                   COALESCE(
                       pg_catalog.pg_get_expr(
                           default_value.adbin, default_value.adrelid, true
                       ),
                       ''
                   ),
                   attribute.attidentity, attribute.attgenerated,
                   COALESCE(attribute.attacl::text, ''),
                   COALESCE(collation_namespace.nspname, ''),
                   COALESCE(collation_value.collname, ''),
                   COALESCE(collation_value.collprovider::text, ''),
                   collation_value.collisdeterministic,
                   collation_value.collencoding,
                   COALESCE(collation_value.collcollate, ''),
                   COALESCE(collation_value.collctype, ''),
                   COALESCE(collation_value.colllocale, ''),
                   COALESCE(collation_value.collicurules, ''),
                   COALESCE(collation_value.collversion, '')
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = relation.oid
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = relation.oid
             AND default_value.adnum = attribute.attnum
            LEFT JOIN pg_catalog.pg_collation AS collation_value
              ON collation_value.oid = attribute.attcollation
            LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
              ON collation_namespace.oid = collation_value.collnamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND NOT attribute.attisdropped
              AND attribute.attnum > 0
              AND (
                  relation.relname IN ('verification_results', 'artifact_references')
                  OR (
                      relation.relname = 'work_items'
                      AND attribute.attname = 'completion_generation'
                  )
                  OR (
                      relation.relname = 'checkpoints'
                      AND attribute.attname = 'completion_generation'
                  )
                  OR (
                      relation.relname = 'work_events'
                      AND attribute.attname = 'reopen_generation'
                  )
              )
                ORDER BY relation.relname, attribute.attname
            """,
            None,
        ),
        "constraints": (
            """
            SELECT relation.relname, constraint_value.conname,
                   constraint_value.contype, constraint_value.condeferrable,
                   constraint_value.condeferred, constraint_value.convalidated,
                   constraint_value.connoinherit,
                   pg_catalog.pg_get_constraintdef(constraint_value.oid, true)
            FROM pg_catalog.pg_constraint AS constraint_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = constraint_value.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND (
                  relation.relname IN ('verification_results', 'artifact_references')
                  OR constraint_value.conname IN (
                      'ck_work_items_completion_generation_range',
                      'ck_checkpoints_completion_generation_kind',
                      'ck_work_events_reopen_generation_kind'
                  )
              )
            ORDER BY relation.relname, constraint_value.conname
            """,
            None,
        ),
        "indexes": (
            """
                SELECT table_relation.relname, index_relation.relname,
                       access_method.amname, index_relation.relpersistence,
                       index_relation.relispartition,
                       index_relation.relowner = (
                           SELECT role.oid FROM pg_catalog.pg_roles AS role
                           WHERE role.rolname = CURRENT_USER
                       ),
                       COALESCE(index_relation.reloptions, ARRAY[]::text[]),
                       COALESCE(tablespace.spcname, ''),
                       index_value.indisunique, index_value.indisprimary,
                       index_value.indisexclusion, index_value.indimmediate,
                       index_value.indisvalid, index_value.indisready,
                       index_value.indislive, index_value.indisclustered,
                       index_value.indisreplident,
                       index_value.indnullsnotdistinct,
                       index_value.indnkeyatts, index_value.indnatts,
                   pg_catalog.pg_get_indexdef(index_relation.oid),
                   COALESCE(
                       pg_catalog.pg_get_expr(
                           index_value.indpred, index_value.indrelid, true
                       ),
                       ''
                   )
            FROM pg_catalog.pg_index AS index_value
            JOIN pg_catalog.pg_class AS index_relation
              ON index_relation.oid = index_value.indexrelid
            JOIN pg_catalog.pg_class AS table_relation
              ON table_relation.oid = index_value.indrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = index_relation.relnamespace
                JOIN pg_catalog.pg_am AS access_method
                  ON access_method.oid = index_relation.relam
                LEFT JOIN pg_catalog.pg_tablespace AS tablespace
                  ON tablespace.oid = index_relation.reltablespace
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND (
                      index_relation.relname = ANY(:names)
                      OR table_relation.relname IN (
                          'verification_results', 'artifact_references'
                      )
                  )
            ORDER BY index_relation.relname
            """,
            _PHASE11_INDEX_NAMES,
        ),
        "triggers": (
            """
            SELECT relation.relname,
                   CASE
                       WHEN trigger_value.tgisinternal THEN
                           'constraint:' || trigger_constraint.conname || ':'
                           || relation.relname || ':' || procedure.proname || ':'
                           || trigger_value.tgtype::text
                       ELSE trigger_value.tgname
                   END,
                   trigger_value.tgenabled, trigger_value.tgtype,
                   trigger_value.tgisinternal, trigger_value.tgdeferrable,
                   trigger_value.tginitdeferred, trigger_value.tgnargs,
                   pg_catalog.encode(trigger_value.tgargs, 'hex'),
                   trigger_value.tgqual IS NOT NULL,
                   COALESCE(constraint_relation.relname, ''),
                   COALESCE(referenced_relation.relname, ''),
                   COALESCE(trigger_constraint.contype::text, ''),
                   COALESCE(trigger_constraint.condeferrable, false),
                   COALESCE(trigger_constraint.condeferred, false),
                   trigger_value.tgparentid = 0,
                   procedure_namespace.nspname = CAST(:audit_schema AS text),
                   procedure_namespace.nspname = 'pg_catalog',
                   procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   CASE
                       WHEN trigger_value.tgisinternal THEN ''
                       ELSE pg_catalog.pg_get_triggerdef(trigger_value.oid, true)
                   END
            FROM pg_catalog.pg_trigger AS trigger_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_value.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_proc AS procedure
              ON procedure.oid = trigger_value.tgfoid
            JOIN pg_catalog.pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            LEFT JOIN pg_catalog.pg_constraint AS trigger_constraint
              ON trigger_constraint.oid = trigger_value.tgconstraint
            LEFT JOIN pg_catalog.pg_class AS constraint_relation
              ON constraint_relation.oid = trigger_constraint.conrelid
            LEFT JOIN pg_catalog.pg_namespace AS constraint_namespace
              ON constraint_namespace.oid = constraint_relation.relnamespace
            LEFT JOIN pg_catalog.pg_class AS referenced_relation
              ON referenced_relation.oid = trigger_constraint.confrelid
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND (
                      (
                          NOT trigger_value.tgisinternal
                          AND (
                              trigger_value.tgname = ANY(:names)
                              OR relation.relname IN (
                                  'verification_results', 'artifact_references'
                              )
                          )
                      )
                      OR (
                          trigger_value.tgisinternal
                          AND trigger_constraint.contype = 'f'
                          AND constraint_namespace.nspname
                                = CAST(:audit_schema AS text)
                          AND constraint_relation.relname IN (
                              'verification_results', 'artifact_references'
                          )
                      )
                  )
            ORDER BY relation.relname, 2, trigger_value.tgtype
            """,
            _PHASE11_TRIGGER_NAMES,
        ),
        "functions": (
            """
            SELECT procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   pg_catalog.format_type(procedure.prorettype, NULL),
                   procedure.proowner = (
                       SELECT role.oid FROM pg_catalog.pg_roles AS role
                       WHERE role.rolname = CURRENT_USER
                   ),
                   procedure.proacl IS NOT NULL,
                   (
                       SELECT pg_catalog.count(*) = 1
                          AND pg_catalog.bool_and(
                                  acl.grantee = procedure.proowner
                                  AND acl.grantor = procedure.proowner
                                  AND acl.privilege_type = 'EXECUTE'
                                  AND NOT acl.is_grantable
                              )
                       FROM pg_catalog.aclexplode(procedure.proacl) AS acl
                   ),
                   procedure.prokind, procedure.pronargs,
                   procedure.pronargdefaults, procedure.proretset,
                   procedure.provolatile, procedure.proisstrict,
                   procedure.proparallel, procedure.prosecdef,
                   procedure.proleakproof, procedure.provariadic,
                   procedure.procost, procedure.prorows,
                   procedure.prosupport::pg_catalog.regproc::text,
                   COALESCE(procedure.proargnames, ARRAY[]::text[]),
                   COALESCE(procedure.proargmodes::text, ''),
                   COALESCE(procedure.proallargtypes::text, ''),
                   COALESCE(procedure.proconfig, ARRAY[]::text[]),
                   language.lanname, procedure.prosrc, procedure.probin,
                   COALESCE(procedure.prosqlbody::text, '')
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            JOIN pg_catalog.pg_language AS language
              ON language.oid = procedure.prolang
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND procedure.proname = ANY(:names)
            ORDER BY procedure.proname,
                     pg_catalog.oidvectortypes(procedure.proargtypes)
            """,
            _PHASE11_FUNCTION_NAMES,
        ),
    }
    digests = {
        category: _phase11_catalog_digest(statement, schema=schema, names=names)
        for category, (statement, names) in definitions.items()
    }
    invalid = [
        category
        for category, digest in digests.items()
        if digest != _PHASE11_CATALOG_SHA256[category]
    ]
    if invalid:
        raise RuntimeError(
            "Cannot proceed with an indeterminate Phase 11 catalog: "
            + ", ".join(f"{category}={digests[category]}" for category in invalid)
        )
    survivor_digest = _phase10_survivor_catalog_digest(schema)
    if survivor_digest not in _PHASE10_SURVIVOR_CATALOG_SHA256S:
        raise RuntimeError(
            "Cannot proceed with an indeterminate Phase 10 survivor catalog: "
            f"survivors={survivor_digest}"
        )
    bind.execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": f"pg_catalog, {quoted_schema}"},
    )


def _phase11_downgrade_blocking_count(
    connection: Connection,
    quoted_schema: str,
    *,
    operation_ids: Sequence[int] | None = None,
    include_evidence_rows: bool = True,
) -> int:
    """Count evidence and receipt rows that make a Phase 11 downgrade unsafe.

    Downgrade uses the default whole-catalog mode after acquiring its exclusive
    locks.  The read-only operational audit supplies bounded operation-ID pages
    and counts evidence tables separately, preserving this exact predicate
    without one unbounded receipt scan.
    """

    parameters: dict[str, object] = {}
    operation_filter = ""
    if operation_ids is not None:
        parameters["operation_ids"] = list(operation_ids)
        operation_filter = (
            "AND operation.id = ANY(CAST(:operation_ids AS bigint[]))"
        )
    evidence_count_sql = (
        f"""(SELECT pg_catalog.count(*)
                    FROM {quoted_schema}.verification_results)
                + (SELECT pg_catalog.count(*)
                   FROM {quoted_schema}.artifact_references)"""
        if include_evidence_rows
        else "0"
    )
    if operation_ids is None:
        duplicate_receipt_sql = f"""
            SELECT pg_catalog.count(*)
            FROM (
                SELECT operation.project_id,
                       operation.response_body #>> '{{checkpoint,id}}'
                FROM {quoted_schema}.client_operations AS operation
                WHERE operation.operation_kind = 'complete_work'
                  AND operation.state = 'completed'
                GROUP BY operation.project_id,
                         operation.response_body #>> '{{checkpoint,id}}'
                HAVING pg_catalog.count(*) > 1
            ) AS duplicate_receipts
        """
    else:
        duplicate_receipt_sql = f"""
            SELECT pg_catalog.count(*)
            FROM {quoted_schema}.client_operations AS operation
            WHERE operation.id = ANY(CAST(:operation_ids AS bigint[]))
              AND operation.operation_kind = 'complete_work'
              AND operation.state = 'completed'
              AND NOT EXISTS (
                  SELECT 1
                  FROM {quoted_schema}.client_operations AS earlier
                  WHERE earlier.operation_kind = 'complete_work'
                    AND earlier.state = 'completed'
                    AND earlier.project_id = operation.project_id
                    AND (earlier.response_body #>> '{{checkpoint,id}}')
                          IS NOT DISTINCT FROM (
                              operation.response_body #>> '{{checkpoint,id}}'
                          )
                    AND earlier.id < operation.id
              )
              AND 1 < (
                  SELECT pg_catalog.count(*)
                  FROM {quoted_schema}.client_operations AS peer
                  WHERE peer.operation_kind = 'complete_work'
                    AND peer.state = 'completed'
                    AND peer.project_id = operation.project_id
                    AND (peer.response_body #>> '{{checkpoint,id}}')
                          IS NOT DISTINCT FROM (
                              operation.response_body #>> '{{checkpoint,id}}'
                          )
              )
        """

    blocking_count = connection.scalar(
        sa.text(
            f"""
            SELECT {evidence_count_sql}
                + (
                    SELECT pg_catalog.count(*)
                    FROM {quoted_schema}.client_operations AS operation
                    WHERE operation.operation_kind = 'complete_work'
                      {operation_filter}
                      AND (
                          (operation.state IN ('pending', 'completed')) IS NOT TRUE
                          OR operation.request_fingerprint_version IS DISTINCT FROM 1
                          OR pg_catalog.octet_length(operation.request_fingerprint_salt)
                                IS DISTINCT FROM 32
                          OR pg_catalog.octet_length(operation.request_fingerprint)
                                IS DISTINCT FROM 32
                          OR operation.response_contract_version IS DISTINCT FROM 1
                          OR operation.completed_at < operation.created_at
                          OR (
                              operation.state = 'pending'
                              AND (
                                  operation.response_status IS NOT NULL
                                  OR operation.response_body IS NOT NULL
                                  OR operation.mutation_applied IS NOT NULL
                                  OR operation.completed_at IS NOT NULL
                              )
                          )
                          OR (
                              operation.state = 'completed'
                              AND (
                                  operation.response_status IS DISTINCT FROM 200
                                  OR operation.mutation_applied IS DISTINCT FROM true
                                  OR operation.completed_at IS NULL
                                  OR pg_catalog.jsonb_typeof(operation.response_body)
                                        IS DISTINCT FROM 'object'
                                  OR pg_catalog.octet_length(
                                         operation.response_body::text
                                     ) > 1048576
                                  OR (operation.response_body ? 'completion_evidence')
                                        IS TRUE
                                  OR NOT (
                                      operation.response_body
                                          ?& ARRAY['work_item', 'checkpoint']
                                  )
                                  OR (
                                      operation.response_body
                                          - ARRAY['work_item', 'checkpoint']
                                  ) IS DISTINCT FROM '{{}}'::jsonb
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body -> 'work_item'
                                     ) IS DISTINCT FROM 'object'
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body -> 'checkpoint'
                                     ) IS DISTINCT FROM 'object'
                                  OR NOT (
                                      (operation.response_body -> 'work_item') ?& ARRAY[
                                          'id', 'project_id', 'title', 'summary',
                                          'status', 'priority', 'initial_checkpoint_id',
                                          'version', 'created_at', 'updated_at'
                                      ]
                                  )
                                  OR (
                                      (operation.response_body -> 'work_item') - ARRAY[
                                          'id', 'project_id', 'title', 'summary',
                                          'status', 'priority', 'initial_checkpoint_id',
                                          'version', 'created_at', 'updated_at'
                                      ]
                                  ) IS DISTINCT FROM '{{}}'::jsonb
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body #> '{{work_item,title}}'
                                     ) IS DISTINCT FROM 'string'
                                  OR pg_catalog.length(
                                         operation.response_body #>> '{{work_item,title}}'
                                     ) NOT BETWEEN 1 AND 200
                                  OR pg_catalog.btrim(
                                         operation.response_body #>> '{{work_item,title}}'
                                     ) = ''
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body #> '{{work_item,summary}}'
                                     ) IS DISTINCT FROM 'string'
                                  OR pg_catalog.length(
                                         operation.response_body #>> '{{work_item,summary}}'
                                     ) NOT BETWEEN 1 AND 1000
                                  OR pg_catalog.btrim(
                                         operation.response_body #>> '{{work_item,summary}}'
                                     ) = ''
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body #> '{{work_item,priority}}'
                                     ) IS DISTINCT FROM 'number'
                                  OR operation.response_body #>> '{{work_item,priority}}'
                                        !~ '^(0|[1-9][0-9]{{0,2}})$'
                                  OR CASE
                                         WHEN operation.response_body
                                                #>> '{{work_item,priority}}'
                                                ~ '^(0|[1-9][0-9]{{0,2}})$'
                                         THEN (
                                             operation.response_body
                                                 #>> '{{work_item,priority}}'
                                         )::integer NOT BETWEEN 0 AND 100
                                         ELSE true
                                     END
                                  OR pg_catalog.jsonb_typeof(
                                         operation.response_body #> '{{work_item,version}}'
                                     ) IS DISTINCT FROM 'number'
                                  OR operation.response_body #>> '{{work_item,version}}'
                                        !~ '^[1-9][0-9]{{0,9}}$'
                                  OR CASE
                                         WHEN operation.response_body
                                                #>> '{{work_item,version}}'
                                                ~ '^[1-9][0-9]{{0,9}}$'
                                         THEN (
                                             operation.response_body
                                                 #>> '{{work_item,version}}'
                                         )::numeric > 2147483647
                                         ELSE true
                                     END
                                  OR NOT EXISTS (
                                      SELECT 1
                                      FROM {quoted_schema}.work_items AS work
                                      JOIN {quoted_schema}.checkpoints AS checkpoint
                                        ON checkpoint.work_item_id = work.id
                                       AND checkpoint.kind = 'completion'
                                      JOIN {quoted_schema}.work_events AS event
                                        ON event.work_item_id = checkpoint.work_item_id
                                       AND event.checkpoint_id = checkpoint.id
                                       AND event.event_type = 'work_completed'
                                      WHERE operation.project_id = work.project_id
                                        AND work.project_id::text
                                              = operation.response_body
                                                  #>> '{{work_item,project_id}}'
                                        AND work.id::text
                                              = operation.response_body
                                                  #>> '{{work_item,id}}'
                                        AND work.initial_checkpoint_id::text
                                              = operation.response_body
                                                  #>> '{{work_item,initial_checkpoint_id}}'
                                        AND pg_catalog.regexp_replace(
                                              pg_catalog.to_char(
                                                  pg_catalog.timezone(
                                                      'UTC', work.created_at
                                                  ),
                                                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                              ),
                                              '\\.000000Z$', 'Z'
                                            ) = operation.response_body
                                                  #>> '{{work_item,created_at}}'
                                        AND operation.response_body
                                              #>> '{{work_item,status}}' = 'done'
                                        AND event.metadata -> 'work_version'
                                              = operation.response_body
                                                  #> '{{work_item,version}}'
                                        AND CASE
                                            WHEN pg_catalog.pg_input_is_valid(
                                                operation.response_body
                                                    #>> '{{work_item,updated_at}}',
                                                'timestamp with time zone'
                                            )
                                            THEN (
                                                operation.response_body
                                                    #>> '{{work_item,updated_at}}'
                                            )::timestamptz <= event.created_at
                                            AND pg_catalog.regexp_replace(
                                                  pg_catalog.to_char(
                                                      pg_catalog.timezone(
                                                          'UTC', (
                                                              operation.response_body
                                                                  #>> '{{work_item,updated_at}}'
                                                          )::timestamptz
                                                      ),
                                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                                  ),
                                                  '\\.000000Z$', 'Z'
                                                ) = operation.response_body
                                                      #>> '{{work_item,updated_at}}'
                                            ELSE false
                                        END
                                        AND operation.response_body -> 'checkpoint'
                                              IS NOT DISTINCT FROM (
                                                  pg_catalog.jsonb_build_object(
                                                      'id', checkpoint.id,
                                                      'work_item_id',
                                                          checkpoint.work_item_id,
                                                      'kind', checkpoint.kind,
                                                      'prompt', checkpoint.prompt,
                                                      'source_client',
                                                          checkpoint.source_client,
                                                      'source_session_id',
                                                          checkpoint.source_session_id,
                                                      'source_model',
                                                          checkpoint.source_model,
                                                      'source_session_url',
                                                          checkpoint.source_session_url,
                                                      'repository_branch',
                                                          checkpoint.repository_branch,
                                                      'verified_against',
                                                          checkpoint.verified_against,
                                                      'tags', checkpoint.tags,
                                                      'source_metadata',
                                                          checkpoint.source_metadata,
                                                      'migration_origin',
                                                          checkpoint.migration_origin,
                                                      'legacy_record_id',
                                                          checkpoint.legacy_record_id,
                                                      'created_at',
                                                          pg_catalog.regexp_replace(
                                                              pg_catalog.to_char(
                                                                  pg_catalog.timezone(
                                                                      'UTC',
                                                                      checkpoint.created_at
                                                                  ),
                                                                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                                              ),
                                                              '\\.000000Z$', 'Z'
                                                          )
                                                  )
                                                  || CASE
                                                      WHEN pg_catalog.cardinality(
                                                          checkpoint.affected_paths
                                                      ) > 0
                                                      THEN pg_catalog.jsonb_build_object(
                                                          'affected_paths',
                                                          checkpoint.affected_paths
                                                      )
                                                      ELSE '{{}}'::jsonb
                                                  END
                                              )
                                  )
                              )
                          )
                      )
                )
                + (
                    {duplicate_receipt_sql}
                )
            """
        ),
        parameters,
    )
    if not isinstance(blocking_count, int):
        raise RuntimeError("Could not count Phase 11 downgrade blockers")
    return blocking_count


def _require_eligible_downgrade(schema: str) -> None:
    bind = op.get_bind()
    if _phase11_downgrade_blocking_count(bind, schema):
        raise RuntimeError(
            "Cannot downgrade structured completion evidence after evidence was used "
            "or while receipt state is indeterminate"
        )
    _validate_upgraded_history(schema)
    _require_work_event_identity_sequence(schema)
    _require_clean_0018_history(schema)


def _drop_phase11_guards(schema: str) -> None:
    trigger_tables = (
        ("work_events", "completion_reopen_event_episode_guard"),
        ("work_events", "completion_lifecycle_event_insert_guard"),
        ("work_items", "completion_generation_reopen_guard"),
        ("work_items", "completion_state_episode_guard"),
        ("work_items", "completion_unsealed_deletion_guard"),
        ("work_items", "completion_pending_exit_guard"),
        ("work_items", "completion_episode_departure_guard"),
        ("work_items", "completion_generation_guard"),
        ("checkpoints", "completion_checkpoint_episode_guard"),
        ("checkpoints", "completion_checkpoint_insert_guard"),
        ("verification_results", "verification_results_episode_guard"),
        ("verification_results", "verification_results_insert_guard"),
        ("verification_results", "verification_results_immutable"),
        ("verification_results", "verification_results_truncate_guard"),
        ("artifact_references", "artifact_references_episode_guard"),
        ("artifact_references", "artifact_references_insert_guard"),
        ("artifact_references", "artifact_references_immutable"),
        ("artifact_references", "artifact_references_truncate_guard"),
        ("work_events", "work_events_phase11_truncate_guard"),
        ("client_operations", "client_operations_phase11_truncate_guard"),
    )
    for table, trigger in trigger_tables:
        op.execute(f"DROP TRIGGER {trigger} ON {schema}.{table}")


def downgrade() -> None:
    bind = op.get_bind()
    isolation = bind.scalar(sa.text("SELECT pg_catalog.current_setting('transaction_isolation')"))
    if isolation != "read committed":
        raise RuntimeError(
            "0019_structured_completion_evidence downgrade requires READ COMMITTED isolation"
        )
    schema = _quoted_current_schema()
    op.execute("SET LOCAL lock_timeout = '5s'")
    for relation in (
        "client_operations",
        "work_items",
        "checkpoints",
        "verification_results",
        "artifact_references",
        "work_events",
    ):
        op.execute(f"LOCK TABLE {schema}.{relation} IN ACCESS EXCLUSIVE MODE")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _require_intact_phase11_catalog(schema)
    _require_eligible_downgrade(schema)
    _drop_phase11_guards(schema)

    # Drop functions with table/column dependencies before their relations.
    for function in (
        "mnemonic_require_completion_reopen_event_episode()",
        "mnemonic_guard_completion_lifecycle_event_insert()",
        "mnemonic_require_completion_evidence_episode()",
        "mnemonic_guard_completion_evidence_insert()",
        "mnemonic_require_completion_checkpoint_episode()",
        "mnemonic_guard_completion_checkpoint_insert()",
        "mnemonic_require_completion_generation_reopen()",
        "mnemonic_require_completion_state_episode()",
        "mnemonic_guard_completion_unsealed_deletion()",
        "mnemonic_guard_completion_pending_exit()",
        "mnemonic_guard_completion_episode_departure()",
        "mnemonic_guard_completion_generation()",
        "mnemonic_reject_completion_evidence_mutation()",
        "mnemonic_reject_completion_evidence_truncate()",
        "mnemonic_reject_phase11_history_truncate()",
        "mnemonic_completion_episode_is_sealed(uuid, bigint)",
        "mnemonic_completion_evidence_text_bytes_v1(uuid, uuid)",
    ):
        op.execute(f"DROP FUNCTION {schema}.{function}")

    op.drop_index("uq_work_events_reopen_generation", table_name="work_events")
    op.drop_constraint(
        op.f("ck_work_events_reopen_generation_kind"),
        "work_events",
        type_="check",
    )
    op.drop_column("work_events", "reopen_generation")
    op.drop_index("uq_checkpoints_completion_generation", table_name="checkpoints")
    op.drop_constraint(
        op.f("ck_checkpoints_completion_generation_kind"),
        "checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_work_items_completion_generation_range"),
        "work_items",
        type_="check",
    )
    op.drop_column("checkpoints", "completion_generation")
    op.drop_column("work_items", "completion_generation")
    op.drop_index("ix_work_events_live_completion_version_order", table_name="work_events")
    op.drop_index("ix_work_events_completion_evidence_history", table_name="work_events")
    op.drop_index(
        "ix_client_operations_completion_checkpoint_receipt",
        table_name="client_operations",
    )
    op.drop_index(
        "ix_client_operations_completion_receipt_correspondence",
        table_name="client_operations",
    )
    op.drop_index(
        "ix_artifact_references_completion_checkpoint_id_id",
        table_name="artifact_references",
    )
    op.drop_index(
        "ix_verification_results_completion_checkpoint_id_id",
        table_name="verification_results",
    )
    op.drop_table("artifact_references")
    op.drop_table("verification_results")

    op.execute(
        f"DROP FUNCTION {schema}.mnemonic_completion_artifact_reference_v1_is_valid(text, text)"
    )
    bind.execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": f"{schema}, pg_catalog"},
    )
