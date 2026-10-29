# Retrieval ablation

Mode: `fixture`; k=4; dataset SHA256: `a85282ceb45129339850abb39e18af517600424ab2f80413c290eef361e9a1f6`.

Toy concept-cosine dense substitute tests wiring only; this is not a real embedding/answer-quality benchmark.

| Split | Method | Recall@k | MRR@k | nDCG@k | No-answer empty rate |
|---|---|---:|---:|---:|---:|
| dev | bm25 | 0.857 | 0.929 | 0.904 | 1.000 |
| dev | dense_fixture_concept_cosine | 0.857 | 0.821 | 0.816 | 1.000 |
| dev | hybrid_rrf | 1.000 | 1.000 | 0.997 | 1.000 |
| holdout | bm25 | 0.833 | 0.889 | 0.870 | 1.000 |
| holdout | dense_fixture_concept_cosine | 1.000 | 0.944 | 0.959 | 1.000 |
| holdout | hybrid_rrf | 1.000 | 0.944 | 0.955 | 1.000 |

Small synthetic corpus, manually labeled. Dev/holdout IDs do not overlap; no real-world generalization claim. No-answer empty rate is a retrieval diagnostic, not answer refusal accuracy. See JSON for per-query results, rankings and config.
