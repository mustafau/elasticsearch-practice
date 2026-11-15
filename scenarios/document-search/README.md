# Permission-aware document search

Interview prompt: search a multi-tenant knowledge base while keeping hits, highlights, totals, facets, resolution and pagination inside the same authorization scope.

The [24 source documents](data/documents.ndjson) use the [strict mapping](setup/index.json). [principals.json](data/principals.json) defines fixture-selected Alice (Acme engineering), Bob (Acme sales) and Carol (Globex engineering). A document is visible only when its tenant matches **and** at least one allowlist entry matches an effective principal. Empty or absent allowlists deny access. Ownership grants nothing by itself.

This direct, unauthenticated local connection simulates ACL query construction. Callers can alter filters. Production enforcement requires trusted identity and query construction or configured Elasticsearch document-level security. Source filtering and filtered aliases do not establish that boundary.

```sh
make bootstrap SCENARIO=document-search
make seed SCENARIO=document-search
make postman-test SCENARIO=document-search
```

Open [document-search](../../postman/collections/elasticsearch-practice/document-search/) in Postman. Each request includes its tenant and principal filters explicitly.

## Walkthrough and expected results

The core matrix uses `deployment guide`, which occurs identically in all five anchors:

| Principal | Visible IDs | Total and guide bucket count |
| --- | --- | --- |
| Alice | `acme-alice`, `acme-all`, `acme-eng` | 3 |
| Bob | `acme-all` | 1 |
| Carol | `globex-eng` | 1 |

The `acme-empty` anchor never appears. The group token `group:engineering` deliberately exists in both tenants; the tenant filter prevents leakage. All restrictions stay in the main query so the matrix also bounds highlights and aggregation buckets. Access restrictions in `post_filter` would leave aggregations exposed.

1. Run the three principal matrix requests; inspect hits, highlight snippets, exact totals and type buckets.
2. Run authorized, private, cross-tenant and missing resolution requests. IDs are resolved inside an ACL-filtered search, never an unfiltered raw `_doc/{id}` call.
3. Compare `title-boosted` and `title-unboosted` on two controlled rollout-handbook documents with query terms moved between title and body. Title boost must prefer the title match. Unboosted scores are printed, not assumed numerically equal: per-field corpus statistics and length normalization can differ.
4. Try phrase matching, type/date filters and the no-result case. Every variant retains tenant and principal restrictions; empty results also have empty type buckets.
5. Run the ordered core PIT open/two pages/close sequence. Page size two and tied scores exercise stable `search_after`; the union must contain exactly Alice's three anchors without duplicates. Retain the latest PIT ID and the complete final sort array.
6. Use analyzer, authorized explain and profile requests to inspect how text retrieval interacts with filters. `_source` omits ACL fields only for response shaping, not enforcement.

## Explicit mutation sequences

Reset with `make reset SCENARIO=document-search` before each independent sequence.

Permission revocation: run `revocation-open-pit`, `revoke-alice`, `revocation-fresh-search`, `revocation-old-snapshot`, then `revocation-close-pit`. A fresh search after `refresh=wait_for` returns only `acme-all` and `acme-eng`; the older PIT can still return `acme-alice`. Snapshot consistency does not promise immediate permission revocation. Group-membership changes and ACL projection lag need their own freshness policy.

Version ordering: from a fresh reset, run `versioned-projection-update`, then `reject-out-of-order-update`. An external source version 10 succeeds; stale version 9 must fail with HTTP 409. Repeating version 10 also fails under strict `external` semantics. A source `revision` field alone does not enforce event ordering.

Migration: from a fresh reset and with writes paused, run the six `migration-*` requests in native order. Explicit target mappings, reindex failure checks, target counts and target relevance precede the atomic read/write alias move. Retain the old index for rollback while writes remain paused; reconcile divergent updates and deletions before any later rollback.

## Interview exercises

- Compare shared and per-tenant indices. What source owns ACLs, and what freshness does revocation require?
- Explain the allowlist OR rule, tenant isolation and why an owner field or hidden ACL does not automatically grant/enforce access.
- Design propagation of group membership and document ACL changes, including invalidating cached queries and older PIT sessions.
- If a document is chunked, explain how ACL changes reach every passage and how stale chunks are removed.
- Discuss filter caching, high-cardinality principals and why throughput measurements need realistic tenant/principal distributions.
