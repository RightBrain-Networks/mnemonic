"""Exact query intent must survive parsing, indexing, field boundaries and evidence."""

import pytest

from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument, SearchHit
from mnemonic_api.search_query import parse_query


def search(documents, query, **options):
    return ArtifactSearchIndex().search('fixture', lambda: iter(documents),
        query=query, fulltext=True, count=len(documents), **options).hits


def test_reversed_quoted_phrases_select_different_records():
    docs = [SearchDocument('forward', '', 'admission cookie'),
            SearchDocument('reverse', '', 'cookie admission')]
    assert [hit.identity for hit in search(docs, '"admission cookie"')] == ['forward']
    assert [hit.identity for hit in search(docs, '"cookie admission"')] == ['reverse']
    assert len(search(docs, 'admission cookie')) == 2
    assert [hit.identity for hit in search(docs, 'admission cookie', query_mode='phrase')] == [
        'forward'
    ]


def test_phrases_cannot_cross_fields_or_distinct_field_values():
    docs = [SearchDocument('fields', 'admission', 'cookie'),
            SearchDocument('segments', '', 'admission\n\ncookie',
                           content_parts=('admission', 'cookie')),
            SearchDocument('properties', 'admission\ncookie',
                           metadata_parts=('admission', 'cookie'))]
    assert search(docs, '"admission cookie"') == []
    assert len(search(docs, 'admission cookie')) == 3


def test_field_evidence_requires_the_complete_phrase():
    docs = [SearchDocument('one', 'admission', 'admission cookie')]
    hits = search(docs, '"admission cookie"')
    assert len(hits) == 1
    assert hits[0].content and not hits[0].metadata


def test_mixed_clauses_are_conjoined_and_repeated_phrase_words_are_retained():
    docs = [SearchDocument('one', 'target', 'very very safe'),
            SearchDocument('two', 'target', 'very safe'),
            SearchDocument('three', 'unrelated', 'very very safe')]
    assert [hit.identity for hit in search(docs, 'target "very very safe"')] == ['one']
    assert len(search(docs, 'target very safe')) == 2


def test_phrase_case_accents_and_punctuation_follow_the_declared_analyzer():
    docs = [SearchDocument('one', '', 'CAFÉ-cookie')]
    assert search(docs, '"cafe cookie"')


@pytest.mark.parametrize('query,mode,code', [
    ('"open', 'terms', 'unclosed_query_phrase'),
    ('word ""', 'terms', 'query_phrase_requires_terms'),
    ('', 'phrase', 'query_phrase_requires_terms'),
    ('', 'literal', 'exact_query_requires_text'),
])
def test_invalid_intent_has_a_reviewed_rule(query, mode, code):
    with pytest.raises(ValueError) as error:
        parse_query(query, mode)
    assert error.value.type == code


def test_literal_mode_requires_an_explicit_access_checked_matcher():
    docs = [SearchDocument('one', '', 'lease token mismatch')]
    with pytest.raises(ValueError, match='access-checked exact matcher'):
        search(docs, 'lease_token_mismatch', query_mode='literal')
    assert search(docs, 'lease_token_mismatch', query_mode='literal',
                  literal_matches=lambda: []) == []
    assert search(docs, '...', query_mode='literal', literal_matches=lambda: [
        SearchHit('exact', 1.0, False, True),
    ])[0].identity == 'exact'
