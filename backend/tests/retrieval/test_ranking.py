import math

import pytest

from app.services.retrieval_ranking import (
    lexical_rank,
    reciprocal_rank_fusion,
    tokenize,
)


def test_tokenization_keeps_model_numbers_and_normalizes_unicode():
    tokens = tokenize("The ＲＸ－７００ / RX-710 型号恢复出厂设置")
    assert "rx-700" in tokens
    assert "rx-710" in tokens
    assert "恢复" in tokens
    assert "出厂" in tokens
    assert "the" not in tokens


def test_bm25_handles_chinese_english_and_exact_model_without_filler():
    corpus = [
        {"id": "model", "content": "RX-700 battery uses CR2032"},
        {"id": "other", "content": "RX-710 battery uses AA"},
        {"id": "zh", "content": "长按复位键十秒即可恢复出厂设置"},
    ]
    assert lexical_rank("RX-700", corpus)[0][0] == "model"
    assert lexical_rank("恢复出厂设置", corpus)[0][0] == "zh"
    assert lexical_rank("cr2032 battery", corpus)[0][0] == "model"
    assert lexical_rank("quasar astrophysics", corpus) == []


def test_rrf_deduplicates_each_list_and_prefers_agreement():
    ranked = reciprocal_rank_fusion([["a", "b", "a"], ["b", "c"]], limit=2)
    assert [identity for identity, _ in ranked] == ["b", "a"]
    assert ranked[0][1] == pytest.approx(1 / 62 + 1 / 61)
    assert all(math.isfinite(score) for _, score in ranked)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], limit=0)
