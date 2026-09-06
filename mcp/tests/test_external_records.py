"""External contracts reject hostile data and bind every successful result to its request."""

import json
from pathlib import Path

import httpx
import pytest
from conftest import PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError
from test_duplicate_suggestions import adapter, required_arguments, structured, suggestion_page

from mnemonic_mcp.external_records import (
    ExternalCandidates,
    ExternalLabel,
    ExternalReference,
    ExternalReferences,
    ExternalURL,
    ObservationTime,
)
from mnemonic_mcp.models import DuplicateSuggestionPage, DuplicateSuggestionRequest, WorkChanges
from mnemonic_mcp.server import _suggestion_matches_request

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / 'tests/fixtures/external-record-contract-v1.json').read_text()
)
REFERENCE = {"url": "HTTPS://Example.COM:00080/issues/21?q=a%20b#note", "kind": "tracked-by",
             "state": "closed", "label": "日本語の課題"}
CANDIDATE = {"url": "https://example.com/issues/21", "title": "Draft objective",
             "body": "Untrusted provider text\n<script>data only</script>", "state": "open"}


@pytest.mark.parametrize('case', FIXTURES['url_cases'])
def test_shared_url_grammar(case):
    check_case(ExternalURL, case)


@pytest.mark.parametrize('case', FIXTURES['label_cases'])
def test_shared_label_grammar(case):
    check_case(ExternalLabel, case)


def check_case(annotation, case):
    if case['valid']:
        assert TypeAdapter(annotation).validate_python(case['value']) == case['value']
    else:
        with pytest.raises(ValidationError):
            TypeAdapter(annotation).validate_python(case['value'])


@pytest.mark.parametrize('case', FIXTURES['timestamp_cases'])
def test_shared_timestamp_grammar(case):
    if case['normalized'] is None:
        with pytest.raises(ValidationError):
            TypeAdapter(ObservationTime).validate_python(case['value'])
    else:
        assert TypeAdapter(ObservationTime).validate_python(case['value']) == case['normalized']


@pytest.mark.parametrize('field,value', [
    ('url', 42), ('label', None), ('state_observed_at', None), ('unknown', 'data'),
    ('label', '\ud800'), ('state', 'active'), ('kind', 'issue'),
])
def test_reference_rejects_noncanonical_values(field, value):
    with pytest.raises(ValidationError):
        ExternalReference.model_validate({**REFERENCE, field: value})


def test_reference_order_clear_and_omission_are_distinct():
    refs = [REFERENCE, {**REFERENCE, 'url': 'https://example.com/issues/22'}]
    original = WorkChanges(external_references=refs)
    reordered = WorkChanges(external_references=list(reversed(refs)))
    clear = WorkChanges(external_references=[])
    omitted = WorkChanges(title='Retained title')
    assert original.model_dump(mode='json', exclude_unset=True) != reordered.model_dump(
        mode='json', exclude_unset=True
    )
    assert clear.model_dump(mode='json', exclude_unset=True) == {'external_references': []}
    assert 'external_references' not in omitted.model_dump(mode='json', exclude_unset=True)
    with pytest.raises(ValidationError):
        WorkChanges(external_references=None)
    with pytest.raises(ValidationError):
        TypeAdapter(ExternalReferences).validate_python([REFERENCE, REFERENCE])
    assert len(TypeAdapter(ExternalReferences).validate_python([
        {**REFERENCE, 'url': f'https://example.com/{i}'} for i in range(10)
    ])) == 10
    with pytest.raises(ValidationError):
        TypeAdapter(ExternalReferences).validate_python([
            {**REFERENCE, 'url': f'https://example.com/{i}'} for i in range(11)
        ])


@pytest.mark.parametrize('value', [None, [CANDIDATE, CANDIDATE], [
    {**CANDIDATE, 'url': f'https://example.com/{i}'} for i in range(65)
], [{**CANDIDATE, 'body': '\ud800'}], [{**CANDIDATE, 'body': None}],
    [{**CANDIDATE, 'kind': 'tracked-by'}], [{**CANDIDATE, 'title': '\t'}]])
def test_external_candidates_are_strict_and_bounded(value):
    with pytest.raises(ValidationError):
        TypeAdapter(ExternalCandidates).validate_python(value)


