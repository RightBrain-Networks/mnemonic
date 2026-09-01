"""Add an immutable, conservatively backfilled per-work event timeline.

Revision ID: 0010_work_events
Revises: 0009_ready_work_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_work_events"
down_revision: str | None = "0009_ready_work_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RELATIONSHIP_EVENT_TYPES = (
    "'dependency_added', 'dependency_removed', "
    "'relationship_added', 'relationship_removed'"
)
CHECKPOINT_EVENT_TYPES = "'work_created', 'checkpoint_added', 'work_completed'"
LEASE_EVENT_TYPES = "'work_claimed', 'work_released'"


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0010_work_events requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _upgrade_leases() -> None:
    op.add_column(
        "work_leases",
        sa.Column("lease_generation_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "work_leases",
        sa.Column("pending_release_id", sa.UUID(), nullable=True),
    )
    op.execute(
        """
        UPDATE work_leases
        SET lease_generation_id = gen_random_uuid()
        WHERE lease_generation_id IS NULL
        """
    )
    op.alter_column(
        "work_leases",
        "lease_generation_id",
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )
    op.create_unique_constraint(
        "uq_work_leases_lease_generation_id",
        "work_leases",
        ["lease_generation_id"],
    )
    op.create_index(
        "uq_work_leases_pending_release_id",
        "work_leases",
        ["pending_release_id"],
        unique=True,
        postgresql_where=sa.text("pending_release_id IS NOT NULL"),
    )


def _create_validation_functions(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_has_non_whitespace(value text)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT pg_catalog.length(
                pg_catalog.translate(
                    value,
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
                    pg_catalog.chr(12288),
                    ''
                )
            ) > 0
        $function$;

        CREATE FUNCTION {schema}.mnemonic_work_event_metadata_v1_is_valid(
            p_event_type text,
            p_origin text,
            p_work_item_id uuid,
            p_checkpoint_id uuid,
            p_lease_generation_id uuid,
            p_lease_release_id uuid,
            p_relationship_id uuid,
            p_relationship_source_work_item_id uuid,
            p_relationship_target_work_item_id uuid,
            p_relationship_context_checkpoint_work_item_id uuid,
            p_relationship_context_checkpoint_id uuid,
            p_metadata_version smallint,
            p_metadata jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_keys text[];
            v_initial jsonb;
            v_changes jsonb;
            v_change jsonb;
            v_before jsonb;
            v_after jsonb;
            v_key text;
            v_datetime text;
            v_relationship_type text;
        BEGIN
            IF p_metadata_version <> 1
               OR p_metadata IS NULL
               OR pg_catalog.jsonb_typeof(p_metadata) <> 'object'
               OR pg_catalog.octet_length(p_metadata::text) > 16384 THEN
                RETURN false;
            END IF;

            IF EXISTS (
                WITH RECURSIVE nodes(value) AS (
                    SELECT p_metadata
                    UNION ALL
                    SELECT child.value
                    FROM nodes
                    CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(
                        CASE pg_catalog.jsonb_typeof(nodes.value)
                            WHEN 'array' THEN nodes.value
                            WHEN 'object' THEN pg_catalog.jsonb_path_query_array(
                                nodes.value,
                                '$.*'::pg_catalog.jsonpath
                            )
                            ELSE '[]'::jsonb
                        END
                    ) AS child(value)
                )
                SELECT 1
                FROM nodes
                CROSS JOIN LATERAL pg_catalog.jsonb_object_keys(
                    CASE
                        WHEN pg_catalog.jsonb_typeof(nodes.value) = 'object'
                            THEN nodes.value
                            ELSE '{{}}'::jsonb
                    END
                ) AS object_key(key)
                WHERE pg_catalog.lower(object_key.key) = ANY (
                    ARRAY[
                        'lease_token',
                        'claim_request_id',
                        'api_key',
                        'authorization',
                        'cookie',
                        'secret'
                    ]::text[]
                )
            ) THEN
                RETURN false;
            END IF;

            IF p_event_type = 'work_created' THEN
                IF p_origin = 'backfill' THEN
                    RETURN p_metadata = '{{}}'::jsonb;
                END IF;
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                IF v_keys IS DISTINCT FROM ARRAY['initial']::text[]
                   OR pg_catalog.jsonb_typeof(p_metadata -> 'initial') <> 'object' THEN
                    RETURN false;
                END IF;
                v_initial := p_metadata -> 'initial';
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(v_initial) AS initial_key(key);
                RETURN v_keys IS NOT DISTINCT FROM
                           ARRAY['priority', 'status', 'summary', 'title', 'version']::text[]
                    AND pg_catalog.jsonb_typeof(v_initial -> 'title') = 'string'
                    AND pg_catalog.length(v_initial ->> 'title') <= 200
                    AND {schema}.mnemonic_has_non_whitespace(v_initial ->> 'title')
                    AND pg_catalog.jsonb_typeof(v_initial -> 'summary') = 'string'
                    AND pg_catalog.length(v_initial ->> 'summary') <= 1000
                    AND {schema}.mnemonic_has_non_whitespace(v_initial ->> 'summary')
                    AND pg_catalog.jsonb_typeof(v_initial -> 'status') = 'string'
                    AND v_initial ->> 'status' IN ('open', 'wont-do', 'promoted')
                    AND pg_catalog.jsonb_typeof(v_initial -> 'priority') = 'number'
                    AND v_initial ->> 'priority' ~ '^(0|[1-9][0-9]*)$'
                    AND (v_initial ->> 'priority')::integer BETWEEN 0 AND 100
                    AND pg_catalog.jsonb_typeof(v_initial -> 'version') = 'number'
                    AND v_initial ->> 'version' = '1';
            END IF;

            IF p_event_type IN (
                'work_updated',
                'work_status_changed',
                'work_reopened'
            ) THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                IF p_event_type = 'work_updated' THEN
                    IF v_keys IS DISTINCT FROM ARRAY['changes', 'work_version']::text[] THEN
                        RETURN false;
                    END IF;
                ELSIF v_keys IS DISTINCT FROM
                    ARRAY['changes', 'from_status', 'to_status', 'work_version']::text[] THEN
                    RETURN false;
                END IF;
                IF pg_catalog.jsonb_typeof(p_metadata -> 'changes') <> 'object'
                   OR p_metadata -> 'changes' = '{{}}'::jsonb
                   OR (
                       SELECT pg_catalog.count(*)
                       FROM pg_catalog.jsonb_object_keys(
                           p_metadata -> 'changes'
                       )
                   ) > 4
                   OR pg_catalog.jsonb_typeof(p_metadata -> 'work_version') <> 'number'
                   OR p_metadata ->> 'work_version' !~ '^[1-9][0-9]*$' THEN
                    RETURN false;
                END IF;
                v_changes := p_metadata -> 'changes';
                FOR v_key IN
                    SELECT key
                    FROM pg_catalog.jsonb_object_keys(v_changes) AS change_key(key)
                LOOP
                    IF v_key NOT IN ('title', 'summary', 'priority', 'status') THEN
                        RETURN false;
                    END IF;
                    v_change := v_changes -> v_key;
                    IF pg_catalog.jsonb_typeof(v_change) <> 'object' THEN
                        RETURN false;
                    END IF;
                    SELECT pg_catalog.array_agg(key ORDER BY key)
                    INTO v_keys
                    FROM pg_catalog.jsonb_object_keys(v_change) AS value_key(key);
                    IF v_keys IS DISTINCT FROM ARRAY['after', 'before']::text[] THEN
                        RETURN false;
                    END IF;
                    v_before := v_change -> 'before';
                    v_after := v_change -> 'after';
                    IF v_key = 'title' THEN
                        IF pg_catalog.jsonb_typeof(v_before) <> 'string'
                           OR pg_catalog.jsonb_typeof(v_after) <> 'string'
                           OR pg_catalog.length(v_before #>> '{{}}') > 200
                           OR pg_catalog.length(v_after #>> '{{}}') > 200
                           OR NOT {schema}.mnemonic_has_non_whitespace(v_before #>> '{{}}')
                           OR NOT {schema}.mnemonic_has_non_whitespace(v_after #>> '{{}}') THEN
                            RETURN false;
                        END IF;
                    ELSIF v_key = 'summary' THEN
                        IF pg_catalog.jsonb_typeof(v_before) <> 'string'
                           OR pg_catalog.jsonb_typeof(v_after) <> 'string'
                           OR pg_catalog.length(v_before #>> '{{}}') > 1000
                           OR pg_catalog.length(v_after #>> '{{}}') > 1000
                           OR NOT {schema}.mnemonic_has_non_whitespace(v_before #>> '{{}}')
                           OR NOT {schema}.mnemonic_has_non_whitespace(v_after #>> '{{}}') THEN
                            RETURN false;
                        END IF;
                    ELSIF v_key = 'priority' THEN
                        IF pg_catalog.jsonb_typeof(v_before) <> 'number'
                           OR pg_catalog.jsonb_typeof(v_after) <> 'number'
                           OR v_before::text !~ '^(0|[1-9][0-9]*)$'
                           OR v_after::text !~ '^(0|[1-9][0-9]*)$'
                           OR v_before::text::integer NOT BETWEEN 0 AND 100
                           OR v_after::text::integer NOT BETWEEN 0 AND 100 THEN
                            RETURN false;
                        END IF;
                    ELSE
                        IF pg_catalog.jsonb_typeof(v_before) <> 'string'
                           OR pg_catalog.jsonb_typeof(v_after) <> 'string'
                           OR v_before #>> '{{}}' NOT IN ('open', 'done', 'wont-do', 'promoted')
                           OR v_after #>> '{{}}' NOT IN (
                               'open', 'done', 'wont-do', 'promoted'
                           ) THEN
                            RETURN false;
                        END IF;
                    END IF;
                END LOOP;

                IF p_event_type = 'work_updated' THEN
                    RETURN NOT (v_changes ? 'status')
                        OR v_changes -> 'status' -> 'before'
                           = v_changes -> 'status' -> 'after';
                END IF;
                IF pg_catalog.jsonb_typeof(p_metadata -> 'from_status') <> 'string'
                   OR pg_catalog.jsonb_typeof(p_metadata -> 'to_status') <> 'string'
                   OR NOT (v_changes ? 'status')
                   OR p_metadata ->> 'from_status'
                      <> v_changes -> 'status' ->> 'before'
                   OR p_metadata ->> 'to_status'
                      <> v_changes -> 'status' ->> 'after' THEN
                    RETURN false;
                END IF;
                IF p_event_type = 'work_status_changed' THEN
                    RETURN p_metadata ->> 'from_status' = 'open'
                        AND p_metadata ->> 'to_status' IN ('wont-do', 'promoted');
                END IF;
                RETURN p_metadata ->> 'from_status' IN ('done', 'wont-do', 'promoted')
                    AND p_metadata ->> 'to_status' = 'open';
            END IF;

            IF p_event_type = 'work_claimed' THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                IF p_origin = 'live' THEN
                    IF v_keys IS DISTINCT FROM ARRAY['expires_at']::text[]
                       OR pg_catalog.jsonb_typeof(p_metadata -> 'expires_at') <> 'string' THEN
                        RETURN false;
                    END IF;
                    v_datetime := p_metadata ->> 'expires_at';
                ELSE
                    IF v_keys IS DISTINCT FROM
                           ARRAY['expiry_basis', 'observed_expires_at']::text[]
                       OR pg_catalog.jsonb_typeof(
                           p_metadata -> 'observed_expires_at'
                       ) <> 'string'
                       OR p_metadata ->> 'expiry_basis'
                          <> 'retained_lease_at_cutover' THEN
                        RETURN false;
                    END IF;
                    v_datetime := p_metadata ->> 'observed_expires_at';
                END IF;
                IF v_datetime !~ (
                    '^[0-9][0-9][0-9][0-9]-(0[1-9]|1[0-2])-'
                    '(0[1-9]|[12][0-9]|3[01])T'
                    '([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
                    '(\\.[0-9]+)?Z$'
                ) THEN
                    RETURN false;
                END IF;
                PERFORM v_datetime::timestamptz;
                RETURN true;
            END IF;

            IF p_event_type = 'work_released' THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                IF p_metadata ->> 'lease_holder_kind' = 'unattributed' THEN
                    RETURN v_keys IS NOT DISTINCT FROM
                        ARRAY['lease_holder_kind']::text[];
                END IF;
                RETURN v_keys IS NOT DISTINCT FROM ARRAY[
                        'lease_holder_client',
                        'lease_holder_kind',
                        'lease_holder_session_id'
                    ]::text[]
                    AND p_metadata ->> 'lease_holder_kind' = 'client'
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'lease_holder_client'
                    ) = 'string'
                    AND pg_catalog.length(p_metadata ->> 'lease_holder_client') <= 80
                    AND {schema}.mnemonic_has_non_whitespace(
                        p_metadata ->> 'lease_holder_client'
                    )
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'lease_holder_session_id'
                    ) = 'string'
                    AND pg_catalog.length(
                        p_metadata ->> 'lease_holder_session_id'
                    ) <= 200
                    AND {schema}.mnemonic_has_non_whitespace(
                        p_metadata ->> 'lease_holder_session_id'
                    );
            END IF;

            IF p_event_type = 'checkpoint_added' THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                RETURN v_keys IS NOT DISTINCT FROM ARRAY['checkpoint_kind']::text[]
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'checkpoint_kind'
                    ) = 'string'
                    AND p_metadata ->> 'checkpoint_kind' IN ('context', 'progress');
            END IF;

            IF p_event_type = 'progress' THEN
                RETURN p_origin = 'live';
            END IF;

            IF p_event_type IN (
                'dependency_added',
                'dependency_removed',
                'relationship_added',
                'relationship_removed'
            ) THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                IF v_keys IS DISTINCT FROM ARRAY['relationship_type']::text[]
                   OR pg_catalog.jsonb_typeof(
                       p_metadata -> 'relationship_type'
                   ) <> 'string' THEN
                    RETURN false;
                END IF;
                v_relationship_type := p_metadata ->> 'relationship_type';
                IF v_relationship_type NOT IN (
                    'blocks',
                    'parent-child',
                    'discovered-from',
                    'duplicate-of',
                    'related'
                ) OR (
                    p_event_type IN ('dependency_added', 'dependency_removed')
                    AND v_relationship_type <> 'blocks'
                ) OR (
                    p_event_type IN ('relationship_added', 'relationship_removed')
                    AND v_relationship_type = 'blocks'
                ) THEN
                    RETURN false;
                END IF;
                IF p_relationship_id IS NULL
                   OR p_relationship_source_work_item_id IS NULL
                   OR p_relationship_target_work_item_id IS NULL
                   OR p_relationship_source_work_item_id
                      = p_relationship_target_work_item_id
                   OR p_work_item_id NOT IN (
                       p_relationship_source_work_item_id,
                       p_relationship_target_work_item_id
                   )
                   OR (
                       p_relationship_context_checkpoint_work_item_id IS NULL
                   ) <> (
                       p_relationship_context_checkpoint_id IS NULL
                   )
                   OR (
                       p_relationship_context_checkpoint_work_item_id IS NOT NULL
                       AND p_relationship_context_checkpoint_work_item_id NOT IN (
                           p_relationship_source_work_item_id,
                           p_relationship_target_work_item_id
                       )
                   )
                   OR (
                       v_relationship_type = 'discovered-from'
                       AND (
                           p_relationship_context_checkpoint_id IS NULL
                           OR p_relationship_context_checkpoint_work_item_id
                              <> p_relationship_target_work_item_id
                       )
                   )
                   OR (
                       v_relationship_type = 'related'
                       AND p_relationship_source_work_item_id
                           >= p_relationship_target_work_item_id
                   ) THEN
                    RETURN false;
                END IF;
                RETURN true;
            END IF;

            IF p_event_type = 'work_completed' THEN
                IF p_origin = 'backfill' THEN
                    RETURN p_metadata = '{{}}'::jsonb;
                END IF;
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                RETURN v_keys IS NOT DISTINCT FROM
                           ARRAY['from_status', 'to_status', 'work_version']::text[]
                    AND p_metadata ->> 'from_status' = 'open'
                    AND p_metadata ->> 'to_status' = 'done'
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'work_version'
                    ) = 'number'
                    AND p_metadata ->> 'work_version' ~ '^[1-9][0-9]*$';
            END IF;

            IF p_event_type = 'work_deleted' THEN
                SELECT pg_catalog.array_agg(key ORDER BY key)
                INTO v_keys
                FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
                RETURN v_keys IS NOT DISTINCT FROM
                           ARRAY['final_status', 'final_version']::text[]
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'final_status'
                    ) = 'string'
                    AND p_metadata ->> 'final_status' IN (
                        'open', 'done', 'wont-do', 'promoted'
                    )
                    AND pg_catalog.jsonb_typeof(
                        p_metadata -> 'final_version'
                    ) = 'number'
                    AND p_metadata ->> 'final_version' ~ '^[1-9][0-9]*$';
            END IF;

            RETURN false;
        EXCEPTION
            WHEN others THEN
                RETURN false;
        END
        $function$;
        """
    )


