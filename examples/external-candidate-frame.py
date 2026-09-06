"""Offline caller example: allocate already-gathered buckets and fit actual MCP frames.

Provider reads are deliberately absent. Follow plugin/reference/external-records.md
for repository binding, classification, ordering, deadlines and failure disclosure.
Input buckets are already validated, relevance/update-time/URL ordered provider
records, with credentials stripped; this example does not infer those facts.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_FRAME_BYTES = 1_048_576


def allocate_candidates(buckets: Sequence[Sequence[Mapping[str, str]]]) -> list[dict[str, str]]:
    """Deduplicate before reserving 32/16/16, then fill spare slots by bucket priority."""
    if len(buckets) != 3 or any(len(bucket) > 64 for bucket in buckets):
        raise ValueError("Supply three buckets of at most 64 already ordered records.")
    unique_buckets: list[list[dict[str, str]]] = []
    seen: set[str] = set()
    for bucket in buckets:
        unique: list[dict[str, str]] = []
        for item in bucket:
            if item['url'] not in seen:
                seen.add(item['url'])
                unique.append(dict(item))
        unique_buckets.append(unique)
    selected: list[dict[str, str]] = []
    remaining: list[dict[str, str]] = []
    for bucket, reserve in zip(unique_buckets, (32, 16, 16), strict=True):
        selected.extend(bucket[:reserve])
        remaining.extend(bucket[reserve:])
    selected.extend(remaining[:64-len(selected)])
    # Keep final presentation/reduction priority even when unused reservations
    # were filled from an earlier bucket.
    order = {item['url']: position for position, item in enumerate(
        item for bucket in unique_buckets for item in bucket
    )}
    return sorted(selected, key=lambda item: order[item['url']])


def frame_bytes(arguments: Mapping[str, Any], request_id: str | int) -> tuple[bytes, bytes]:
    """Use the real JSON-RPC envelope and the same UTF-8 encoding as MCP model_dump_json."""
    envelope = {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                'params': {'name': 'suggest_duplicate_work', 'arguments': dict(arguments)}}
    encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    return encoded, encoded + b'\n'


def fit_comparison_frame(
    draft_arguments: Mapping[str, Any],
    candidates: Sequence[Mapping[str, str]],
    *,
    request_id: str | int,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Keep draft and identities intact; disclose prefix truncation/count reduction."""
    if len(candidates) > 64 or 'external_candidates' in draft_arguments:
        raise ValueError("Pass the unmodified draft and at most 64 allocated candidates.")
    bounded = [{**item, 'body': item['body'][:1500]} for item in candidates]
    shortened = sum(len(item['body']) > 1500 for item in candidates)
    while True:
        arguments = dict(draft_arguments)
        if bounded:
            arguments['external_candidates'] = bounded
        http, stdio = frame_bytes(arguments, request_id)
        if max(len(http), len(stdio)) <= MAX_FRAME_BYTES:
            return arguments, {'submitted_count': len(bounded), 'bodies_truncated': shortened,
                               'records_removed': len(candidates)-len(bounded),
                               'http_frame_bytes': len(http), 'stdio_frame_bytes': len(stdio)}
        if not bounded:
            raise ValueError("The unchanged draft exceeds the complete MCP frame limit.")
        bounded.pop()
