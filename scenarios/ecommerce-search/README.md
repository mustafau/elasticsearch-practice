# Ecommerce search

Interview prompt: design product search with brand/category facets and purchasable color/size/stock/price filters. Explain what a product hit and a matching SKU count each represent.

One [document](data/products.ndjson) represents a product; its one to five purchasable variants use the [nested mapping](setup/index.json). Prices are integer USD cents. The search inventory is a projection: checkout still consults authoritative inventory.

```sh
make bootstrap SCENARIO=ecommerce-search
make seed SCENARIO=ecommerce-search
make postman-test SCENARIO=ecommerce-search
```

Open [ecommerce-search](../../postman/collections/elasticsearch-practice/ecommerce-search/) in the shared collection.

## Walkthrough and expected results

1. `core/nested-variant-filter` searches `running shoe` with red, L, in-stock, at most 10000 cents in the same nested query. Only `shoe-valid-red-l` and `shoe-other-brand` qualify. Cross-variant, sold-out, expensive and wrong-category anchors do not. Inspect `inner_hits.matching_variants` to identify the actual qualifying SKUs.
2. `selected-brand-query-facet` puts Acme in the main query: hits and brand buckets contain only Acme. `disjunctive-brand-facet` puts the selection in `post_filter`: hits contain Acme, while brand buckets show Acme and Stride. Text/category/variant restrictions remain in the main query. This is explicitly disjunctive for brand, not every facet.
3. `matching-variant-price-sort` uses nested sort with the same child conditions as retrieval. The matching prices are 8000 then 9000; the Acme product's cheap blue/S variant at 1000 must not determine its red/L sort value.
4. `sku-versus-product-counts` enters the nested context and filters qualifying children: three matching SKUs belong to two matching products. `reverse_nested` counts parents again. Ordinary brand/category facets count products, not SKUs.
5. `exact-product-id` demonstrates a keyword lookup. Compare its term query with analyzed title matching. Use analyzer, explain and profile requests to inspect the difference.

Separate nested clauses can match different children; all child restrictions must be inside one nested query. Returning one product per document also avoids having to group SKU hits, at the cost of rewriting nested product projections when inventory changes.

## Explicit manual experiments

Start each independent sequence from `make reset SCENARIO=ecommerce-search`.

- Object failure: run `object-demo-create`, `object-demo-seed`, then `object-cross-variant-failure`. The isolated object-mapped index returns the cross-variant product even though no red/L SKU exists. This is an intentional false positive. The normal nested index is unaffected.
- Inventory freshness: run `inventory-update`, then `inventory-search-after-update`. Updating the red/L stock to zero with `refresh=wait_for` removes the Acme hit. This is not a transaction with checkout.
- Migration: pause writes; run `migration-create-target`, `migration-reindex`, `migration-target-count`, `migration-target-relevance`, `migration-atomic-cutover`, `migration-verify-aliases`. Count/relevance checks precede the atomic alias move. Keep the old generation while evaluating rollback; divergent writes require reconciliation.

Reset removes the exact owned demo/migration indices and restores the baseline. Do not run the entire manual folder as a single sequence: inventory mutation deliberately changes migration relevance expectations.

## Interview exercises

- Explain nested update and storage costs. When would one document per SKU be preferable? How would grouping and product facets work then?
- Add another red/L variant and predict product counts, SKU counts, inner hits and price sorting separately.
- Move brand selection between query and post-filter and explain the change in aggregation scope.
- Design stock-event ordering, source replay, product deletion propagation and a projection rebuild. Where must overselling be prevented?
- Distinguish Lucene physical document statistics (which include nested children) from Elasticsearch product hits.
