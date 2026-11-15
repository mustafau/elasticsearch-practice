# Elasticsearch design notes

The lab explores five search problems against a single local Elasticsearch node. Postman sends requests directly to Elasticsearch. Python handles resource setup, fixture loading and a client-side rank fusion example.

The small synthetic datasets make query behavior easy to inspect. They don't model production traffic or establish performance expectations. Each [scenario walkthrough](../README.md#scenarios) explains its requests and expected results.

## Data models

| Scenario | One document represents | Main mapping choices |
| --- | --- | --- |
| Address | A selectable place or address | `search_as_you_type`, keyword identifiers, `geo_point` |
| Ecommerce | A product with purchasable variants | Text fields, keyword facets, nested variants, integer prices |
| Logs | An event in an append-oriented stream | Date, text message, keyword dimensions, flattened labels |
| Documents | A document with a tenant and allowlist | Text title/body, keyword principals, dates |
| Hybrid | A passage with text and a vector | Text fields, indexed cosine vector, keyword tenant and principals |

Mappings use `dynamic: strict` so unexpected fields fail ingestion. Log labels use `flattened` to accommodate arbitrary label keys without adding a mapped field for each key. The local indices have one primary shard and no replicas.

The `scenarios/` directories contain setup definitions and source-only NDJSON fixtures. The matching folders in `postman/collections/elasticsearch-practice/` contain the requests. Python supplies bulk action lines and stable document IDs when loading fixtures.

## Address search

A place has a stable `place_id`, display fields, a representative coordinate, popularity and an array of search strings. The first search string is the display label; later strings are curated aliases such as `SFO`. Matching multiple aliases still returns one document.

`search_as_you_type` supports prefix matching at token boundaries. The query uses `multi_match` with `type: bool_prefix` against the root field and its shingle subfields. That lets `market` match a later token in `123 Market Street`; it doesn't make `arket` a substring match. Lowercasing and ASCII folding handle basic case and accent differences. They aren't a complete multilingual address strategy. [Search as you type](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/search-as-you-type.html)

Text matching and filtering have separate roles:

- Text determines candidate relevance.
- Country, city and place type use exact keyword filters.
- A geographic radius limits membership.
- Ranking decides the order of the remaining candidates.

The ranking variations compare text relevance, distance sorting and a balanced `function_score` query. Balanced scoring multiplies text relevance by a bounded combination of proximity and popularity. A hard radius doesn't imply distance sorting, and balanced scoring doesn't guarantee the closest result appears first. [Function score](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/query-dsl-function-score-query.html)

The typo experiment illustrates a limit of boolean prefix queries: fuzziness can correct an earlier completed token, such as `fery` in `fery b`, but doesn't apply to the final prefix. On the pinned version, trailing whitespace completes the last token: `ferry b ` no longer matches the prefix of Building. [Boolean prefix queries](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-match-bool-prefix-query)

This model searches known indexed records. Parsing arbitrary postal addresses, interpolating house numbers, reverse geocoding, routing and validating deliverability are separate problems.

## Ecommerce search

One product contains one to five nested variants. Each variant carries its SKU, color, size, price and stock quantity. Prices use integer cents.

All variant restrictions belong inside the same nested query. Otherwise a product could qualify because one variant is red while another is large and in stock. The manual object-mapping comparison demonstrates this cross-variant error. `inner_hits` shows the variants that actually qualified. [Nested fields](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/nested.html)

Nested price sorting repeats the variant restrictions. A cheap blue variant shouldn't determine the sort price of a product found through a red-only search.

Facet scope depends on filter placement. Putting a selected brand in the main query restricts both hits and aggregation buckets. Moving it to `post_filter` restricts hits while retaining alternative brands in the facet. A nested aggregation counts variant records; a `reverse_nested` aggregation returns to product counts.

Inventory is a search projection. Updating stock and refreshing the index changes search visibility, but checkout still needs authoritative inventory checks. Frequent variant updates also make the cost of rewriting nested product documents relevant.

## Log search

Logs use a data stream backed by an index template and an ingest pipeline. The pipeline requires event ID, service and message, validates timestamps without discarding their fractional seconds, and lowercases severity. Bulk item errors remain ingestion failures even when the overall HTTP response is 200.

Data streams accept bulk `create` actions. An event ID isn't globally unique across backing indices, so retrying after rollover needs more than an assumption that `_id` deduplicates the entire stream. The loader checks the canonical fixture IDs across the stream before loading: a complete matching set is a no-op, an absent set can be created, and a partial or changed set requires reset. This local preflight assumes no concurrent writes. [Data streams](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/set-up-a-data-stream.html)

The dashboard queries use a fixed January 1, 2026 time window. The payments/prod slice contains ten events, including three errors. A half-open range includes the start and excludes the end. Histogram bounds can add empty buckets, but don't replace the range filter.

Duration metrics ignore missing duration values, while the event count can still include those records. Percentiles are approximate. These details matter when defining dashboard denominators and comparing event metrics.

Rollover changes the write backing index while preserving the stream name. The manual exercise captures the new index from the response. Data-stream lifecycle sets seven-day retention; deletion is asynchronous and isn't an exact event-timestamp TTL. [Data-stream lifecycle](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/data-stream-lifecycle.html)

