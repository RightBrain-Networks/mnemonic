"""Nullable source/session activity timestamps; historical times are never invented."""

import sqlalchemy as sa


def metadata_columns() -> list:
    return [
        sa.Column("last_updated_at", sa.DateTime(timezone=True)),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
    ]
