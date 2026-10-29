import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "retrieval_eval", ROOT / "evals/retrieval/run.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_metrics_use_graded_relevance_and_do_not_count_missing_answers_as_recall():
    result = module.metrics(["irrelevant", "a", "b"], {"a": 2, "b": 1}, 2)
    assert result["recall"] == 0.5
    assert result["mrr"] == 0.5
    assert 0 < result["ndcg"] < 1
    assert module.metrics([], {}, 4) == {
        "recall": None,
        "mrr": None,
        "ndcg": None,
        "empty_result": 1,
    }
    assert module.metrics(["a"], {}, 4)["empty_result"] == 0


def test_fixed_corpus_splits_and_ablations_are_reproducible_without_provider():
    dataset = json.loads((ROOT / "evals/retrieval/dataset.json").read_text())
    ids = [item["id"] for item in dataset["queries"]]
    assert len(ids) == len(set(ids))
    assert {item["split"] for item in dataset["queries"]} == {"dev", "holdout"}
    allowed = {item["id"] for item in dataset["documents"]}
    assert all(set(item["relevance"]).issubset(allowed) for item in dataset["queries"])
    report = module.evaluate(dataset)
    assert len(report["aggregates"]) == 6
    assert {row["method"] for row in report["aggregates"]} == {
        "bm25",
        "dense_fixture_concept_cosine",
        "hybrid_rrf",
    }
    assert all(0 <= row["recall"] <= 1 for row in report["aggregates"])
    assert report["aggregates"][0]["recall"] > 0
    second = module.evaluate(dataset)
    assert [row["ranked_ids"] for row in report["queries"]] == [
        row["ranked_ids"] for row in second["queries"]
    ]


def test_provider_mode_refuses_without_explicit_paid_opt_in(monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval", "--mode", "provider"])
    with pytest.raises(SystemExit):
        module.main()