def external_page():
    return {
        **suggestion_page(),
        'external_items': [{'rank': 1, 'signals': ['exact_title', 'lexical'],
                            'reference': {key: CANDIDATE[key] for key in ('url', 'title', 'state')}}],
        'external_candidate_count': 1,
        'external_scope': 'lexical',
    }


def request(candidates=None):
    args = {key: value for key, value in required_arguments().items() if key != 'project_id'}
    return DuplicateSuggestionRequest(**args, external_candidates=candidates or [])


def test_independent_external_scope_and_sparse_response():
    page = DuplicateSuggestionPage.model_validate(external_page())
    assert _suggestion_matches_request(page, request([CANDIDATE]))
    assert page.mode == 'hybrid_full' and page.external_scope == 'lexical'
    assert not _suggestion_matches_request(page, request())
    old = DuplicateSuggestionPage.model_validate(suggestion_page())
    assert old.model_dump(mode='json') == suggestion_page()
    assert not _suggestion_matches_request(old, request([CANDIDATE]))
    for empty in [[], None]:
        assert 'external_candidates' not in request(empty).model_dump(mode='json')


@pytest.mark.parametrize('mutation', [
    lambda page: page.pop('external_scope'),
    lambda page: page.update(external_items=None),
    lambda page: page.update(external_candidate_count=0),
    lambda page: page.update(external_scope='unavailable'),
    lambda page: page['external_items'][0].update(rank=2),
    lambda page: page['external_items'][0].update(signals=['lexical', 'lexical']),
    lambda page: page['external_items'][0].update(signals=['semantic']),
    lambda page: page['external_items'][0]['reference'].update(body='must not echo'),
])
def test_external_response_rejects_malformed_extension(mutation):
    page = external_page()
    mutation(page)
    with pytest.raises(ValidationError):
        DuplicateSuggestionPage.model_validate(page)


@pytest.mark.parametrize('field,value', [('url', 'https://example.com/forged'),
                                        ('title', 'forged'), ('state', 'closed')])
def test_external_identity_is_request_bound(field, value):
    page = external_page()
    page['external_items'][0]['reference'][field] = value
    assert not _suggestion_matches_request(DuplicateSuggestionPage.model_validate(page),
                                           request([CANDIDATE]))


def test_exact_overflow_selection_is_request_bound_and_url_sorted():
    candidates = [{**CANDIDATE, 'url': f'https://example.com/{i}'} for i in range(8)]
    args = request(candidates)
    page = external_page()
    page['external_candidate_count'] = len(candidates)
    page['external_items'] = [
        {'rank': i + 1, 'signals': ['exact_title'],
         'reference': {key: candidate[key] for key in ('url', 'title', 'state')}}
        for i, candidate in enumerate(candidates[:args.limit])
    ]
    assert _suggestion_matches_request(DuplicateSuggestionPage.model_validate(page), args)
    page['external_items'][-1]['reference']['url'] = candidates[-1]['url']
    assert not _suggestion_matches_request(DuplicateSuggestionPage.model_validate(page), args)


async def test_tool_sends_bounded_candidates_as_explicit_safe_read(settings):
    seen = []

    def handler(req):
        seen.append(req)
        assert json.loads(req.content)['external_candidates'] == [CANDIDATE]
        return httpx.Response(200, json=external_page())

    result = await adapter(settings, handler).call_tool('suggest_duplicate_work', {
        **required_arguments(), 'external_candidates': [CANDIDATE],
    })
    assert structured(result)['external_candidate_count'] == 1
    assert len(seen) == 1


async def test_external_roots_search_is_rejected_before_dispatch(settings):
    def handler(req):
        raise AssertionError('Invalid roots URL filter must not dispatch')

    with pytest.raises(ToolError, match='external_url'):
        await adapter(settings, handler).call_tool('search_work', {
            'project_id': PROJECT_ID, 'view': 'roots', 'external_url': CANDIDATE['url'],
        })


