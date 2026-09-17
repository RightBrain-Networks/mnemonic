"""Real bounded WordLevel tokenizer for deterministic passage tests without model downloads."""

from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors

from mnemonic_api.artifact_tokenizer import PassageTokenizer


def document_tokenizer(maximum=512):
    vocabulary = {word: index for index, word in enumerate([
        "[UNK]", "[CLS]", "[SEP]", "雪", "界", "needle", "authorize", "listener", "cookies",
    ])}
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.normalizer = normalizers.BertNormalizer(lowercase=False, strip_accents=False)
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    tokenizer.enable_truncation(max_length=maximum)
    return tokenizer


def passage_policy(maximum=512):
    return PassageTokenizer(document_tokenizer(maximum))
