"""Migration retains each existing composition and permits isolated cache populations."""

import pytest
from sqlalchemy import text

from .test_artifact_extraction_migration_postgres import migrate
from .test_duplicate_embedding_jobs_postgres import next_job
from .test_duplicate_suggestions_postgres import save, suggest

pytestmark = pytest.mark.postgres


def test_upgrade_preserves_existing_consumers_and_allows_both_for_one_item(
    api, project, work_payload, postgres_engine,
):
    first = save(api, project, work_payload, title="Search cache")
    second = save(api, project, work_payload, title="Duplicate cache")
    migrate(postgres_engine, "0048_force_claims", downgrade=True)
    with postgres_engine.begin() as database:
        database.execute(text("""
            INSERT INTO work_item_embeddings(work_item_id,model,digest,vector)
            VALUES (:first, 'search-configuration', repeat('a',64), ARRAY[1.0,0.0]),
                   (:second, 'duplicate-suggestion-v1|configuration',
                    repeat('b',64), ARRAY[0.0,1.0])
        """), {"first": first["id"], "second": second["id"]})
    migrate(postgres_engine, "head")
    with postgres_engine.begin() as database:
        rows = database.execute(text(
            "SELECT work_item_id,purpose,model,digest FROM work_item_embeddings"
        )).mappings().all()
        assert {str(row["work_item_id"]): row["purpose"] for row in rows} == {
            first["id"]: "work_search", second["id"]: "duplicate_suggestions"}
        assert {row["digest"] for row in rows} == {"a" * 64, "b" * 64}
        database.execute(text("""
            INSERT INTO work_item_embeddings(work_item_id,purpose,model,digest,vector)
            VALUES (:id,'duplicate_suggestions','new-config',repeat('c',64),ARRAY[1.0,0.0])
        """), {"id": first["id"]})
        assert database.scalar(text("SELECT count(*) FROM work_item_embeddings")) == 3


def test_delivery_history_prevents_unsafe_downgrade(api, project, work_payload, postgres_engine):
    save(api, project, work_payload, title="cache candidate")
    suggest(api, project)
    assert next_job(api) is not None
    with pytest.raises(RuntimeError, match="delivery history prevents a safe downgrade"):
        migrate(postgres_engine, "0048_force_claims", downgrade=True)