## Document search and permissions

A document is visible when its tenant matches and at least one entry in `allowed_principals` matches the user's effective principals. Empty or missing allowlists deny access. The owner field doesn't independently grant access.

The fixtures include user, group and tenant-wide principals. They deliberately reuse a group name across tenants so the queries exercise both parts of the access rule.

Permission restrictions stay in the main query. This scopes hits, highlights, totals and aggregations together. Using `post_filter` for access restrictions would leave aggregation results outside that restriction. ID resolution also uses an ACL-filtered search rather than a raw document lookup.

These requests demonstrate query construction with synthetic identities. The local connection has security disabled, so callers can change or remove the filters. Production enforcement requires a trusted application or configured Elasticsearch document-level security. Source filtering and filtered aliases don't establish that boundary. [Document-level security](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/document-level-security.html)

The update exercises explore two distinct concerns:

- Permission revocation changes fresh search results after refresh, while an existing point-in-time snapshot can retain the old visibility.
- External versioning rejects an out-of-order projection update. A source field named `revision` alone doesn't enforce update ordering.

## Hybrid retrieval

Hybrid passages have both text fields and indexed four-dimensional cosine vectors. The vectors are hand-authored fixtures marked `embedding_model: fixture-v1`. They illustrate retrieval mechanics for committed query/vector pairs; they aren't embeddings from a language model.

Lexical retrieval finds exact terms. Vector retrieval can find a passage with different wording when its fixture vector is close to the query vector. The combined request uses top-level lexical and kNN branches, whose candidates form a union. Each branch repeats tenant and principal filters because a restriction on one branch doesn't constrain the other. [kNN and hybrid search](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/knn-search.html)

BM25 and cosine-derived scores have different scales. Boosts change their relative influence but don't calibrate relevance. For cosine kNN, `_score` is `(1 + cosine) / 2`; the kNN `similarity` threshold uses raw cosine. The exact reference query uses `cosine + 1`, so its numeric scores use another scale.

Approximate retrieval is compared with an exact `script_score` search over the same authorized corpus. Increasing `num_candidates` trades more work for recall. It doesn't prove exactness, and tied vectors can produce different candidate membership.

### Reciprocal rank fusion

The Python demo and the ordered Postman requests fuse up to ten results from each branch. For each passage, they sum:

```text
1 / (60 + branch_rank)
```

Ranks start at one. A passage absent from a branch contributes zero for that branch. Each branch contributes once per passage, and passage ID breaks ties in the final order.

For the reset-password case, the exact match ranks first lexically and second by vector, giving `1/61 + 1/62`. The paraphrase appears only at vector rank one, giving `1/61`. The exact match therefore wins fusion even though the paraphrase wins vector-only retrieval.

Fusion can't recover a passage absent from both candidate windows. Multiple passages from the same parent remain separate results; grouping them would need an explicit parent scoring and permission policy.

A real embedding extension would use one model and preprocessing scheme for both documents and queries, record the model revision and dimensions, and populate a separate matching index.

## Pagination

Log and document exercises use point-in-time snapshots with `search_after`:

1. Open a PIT on the read alias or data stream.
2. Search through `/_search` with the PIT, preserving the query and sort.
3. Copy the complete final hit's sort array into the next request, including Elasticsearch's implicit PIT tie breaker.
4. Use the latest returned PIT ID and close it after paging.

The fixtures include tied visible sort values to exercise stable paging. A PIT provides snapshot consistency, which can also retain older permission visibility. [Point in time](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/point-in-time-api.html)

## Loading, reset and migration

Ordinary indices have separate read and write aliases. The loader writes through the write alias with `require_alias=true`, so a typo can't silently create a physical index. Batches contain at most 500 records or 5 MiB, and every bulk item is checked. `refresh=wait_for` makes the writes visible before subsequent searches.

Re-seeding ordinary indices overwrites canonical IDs while preserving added records. Reset deletes the scenario's exact owned resources and restores the fixtures. Log reset deletes the data stream before its template and pipeline.

The migration exercises create a target index with explicit mappings and settings, pause writes, reindex, check failures, compare counts and relevance, and switch both aliases in one request. Reindexing doesn't copy mappings or settings. The old index remains available for rollback while writes are paused. [Aliases API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/indices-aliases.html)

An atomic alias switch doesn't capture concurrent updates or deletions. Production migrations need replay or catch-up, delete propagation and a cutover policy. Rolling back after divergent writes needs reconciliation.

## Further experiments

| Scenario | Questions to explore |
| --- | --- |
| Address | How do language, proximity, popularity and common prefixes affect relevance? |
| Ecommerce | When should SKUs become separate documents? How fresh must inventory be? |
| Logs | What provides backpressure, durable replay and duplicate handling across rollover? |
| Documents | How quickly must ACL changes reach queries, caches, chunks and open PITs? |
| Hybrid | How do candidate windows, model versions and grouping affect retrieval quality? |

Use separate benchmark indices for larger datasets so they don't alter the controlled examples. Record data distribution, hardware, shards, heap, concurrency, errors and latency percentiles. Profiling helps explain execution but adds overhead. A single-node experiment doesn't establish distributed scalability.
