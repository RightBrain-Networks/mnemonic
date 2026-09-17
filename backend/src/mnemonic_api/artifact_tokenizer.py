"""Bound artifact passages against the exact tokenizer used for document inference."""

import hashlib
import json

from tokenizers import Tokenizer
from tokenizers import __version__ as tokenizer_version


class PassageTokenizer:
    def __init__(self, source: Tokenizer):
        truncation = source.truncation
        maximum = truncation.get("max_length") if truncation else None
        if type(maximum) is not int or not 1 <= maximum <= 8192:
            raise ValueError("The embedding tokenizer must declare a bounded input window")
        self.maximum = maximum
        # Never mutate the tokenizer shared by query/work inference. Counting the
        # complete encoding includes special tokens and cannot silently truncate.
        self.tokenizer = Tokenizer.from_str(source.to_str())
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        specification = {"engine_version": tokenizer_version,
                         "tokenizer": json.loads(self.tokenizer.to_str())}
        canonical = json.dumps(specification, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        self.sha256 = hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def config(self) -> str:
        from mnemonic_api.artifact_passages import CHUNK_CONFIG

        return f"{CHUNK_CONFIG}:{self.sha256}:{self.maximum}"

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)

    def fit(self, text: str) -> tuple[int, int]:
        count = self.count(text)
        if count <= self.maximum:
            return len(text), count
        # Counts need not be strictly monotonic for every subword tokenizer.
        # Binary search only chooses a VERIFIED fitting prefix, not necessarily
        # the longest possible one. Complete source coverage does not depend on
        # maximizing a passage's length.
        low, high, fitting, fitting_count = 1, len(text) - 1, 0, 0
        while low <= high:
            middle = (low + high) // 2
            count = self.count(text[:middle])
            if count <= self.maximum:
                fitting, fitting_count, low = middle, count, middle + 1
            else:
                high = middle - 1
        if not fitting:
            raise ValueError("One source character exceeds the embedding tokenizer window")
        return fitting, fitting_count


def passage_tokenizer(embedder) -> PassageTokenizer:
    provider = getattr(embedder, "passage_tokenizer", None)
    if not callable(provider):
        raise ValueError("The embedding provider does not expose its document tokenizer")
    result = provider()
    if not isinstance(result, PassageTokenizer):
        raise ValueError("The embedding provider returned an unsupported tokenizer")
    return result
