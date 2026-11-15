# Hybrid lexical and vector search

Interview prompt: retrieve exact terms and related concepts, compare lexical and vector candidate sets, and fuse ranks without a paid retrieval feature or inference service.

The [20 passages](data/passages.ndjson) use indexed four-dimensional cosine vectors in the [mapping](setup/index.json). They are hand-authored **illustrative mechanics fixtures**, marked `embedding_model=fixture-v1`. They do not show arbitrary semantic understanding. Every document/query vector must be finite, nonzero, four-dimensional and from this same declared vector space.

```sh
make bootstrap SCENARIO=hybrid-search
make seed SCENARIO=hybrid-search
make postman-test SCENARIO=hybrid-search
make hybrid-demo
```

Open [hybrid-search](../../postman/collections/elasticsearch-practice/hybrid-search/) in Postman. Run core before variations so the comparison has both branches' results. `make hybrid-demo` runs the same lexical and vector queries and prints the fused ranking.

## Walkthrough and expected results

1. `core/lexical` searches `reset password` and returns `hybrid-exact`, excluding `hybrid-paraphrase`. It clears prior hybrid run variables and stores its branch IDs.
2. `core/vector` uses committed `[1,0,0,0]`: `hybrid-paraphrase` ranks first at cosine 1, above `hybrid-exact` at cosine 0.6. It stores vector IDs and computes JavaScript RRF against the preceding lexical result.
3. Both branches exclude `hybrid-private` and `hybrid-other-tenant`. The combined request repeats tenant and allowlist filters in the lexical query **and** the kNN prefilter. Either branch can admit a hit: lexical filters do not constrain top-level kNN, and kNN filters do not constrain lexical retrieval. Do not filter unauthorized candidates only after retrieval.
4. `combined-weighted` combines lexical and vector scores with a union. Both the exact phrase and paraphrase appear. `vector-dominant-boost` makes the vector contribution dominate and puts paraphrase above exact. BM25 and cosine-derived scores have different scales; weights are teaching parameters, not calibrated relevance.
5. `exact-cosine-reference` runs `script_score` over the same prefiltered corpus. Compare the controlled first two IDs and log top-10 overlap. Remaining vectors tie at cosine zero; approximate tie membership need not match exact ID sorting. More `num_candidates` trades work for recall and does not prove exactness.
6. `neighbors-without-threshold` exposes unrelated zero-cosine neighbors. `raw-cosine-threshold` sets kNN `similarity=0.8`, retaining only the paraphrase. The threshold is raw cosine; Elasticsearch's cosine `_score` is `(1+cosine)/2`. The exact script uses `cosine+1` for nonnegative scoring, so its numeric scores use another scale.
7. The warehouse/release variations use the other [committed query/vector pairs](data/query-cases.json). Warehouse has a unique best anchor; the release case deliberately has tied note vectors. Never invent a fixture embedding for an unseen input string.

## Rank fusion

Both the CLI and ordered Postman sequence fetch at most ten authorized passages per branch. For each passage, sum `1/(60+rank)` using ranks starting at one. Missing IDs contribute zero; each branch contributes once per ID. Sort by fused score descending and passage ID ascending for ties. Print branch ranks with fused scores.

For the reset-password case, exact ranks lexical 1 and vector 2, yielding `1/61 + 1/62`; paraphrase has only vector rank 1, yielding `1/61`. Exact therefore wins RRF even though paraphrase wins vector-only search. Tooling tests verify arithmetic, missing candidates, deduplication and ties. This is client-side arithmetic under Basic, not Elasticsearch's licensed server-side RRF.

Candidate windows matter: fusion cannot recover an ID absent from both branch lists. Separate passages with the same parent remain separate candidates. A parent-document extension must explicitly define grouping, scoring and permission semantics.

## Explicit manual experiments

`malformed-dimensions` and `zero-vector` expect HTTP 400 indexing failures. Python validates those conditions before normal fixture writes. These expected errors live outside automated retrieval groups.

For migration, reset first and pause writes, then run the six `migration-*` requests in order. Target mapping/settings are explicit; counts/relevance must pass before switching both aliases atomically. Keep the old index for rollback while writes remain paused. Changing vector dimensions or model version requires a compatible separate index and re-embedding, not reusing unrelated vector spaces.

## Interview exercises

- Contrast exact identifiers, paraphrases and related concepts. How would labeled relevance cases guide candidate-window and weight choices?
- Discuss approximate recall, prefilter versus post-filter, HNSW indexing/memory cost and filtered retrieval latency.
- Define chunking and parent-level grouping without losing ACL propagation or deletion correctness.
- Plan a real offline semantic extension with one pinned local model/revision, separate matching index, batch generation and identical document/query preprocessing. Keep this deterministic baseline independent of model downloads.
- Explain model migration, score calibration versus RRF, and why toy vectors are not semantic-quality evidence.
