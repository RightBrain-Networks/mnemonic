"""Evidence regressions from agent discovery queries over repetitive transcripts."""

from mnemonic_api.artifact_index import literal_terms
from mnemonic_api.search_snippets import supporting_snippet


def snippet(text, query, **options):
    return supporting_snippet(text, query, literal_terms, **options)


def test_late_identifier_beats_repeated_readiness_terms():
    text = ('{"lease": "active", "lease_token": "opaque"}\n' * 2000
            + 'The command failed with lease_token_mismatch during renewal.\n'
            + 'Ordinary trailing output.\n' * 200)
    result = snippet(text, 'lease_token_mismatch')
    assert result is not None
    assert 'failed with lease_token_mismatch during renewal' in result
    assert len(result) <= 320


def test_distinct_coverage_wins_over_repetitions_without_exact_string():
    text = 'telemetry ' * 500 + 'The shards now emit telemetry correctly.' + ' idle' * 500
    result = snippet(text, 'shards telemetry')
    assert result is not None
    assert 'shards now emit telemetry' in result


def test_distant_terms_return_bounded_excerpts_in_source_order():
    text = 'first needle' + ' filler' * 500 + 'second marker' + ' filler' * 500
    result = snippet(text, 'needle marker')
    assert result is not None
    assert 'needle' in result and 'marker' in result
    assert result.index('needle') < result.index('marker')
    assert ' […] ' in result and len(result) <= 960


def test_accent_and_unicode_expansion_keep_native_text_offsets():
    text = 'padding ' * 100 + 'Straße café payroll' + ' padding' * 100
    result = snippet(text, 'strasse cafe')
    assert result is not None and 'Straße café' in result


def test_literal_is_case_sensitive_and_preserves_punctuation():
    assert snippet('Lease_token_mismatch', 'lease_token_mismatch', literal=True) is None
    assert snippet('lease token mismatch', 'lease_token_mismatch', literal=True) is None
    assert snippet('lease_token_mismatch', 'lease_token_mismatch', literal=True) == (
        'lease_token_mismatch'
    )


def test_absent_terms_return_no_fabricated_prefix():
    assert snippet('unrelated readiness JSON', 'telemetry') is None
    assert snippet('ordinary text', '') is None


def test_plain_untrusted_source_text_is_not_html_rendered():
    assert snippet('<script>needle()</script>', 'needle') == '<script>needle()</script>'
