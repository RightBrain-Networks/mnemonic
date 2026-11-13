"""Prepare deliberately pre-feature fixtures without weakening production downgrade guards."""

from sqlalchemy import Connection, text


def discard_project_changes_for_legacy_fixture(connection: Connection) -> None:
    """Discard only a disposable test journal before constructing authentic older history.

    These fixtures create basic identities with the current API, then migrate back
    to the schema whose historical behavior they exercise. Production downgrade
    deliberately refuses live change loss; this explicit test setup is not a
    supported operator recovery or downgrade procedure.
    """
    schema = connection.scalar(text("SELECT current_schema()"))
    if not isinstance(schema, str) or not schema.startswith("mnemonic_test_"):
        raise RuntimeError("Refusing to discard change history outside a disposable test schema")
    if connection.scalar(text("SELECT to_regclass('project_changes')")) is None:
        return
    connection.execute(text("ALTER TABLE project_changes DISABLE TRIGGER USER"))
    connection.execute(text("DELETE FROM project_changes"))
    connection.execute(text("ALTER TABLE project_changes ENABLE TRIGGER USER"))
    connection.execute(text("ALTER TABLE project_change_heads DISABLE TRIGGER USER"))
    connection.execute(text("UPDATE project_change_heads SET last_sequence=0"))
    connection.execute(text("ALTER TABLE project_change_heads ENABLE TRIGGER USER"))