def test_collection_example_reserves_buckets_and_fits_actual_mcp_frames():
    import runpy

    from mcp.types import JSONRPCRequest

    example = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                / 'examples/external-candidate-frame.py'))
    buckets = [
        [{**CANDIDATE, 'url': f'https://example.com/{bucket}/{i}',
          'title': '題' * 500, 'body': ('😀\\"\n' * 4000)} for i in range(64)]
        for bucket in range(3)
    ]
    # This duplicate must not consume an open bucket reservation.
    buckets[1][0] = buckets[0][0]
    allocated = example['allocate_candidates'](buckets)
    assert len(allocated) == 64
    assert [sum(f'/{bucket}/' in item['url'] for item in allocated)
            for bucket in range(3)] == [32, 16, 16]
    sparse = example['allocate_candidates']([buckets[0], buckets[1][:4], buckets[2][:4]])
    assert len(sparse) == 64
    assert all('/0/' in item['url'] for item in sparse[:57])
    assert all('/2/' in item['url'] for item in sparse[-4:])
    args, disclosure = example['fit_comparison_frame'](
        {**required_arguments(), 'initial_prompt': '😀' * 100000}, allocated,
        request_id='request-"\\' * 4000,
    )
    http, stdio = example['frame_bytes'](args, 'request-"\\' * 4000)
    assert max(len(http), len(stdio)) <= 1_048_576
    assert disclosure['submitted_count'] == len(args.get('external_candidates', []))
    assert disclosure['bodies_truncated'] == 64
    assert all(len(item['body']) == 1500 for item in args['external_candidates'])
    # Verify the exact SDK serialization, including envelope and stdio newline.
    sdk = JSONRPCRequest.model_validate_json(http)
    sdk_stdio = sdk.model_dump_json().encode('utf-8') + b'\n'
    assert json.loads(sdk_stdio) == json.loads(stdio)
    assert len(sdk_stdio) == len(stdio)
    assert len(stdio) == disclosure['stdio_frame_bytes']
    assert args['initial_prompt'] == '😀' * 100000
    assert args['external_candidates'][0]['title'] == '題' * 500


def test_collection_example_count_reduction_keeps_identity_and_priority():
    import runpy

    example = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                / 'examples/external-candidate-frame.py'))
    candidates = [{**CANDIDATE, 'url': f'https://example.com/{i}', 'body': '😀' * 20000}
                  for i in range(64)]
    args, disclosure = example['fit_comparison_frame'](
        {**required_arguments(), 'initial_prompt': '😀' * 100000}, candidates,
        request_id='x' * 600000,
    )
    assert 0 < disclosure['submitted_count'] < 64
    assert disclosure['records_removed'] == 64 - disclosure['submitted_count']
    assert [item['url'] for item in args['external_candidates']] == [
        item['url'] for item in candidates[:disclosure['submitted_count']]
    ]
    with pytest.raises(ValueError, match='unchanged draft'):
        example['fit_comparison_frame'](
            {**required_arguments(), 'initial_prompt': '😀' * 100000}, candidates,
            request_id='x' * 1_000_000,
        )


async def test_update_clear_is_forwarded_and_receipt_correspondence_is_enforced(settings, work_item):
    from conftest import CLIENT_OPERATION_ID, WORK_ID

    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        assert body['external_references'] == []
        return httpx.Response(200, json={**work_item, 'version': 4})

    result = await adapter(settings, handler).call_tool('update_work', {
        'project_id': PROJECT_ID, 'work_item_id': WORK_ID, 'expected_version': 3,
        'changes': {'external_references': []}, 'client_operation_id': CLIENT_OPERATION_ID,
        'actor_client': 'example-client', 'actor_session_id': 'real-current-session',
    })
    assert 'external_references' not in structured(result)
    assert len(seen) == 1


async def test_update_rejects_wrong_order_as_unknown_outcome(settings, work_item):
    from conftest import CLIENT_OPERATION_ID, WORK_ID

    from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME

    refs = [REFERENCE, {**REFERENCE, 'url': 'https://example.com/second'}]

    def handler(req):
        assert json.loads(req.content)['external_references'] == refs
        return httpx.Response(200, json={**work_item, 'version': 4,
                                         'external_references': list(reversed(refs))})

    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await adapter(settings, handler).call_tool('update_work', {
            'project_id': PROJECT_ID, 'work_item_id': WORK_ID, 'expected_version': 3,
            'changes': {'external_references': refs}, 'client_operation_id': CLIENT_OPERATION_ID,
            'actor_client': 'example-client', 'actor_session_id': 'real-current-session',
        })


