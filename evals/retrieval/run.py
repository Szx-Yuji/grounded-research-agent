"""Fixed-data retrieval ablations. Default execution is local and provider-free.

python evals/retrieval/run.py --output evals/results/retrieval-fixture.json
Provider mode is opt-in, uses the configured embedding API and requires
--mode provider --allow-paid-provider. It does not evaluate generated answers.
"""

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.retrieval_ranking import (
    TOKENIZER_VERSION,
    lexical_rank,
    reciprocal_rank_fusion,
    tokenize,
)

# An explicit toy concept vocabulary, independent of qrels and document IDs.
# It tests ranking/fusion wiring; it is not a learned or realistic embedding.
CONCEPTS = {
    "reset": ["factory reset", "restore defaults", "恢复出厂", "清除网络", "复位键"],
    "retention": ["retention", "records stay", "资料保留", "保留多久"],
    "customer": ["customer", "客户", "账户关闭"],
    "archive": ["archived", "audit", "审计"],
    "cancel": ["cancel", "closing the browser", "关闭浏览器", "取消回复"],
    "expiry": ["expire", "valid for", "到期"],
    "delete": ["delete", "删除"],
    "private": ["private", "私有"],
}


def fixture_vector(text):
    lower = text.casefold()
    counts = Counter(
        term
        for term in tokenize(lower)
        if len(term) > 2 and not any(character.isdigit() for character in term)
    )
    for concept, variants in CONCEPTS.items():
        if any(variant in lower for variant in variants):
            counts["concept:" + concept] += 3
    return counts


def cosine(left, right):
    numerator = sum(value * right.get(key, 0) for key, value in left.items())
    denominator = math.sqrt(
        sum(value * value for value in left.values())
        * sum(value * value for value in right.values())
    )
    return numerator / denominator if denominator else 0


def metrics(ranked, relevance, k):
    """Recall/MRR/nDCG exclude no-answer questions; those have separate abstention."""
    ranked = list(dict.fromkeys(ranked))[:k]
    relevant = {identity for identity, grade in relevance.items() if grade > 0}
    if not relevant:
        return {
            "recall": None,
            "mrr": None,
            "ndcg": None,
            "empty_result": float(not ranked),
        }
    recall = len(set(ranked) & relevant) / len(relevant)
    mrr = next(
        (1 / rank for rank, identity in enumerate(ranked, 1) if identity in relevant), 0
    )
    dcg = sum(
        (2 ** relevance.get(identity, 0) - 1) / math.log2(rank + 1)
        for rank, identity in enumerate(ranked, 1)
    )
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(
        (2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1)
    )
    return {
        "recall": recall,
        "mrr": mrr,
        "ndcg": dcg / idcg if idcg else 0,
        "empty_result": None,
    }


