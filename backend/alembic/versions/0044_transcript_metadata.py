"""Keep native session activity distinct from index construction."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_metadata import retained_time_bounds, timeline_metadata

revision = "0044_transcript_metadata"
down_revision = "0043_transcript_source_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("last_updated_at", sa.DateTime(timezone=True)))
    op.add_column("transcripts", sa.Column("source_modified_at", sa.DateTime(timezone=True)))
    # Only derive metadata from the retained current revision. No native files,
    # jobs, source assertions, receipts, content bytes or leases are changed.
    connection = op.get_bind()
    rows = (
        connection.execute(
            sa.text(
                "SELECT id, normalized_revision, extracted_metadata, "
                "status, indexing_completed_at FROM transcripts"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        first, last = retained_time_bounds(connection, row["id"], row["normalized_revision"])
        properties = timeline_metadata(
            row["extracted_metadata"],
            started_at=first,
            updated_at=last,
            source_modified_at=None,
            indexed_at=row["indexing_completed_at"] if row["status"] == "ready" else None,
        )
        connection.execute(
            sa.text(
                "UPDATE transcripts SET last_updated_at=:last, "
                "extracted_metadata=:metadata WHERE id=:id"
            ).bindparams(sa.bindparam("metadata", type_=sa.JSON)),
            {"last": last, "metadata": properties, "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_column("transcripts", "source_modified_at")
    op.drop_column("transcripts", "last_updated_at")
