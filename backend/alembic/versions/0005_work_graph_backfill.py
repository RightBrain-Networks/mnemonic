"""Backfill canonical work/checkpoint history and freeze legacy storage.

Revision ID: 0005_work_graph_backfill
Revises: 0004_work_graph_expand

This revision is the runtime head during the legacy-table observation window.
The legacy tables and ORM metadata intentionally remain present and read-only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_work_graph_backfill"
down_revision: str | None = "0004_work_graph_expand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Reserve all original hand-off and collision-free comment UUIDs before copying.
    # A deterministic salted MD5 UUID is used only when a comment UUID is already
    # occupied by an initial checkpoint. The loop also proves that generated IDs
    # cannot collide with any source UUID or another mapped comment.
    op.execute(
        """
        CREATE TEMPORARY TABLE mnemonic_legacy_comment_checkpoint_ids (
            legacy_record_id uuid PRIMARY KEY,
            checkpoint_id uuid NOT NULL UNIQUE
        ) ON COMMIT DROP;

        DO $migration$
        DECLARE
            source_comment record;
            candidate uuid;
            salt integer;
        BEGIN
            FOR source_comment IN SELECT id FROM handoff_comments ORDER BY id LOOP
                candidate := source_comment.id;
                IF EXISTS (SELECT 1 FROM handoffs WHERE id = candidate) THEN
                    salt := 0;
                    LOOP
                        candidate := (
                            md5(
                                'mnemonic:legacy-comment:' || source_comment.id::text || ':' || salt
                            )
                        )::uuid;
                        EXIT WHEN NOT EXISTS (SELECT 1 FROM handoffs WHERE id = candidate)
                            AND NOT EXISTS (
                                SELECT 1 FROM handoff_comments WHERE id = candidate
                            )
                            AND NOT EXISTS (
                                SELECT 1
                                FROM mnemonic_legacy_comment_checkpoint_ids
                                WHERE checkpoint_id = candidate
                            );
                        salt := salt + 1;
                    END LOOP;
                END IF;
                INSERT INTO mnemonic_legacy_comment_checkpoint_ids
                    (legacy_record_id, checkpoint_id)
                VALUES (source_comment.id, candidate);
            END LOOP;
        END
        $migration$;

        SET CONSTRAINTS fk_work_items_initial_checkpoint DEFERRED;

        INSERT INTO work_items (
            id,
            project_id,
            title,
            summary,
            status,
            priority,
            initial_checkpoint_id,
            version,
            created_at,
            updated_at,
            deleted_at
        )
        SELECT
            id,
            project_id,
            title,
            summary,
            status,
            0,
            id,
            version,
            created_at,
            updated_at,
            deleted_at
        FROM handoffs;

        INSERT INTO checkpoints (
            id,
            work_item_id,
            kind,
            prompt,
            source_client,
            source_session_id,
            source_model,
            source_session_url,
            repository_branch,
            verified_against,
            tags,
            source_metadata,
            migration_origin,
            legacy_record_id,
            created_at
        )
        SELECT
            id,
            id,
            'context',
            prompt,
            source_client,
            source_session_id,
            source_model,
            source_session_url,
            repository_branch,
            verified_against,
            tags,
            source_metadata,
            'legacy-handoff-snapshot',
            id,
            created_at
        FROM handoffs;

        INSERT INTO checkpoints (
            id,
            work_item_id,
            kind,
            prompt,
            source_client,
            source_session_id,
            source_model,
            source_session_url,
            repository_branch,
            verified_against,
            tags,
            source_metadata,
            migration_origin,
            legacy_record_id,
            created_at
        )
        SELECT
            id_map.checkpoint_id,
            legacy_comment.handoff_id,
            CASE
                WHEN legacy_comment.kind = 'work-summary' THEN 'completion'
                ELSE 'progress'
            END,
            legacy_comment.body,
            legacy_comment.source_client,
            legacy_comment.source_session_id,
            legacy_comment.source_model,
            NULL,
            NULL,
            NULL,
            '{}'::varchar[],
            '{}'::jsonb,
            'legacy-comment',
            legacy_comment.id,
            legacy_comment.created_at
        FROM handoff_comments AS legacy_comment
        JOIN mnemonic_legacy_comment_checkpoint_ids AS id_map
          ON id_map.legacy_record_id = legacy_comment.id;

        DO $parity$
        BEGIN
            IF (SELECT count(*) FROM work_items) <> (SELECT count(*) FROM handoffs) THEN
                RAISE EXCEPTION 'work item backfill count mismatch';
            END IF;
            IF (SELECT count(*) FROM checkpoints)
                <> (SELECT count(*) FROM handoffs) + (SELECT count(*) FROM handoff_comments)
            THEN
                RAISE EXCEPTION 'checkpoint backfill count mismatch';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM handoffs AS legacy
                LEFT JOIN work_items AS canonical ON canonical.id = legacy.id
                LEFT JOIN checkpoints AS initial
                  ON initial.work_item_id = canonical.id
                 AND initial.id = canonical.initial_checkpoint_id
                WHERE canonical.id IS NULL
                   OR initial.id IS NULL
                   OR canonical.project_id IS DISTINCT FROM legacy.project_id
                   OR canonical.title IS DISTINCT FROM legacy.title
                   OR canonical.summary IS DISTINCT FROM legacy.summary
                   OR canonical.status IS DISTINCT FROM legacy.status
                   OR canonical.priority IS DISTINCT FROM 0
                   OR canonical.version IS DISTINCT FROM legacy.version
                   OR canonical.created_at IS DISTINCT FROM legacy.created_at
                   OR canonical.updated_at IS DISTINCT FROM legacy.updated_at
                   OR canonical.deleted_at IS DISTINCT FROM legacy.deleted_at
                   OR initial.kind IS DISTINCT FROM 'context'
                   OR initial.prompt IS DISTINCT FROM legacy.prompt
                   OR initial.source_client IS DISTINCT FROM legacy.source_client
                   OR initial.source_session_id IS DISTINCT FROM legacy.source_session_id
                   OR initial.source_model IS DISTINCT FROM legacy.source_model
                   OR initial.source_session_url IS DISTINCT FROM legacy.source_session_url
                   OR initial.repository_branch IS DISTINCT FROM legacy.repository_branch
                   OR initial.verified_against IS DISTINCT FROM legacy.verified_against
                   OR initial.tags IS DISTINCT FROM legacy.tags
                   OR initial.source_metadata IS DISTINCT FROM legacy.source_metadata
                   OR initial.created_at IS DISTINCT FROM legacy.created_at
                   OR initial.migration_origin IS DISTINCT FROM 'legacy-handoff-snapshot'
                   OR initial.legacy_record_id IS DISTINCT FROM legacy.id
            ) THEN
                RAISE EXCEPTION 'hand-off parity validation failed';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM handoff_comments AS legacy
                LEFT JOIN checkpoints AS canonical
                  ON canonical.migration_origin = 'legacy-comment'
                 AND canonical.legacy_record_id = legacy.id
                WHERE canonical.id IS NULL
                   OR canonical.work_item_id IS DISTINCT FROM legacy.handoff_id
                   OR canonical.kind IS DISTINCT FROM CASE
                        WHEN legacy.kind = 'work-summary' THEN 'completion'
                        ELSE 'progress'
                      END
                   OR canonical.prompt IS DISTINCT FROM legacy.body
                   OR canonical.source_client IS DISTINCT FROM legacy.source_client
                   OR canonical.source_session_id IS DISTINCT FROM legacy.source_session_id
                   OR canonical.source_model IS DISTINCT FROM legacy.source_model
                   OR canonical.source_session_url IS NOT NULL
                   OR canonical.repository_branch IS NOT NULL
                   OR canonical.verified_against IS NOT NULL
                   OR canonical.tags IS DISTINCT FROM '{}'::varchar[]
                   OR canonical.source_metadata IS DISTINCT FROM '{}'::jsonb
                   OR canonical.created_at IS DISTINCT FROM legacy.created_at
            ) THEN
                RAISE EXCEPTION 'legacy comment parity validation failed';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM checkpoints AS checkpoint
                LEFT JOIN work_items AS work_item ON work_item.id = checkpoint.work_item_id
                WHERE work_item.id IS NULL
            ) THEN
                RAISE EXCEPTION 'orphan checkpoint found after backfill';
            END IF;
        END
        $parity$;

        CREATE FUNCTION mnemonic_reject_checkpoint_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'checkpoints are immutable';
        END
        $function$;

        CREATE TRIGGER checkpoints_immutable
        BEFORE UPDATE OR DELETE ON checkpoints
        FOR EACH ROW EXECUTE FUNCTION mnemonic_reject_checkpoint_mutation();

        CREATE FUNCTION mnemonic_reject_legacy_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'legacy Mnemonic tables are read-only after the work graph cutover';
        END
        $function$;

        CREATE TRIGGER handoffs_read_only
        BEFORE INSERT OR UPDATE OR DELETE ON handoffs
        FOR EACH ROW EXECUTE FUNCTION mnemonic_reject_legacy_write();

        CREATE TRIGGER handoff_comments_read_only
        BEFORE INSERT OR UPDATE OR DELETE ON handoff_comments
        FOR EACH ROW EXECUTE FUNCTION mnemonic_reject_legacy_write();

        CREATE TRIGGER handoff_embeddings_read_only
        BEFORE INSERT OR UPDATE OR DELETE ON handoff_embeddings
        FOR EACH ROW EXECUTE FUNCTION mnemonic_reject_legacy_write();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS handoff_embeddings_read_only ON handoff_embeddings;
        DROP TRIGGER IF EXISTS handoff_comments_read_only ON handoff_comments;
        DROP TRIGGER IF EXISTS handoffs_read_only ON handoffs;
        DROP FUNCTION IF EXISTS mnemonic_reject_legacy_write();
        DROP TRIGGER IF EXISTS checkpoints_immutable ON checkpoints;
        DROP FUNCTION IF EXISTS mnemonic_reject_checkpoint_mutation();
        SET CONSTRAINTS fk_work_items_initial_checkpoint DEFERRED;
        DELETE FROM checkpoints;
        DELETE FROM work_items;
        """
    )
