"""Project labels have a human-only catalog, current assignments and boolean search."""
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres
HEADERS = {"Host": "localhost:3000", "Origin": "http://localhost:3000"}
ACTOR = {"actor_client": "dashboard", "actor_session_id": "tag-tests"}


def browser(api, project, method, suffix="", **body):
    return api.request(method, f"/api/mnemonic/projects/{project['id']}/tags{suffix}",
                       headers=HEADERS, json={"client_operation_id": str(uuid4()), **body})



def catalog_names(api, project, names):
    normalized = list(dict.fromkeys(name.strip().lower() for name in names))
    existing = api.get(f"/api/v1/projects/{project['id']}/tags").json()['items']
    existing_names = {tag['name'] for tag in existing}
    for name in normalized:
        if name not in existing_names:
            response = browser(api, project, 'POST', name=name,
                               description='Test work classification')
            assert response.status_code == 201, response.text
    return normalized or None

def new_work(api, project, names):
    return api.post(f"/api/v1/projects/{project['id']}/work-items", json={
        "title": "Labelled work", "summary": "A useful label test.", "tags": names,
        "discovered_from_work_item_id": None,
        "initial_checkpoint": {"prompt": "Test labels", "source_client": "codex",
                               "source_session_id": "tag-tests"}})


def test_catalog_seeds_normalizes_and_protects_system_tags(api, project):
    path = f"/api/v1/projects/{project['id']}/tags"
    seeded = api.get(path).json()
    assert [tag['name'] for tag in seeded['items'][:3]] == ['code-review', 'remediation', 'dir']
    assert all(tag['system'] for tag in seeded['items'][:3])
    assert not any(tag['system'] for tag in seeded['items'][3:])
    created = browser(api, project, 'POST', name='Needs  Design', description='Design work')
    assert created.status_code == 201, created.text
    tag = created.json()['items'][-1]
    assert tag['name'] == 'needs-design'
    assert tag['description'] == 'Design work'
    assert tag['color'].startswith('#')
    assert api.post(path, json={'name': 'agent-created'}).status_code == 405
    for method, body in [('PATCH', {'color': '#ffffff'}), ('DELETE', {})]:
        denied = browser(api, project, method, '/' + seeded['items'][0]['id'], **body)
        assert denied.status_code == 409, denied.text
    order = [tag['id'], *[row['id'] for row in seeded['items']]]
    reordered = browser(api, project, 'PUT', '/order', tag_ids=order)
    assert reordered.status_code == 200, reordered.text
    assert [row['id'] for row in reordered.json()['items']] == order
    assert [row['menu_order'] for row in reordered.json()['items']] == list(range(len(order)))


def test_current_assignments_edit_and_catalog_rename_delete(api, project):
    response = browser(api, project, 'POST', name='bug', description='Correct broken behavior')
    assert response.status_code == 201, response.text
    tag = response.json()['items'][-1]
    creation = new_work(api, project, ['bug'])
    assert creation.status_code == 201, creation.text
    work = creation.json()['work_item']
    assert work['tags'] == ['bug']
    renamed = browser(api, project, 'PATCH', '/' + tag['id'], name='Defect')
    assert renamed.status_code == 200, renamed.text
    path = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert api.get(path).json()['work_item']['tags'] == ['defect']
    update = api.patch(path, json={'expected_version': 2, 'tags': None, 'actor': ACTOR,
                                  'client_operation_id': str(uuid4())})
    assert update.status_code == 200, update.text
    assert update.json()['tags'] is None
    deleted = browser(api, project, 'DELETE', '/' + tag['id'])
    assert deleted.status_code == 200, deleted.text
    assert new_work(api, project, ['defect']).status_code == 422
    assert new_work(api, project, []).status_code == 422
    assert new_work(api, project, None).status_code == 201


def test_fresh_creation_requires_explicit_tags_and_rejects_checkpoint_assignments(
    api, project, work_payload,
):
    path = f"/api/v1/projects/{project['id']}/work-items"
    payload = {key: value for key, value in work_payload.items() if key != 'tags'}
    omitted = api.post(path, json=payload)
    assert omitted.status_code == 422, omitted.text
    assert omitted.json()['detail']['code'] == 'tags_required'
    legacy = api.post(path, json={**work_payload, 'initial_checkpoint': {
        **work_payload['initial_checkpoint'], 'tags': ['legacy']}})
    assert legacy.status_code == 422, legacy.text
    assert legacy.json()['detail']['code'] == 'checkpoint_tags_retired'


def test_boolean_filters_and_counts_ignore_checkpoint_history(api, project):
    for name in ('bug', 'frontend'):
        assert browser(api, project, 'POST', name=name).status_code == 201
    for names in (['bug'], ['frontend'], ['bug', 'frontend'], None):
        response = new_work(api, project, names)
        assert response.status_code == 201, response.text
    path = f"/api/v1/projects/{project['id']}/search"
    for mode, expected in [('or', 3), ('and', 1)]:
        response = api.post(path, json={'facets': ['work_items'], 'filters': {
            'work_items': {'tags': ['bug', 'frontend'], 'tag_mode': mode}},
            'tag_counts': {'limit': 100}})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == expected
    roots = api.get(f"/api/v1/projects/{project['id']}/work-items", params={
        'view': 'roots', 'tags': ['bug', 'frontend'], 'tag_mode': 'and'})
    assert roots.status_code == 200, roots.text
    assert roots.json()['total'] == 1
    browser_roots = api.get(f"/api/mnemonic/projects/{project['id']}/work-items", params={
        'view': 'roots', 'tags': ['bug', 'frontend'], 'tag_mode': 'and'}, headers=HEADERS)
    assert browser_roots.status_code == 200, browser_roots.text
    assert browser_roots.json()['total'] == 1


