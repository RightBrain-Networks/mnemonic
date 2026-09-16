"""Bounded plain excerpts chosen by distinct-term coverage and proximity."""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TermSpan:
    term: str
    start: int
    end: int
    position: int


def term_spans(text: str, analyze: Callable[[str], list[str]]) -> list[TermSpan]:
    # SimpleTokenizer separates underscores and punctuation. Analyze each native
    # token so accent folding/expansion never changes the source offsets.
    return [TermSpan(term, match.start(), match.end(), position)
            for position, match in enumerate(re.finditer(r"[^\W_]+", text))
            for term in analyze(match.group())]


def phrase_span(text: str, phrase: str,
                analyze: Callable[[str], list[str]]) -> tuple[int, int] | None:
    wanted = analyze(phrase)
    if not wanted:
        return None
    spans = term_spans(text, analyze)
    for start in range(len(spans) - len(wanted) + 1):
        selected = spans[start:start + len(wanted)]
        adjacent = selected[-1].position - selected[0].position == len(wanted) - 1
        if adjacent and [span.term for span in selected] == wanted:
            return selected[0].start, selected[-1].end
    return None


def phrase_snippet(text: str, phrases: tuple[str, ...], unquoted: str,
                   analyze: Callable[[str], list[str]], *, maximum_chars: int = 960) -> str | None:
    """Show a complete analyzed phrase, never a nearby reversed-term decoy."""
    for phrase in phrases:
        span = phrase_span(text, phrase, analyze)
        if span is not None:
            start, end = span
            if end - start > maximum_chars:
                return None
            return _excerpt(text, start, end, min(maximum_chars, max(320, end - start)))
    # A phrase may qualify only in metadata while body evidence supplies an
    # unquoted clause. Do not substitute arbitrary phrase terms in that case.
    return supporting_snippet(text, unquoted, analyze, maximum_chars=maximum_chars)


def _best_window(spans: list[TermSpan], budget: int) -> tuple[int, int, set[str]] | None:
    counts: Counter[str] = Counter()
    left = 0
    best = None
    best_score = (0, 0)
    for right, span in enumerate(spans):
        counts[span.term] += 1
        while left < right and span.end - spans[left].start > budget:
            term = spans[left].term
            counts[term] -= 1
            if not counts[term]:
                del counts[term]
            left += 1
        score = (len(counts), -(span.end - spans[left].start))
        if score > best_score:
            best = spans[left].start, span.end, set(counts)
            best_score = score
    return best


def _excerpt(text: str, start: int, end: int, budget: int) -> str:
    spare = max(0, budget - (end - start))
    lower = max(0, start - spare // 2)
    upper = min(len(text), lower + budget)
    lower = max(0, upper - budget)
    return text[lower:upper]


def supporting_snippet(text: str, query: str, analyze: Callable[[str], list[str]],
                       *, literal: bool = False, maximum_chars: int = 960) -> str | None:
    """An exact contiguous occurrence wins; otherwise cover distinct query terms.

    At most three excerpts share one character budget. Returning None rather than
    an unrelated prefix makes absence of lexical evidence explicit.
    """
    if not text or not query.strip():
        return None
    needle = query if literal else query.strip().strip('"')
    exact = re.search(re.escape(needle), text, flags=0 if literal else re.IGNORECASE)
    if exact is not None:
        return _excerpt(text, exact.start(), exact.end(), min(maximum_chars, 320))
    if literal:
        return None
    remaining = set(analyze(query))
    spans = [span for span in term_spans(text, analyze) if span.term in remaining]
    excerpts = []
    budget = min(320, maximum_chars)
    while spans and len(excerpts) < 3 and maximum_chars > 0:
        window = _best_window(spans, min(budget, maximum_chars))
        if window is None:
            break
        start, end, supported = window
        excerpt = _excerpt(text, start, end, min(budget, maximum_chars))
        excerpts.append((start, excerpt))
        maximum_chars -= len(excerpt) + 5
        remaining -= supported
        spans = [span for span in spans if span.term in remaining]
    return " […] ".join(excerpt for _, excerpt in sorted(excerpts)) or None
