"""Citation hints preserve literal evidence while bounding payload cost."""

from mnemonic_api.services.decision_citation_excerpts import citation_excerpts

PATH = ["ADR-02779-1", "BDR-00042-3", "ADR-02798-1"]


def test_metadata_citation_includes_adjacent_sentences_and_exact_source_bytes():
    content = (
        "# ADR-02798\nBackground first. Keep this context.\n"
        "Related (2026-10-23): Superseded in part by BDR-00042 for line ordering.\n"
        "The other ordering rules remain. Further detail."
    )
    excerpts, truncated = citation_excerpts(content, PATH)
    assert excerpts == [
        "Keep this context.\nRelated (2026-10-23): Superseded in part by BDR-00042 "
        "for line ordering.\nThe other ordering rules remain."
    ]
    assert excerpts[0] in content
    assert truncated is False


def test_identifier_boundaries_avoid_own_id_and_similar_decision_numbers():
    content = "ADR-02798-1 XBDR-00042 BDR-000420 BDR-00042-more BDR-00042-3."
    excerpts, truncated = citation_excerpts(content, PATH)
    assert excerpts == [content]
    assert not truncated
    assert citation_excerpts(content[:-13], PATH) == ([], False)
    assert citation_excerpts(content, [PATH[-1]]) == ([], False)


def test_long_unicode_context_keeps_the_citation_within_the_character_cap():
    content = "🙂" * 1200 + " BDR-00042 " + "é" * 1200
    excerpts, truncated = citation_excerpts(content, PATH)
    assert len(excerpts) == 1 and len(excerpts[0]) == 400
    assert "BDR-00042" in excerpts[0]
    assert excerpts[0] in content
    assert truncated is True


def test_three_excerpts_cap_reports_additional_evidence_without_duplicating_context():
    blocks = [f"Before {index}. BDR-00042 means rule {index}. After {index}. "
              + "Unrelated sentence. " * 4 for index in range(5)]
    excerpts, truncated = citation_excerpts("".join(blocks), PATH)
    assert len(excerpts) == 3
    assert all(len(value) <= 400 for value in excerpts)
    assert all(f"rule {index}" in excerpts[index] for index in range(3))
    assert truncated is True


def test_nearby_citations_share_one_excerpt_and_absence_is_not_a_verdict():
    content = "Before. BDR-00042 and ADR-02779 both apply. After."
    assert citation_excerpts(content, PATH) == ([content], False)
    assert citation_excerpts("The rule still applies without spelling its ID.", PATH) == ([], False)