async def test_create_sends_references_and_requires_exact_correspondence(settings, work_item,
                                                                       checkpoint):
    from conftest import CLIENT_OPERATION_ID

    from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME

    args = {
        'project_id': PROJECT_ID, 'title': work_item['title'], 'summary': work_item['summary'],
        'priority': work_item['priority'], 'initial_checkpoint': {
            key: checkpoint[key] for key in ('prompt', 'source_client', 'source_session_id')
        }, 'client_operation_id': CLIENT_OPERATION_ID, 'external_references': [REFERENCE],
    }

    def handler(req):
        assert json.loads(req.content)['external_references'] == [REFERENCE]
        # A sparse historical receipt cannot satisfy a populated create intent.
        return httpx.Response(201, json={'work_item': {**work_item, 'version': 1},
                                         'initial_checkpoint': checkpoint,
                                         'initial_relationships': []})

    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await adapter(settings, handler).call_tool('create_work', args)


async def test_resource_and_resume_prompt_expose_observed_references(settings, work_context):
    work_context['work_item']['external_references'] = [REFERENCE]

    def handler(req):
        return httpx.Response(200, json=work_context)

    server = adapter(settings, handler)
    resource = await server.read_resource(f'mnemonic://projects/{PROJECT_ID}/work-items/'
                                          f'{work_context["work_item"]["id"]}')
    assert REFERENCE['url'] in str(resource)
    prompt = await server.get_prompt('resume_work', {'project_id': PROJECT_ID,
                                                    'work_item_id': work_context['work_item']['id']})
    assert REFERENCE['url'] in str(prompt)
    assert 'caller observations' in str(prompt)


@pytest.mark.parametrize('event_type', [
    'work_created', 'work_updated', 'work_status_changed', 'work_reopened',
])
def test_expanded_system_events_accept_large_reference_snapshots(event_type, progress_event):
    from conftest import CHECKPOINT_ID

    from mnemonic_mcp.models import WorkEventRead

    refs = [{**REFERENCE, 'url': 'https://example.com/' + str(i) + 'x' * 1979,
             'label': '😀' * 120} for i in range(10)]
    after = [{**item, 'state': 'open'} for item in refs]
    if event_type == 'work_created':
        metadata = {'initial': {'title': 'Known title', 'summary': 'Existing initial context',
                                'priority': 0, 'status': 'pending', 'version': 1,
                                'external_references': refs}}
    else:
        metadata = {'work_version': 4, 'changes': {
            'title': {'before': '"' * 200, 'after': '\\' * 200},
            'summary': {'before': '"' * 1000, 'after': '\\' * 1000},
            'priority': {'before': 0, 'after': 100},
            'external_references': {'before': refs, 'after': after},
        }}
        if event_type != 'work_updated':
            before_status, after_status = ('pending', 'promoted') if (
                event_type == 'work_status_changed'
            ) else ('promoted', 'pending')
            metadata.update(from_status=before_status, to_status=after_status)
            metadata['changes']['status'] = {'before': before_status, 'after': after_status}
    assert len(json.dumps(metadata, ensure_ascii=False).encode('utf-8')) > 16384
    event = {**progress_event, 'event_type': event_type, 'body': None, 'metadata': metadata,
             'checkpoint_id': CHECKPOINT_ID if event_type == 'work_created' else None}
    parsed = WorkEventRead.model_validate(event)
    assert parsed.model_dump(mode='json')['metadata'] == metadata


def test_external_clear_event_preserves_arrays_and_progress_limit(progress_event):
    from mnemonic_mcp.models import WorkEventRead

    metadata = {'work_version': 4, 'changes': {'external_references': {
        'before': [REFERENCE], 'after': [],
    }}}
    parsed = WorkEventRead.model_validate({**progress_event, 'event_type': 'work_updated',
                                           'body': None, 'metadata': metadata})
    assert parsed.model_dump(mode='json')['metadata'] == metadata
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate({**progress_event, 'metadata': {'text': 'x' * 16384}})
