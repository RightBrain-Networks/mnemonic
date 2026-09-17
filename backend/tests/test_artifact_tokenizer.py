"""Token-aware passage windows cover Unicode source characters without inference truncation."""

from types import SimpleNamespace

import pytest

from mnemonic_api.artifact_passages import BATCH_CHARS, next_passage_offset, passage_batch
from mnemonic_api.artifact_tokenizer import PassageTokenizer
from mnemonic_api.semantic import FastembedEmbedder

from .artifact_passage_fixtures import document_tokenizer, passage_policy


@pytest.mark.parametrize("body", [
    "雪" * 1400 + " needle " + "界" * 5000,
    ("雪e\u0301界😃 " * 2000) + "needle",
    "one word " * 5000,
])
def test_every_character_is_covered_and_each_retokenized_passage_fits(body):
    tokenizer = passage_policy()
    offset, ordinal, previous_end = 0, 0, 0
    chunks = []
    while offset < len(body):
        batch = passage_batch(body[offset:offset + BATCH_CHARS], offset, ordinal,
                              len(body), tokenizer)
        assert 1 <= len(batch) <= 16
        for chunk in batch:
            assert chunk.start <= previous_end and chunk.end > chunk.start
            assert chunk.text == body[chunk.start:chunk.end]
            assert tokenizer.count(chunk.text) == chunk.token_count <= tokenizer.maximum
            # This is the actual, truncating inference tokenizer; no overflow
            # proves that no source token was dropped before the fake model.
            encoded = document_tokenizer().encode(chunk.text)
            assert not encoded.overflowing and len(encoded.ids) == chunk.token_count
            previous_end = chunk.end
        chunks.extend(batch)
        ordinal = batch[-1].ordinal + 1
        offset = len(body) if batch[-1].end == len(body) else next_passage_offset(batch[-1])
    assert previous_end == len(body)
    assert any("needle" in chunk.text for chunk in chunks) == ("needle" in body)


def test_fastembed_adapter_clones_exact_native_tokenizer_without_mutating_inference():
    native = document_tokenizer()
    embedder = FastembedEmbedder()
    embedder._model = SimpleNamespace(model=SimpleNamespace(tokenizer=native))
    policy = embedder.passage_tokenizer()
    assert policy is embedder.passage_tokenizer()
    assert native.truncation["max_length"] == 512 and policy.tokenizer.truncation is None
    assert len(native.encode("雪" * 1400).ids) == 512
    assert policy.count("雪" * 1400) == 1402
    length, tokens = policy.fit("雪" * 1400)
    assert length == 510 and tokens == 512


def test_effective_configuration_binds_tokenizer_rules_and_input_limit():
    original = document_tokenizer()
    bounded = document_tokenizer(64)
    changed = document_tokenizer()
    changed.add_tokens(["newly-known-word"])
    policies = [PassageTokenizer(value) for value in (original, bounded, changed)]
    assert len({policy.config for policy in policies}) == 3
    assert all(len(policy.config) <= 100 for policy in policies)
    assert PassageTokenizer(original).config == policies[0].config


def test_too_small_or_missing_token_window_fails_without_claiming_source_coverage():
    with pytest.raises(ValueError, match="source character"):
        passage_policy(2).fit("雪")
    tokenizer = document_tokenizer()
    tokenizer.no_truncation()
    with pytest.raises(ValueError, match="bounded input window"):
        PassageTokenizer(tokenizer)
