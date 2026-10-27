"""Catalog response coherence and current-label request validation."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.schemas import WorkItemCreate, WorkItemRead
from mnemonic_api.services.client_operations import _create_work_matches, operation_spec
from mnemonic_api.services.tags import tag_response_matches
from mnemonic_api.tag_schemas import TagCatalog, TagCreate, TagPatch


def test_catalog_create_receipt_checks_explicit_color():
    project = uuid4()
    request = TagCreate(name='Needs Design', description='Plan UI changes', color='#AABBCC')
    response = TagCatalog(project_id=project, total=1, items=[{
        'id': uuid4(), 'project_id': project, 'name': 'needs-design',
        'description': 'Plan UI changes', 'color': '#aabbcc', 'menu_order': 0, 'system': False}])
    assert tag_response_matches(project, {}, request, response)
    response.items[0].color = '#000000'
    assert not tag_response_matches(project, {}, request, response)
    assert TagPatch(color='#AABBCC').color == '#aabbcc'


def test_work_creation_tags_require_normalized_unique_nonempty_list(work_payload):
    assert WorkItemCreate.model_validate(work_payload).tags is None
    for tags in ([], ['Bug'], ['bug', 'bug'], ['needs design']):
        with pytest.raises(ValidationError):
            WorkItemCreate.model_validate({**work_payload, 'tags': tags})


def test_historical_read_does_not_invent_tag_fields():
    payload = {'id': uuid4(), 'project_id': uuid4(), 'title': 'Historical work',
        'summary': 'Historic immutable receipt', 'status': 'pending', 'priority': 40,
        'initial_checkpoint_id': uuid4(), 'version': 1,
        'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-01T00:00:00Z'}
    restored = WorkItemRead.model_validate(payload).model_dump()
    assert 'tags' not in restored and 'system_tags' not in restored
    current = WorkItemRead.model_validate({**payload, 'tags': None, 'system_tags': []}).model_dump()
    assert current['tags'] is None and current['system_tags'] == []


@pytest.mark.parametrize('tags', [None, ['bug', 'frontend']])
def test_creation_receipt_requires_declared_tags_but_preserves_historical_omission(
    work_payload, tags,
):
    project, work, checkpoint = uuid4(), uuid4(), uuid4()
    payload = WorkItemCreate.model_validate({**work_payload, 'tags': tags})
    source = {
        'work_item': {'id': work, 'project_id': project, 'title': payload.title,
            'summary': payload.summary, 'priority': payload.priority, 'status': 'pending',
            'initial_checkpoint_id': checkpoint, 'version': 1, 'tags': tags,
            'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-01T00:00:00Z'},
        'initial_checkpoint': {**payload.initial_checkpoint.model_dump(), 'id': checkpoint,
            'work_item_id': work, 'kind': 'context', 'migration_origin': None,
            'legacy_record_id': None, 'created_at': '2026-10-01T00:00:00Z'},
        'initial_relationships': [],
    }
    model = operation_spec('create_work').response_model
    assert _create_work_matches(project, {}, payload, model.model_validate(source))
    source['work_item']['tags'] = ['wrong-tag']
    assert not _create_work_matches(project, {}, payload, model.model_validate(source))
    source['work_item'].pop('tags')
    assert not _create_work_matches(project, {}, payload, model.model_validate(source))
    historical = WorkItemCreate.model_validate({
        key: value for key, value in work_payload.items() if key != 'tags'})
    assert _create_work_matches(project, {}, historical, model.model_validate(source))