def _create_work_events_table() -> None:
    op.create_table(
        "work_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_client", sa.String(length=80), nullable=True),
        sa.Column("actor_session_id", sa.String(length=200), nullable=True),
        sa.Column("actor_model", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("checkpoint_id", sa.UUID(), nullable=True),
        sa.Column("lease_generation_id", sa.UUID(), nullable=True),
        sa.Column("lease_release_id", sa.UUID(), nullable=True),
        sa.Column("relationship_id", sa.UUID(), nullable=True),
        sa.Column("relationship_source_work_item_id", sa.UUID(), nullable=True),
        sa.Column("relationship_target_work_item_id", sa.UUID(), nullable=True),
        sa.Column(
            "relationship_context_checkpoint_work_item_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column("relationship_context_checkpoint_id", sa.UUID(), nullable=True),
        sa.Column(
            "metadata_version",
            sa.SmallInteger(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.String(length=16),
            server_default="live",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('work_created', 'work_updated', 'work_status_changed', "
            "'work_reopened', 'work_claimed', 'work_released', 'checkpoint_added', "
            "'progress', 'dependency_added', 'dependency_removed', "
            "'relationship_added', 'relationship_removed', 'work_completed', "
            "'work_deleted')",
            name=op.f("ck_work_events_event_type_valid"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('client', 'unattributed')",
            name=op.f("ck_work_events_actor_kind_valid"),
        ),
        sa.CheckConstraint(
            "(actor_kind = 'client' AND actor_client IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_client) "
            "AND actor_session_id IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_session_id) "
            "AND (actor_model IS NULL OR mnemonic_has_non_whitespace(actor_model))) OR "
            "(actor_kind = 'unattributed' AND actor_client IS NULL "
            "AND actor_session_id IS NULL AND actor_model IS NULL)",
            name=op.f("ck_work_events_actor_fields_valid"),
        ),
        sa.CheckConstraint(
            "(origin = 'live' AND ("
            "event_type NOT IN ('work_created', 'checkpoint_added', 'work_completed', "
            "'work_claimed', 'dependency_added', 'relationship_added', 'progress') "
            "OR actor_kind = 'client')) OR "
            "(origin = 'backfill' AND (event_type <> 'work_deleted' "
            "OR actor_kind = 'unattributed'))",
            name=op.f("ck_work_events_actor_matrix_valid"),
        ),
        sa.CheckConstraint(
            "(event_type = 'progress' AND body IS NOT NULL "
            "AND length(body) <= 4000 AND mnemonic_has_non_whitespace(body)) OR "
            "(event_type <> 'progress' AND body IS NULL)",
            name=op.f("ck_work_events_body_valid"),
        ),
        sa.CheckConstraint(
            f"(event_type IN ({CHECKPOINT_EVENT_TYPES}) AND checkpoint_id IS NOT NULL) OR "
            f"(event_type NOT IN ({CHECKPOINT_EVENT_TYPES}) AND checkpoint_id IS NULL)",
            name=op.f("ck_work_events_checkpoint_reference_valid"),
        ),
        sa.CheckConstraint(
            f"(event_type IN ({LEASE_EVENT_TYPES}) AND lease_generation_id IS NOT NULL) OR "
            f"(event_type NOT IN ({LEASE_EVENT_TYPES}) AND lease_generation_id IS NULL)",
            name=op.f("ck_work_events_lease_generation_reference_valid"),
        ),
        sa.CheckConstraint(
            "(event_type = 'work_released' AND lease_release_id IS NOT NULL) OR "
            "(event_type <> 'work_released' AND lease_release_id IS NULL)",
            name=op.f("ck_work_events_lease_release_reference_valid"),
        ),
        sa.CheckConstraint(
            f"(event_type IN ({RELATIONSHIP_EVENT_TYPES}) "
            "AND relationship_id IS NOT NULL "
            "AND relationship_source_work_item_id IS NOT NULL "
            "AND relationship_target_work_item_id IS NOT NULL "
            "AND ((relationship_context_checkpoint_work_item_id IS NULL "
            "AND relationship_context_checkpoint_id IS NULL) OR "
            "(relationship_context_checkpoint_work_item_id IS NOT NULL "
            "AND relationship_context_checkpoint_id IS NOT NULL)) "
            "AND (relationship_context_checkpoint_work_item_id IS NULL OR "
            "relationship_context_checkpoint_work_item_id IN "
            "(relationship_source_work_item_id, relationship_target_work_item_id)) "
            "AND work_item_id IN "
            "(relationship_source_work_item_id, relationship_target_work_item_id)) OR "
            f"(event_type NOT IN ({RELATIONSHIP_EVENT_TYPES}) "
            "AND relationship_id IS NULL "
            "AND relationship_source_work_item_id IS NULL "
            "AND relationship_target_work_item_id IS NULL "
            "AND relationship_context_checkpoint_work_item_id IS NULL "
            "AND relationship_context_checkpoint_id IS NULL)",
            name=op.f("ck_work_events_relationship_references_valid"),
        ),
        sa.CheckConstraint(
            "metadata_version = 1",
            name=op.f("ck_work_events_metadata_version_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object' "
            "AND octet_length(metadata::text) <= 16384",
            name=op.f("ck_work_events_metadata_envelope_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_work_event_metadata_v1_is_valid("
            "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
            "lease_release_id, relationship_id, relationship_source_work_item_id, "
            "relationship_target_work_item_id, "
            "relationship_context_checkpoint_work_item_id, "
            "relationship_context_checkpoint_id, metadata_version, metadata)",
            name=op.f("ck_work_events_metadata_v1_valid"),
        ),
        sa.CheckConstraint(
            "origin IN ('live', 'backfill')",
            name=op.f("ck_work_events_origin_valid"),
        ),
        sa.CheckConstraint(
            "origin = 'live' OR event_type IN ('work_created', 'checkpoint_added', "
            "'work_completed', 'work_claimed', 'dependency_added', "
            "'relationship_added', 'work_deleted')",
            name=op.f("ck_work_events_backfill_event_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_events_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "relationship_source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_relationship_source_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "relationship_target_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_relationship_target_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "relationship_context_checkpoint_work_item_id",
                "relationship_context_checkpoint_id",
            ],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_events_relationship_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_events")),
    )
    op.create_index(
        "uq_work_events_checkpoint_fact",
        "work_events",
        ["work_item_id", "checkpoint_id"],
        unique=True,
        postgresql_where=sa.text("checkpoint_id IS NOT NULL"),
    )
    op.create_index(
        "uq_work_events_work_created",
        "work_events",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_created'"),
    )
    op.create_index(
        "uq_work_events_work_deleted",
        "work_events",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_deleted'"),
    )
    op.create_index(
        "uq_work_events_work_claimed_fact",
        "work_events",
        ["work_item_id", "lease_generation_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_claimed'"),
    )
    op.create_index(
        "uq_work_events_work_released_fact",
        "work_events",
        ["work_item_id", "lease_generation_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_released'"),
    )
    op.create_index(
        "uq_work_events_lease_release_id",
        "work_events",
        ["lease_release_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'work_released'"),
    )
    op.create_index(
        "uq_work_events_relationship_added_fact",
        "work_events",
        ["work_item_id", "relationship_id"],
        unique=True,
        postgresql_where=sa.text(
            "event_type IN ('dependency_added', 'relationship_added')"
        ),
    )
    op.create_index(
        "uq_work_events_relationship_removed_fact",
        "work_events",
        ["work_item_id", "relationship_id"],
        unique=True,
        postgresql_where=sa.text(
            "event_type IN ('dependency_removed', 'relationship_removed')"
        ),
    )
    op.create_index(
        "ix_work_events_timeline",
        "work_events",
        [
            "project_id",
            "work_item_id",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )
    op.create_index(
        "ix_work_events_timeline_type",
        "work_events",
        [
            "project_id",
            "work_item_id",
            "event_type",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )


def _create_guard_triggers(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_work_event_source_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work record;
            v_checkpoint record;
            v_lease record;
            v_relationship record;
            v_source_client text;
            v_source_session_id text;
            v_source_model text;
            v_source_checked boolean := false;
            v_source_valid boolean;
            v_expected_model text;
        BEGIN
            IF NEW.event_type = 'work_created' THEN
                SELECT *
                INTO v_work
                FROM {schema}.work_items
                WHERE id = NEW.work_item_id;
                IF NOT FOUND
                   OR NEW.project_id IS DISTINCT FROM v_work.project_id
                   OR NEW.checkpoint_id IS DISTINCT FROM v_work.initial_checkpoint_id
                   OR NEW.created_at IS DISTINCT FROM v_work.created_at THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                SELECT *
                INTO v_checkpoint
                FROM {schema}.checkpoints
                WHERE id = NEW.checkpoint_id
                  AND work_item_id = NEW.work_item_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                IF NEW.origin = 'live' AND (
                    (NEW.metadata -> 'initial' ->> 'title') IS DISTINCT FROM v_work.title
                    OR (NEW.metadata -> 'initial' ->> 'summary')
                       IS DISTINCT FROM v_work.summary
                    OR (NEW.metadata -> 'initial' ->> 'status')
                       IS DISTINCT FROM v_work.status
                    OR (NEW.metadata -> 'initial' ->> 'priority')::integer
                       IS DISTINCT FROM v_work.priority
                    OR (NEW.metadata -> 'initial' ->> 'version')::integer
                       IS DISTINCT FROM v_work.version
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                v_source_client := v_checkpoint.source_client;
                v_source_session_id := v_checkpoint.source_session_id;
                v_source_model := v_checkpoint.source_model;
                v_source_checked := true;

            ELSIF NEW.event_type IN ('checkpoint_added', 'work_completed') THEN
                SELECT *
                INTO v_work
                FROM {schema}.work_items
                WHERE id = NEW.work_item_id;
                SELECT *
                INTO v_checkpoint
                FROM {schema}.checkpoints
                WHERE id = NEW.checkpoint_id
                  AND work_item_id = NEW.work_item_id;
                IF NOT FOUND
                   OR NEW.checkpoint_id IS NOT DISTINCT FROM v_work.initial_checkpoint_id
                   OR NEW.created_at IS DISTINCT FROM v_checkpoint.created_at
                   OR (
                       NEW.event_type = 'checkpoint_added'
                       AND (
                           v_checkpoint.kind NOT IN ('context', 'progress')
                           OR NEW.metadata ->> 'checkpoint_kind'
                              IS DISTINCT FROM v_checkpoint.kind
                       )
                   )
                   OR (
                       NEW.event_type = 'work_completed'
                       AND v_checkpoint.kind <> 'completion'
                   )
                   OR (
                       NEW.event_type = 'work_completed'
                       AND NEW.origin = 'live'
                       AND (
                           v_work.status <> 'done'
                           OR (NEW.metadata ->> 'work_version')::integer
                              IS DISTINCT FROM v_work.version
                       )
                   ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                v_source_client := v_checkpoint.source_client;
                v_source_session_id := v_checkpoint.source_session_id;
                v_source_model := v_checkpoint.source_model;
                v_source_checked := true;

            ELSIF NEW.event_type = 'work_claimed' THEN
                SELECT *
                INTO v_lease
                FROM {schema}.work_leases
                WHERE work_item_id = NEW.work_item_id
                  AND lease_generation_id = NEW.lease_generation_id
                FOR KEY SHARE;
                IF NOT FOUND
                   OR NEW.created_at IS DISTINCT FROM v_lease.acquired_at
                   OR (
                       NEW.origin = 'live'
                       AND (NEW.metadata ->> 'expires_at')::timestamptz
                           IS DISTINCT FROM v_lease.expires_at
                   )
                   OR (
                       NEW.origin = 'backfill'
                       AND (NEW.metadata ->> 'observed_expires_at')::timestamptz
                           IS DISTINCT FROM v_lease.expires_at
                   ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                v_source_client := v_lease.holder_client;
                v_source_session_id := v_lease.holder_session_id;
                v_source_model := NULL;
                v_source_checked := true;

            ELSIF NEW.event_type = 'work_released' THEN
                SELECT *
                INTO v_lease
                FROM {schema}.work_leases
                WHERE work_item_id = NEW.work_item_id
                  AND lease_generation_id = NEW.lease_generation_id
                FOR KEY SHARE;
                IF NOT FOUND
                   OR v_lease.pending_release_id IS NULL
                   OR NEW.lease_release_id IS DISTINCT FROM v_lease.pending_release_id THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                v_source_valid :=
                    {schema}.mnemonic_has_non_whitespace(v_lease.holder_client)
                    AND {schema}.mnemonic_has_non_whitespace(
                        v_lease.holder_session_id
                    );
                IF v_source_valid THEN
                    IF NEW.metadata ->> 'lease_holder_kind' <> 'client'
                       OR NEW.metadata ->> 'lease_holder_client'
                          IS DISTINCT FROM v_lease.holder_client
                       OR NEW.metadata ->> 'lease_holder_session_id'
                          IS DISTINCT FROM v_lease.holder_session_id THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = 'work event source fact does not match retained state';
                    END IF;
                ELSIF NEW.metadata
                    IS DISTINCT FROM '{{"lease_holder_kind": "unattributed"}}'::jsonb THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                RETURN NEW;

            ELSIF NEW.event_type = 'work_deleted' THEN
                SELECT *
                INTO v_work
                FROM {schema}.work_items
                WHERE id = NEW.work_item_id;
                IF NOT FOUND
                   OR v_work.deleted_at IS NULL
                   OR NEW.created_at IS DISTINCT FROM v_work.deleted_at
                   OR NEW.metadata ->> 'final_status'
                      IS DISTINCT FROM v_work.status
                   OR (NEW.metadata ->> 'final_version')::integer
                      IS DISTINCT FROM v_work.version THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                RETURN NEW;

            ELSIF NEW.event_type IN (
                'dependency_added',
                'dependency_removed',
                'relationship_added',
                'relationship_removed'
            ) THEN
                SELECT *
                INTO v_relationship
                FROM {schema}.work_relationships
                WHERE id = NEW.relationship_id
                FOR KEY SHARE;
                IF NOT FOUND
                   OR NEW.project_id IS DISTINCT FROM v_relationship.project_id
                   OR NEW.metadata ->> 'relationship_type'
                      IS DISTINCT FROM v_relationship.relationship_type
                   OR NEW.relationship_source_work_item_id
                      IS DISTINCT FROM v_relationship.source_work_item_id
                   OR NEW.relationship_target_work_item_id
                      IS DISTINCT FROM v_relationship.target_work_item_id
                   OR NEW.relationship_context_checkpoint_work_item_id
                      IS DISTINCT FROM
                         v_relationship.context_checkpoint_work_item_id
                   OR NEW.relationship_context_checkpoint_id
                      IS DISTINCT FROM v_relationship.context_checkpoint_id THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event source fact does not match retained state';
                END IF;
                IF NEW.event_type IN ('dependency_added', 'relationship_added') THEN
                    IF NEW.created_at IS DISTINCT FROM v_relationship.created_at THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = 'work event source fact does not match retained state';
                    END IF;
                    v_source_client := v_relationship.created_by_client;
                    v_source_session_id := v_relationship.created_by_session_id;
                    v_source_model := v_relationship.created_by_model;
                    v_source_checked := true;
                ELSE
                    RETURN NEW;
                END IF;
            ELSE
                RETURN NEW;
            END IF;

            IF v_source_checked THEN
                v_source_valid :=
                    {schema}.mnemonic_has_non_whitespace(v_source_client)
                    AND {schema}.mnemonic_has_non_whitespace(v_source_session_id);
                v_expected_model := CASE
                    WHEN v_source_model IS NULL
                      OR {schema}.mnemonic_has_non_whitespace(v_source_model)
                    THEN v_source_model
                    ELSE NULL
                END;
                IF NEW.origin = 'live' THEN
                    IF NEW.actor_kind <> 'client'
                       OR NEW.actor_client IS DISTINCT FROM v_source_client
                       OR NEW.actor_session_id IS DISTINCT FROM v_source_session_id
                       OR NEW.actor_model IS DISTINCT FROM v_source_model THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = 'work event actor does not match retained source';
                    END IF;
                ELSIF v_source_valid THEN
                    IF NEW.actor_kind <> 'client'
                       OR NEW.actor_client IS DISTINCT FROM v_source_client
                       OR NEW.actor_session_id IS DISTINCT FROM v_source_session_id
                       OR NEW.actor_model IS DISTINCT FROM v_expected_model THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = 'work event actor does not match retained source';
                    END IF;
                ELSIF NEW.actor_kind <> 'unattributed'
                   OR NEW.actor_client IS NOT NULL
                   OR NEW.actor_session_id IS NOT NULL
                   OR NEW.actor_model IS NOT NULL THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work event actor does not match retained source';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_event_source_fact_guard
        BEFORE INSERT ON {schema}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_work_event_source_fact();

        CREATE FUNCTION {schema}.mnemonic_guard_work_event_state()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NEW.event_type IN ('dependency_added', 'relationship_added') THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM {schema}.work_relationships AS relationship
                    WHERE relationship.id = NEW.relationship_id
                      AND relationship.project_id = NEW.project_id
                      AND relationship.relationship_type
                          = NEW.metadata ->> 'relationship_type'
                      AND relationship.source_work_item_id
                          = NEW.relationship_source_work_item_id
                      AND relationship.target_work_item_id
                          = NEW.relationship_target_work_item_id
                      AND relationship.context_checkpoint_work_item_id
                          IS NOT DISTINCT FROM
                              NEW.relationship_context_checkpoint_work_item_id
                      AND relationship.context_checkpoint_id
                          IS NOT DISTINCT FROM
                              NEW.relationship_context_checkpoint_id
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'added relationship event requires retained edge at commit';
                END IF;
            ELSIF NEW.event_type IN (
                'dependency_removed',
                'relationship_removed'
            ) THEN
                IF EXISTS (
                    SELECT 1
                    FROM {schema}.work_relationships AS relationship
                    WHERE relationship.id = NEW.relationship_id
                ) OR NOT EXISTS (
                    SELECT 1
                    FROM {schema}.work_events AS added
                    WHERE added.work_item_id = NEW.work_item_id
                      AND added.relationship_id = NEW.relationship_id
                      AND added.event_type = CASE
                          WHEN NEW.event_type = 'dependency_removed'
                              THEN 'dependency_added'
                          ELSE 'relationship_added'
                      END
                      AND added.metadata = NEW.metadata
                      AND added.relationship_source_work_item_id
                          = NEW.relationship_source_work_item_id
                      AND added.relationship_target_work_item_id
                          = NEW.relationship_target_work_item_id
                      AND added.relationship_context_checkpoint_work_item_id
                          IS NOT DISTINCT FROM
                              NEW.relationship_context_checkpoint_work_item_id
                      AND added.relationship_context_checkpoint_id
                          IS NOT DISTINCT FROM
                              NEW.relationship_context_checkpoint_id
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'removed relationship event requires prior fact and absent edge';
                END IF;
            ELSIF NEW.event_type = 'work_released' THEN
                IF EXISTS (
                    SELECT 1
                    FROM {schema}.work_leases AS lease
                    WHERE lease.work_item_id = NEW.work_item_id
                      AND lease.lease_generation_id = NEW.lease_generation_id
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'released lease generation must be absent at commit';
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER work_event_state_guard
        AFTER INSERT ON {schema}.work_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_work_event_state();

        CREATE FUNCTION {schema}.mnemonic_guard_work_lease_release_marker()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_release_count bigint;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.pending_release_id IS NOT NULL THEN
                    SELECT pg_catalog.count(*)
                    INTO v_release_count
                    FROM {schema}.work_events AS event
                    WHERE event.event_type = 'work_released'
                      AND event.work_item_id = OLD.work_item_id
                      AND event.lease_generation_id = OLD.lease_generation_id
                      AND event.lease_release_id = OLD.pending_release_id;
                    IF v_release_count <> 1 THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = 'marked lease deletion requires one release event';
                    END IF;
                END IF;
                RETURN NULL;
            END IF;

            IF NEW.pending_release_id IS NOT NULL
               AND (
                   TG_OP = 'INSERT'
                   OR OLD.pending_release_id IS DISTINCT FROM NEW.pending_release_id
               ) THEN
                SELECT pg_catalog.count(*)
                INTO v_release_count
                FROM {schema}.work_events AS event
                WHERE event.event_type = 'work_released'
                  AND event.work_item_id = NEW.work_item_id
                  AND event.lease_generation_id = NEW.lease_generation_id
                  AND event.lease_release_id = NEW.pending_release_id;
                IF v_release_count <> 1 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'lease release marker transition requires one release event';
                END IF;
            END IF;

            IF NEW.pending_release_id IS NOT NULL
               AND EXISTS (
                   SELECT 1
                   FROM {schema}.work_leases AS retained
                   WHERE retained.work_item_id = NEW.work_item_id
                     AND retained.lease_generation_id = NEW.lease_generation_id
                     AND retained.pending_release_id = NEW.pending_release_id
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'lease release marker cannot remain set at commit';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER work_lease_release_marker_guard
        AFTER INSERT OR UPDATE OR DELETE ON {schema}.work_leases
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_work_lease_release_marker();
        """
    )


def _report_actor_fallbacks(schema: str) -> None:
    op.execute(
        f"""
        DO $diagnostics$
        DECLARE
            v_row record;
            v_field text;
            v_checkpoint_fallbacks bigint;
            v_lease_fallbacks bigint;
            v_relationship_fallbacks bigint;
        BEGIN
            FOR v_row IN
                SELECT
                    checkpoint.id,
                    ARRAY_REMOVE(ARRAY[
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                checkpoint.source_client
                            ) THEN 'source_client'
                        END,
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                checkpoint.source_session_id
                            ) THEN 'source_session_id'
                        END,
                        CASE
                            WHEN checkpoint.source_model IS NOT NULL
                             AND NOT {schema}.mnemonic_has_non_whitespace(
                                 checkpoint.source_model
                             ) THEN 'source_model'
                        END
                    ], NULL) AS invalid_fields
                FROM {schema}.checkpoints AS checkpoint
            LOOP
                FOREACH v_field IN ARRAY v_row.invalid_fields LOOP
                    RAISE NOTICE
                        'Mnemonic 0010 actor fallback source=checkpoints row_id=% field=%',
                        v_row.id,
                        v_field;
                END LOOP;
            END LOOP;

            FOR v_row IN
                SELECT
                    lease.work_item_id AS id,
                    ARRAY_REMOVE(ARRAY[
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                lease.holder_client
                            ) THEN 'holder_client'
                        END,
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                lease.holder_session_id
                            ) THEN 'holder_session_id'
                        END
                    ], NULL) AS invalid_fields
                FROM {schema}.work_leases AS lease
            LOOP
                FOREACH v_field IN ARRAY v_row.invalid_fields LOOP
                    RAISE NOTICE
                        'Mnemonic 0010 actor fallback source=work_leases row_id=% field=%',
                        v_row.id,
                        v_field;
                END LOOP;
            END LOOP;

            FOR v_row IN
                SELECT
                    relationship.id,
                    ARRAY_REMOVE(ARRAY[
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                relationship.created_by_client
                            ) THEN 'created_by_client'
                        END,
                        CASE
                            WHEN NOT {schema}.mnemonic_has_non_whitespace(
                                relationship.created_by_session_id
                            ) THEN 'created_by_session_id'
                        END,
                        CASE
                            WHEN relationship.created_by_model IS NOT NULL
                             AND NOT {schema}.mnemonic_has_non_whitespace(
                                 relationship.created_by_model
                             ) THEN 'created_by_model'
                        END
                    ], NULL) AS invalid_fields
                FROM {schema}.work_relationships AS relationship
            LOOP
                FOREACH v_field IN ARRAY v_row.invalid_fields LOOP
                    RAISE NOTICE
                        'Mnemonic 0010 actor fallback source=work_relationships row_id=% field=%',
                        v_row.id,
                        v_field;
                END LOOP;
            END LOOP;

            SELECT pg_catalog.count(*)
            INTO v_checkpoint_fallbacks
            FROM {schema}.checkpoints AS checkpoint
            WHERE NOT {schema}.mnemonic_has_non_whitespace(checkpoint.source_client)
               OR NOT {schema}.mnemonic_has_non_whitespace(
                   checkpoint.source_session_id
               );
            SELECT pg_catalog.count(*)
            INTO v_lease_fallbacks
            FROM {schema}.work_leases AS lease
            WHERE NOT {schema}.mnemonic_has_non_whitespace(lease.holder_client)
               OR NOT {schema}.mnemonic_has_non_whitespace(lease.holder_session_id);
            SELECT pg_catalog.count(*)
            INTO v_relationship_fallbacks
            FROM {schema}.work_relationships AS relationship
            WHERE NOT {schema}.mnemonic_has_non_whitespace(
                      relationship.created_by_client
                  )
               OR NOT {schema}.mnemonic_has_non_whitespace(
                      relationship.created_by_session_id
                  );

            RAISE NOTICE
                'Mnemonic 0010 actor fallback counts '
                'checkpoints=% work_leases=% work_relationships=%',
                v_checkpoint_fallbacks,
                v_lease_fallbacks,
                v_relationship_fallbacks;
        END
        $diagnostics$;
        """
    )


def _backfill_events(schema: str) -> None:
    op.execute(
        f"""
        WITH candidates (
            project_id,
            work_item_id,
            event_type,
            actor_kind,
            actor_client,
            actor_session_id,
            actor_model,
            body,
            checkpoint_id,
            lease_generation_id,
            lease_release_id,
            relationship_id,
            relationship_source_work_item_id,
            relationship_target_work_item_id,
            relationship_context_checkpoint_work_item_id,
            relationship_context_checkpoint_id,
            metadata_version,
            metadata,
            origin,
            created_at,
            source_kind_rank,
            source_record_id,
            endpoint_role_rank
        ) AS (
            SELECT
                work.project_id,
                work.id,
                'work_created'::text,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN 'client'
                    ELSE 'unattributed'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN checkpoint.source_client
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN checkpoint.source_session_id
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                     AND (
                         checkpoint.source_model IS NULL
                         OR {schema}.mnemonic_has_non_whitespace(
                             checkpoint.source_model
                         )
                     )
                    THEN checkpoint.source_model
                END,
                NULL::text,
                checkpoint.id,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                1::smallint,
                '{{}}'::jsonb,
                'backfill'::text,
                work.created_at,
                0,
                work.id,
                0
            FROM {schema}.work_items AS work
            JOIN {schema}.checkpoints AS checkpoint
              ON checkpoint.work_item_id = work.id
             AND checkpoint.id = work.initial_checkpoint_id

            UNION ALL

            SELECT
                work.project_id,
                work.id,
                CASE
                    WHEN checkpoint.kind = 'completion'
                        THEN 'work_completed'
                    ELSE 'checkpoint_added'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN 'client'
                    ELSE 'unattributed'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN checkpoint.source_client
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                    THEN checkpoint.source_session_id
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            checkpoint.source_session_id
                         )
                     AND (
                         checkpoint.source_model IS NULL
                         OR {schema}.mnemonic_has_non_whitespace(
                             checkpoint.source_model
                         )
                     )
                    THEN checkpoint.source_model
                END,
                NULL::text,
                checkpoint.id,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                1::smallint,
                CASE
                    WHEN checkpoint.kind = 'completion' THEN '{{}}'::jsonb
                    ELSE pg_catalog.jsonb_build_object(
                        'checkpoint_kind',
                        checkpoint.kind
                    )
                END,
                'backfill'::text,
                checkpoint.created_at,
                1,
                checkpoint.id,
                0
            FROM {schema}.checkpoints AS checkpoint
            JOIN {schema}.work_items AS work
              ON work.id = checkpoint.work_item_id
            WHERE checkpoint.id <> work.initial_checkpoint_id

            UNION ALL

            SELECT
                relationship.project_id,
                endpoint.work_item_id,
                CASE
                    WHEN relationship.relationship_type = 'blocks'
                        THEN 'dependency_added'
                    ELSE 'relationship_added'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_session_id
                         )
                    THEN 'client'
                    ELSE 'unattributed'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_session_id
                         )
                    THEN relationship.created_by_client
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_session_id
                         )
                    THEN relationship.created_by_session_id
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_client
                         )
                     AND {schema}.mnemonic_has_non_whitespace(
                            relationship.created_by_session_id
                         )
                     AND (
                         relationship.created_by_model IS NULL
                         OR {schema}.mnemonic_has_non_whitespace(
                             relationship.created_by_model
                         )
                     )
                    THEN relationship.created_by_model
                END,
                NULL::text,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                relationship.id,
                relationship.source_work_item_id,
                relationship.target_work_item_id,
                relationship.context_checkpoint_work_item_id,
                relationship.context_checkpoint_id,
                1::smallint,
                pg_catalog.jsonb_build_object(
                    'relationship_type',
                    relationship.relationship_type
                ),
                'backfill'::text,
                relationship.created_at,
                2,
                relationship.id,
                endpoint.endpoint_role_rank
            FROM {schema}.work_relationships AS relationship
            CROSS JOIN LATERAL (
                VALUES
                    (relationship.source_work_item_id, 0),
                    (relationship.target_work_item_id, 1)
            ) AS endpoint(work_item_id, endpoint_role_rank)

            UNION ALL

            SELECT
                work.project_id,
                lease.work_item_id,
                'work_claimed'::text,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(lease.holder_client)
                     AND {schema}.mnemonic_has_non_whitespace(
                            lease.holder_session_id
                         )
                    THEN 'client'
                    ELSE 'unattributed'
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(lease.holder_client)
                     AND {schema}.mnemonic_has_non_whitespace(
                            lease.holder_session_id
                         )
                    THEN lease.holder_client
                END,
                CASE
                    WHEN {schema}.mnemonic_has_non_whitespace(lease.holder_client)
                     AND {schema}.mnemonic_has_non_whitespace(
                            lease.holder_session_id
                         )
                    THEN lease.holder_session_id
                END,
                NULL::text,
                NULL::text,
                NULL::uuid,
                lease.lease_generation_id,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                1::smallint,
                pg_catalog.jsonb_build_object(
                    'observed_expires_at',
                    pg_catalog.to_char(
                        lease.expires_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ),
                    'expiry_basis',
                    'retained_lease_at_cutover'
                ),
                'backfill'::text,
                lease.acquired_at,
                3,
                lease.work_item_id,
                0
            FROM {schema}.work_leases AS lease
            JOIN {schema}.work_items AS work
              ON work.id = lease.work_item_id

            UNION ALL

            SELECT
                work.project_id,
                work.id,
                'work_deleted'::text,
                'unattributed'::text,
                NULL::text,
                NULL::text,
                NULL::text,
                NULL::text,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                NULL::uuid,
                1::smallint,
                pg_catalog.jsonb_build_object(
                    'final_status',
                    work.status,
                    'final_version',
                    work.version
                ),
                'backfill'::text,
                work.deleted_at,
                4,
                work.id,
                0
            FROM {schema}.work_items AS work
            WHERE work.deleted_at IS NOT NULL
        )
        INSERT INTO {schema}.work_events (
            project_id,
            work_item_id,
            event_type,
            actor_kind,
            actor_client,
            actor_session_id,
            actor_model,
            body,
            checkpoint_id,
            lease_generation_id,
            lease_release_id,
            relationship_id,
            relationship_source_work_item_id,
            relationship_target_work_item_id,
            relationship_context_checkpoint_work_item_id,
            relationship_context_checkpoint_id,
            metadata_version,
            metadata,
            origin,
            created_at
        )
        SELECT
            project_id,
            work_item_id,
            event_type,
            actor_kind,
            actor_client,
            actor_session_id,
            actor_model,
            body,
            checkpoint_id,
            lease_generation_id,
            lease_release_id,
            relationship_id,
            relationship_source_work_item_id,
            relationship_target_work_item_id,
            relationship_context_checkpoint_work_item_id,
            relationship_context_checkpoint_id,
            metadata_version,
            metadata,
            origin,
            created_at
        FROM candidates
        ORDER BY
            created_at,
            source_kind_rank,
            source_record_id,
            endpoint_role_rank;
        """
    )


def _verify_backfill(schema: str) -> None:
    op.execute(
        f"""
        DO $parity$
        DECLARE
            v_work_created bigint;
            v_checkpoint_facts bigint;
            v_relationship_facts bigint;
            v_claim_facts bigint;
            v_deleted_facts bigint;
            v_actual bigint;
            v_expected bigint;
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM {schema}.work_leases
                WHERE lease_generation_id IS NULL
                   OR pending_release_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'lease generation/release marker parity failed after backfill';
            END IF;

            SELECT pg_catalog.count(*)
            INTO v_work_created
            FROM {schema}.work_events
            WHERE origin = 'backfill'
              AND event_type = 'work_created';
            IF v_work_created <> (
                SELECT pg_catalog.count(*) FROM {schema}.work_items
            ) THEN
                RAISE EXCEPTION 'work-created backfill parity failed';
            END IF;

            SELECT pg_catalog.count(*)
            INTO v_checkpoint_facts
            FROM {schema}.work_events
            WHERE origin = 'backfill'
              AND event_type IN ('checkpoint_added', 'work_completed');
            IF v_checkpoint_facts <> (
                SELECT pg_catalog.count(*)
                FROM {schema}.checkpoints AS checkpoint
                JOIN {schema}.work_items AS work
                  ON work.id = checkpoint.work_item_id
                WHERE checkpoint.id <> work.initial_checkpoint_id
            ) THEN
                RAISE EXCEPTION 'checkpoint backfill parity failed';
            END IF;

            SELECT pg_catalog.count(*)
            INTO v_relationship_facts
            FROM {schema}.work_events
            WHERE origin = 'backfill'
              AND event_type IN ('dependency_added', 'relationship_added');
            IF v_relationship_facts <> (
                SELECT 2 * pg_catalog.count(*)
                FROM {schema}.work_relationships
            ) THEN
                RAISE EXCEPTION 'relationship backfill parity failed';
            END IF;

            SELECT pg_catalog.count(*)
            INTO v_claim_facts
            FROM {schema}.work_events
            WHERE origin = 'backfill'
              AND event_type = 'work_claimed';
            IF v_claim_facts <> (
                SELECT pg_catalog.count(*) FROM {schema}.work_leases
            ) THEN
                RAISE EXCEPTION 'lease backfill parity failed';
            END IF;

            SELECT pg_catalog.count(*)
            INTO v_deleted_facts
            FROM {schema}.work_events
            WHERE origin = 'backfill'
              AND event_type = 'work_deleted';
            IF v_deleted_facts <> (
                SELECT pg_catalog.count(*)
                FROM {schema}.work_items
                WHERE deleted_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'deletion backfill parity failed';
            END IF;

            v_expected :=
                v_work_created
                + v_checkpoint_facts
                + v_relationship_facts
                + v_claim_facts
                + v_deleted_facts;
            SELECT pg_catalog.count(*)
            INTO v_actual
            FROM {schema}.work_events
            WHERE origin = 'backfill';
            IF v_actual <> v_expected THEN
                RAISE EXCEPTION 'complete work-event backfill formula failed';
            END IF;

            RAISE NOTICE
                'Mnemonic 0010 backfill parity work_created=% '
                'checkpoint_facts=% relationship_facts=% claim_facts=% '
                'work_deleted=% total=%',
                v_work_created,
                v_checkpoint_facts,
                v_relationship_facts,
                v_claim_facts,
                v_deleted_facts,
                v_actual;
        END
        $parity$;
        """
    )


def _install_immutability(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_work_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'work events are immutable';
        END
        $function$;

        CREATE TRIGGER events_immutable
        BEFORE UPDATE OR DELETE ON {schema}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_work_event_mutation();
        """
    )


def upgrade() -> None:
    schema = _quoted_current_schema()
    _upgrade_leases()
    _create_validation_functions(schema)
    _create_work_events_table()
    _create_guard_triggers(schema)
    _report_actor_fallbacks(schema)
    _backfill_events(schema)
    _verify_backfill(schema)
    _install_immutability(schema)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS events_immutable ON work_events;
        DROP FUNCTION IF EXISTS mnemonic_reject_work_event_mutation();
        DROP TRIGGER IF EXISTS work_event_source_fact_guard ON work_events;
        DROP FUNCTION IF EXISTS mnemonic_guard_work_event_source_fact();
        DROP TRIGGER IF EXISTS work_event_state_guard ON work_events;
        DROP FUNCTION IF EXISTS mnemonic_guard_work_event_state();
        DROP TRIGGER IF EXISTS work_lease_release_marker_guard ON work_leases;
        DROP FUNCTION IF EXISTS mnemonic_guard_work_lease_release_marker();
        """
    )
    op.drop_table("work_events")
    op.execute(
        """
        DROP FUNCTION IF EXISTS mnemonic_work_event_metadata_v1_is_valid(
            text, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid,
            smallint, jsonb
        );
        """
    )
    op.drop_index(
        "uq_work_leases_pending_release_id",
        table_name="work_leases",
    )
    op.drop_constraint(
        "uq_work_leases_lease_generation_id",
        "work_leases",
        type_="unique",
    )
    op.drop_column("work_leases", "pending_release_id")
    op.drop_column("work_leases", "lease_generation_id")
    op.execute("DROP FUNCTION IF EXISTS mnemonic_has_non_whitespace(text)")
