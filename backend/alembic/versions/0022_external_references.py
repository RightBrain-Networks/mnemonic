"""Bounded authored external references and truthful system-event snapshots.

Revision ID: 0022_external_references
Revises: 0021_job_completion_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_external_references"
down_revision: str | None = "0021_job_completion_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS = "('work_created', 'work_updated', 'work_status_changed', 'work_reopened')"
_TYPES = "text,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,smallint,jsonb"


def _schema() -> str:
    bind = op.get_bind()
    name = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(name, str):
        raise RuntimeError("External references require a PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(name)


def _replace_function(
    s: str, name: str, signature: str, replacements: list[tuple[str, str]], *, reverse: bool = False
) -> None:
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:function AS regprocedure))"),
        {"function": f"{s}.{name}({signature})"},
    )
    if not isinstance(definition, str):
        raise RuntimeError(f"Missing predecessor function {name}")
    for old, new in replacements:
        before, after = (new, old) if reverse else (old, new)
        if definition.count(before) != 1:
            raise RuntimeError(f"Unexpected predecessor body for {name}")
        definition = definition.replace(before, after)
    op.execute(sa.text(definition))


def _validators(s: str) -> None:
    op.execute(
        sa.text(
            r"""
    CREATE FUNCTION SCHEMA.mnemonic_external_url_is_valid(p_url text)
    RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path = pg_catalog AS $function$
    DECLARE v_authority text; v_host text; v_port text; v_label text;
    BEGIN
        IF p_url IS NULL OR octet_length(p_url) NOT BETWEEN 1 AND 2000
           OR octet_length(p_url) <> length(p_url)
           OR p_url ~ '[[:space:][:cntrl:]]'
           OR p_url !~* '^https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.?)(:[0-9]+)?(/[A-Za-z0-9._~!$&''()*+,;=:@%/-]*)?(\?[A-Za-z0-9._~!$&''()*+,;=:@%/?-]*)?(#[A-Za-z0-9._~!$&''()*+,;=:@%/?-]*)?$'
           OR p_url ~ '%($|[^0-9A-Fa-f]|[0-9A-Fa-f]($|[^0-9A-Fa-f]))' THEN
            RETURN false;
        END IF;
        v_authority := split_part(regexp_replace(p_url, '^https?://', '', 'i'), '/', 1);
        v_authority := split_part(split_part(v_authority, '?', 1), '#', 1);
        IF left(v_authority, 1) = '[' THEN
            v_host := split_part(substr(v_authority, 2), ']', 1);
            IF family(v_host::inet) <> 6 THEN RETURN false; END IF;
            v_port := nullif(substr(v_authority, strpos(v_authority, ']') + 2), '');
        ELSE
            v_host := split_part(v_authority, ':', 1);
            v_port := nullif(split_part(v_authority, ':', 2), '');
            IF v_host ~ '^[0-9.]+$' THEN
                IF family(v_host::inet) <> 4 OR host(v_host::inet) <> v_host THEN
                    RETURN false;
                END IF;
            ELSE
                v_host := regexp_replace(v_host, '\.$', '');
                IF length(v_host) > 253 THEN RETURN false; END IF;
                FOREACH v_label IN ARRAY string_to_array(v_host, '.') LOOP
                    IF length(v_label) NOT BETWEEN 1 AND 63
                       OR v_label !~* '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$' THEN
                        RETURN false;
                    END IF;
                END LOOP;
            END IF;
        END IF;
        RETURN v_port IS NULL OR v_port::numeric BETWEEN 0 AND 65535;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
    END $function$;

    CREATE FUNCTION SCHEMA.mnemonic_external_references_is_valid(p_refs jsonb)
    RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path = pg_catalog AS $function$
    DECLARE v_ref jsonb; v_keys text[]; v_urls text[] := ARRAY[]::text[];
            v_label text; v_time text; v_char text; v_bytes integer; v_count integer;
    BEGIN
        IF p_refs IS NULL OR jsonb_typeof(p_refs) <> 'array' THEN RETURN false; END IF;
        v_count := jsonb_array_length(p_refs);
        IF v_count > 10 THEN RETURN false; END IF;
        v_bytes := octet_length(p_refs::text) - greatest(v_count - 1, 0);
        FOR v_ref IN SELECT value FROM jsonb_array_elements(p_refs) LOOP
            IF jsonb_typeof(v_ref) <> 'object' THEN RETURN false; END IF;
            SELECT array_agg(key ORDER BY key) INTO v_keys FROM jsonb_object_keys(v_ref) key;
            IF NOT (v_ref ?& ARRAY['url','kind','state'])
               OR v_keys IS NULL
               OR NOT (v_keys <@ ARRAY['url','kind','state','label','state_observed_at'])
               OR jsonb_typeof(v_ref->'url') <> 'string'
               OR NOT SCHEMA.mnemonic_external_url_is_valid(v_ref->>'url')
               OR jsonb_typeof(v_ref->'kind') <> 'string'
               OR v_ref->>'kind' NOT IN ('tracked-by','references')
               OR jsonb_typeof(v_ref->'state') <> 'string'
               OR v_ref->>'state' NOT IN ('open','closed','merged','unknown')
               OR v_ref->>'url' = ANY(v_urls) THEN RETURN false; END IF;
            v_urls := array_append(v_urls, v_ref->>'url');
            v_bytes := v_bytes - (2 * cardinality(v_keys) - 1);
            IF v_ref ? 'label' THEN
                v_label := v_ref->>'label';
                IF jsonb_typeof(v_ref->'label') <> 'string'
                   OR length(v_label) NOT BETWEEN 1 AND 120 OR octet_length(v_label) > 480
                   OR NOT SCHEMA.mnemonic_has_non_whitespace(v_label) THEN RETURN false; END IF;
                FOREACH v_char IN ARRAY regexp_split_to_array(v_label, '') LOOP
                    IF ascii(v_char) < 32 OR ascii(v_char) BETWEEN 127 AND 159
                       OR ascii(v_char) = ANY(ARRAY[1564,8206,8207,8232,8233,8234,8235,
                                                   8236,8237,8238,8294,8295,8296,8297]) THEN
                        RETURN false;
                    END IF;
                END LOOP;
            END IF;
            IF v_ref ? 'state_observed_at' THEN
                v_time := v_ref->>'state_observed_at';
                IF jsonb_typeof(v_ref->'state_observed_at') <> 'string'
                   OR v_time !~ ('^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:'
                                  || '[0-9]{2}(\.[0-9]{0,5}[1-9])?Z$')
                   OR to_char(v_time::timestamp, 'YYYY-MM-DD"T"HH24:MI:SS')
                      <> left(v_time, 19) THEN RETURN false; END IF;
            END IF;
        END LOOP;
        -- jsonb::text adds one space per object colon/comma and array comma.
        -- Subtract only structural spaces; authored strings remain untouched.
        RETURN v_bytes <= 32768;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
    END $function$;
    """.replace("SCHEMA", s)
        )
    )


def _metadata_replacements(s: str) -> list[tuple[str, str]]:
    return [
        (
            "pg_catalog.octet_length(p_metadata::text) > 16384",
            "pg_catalog.octet_length(p_metadata::text) > (CASE WHEN p_event_type IN "
            + _EVENTS
            + " THEN 131072 ELSE 16384 END)",
        ),
        (
            "RETURN v_keys IS NOT DISTINCT FROM\n"
            "                           ARRAY['priority', 'status', "
            "'summary', 'title', 'version']::text[]",
            "RETURN (v_keys IS NOT DISTINCT FROM\n"
            "                           ARRAY['priority', 'status', "
            "'summary', 'title', 'version']::text[]\n"
            "                    OR v_keys IS NOT DISTINCT FROM ARRAY['external_references',\n"
            "                        'priority', 'status', 'summary', "
            "'title', 'version']::text[])\n"
            "                    AND (NOT (v_initial ? 'external_references') OR (\n"
            f"                        {s}.mnemonic_external_references_is_valid(\n"
            "                            v_initial -> 'external_references')\n"
            "                        AND v_initial -> 'external_references' <> '[]'::jsonb))",
        ),
        (
            ") > 4\n                   OR pg_catalog.jsonb_typeof(p_metadata -> 'work_version')",
            ") > 5\n                   OR pg_catalog.jsonb_typeof(p_metadata -> 'work_version')",
        ),
        (
            "IF v_key NOT IN ('title', 'summary', 'priority', 'status') THEN",
            "IF v_key NOT IN ('title', 'summary', 'priority', 'status', "
            "'external_references') THEN",
        ),
        (
            "IF v_key = 'title' THEN",
            "IF v_key = 'external_references' THEN\n"
            f"                        IF NOT {s}.mnemonic_external_references_is_valid(v_before)\n"
            f"                           OR NOT {s}."
            "mnemonic_external_references_is_valid(v_after)\n"
            "                           THEN\n"
            "                            RETURN false;\n"
            "                        END IF;\n"
            "                    ELSIF v_key = 'title' THEN",
        ),
    ]


def _source_replacements() -> list[tuple[str, str]]:
    return [
        (
            "IS DISTINCT FROM v_work.version\n                ) THEN",
            "IS DISTINCT FROM v_work.version\n"
            "                    OR COALESCE(NEW.metadata -> 'initial' -> 'external_references',\n"
            "                                '[]'::jsonb) "
            "IS DISTINCT FROM v_work.external_references\n"
            "                ) THEN",
        )
    ]


def _event_bound(*, expanded: bool) -> None:
    op.drop_constraint(op.f("ck_work_events_metadata_envelope_valid"), "work_events")
    bound = f"CASE WHEN event_type IN {_EVENTS} THEN 131072 ELSE 16384 END" if expanded else "16384"
    op.create_check_constraint(
        op.f("ck_work_events_metadata_envelope_valid"),
        "work_events",
        f"jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= {bound}",
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    s = _schema()
    _validators(s)
    op.add_column(
        "work_items",
        sa.Column(
            "external_references",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_work_items_external_references_valid"),
        "work_items",
        f"{s}.mnemonic_external_references_is_valid(external_references)",
    )
    op.create_index(
        "ix_work_items_external_references",
        "work_items",
        ["external_references"],
        postgresql_using="gin",
        postgresql_ops={"external_references": "jsonb_path_ops"},
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _replace_function(
        s, "mnemonic_work_event_metadata_v1_is_valid", _TYPES, _metadata_replacements(s)
    )
    _replace_function(s, "mnemonic_guard_work_event_source_fact", "", _source_replacements())
    _event_bound(expanded=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    s = _schema()
    # Keep the emptiness witness valid until the column and expanded ledger guards
    # are removed. A writer that was already active must finish before this check.
    op.execute(
        sa.text(
            f"LOCK TABLE {s}.work_items, {s}.work_events, {s}.client_operations "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    populated = op.get_bind().scalar(
        sa.text(f"""
        SELECT EXISTS(SELECT 1 FROM {s}.work_items WHERE external_references <> '[]'::jsonb)
            OR EXISTS(SELECT 1 FROM {s}.work_events WHERE
                metadata->'initial' ? 'external_references'
                OR metadata->'changes' ? 'external_references')
            OR EXISTS(SELECT 1 FROM {s}.client_operations WHERE
                jsonb_path_exists(response_body,
                    '$.**.external_references ? (@.type() != "array" || @.size() > 0)'))
    """)
    )
    if populated:
        raise RuntimeError(
            "Cannot downgrade retained external reference content, history, or receipts"
        )
    _replace_function(
        s,
        "mnemonic_work_event_metadata_v1_is_valid",
        _TYPES,
        _metadata_replacements(s),
        reverse=True,
    )
    _replace_function(
        s, "mnemonic_guard_work_event_source_fact", "", _source_replacements(), reverse=True
    )
    _event_bound(expanded=False)
    op.drop_index("ix_work_items_external_references", table_name="work_items")
    op.drop_constraint(op.f("ck_work_items_external_references_valid"), "work_items")
    op.drop_column("work_items", "external_references")
    op.execute(f"DROP FUNCTION {s}.mnemonic_external_references_is_valid(jsonb)")
    op.execute(f"DROP FUNCTION {s}.mnemonic_external_url_is_valid(text)")
