"""Bounded search summaries derived from the findings retained in full at creation."""

from collections import Counter

from mnemonic_api.code_review_schemas import CodeReviewFindingInput


def _abbreviate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum - 1].rstrip() + "…"


def remediation_summary(findings: list[CodeReviewFindingInput], maximum: int) -> str:
    """Count findings, identify their primary file, and give each title a clause.

    Ties use submitted finding order. Long summaries share the remaining budget
    across clauses; the immutable initial checkpoint always retains full details.
    """
    locations = Counter((finding.repository_key, finding.path) for finding in findings)
    (repository, path), _ = locations.most_common(1)[0]
    location = _abbreviate(" ".join(f"{repository}:{path}".split()), min(160, max(1, maximum // 4)))
    noun = "finding" if len(findings) == 1 else "findings"
    prefix = f"Fix {len(findings)} review {noun} (primary file: {location}): "
    clauses = [" ".join(finding.title.split()) for finding in findings]
    if len(prefix) + len("; ".join(clauses)) > maximum:
        clause_budget = max(1, (maximum - len(prefix) - 2 * (len(clauses) - 1)) // len(clauses))
        clauses = [_abbreviate(clause, clause_budget) for clause in clauses]
    return _abbreviate(prefix + "; ".join(clauses), maximum)