def test_catalog_write_receipt_replays_after_later_changes(api, project):
    path = f"/api/mnemonic/projects/{project['id']}/tags"
    intent = {'name': 'Bug Fix', 'color': '#ABCDEF', 'client_operation_id': str(uuid4())}
    first = api.post(path, headers=HEADERS, json=intent)
    assert first.status_code == 201, first.text
    tag = first.json()['items'][-1]
    assert tag['color'] == '#abcdef'
    work = new_work(api, project, ['bug-fix']).json()['work_item']
    deleted = browser(api, project, 'DELETE', '/' + tag['id'])
    assert deleted.status_code == 200, deleted.text
    replay = api.post(path, headers=HEADERS, json=intent)
    assert replay.status_code == 201 and replay.json() == first.json()
    path = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    current = api.get(path).json()['work_item']
    assert current['tags'] is None and current['version'] == 2


def test_empty_catalog_schema_can_downgrade_without_inventing_legacy_tags(postgres_engine):
    from alembic import command
    from alembic.config import Config

    from .conftest import BACKEND_DIR, reset_disposable_schema

    reset_disposable_schema(postgres_engine)
    config = Config(str(BACKEND_DIR / 'alembic.ini'))
    with postgres_engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '0066_candidate_reuse')
        command.upgrade(config, 'head')


def test_move_keeps_only_names_available_in_target_project(api, project):
    target = api.post('/api/v1/projects', json={'name': 'Target labels'}).json()
    catalog_names(api, project, ['shared', 'source-only'])
    catalog_names(api, target, ['shared'])
    work = new_work(api, project, ['shared', 'source-only']).json()['work_item']
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move", json={
        'target_project_id': target['id'], 'expected_version': 1,
        'client_operation_id': str(uuid4()), 'actor': ACTOR})
    assert moved.status_code == 200, moved.text
    assert moved.json()['work_item']['tags'] == ['shared']
    assert moved.json()['work_item']['version'] == 2


def test_catalog_rename_remains_available_with_duplicate_work_assignments(api, project):
    from .test_duplicate_handling_postgres import merge_work

    catalog_names(api, project, ['bug'])
    root = new_work(api, project, ['bug']).json()['work_item']
    alias = new_work(api, project, ['bug']).json()['work_item']
    edit = api.patch(f"/api/v1/projects/{project['id']}/work-items/{alias['id']}", json={
        'expected_version': 1, 'title': 'Historical alias evidence', 'actor': ACTOR,
        'client_operation_id': str(uuid4())})
    assert edit.status_code == 200, edit.text
    alias = edit.json()
    merge_work(api, project, alias, root)
    tag = next(tag for tag in api.get(
        f"/api/v1/projects/{project['id']}/tags").json()['items'] if tag['name'] == 'bug')
    renamed = browser(api, project, 'PATCH', '/' + tag['id'], name='defect')
    assert renamed.status_code == 200, renamed.text
    search = api.post(f"/api/v1/projects/{project['id']}/search", json={
        'facets': ['work_items'], 'tag_counts': {'limit': 100}})
    assert search.status_code == 200, search.text
    assert search.json()['tag_counts']['items'] == [{'tag': 'defect', 'count': 1}]
    filtered = api.get(f"/api/v1/projects/{project['id']}/work-items", params={
        'tag': 'bug', 'status': 'all'})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()['total'] == 0
    filtered = api.post(f"/api/v1/projects/{project['id']}/search", json={
        'q': 'Historical alias evidence', 'facets': ['work_items'],
        'filters': {'work_items': {'tags': ['defect']}}})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()['total'] == 1


def test_migration_clears_all_legacy_checkpoint_tags_and_seeds_every_project(
    api, project, postgres_engine,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from .conftest import BACKEND_DIR

    new_work(api, project, None)
    api.post('/api/v1/projects', json={'name': 'Other legacy project'})
    config = Config(str(BACKEND_DIR / 'alembic.ini'))
    with postgres_engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '0066_candidate_reuse')
        connection.execute(text('ALTER TABLE checkpoints DISABLE TRIGGER checkpoints_immutable'))
        connection.execute(text("UPDATE checkpoints SET tags=ARRAY['Decorative','legacy-tag']"))
        connection.execute(text('ALTER TABLE checkpoints ENABLE TRIGGER checkpoints_immutable'))
        command.upgrade(config, 'head')
        assert connection.scalar(text(
            'SELECT count(*) FROM checkpoints WHERE cardinality(tags)>0')) == 0
        assert connection.scalar(text(
            'SELECT count(*) FROM work_items WHERE tags IS NOT NULL')) == 0
        assert connection.execute(text('SELECT count(*) FROM project_tags GROUP BY project_id'))\
            .scalars().all() == [3, 3]
