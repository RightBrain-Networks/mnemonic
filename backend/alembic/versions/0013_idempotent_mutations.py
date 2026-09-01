"""Add durable project-scoped receipts for idempotent mutations.

Revision ID: 0013_idempotent_mutations
Revises: 0012_pending_deferred_statuses
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_idempotent_mutations"
down_revision: str | None = "0012_pending_deferred_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATION_KINDS = (
    "'create_work', 'add_checkpoint', 'append_event', 'add_relationship', "
    "'update_work', 'defer_work', 'complete_work', 'delete_work', "
    "'remove_relationship', 'release_claim'"
)


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0013_idempotent_mutations requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _create_client_operations() -> None:
    op.create_table(
        "client_operations",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("client_operation_id", sa.UUID(), nullable=False),
        sa.Column("operation_kind", sa.String(length=40), nullable=False),
        sa.Column(
            "request_fingerprint_version",
            sa.SmallInteger(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("request_fingerprint_salt", sa.LargeBinary(), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column(
            "response_contract_version",
            sa.SmallInteger(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("mutation_applied", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"operation_kind IN ({OPERATION_KINDS})",
            name=op.f("ck_client_operations_operation_kind_valid"),
        ),
        sa.CheckConstraint(
            "request_fingerprint_version = 1",
            name=op.f("ck_client_operations_request_fingerprint_version_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(request_fingerprint_salt) = 32",
            name=op.f("ck_client_operations_request_fingerprint_salt_length"),
        ),
        sa.CheckConstraint(
            "octet_length(request_fingerprint) = 32",
            name=op.f("ck_client_operations_request_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "response_contract_version = 1",
            name=op.f("ck_client_operations_response_contract_version_valid"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'completed')",
            name=op.f("ck_client_operations_state_valid"),
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND response_status IS NULL AND response_body IS NULL "
            "AND mutation_applied IS NULL AND completed_at IS NULL) OR "
            "(state = 'completed' AND response_status BETWEEN 200 AND 299 "
            "AND response_body IS NOT NULL AND jsonb_typeof(response_body) = 'object' "
            "AND octet_length(response_body::text) <= 1048576 "
            "AND mutation_applied IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_client_operations_state_fields_valid"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name=op.f("ck_client_operations_timestamp_order"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_operations")),
        sa.UniqueConstraint(
            "project_id",
            "client_operation_id",
            name="uq_client_operations_scope",
        ),
    )


def _create_phase6_metadata_guard(schema: str) -> None:
    # This is intentionally separate from the Phase 5 metadata-v1 validator.
    # NOT VALID preserves historical progress metadata while enforcing the rule
    # for every row inserted or updated after this migration.
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_phase6_progress_metadata_is_valid(
            p_metadata jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT NOT EXISTS (
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
                WHERE pg_catalog.lower(object_key.key) = 'client_operation_id'
            )
        $function$;
        """
    )
    op.execute(
        f"""
        ALTER TABLE {schema}.work_events
        ADD CONSTRAINT ck_work_events_client_operation_id_reserved
        CHECK (
            event_type <> 'progress'
            OR {schema}.mnemonic_phase6_progress_metadata_is_valid(metadata)
        )
        NOT VALID
        """
    )


def _create_client_operation_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_client_operation_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NEW.state <> 'pending'
               OR NEW.response_status IS NOT NULL
               OR NEW.response_body IS NOT NULL
               OR NEW.mutation_applied IS NOT NULL
               OR NEW.completed_at IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'client operations must be inserted pending';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER client_operation_insert_guard
        BEFORE INSERT ON {schema}.client_operations
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_client_operation_insert();
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_client_operation_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'client operations cannot be deleted';
            END IF;

            IF OLD.state <> 'pending'
               OR NEW.state <> 'completed'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.project_id IS DISTINCT FROM OLD.project_id
               OR NEW.client_operation_id IS DISTINCT FROM OLD.client_operation_id
               OR NEW.operation_kind IS DISTINCT FROM OLD.operation_kind
               OR NEW.request_fingerprint_version
                  IS DISTINCT FROM OLD.request_fingerprint_version
               OR NEW.request_fingerprint_salt
                  IS DISTINCT FROM OLD.request_fingerprint_salt
               OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
               OR NEW.response_contract_version
                  IS DISTINCT FROM OLD.response_contract_version
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'client operation mutation is not permitted';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER client_operation_mutation_guard
        BEFORE UPDATE OR DELETE ON {schema}.client_operations
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_client_operation_mutation();
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_require_completed_client_operation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_state text;
        BEGIN
            SELECT state
            INTO v_state
            FROM {schema}.client_operations
            WHERE id = NEW.id;

            IF NOT FOUND OR v_state <> 'completed' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'pending client operation cannot commit';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER client_operation_completion_guard
        AFTER INSERT ON {schema}.client_operations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_completed_client_operation();
        """
    )


def upgrade() -> None:
    # A fresh 0005 -> head run performs the 0010 event backfill in this same
    # Alembic transaction. Fire its deferred source-fact/state guards before
    # ALTER TABLE needs work_events to have no pending trigger events.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")

    schema = _quoted_current_schema()
    _create_client_operations()
    _create_phase6_metadata_guard(schema)
    _create_client_operation_guards(schema)


def downgrade() -> None:
    schema = _quoted_current_schema()
    bind = op.get_bind()

    # Writers must be quiesced operationally. The lock closes the check/drop
    # race even if a writer was already attempting a reservation.
    bind.execute(sa.text(f"LOCK TABLE {schema}.client_operations IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {schema}.client_operations)")):
        raise RuntimeError(
            "Cannot downgrade idempotent mutations after a client operation receipt exists"
        )

    op.execute(
        f"""
        ALTER TABLE {schema}.work_events
        DROP CONSTRAINT ck_work_events_client_operation_id_reserved
        """
    )
    op.execute(
        f"""
        DROP FUNCTION {schema}.mnemonic_phase6_progress_metadata_is_valid(jsonb)
        """
    )

    op.execute(
        f"DROP TRIGGER client_operation_completion_guard "
        f"ON {schema}.client_operations"
    )
    op.execute(
        f"DROP TRIGGER client_operation_mutation_guard "
        f"ON {schema}.client_operations"
    )
    op.execute(
        f"DROP TRIGGER client_operation_insert_guard "
        f"ON {schema}.client_operations"
    )
    op.drop_table("client_operations")
    op.execute(
        f"DROP FUNCTION {schema}.mnemonic_require_completed_client_operation()"
    )
    op.execute(
        f"DROP FUNCTION {schema}.mnemonic_guard_client_operation_mutation()"
    )
    op.execute(
        f"DROP FUNCTION {schema}.mnemonic_guard_client_operation_insert()"
    )
