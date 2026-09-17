"""Nullable evidence is captured only for new, verified source assertions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def source_columns() -> list[sa.Column]:
    return [sa.Column("source_identity", JSONB(none_as_null=True)),
            sa.Column("copy_source_path", sa.String(4096))]


SOURCE_CHECKS = {
    "source_identity_valid": "source_identity IS NULL OR coalesce(("
        "jsonb_typeof(source_identity) = 'object' "
        "AND source_identity - ARRAY['version','filename','prefix_size','prefix_sha256'] "
        "= '{}'::jsonb AND source_identity->'version' = '1'::jsonb "
        "AND jsonb_typeof(source_identity->'filename') = 'string' "
        "AND source_identity->>'filename' = regexp_replace(regexp_replace(regexp_replace("
        "source_path, '/(\\.?/)*', '/', 'g'), '/\\.?$', ''), '^.*/', '') "
        "AND jsonb_typeof(source_identity->'prefix_size') = 'number' "
        "AND (source_identity->>'prefix_size') ~ '^[0-9]+$' "
        "AND (source_identity->>'prefix_size')::numeric BETWEEN 256 AND 65536 "
        "AND jsonb_typeof(source_identity->'prefix_sha256') = 'string' "
        "AND (source_identity->>'prefix_sha256') ~ '^[0-9a-f]{64}$'), false)",
    "copy_source_path_valid": "copy_source_path IS NULL OR "
        "(copy_status = 'ready' AND left(copy_source_path, 1) = '/')",
}


def source_elements() -> list:
    return [*source_columns(), *(sa.CheckConstraint(value, name=name)
                                for name, value in SOURCE_CHECKS.items())]
