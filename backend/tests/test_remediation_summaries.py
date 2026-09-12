"""Generated remediation summaries describe every finding within deployment bounds."""

import pytest

from mnemonic_api.code_review_schemas import CodeReviewFindingInput
from mnemonic_api.services.remediation_summaries import remediation_summary

from .code_review_fixtures import finding


def rows(*changes):
    return [CodeReviewFindingInput.model_validate({**finding(f"F{index:03}"), **fields})
            for index, fields in enumerate(changes, 1)]


def test_summary_names_primary_file_and_each_finding_in_submission_order():
    findings = rows(
        {"path": "src/other.py", "title": "Guard missing rows"},
        {"title": "Invalidate stale readers"},
        {"title": "Serialize concurrent writers"},
    )
    assert remediation_summary(findings, 2048) == (
        "Fix 3 review findings (primary file: main:src/cache.py): "
        "Guard missing rows; Invalidate stale readers; Serialize concurrent writers"
    )


def test_single_finding_and_tied_primary_location_are_deterministic():
    findings = rows({"repository_key": "api"}, {"repository_key": "dashboard"})
    assert "primary file: api:src/cache.py" in remediation_summary(findings, 2048)
    assert remediation_summary(findings[:1], 2048) == (
        "Fix 1 review finding (primary file: api:src/cache.py): Invalidation misses readers"
    )


@pytest.mark.parametrize("maximum", [1, 32, 256, 2048])
def test_long_paths_unicode_and_titles_remain_bounded(maximum):
    findings = rows(*({"path": "src/" + "é" * 2000 + ".py", "title": "🧠" * 200}
                      for _ in range(3)))
    summary = remediation_summary(findings, maximum)
    assert 0 < len(summary) <= maximum
    assert "…" in summary
    if maximum >= 256:
        assert summary.count("🧠") >= 3
        assert summary.count("; ") == 2


def test_summary_collapses_title_whitespace():
    assert remediation_summary(rows({"title": "Fix   stale  readers"}), 2048).endswith(
        ": Fix stale readers"
    )