def evaluate(dataset, k=4, provider_embeddings=None):
    corpus = dataset["documents"]
    questions = dataset["queries"]
    if provider_embeddings is None:
        vectors = [fixture_vector(item["content"]) for item in corpus]
        query_vectors = [fixture_vector(item["query"]) for item in questions]
        dense_name = "dense_fixture_concept_cosine"
    else:
        # Explicit opt-in path only. Both query and corpus vectors use the same model.
        vectors = [
            dict(enumerate(vector))
            for vector in provider_embeddings.embed_documents(
                [item["content"] for item in corpus]
            )
        ]
        query_vectors = [
            dict(enumerate(provider_embeddings.embed_query(item["query"])))
            for item in questions
        ]
        dense_name = "dense_provider"
    rows = []
    for question, query_vector in zip(questions, query_vectors, strict=True):
        start = time.perf_counter()
        lexical = [
            identity
            for identity, _ in lexical_rank(question["query"], corpus, limit=20)
        ]
        lexical_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        scored = [
            (item["id"], cosine(query_vector, vector))
            for item, vector in zip(corpus, vectors, strict=True)
        ]
        # Zero similarity is the only fixture abstention rule. Provider cosine
        # scores are not calibrated; no-answer accuracy must not be overclaimed.
        dense = [
            identity
            for identity, score in sorted(scored, key=lambda item: (-item[1], item[0]))
            if score > 0
        ][:20]
        dense_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        hybrid = [
            identity
            for identity, _ in reciprocal_rank_fusion([lexical, dense], limit=20)
        ]
        fusion_ms = (time.perf_counter() - start) * 1000
        for method, ranking, elapsed in [
            ("bm25", lexical, lexical_ms),
            (dense_name, dense, dense_ms),
            ("hybrid_rrf", hybrid, lexical_ms + dense_ms + fusion_ms),
        ]:
            rows.append(
                {
                    "query_id": question["id"],
                    "split": question["split"],
                    "category": question["category"],
                    "method": method,
                    "ranked_ids": ranking[:k],
                    "relevance": question["relevance"],
                    "ranking_ms": elapsed,
                    **metrics(ranking, question["relevance"], k),
                }
            )
    aggregates = []
    for split in ["dev", "holdout"]:
        for method in ["bm25", dense_name, "hybrid_rrf"]:
            selected = [
                row for row in rows if row["split"] == split and row["method"] == method
            ]
            summary = {
                "split": split,
                "method": method,
                "queries": len(selected),
                "answerable": sum(row["recall"] is not None for row in selected),
                "unanswerable": sum(
                    row["empty_result"] is not None for row in selected
                ),
            }
            for key in ["recall", "mrr", "ndcg", "empty_result"]:
                values = [row[key] for row in selected if row[key] is not None]
                summary[key] = sum(values) / len(values) if values else None
            summary["mean_ranking_ms"] = sum(
                row["ranking_ms"] for row in selected
            ) / len(selected)
            aggregates.append(summary)
    return {"aggregates": aggregates, "queries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path(__file__).with_name("dataset.json")
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "evals/results/retrieval-fixture.json"
    )
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--mode", choices=["fixture", "provider"], default="fixture")
    parser.add_argument("--allow-paid-provider", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.k <= 20:
        parser.error("k must be between 1 and 20")
    if args.mode == "provider" and not args.allow_paid_provider:
        parser.error(
            "Provider mode may incur embedding API costs; --allow-paid-provider is required"
        )
    raw = args.dataset.read_bytes()
    dataset = json.loads(raw)
    embeddings = None
    model = None
    if args.mode == "provider":
        from app.core.config import settings
        from app.services.rag_service import create_embeddings

        embeddings = create_embeddings()
        model = settings.ZHIPUAI_EMBEDDING_MODEL
    report = {
        "scope": "retrieval ranking only; no generated answers, no fact verification",
        "mode": args.mode,
        "dense_provider_used": args.mode == "provider",
        "fixture_warning": "Toy concept-cosine dense substitute tests wiring only; this is not a real embedding/answer-quality benchmark."
        if embeddings is None
        else None,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "configuration": {
            "tokenizer": TOKENIZER_VERSION,
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "rrf_constant": 60,
            "candidate_limit": 20,
            "k": args.k,
            "embedding_model": model,
        },
        "timing_scope": "ranking_ms excludes tokenization/vector preparation, provider network time and generation; do not call it end-to-end latency",
        **evaluate(dataset, args.k, embeddings),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Retrieval ablation",
        "",
        f"Mode: `{args.mode}`; k={args.k}; dataset SHA256: `{report['dataset_sha256']}`.",
        "",
        report["fixture_warning"]
        or "Embedding provider used explicitly; no generated-answer evaluation.",
        "",
        "| Split | Method | Recall@k | MRR@k | nDCG@k | No-answer empty rate |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for summary in report["aggregates"]:
        values = [
            "—" if summary[key] is None else f"{summary[key]:.3f}"
            for key in ["recall", "mrr", "ndcg", "empty_result"]
        ]
        lines.append(
            "| " + " | ".join([summary["split"], summary["method"], *values]) + " |"
        )
    lines += [
        "",
        "Small synthetic corpus, manually labeled. Dev/holdout IDs do not overlap; no real-world generalization claim. No-answer empty rate is a retrieval diagnostic, not answer refusal accuracy. See JSON for per-query results, rankings and config.",
    ]
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "mode": args.mode,
                "aggregates": report["aggregates"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
