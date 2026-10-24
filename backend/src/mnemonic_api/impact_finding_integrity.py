"""Compare nullable new metadata without rewriting historical receipt bodies."""

_DEFAULTS = "jsonb_build_object('finding_id',NULL,'remediation_target',NULL)"


def disposition_receipt_contains(item: str) -> str:
    """SQL fragments are fixed identifiers supplied only by the audit definitions."""
    return (
        f"({_DEFAULTS} || ({item})) @> "
        f"({_DEFAULTS} || (to_jsonb(disposition.*)-'created_at'))"
    )
