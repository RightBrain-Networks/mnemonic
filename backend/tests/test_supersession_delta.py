"""Supersedes heading extraction is bounded evidence, never a rule classifier."""

import pytest

from mnemonic_api.services.supersession_delta import supersedes_section


@pytest.mark.parametrize("heading", ["## Supersedes", "# supersedes #", "Supersedes\n----------"])
def test_extracts_literal_section_and_subsections_without_other_sections(heading):
    text = (f"# Record\nIntro\n{heading}\nPartial replacement.\n### Detail\n"
            "Still selected.\n# Other\nNo.")
    result = supersedes_section(text)
    assert result.found and result.section_count == 1
    assert "Partial replacement." in result.text and "Still selected." in result.text
    assert "# Record" not in result.text and "# Other" not in result.text
    assert not result.truncated


def test_code_fences_and_embedded_words_do_not_invent_supersedes_sections():
    text = "```md\n## Supersedes\nExample\n```\n# Context\nThis supersedes no rule."
    result = supersedes_section(text)
    assert not result.found and result.text == ""


def test_multiple_literal_sections_remain_visible():
    text = "## Supersedes\nFirst\n## Other\nContext\n## Supersedes\nSecond"
    result = supersedes_section(text)
    assert result.section_count == 2 and "First" in result.text and "Second" in result.text
    assert "Context" not in result.text


def test_utf8_section_bytes_and_source_scan_are_bounded_and_reported():
    result = supersedes_section("## Supersedes\n" + "🦊" * 20000)
    assert result.found and result.truncated and len(result.text.encode("utf-8")) <= 8192
    missing = supersedes_section("x" * 65536 + "\n## Supersedes\nBeyond the scan")
    assert not missing.found and missing.truncated and missing.source_characters_scanned == 65536
    compact = supersedes_section("## Supersedes\nA", include_text=False)
    assert "text" not in compact.model_dump() and compact.found
