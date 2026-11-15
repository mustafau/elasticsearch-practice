# Address search

Interview prompt: design pickup and destination search for an Uber-like app. Return known places from partial text, optionally constrained by location. Explain candidate selection separately from relevance and distance ranking.

The [mapping](setup/index.json) indexes one selectable place per stable `place_id`, with `search_text` as `search_as_you_type`. Its first value is the display label; curated aliases follow without duplicates. Display fields remain keywords. Lowercase and basic ASCII folding preserve the original display text while allowing `cafe jose` to match `Café José`. The [42 fixtures](data/places.ndjson) use synthetic or approximate coordinates; they are not navigation data.

```sh
make bootstrap SCENARIO=address-search
make seed SCENARIO=address-search
make postman-test SCENARIO=address-search
make status SCENARIO=address-search
```

Open the [address requests](../../postman/collections/elasticsearch-practice/address-search/) in Postman. In the examples below, `q` refers to the `multi_match.query` value in the request body.

## Walkthrough and expected results

1. Run `core/ferry-prefix`: `ferry b` finds `sf-ferry-building` inside the explicit 25 km radius. Resolve the selected ID with `resolve-place`; a missing ID yields zero hits.
2. Run `market-radius`: local Market places survive; LA and New York do not. Across-the-bay Oakland is not automatically outside the radius. `house-number-city-type` narrows `123 mar` to the San Francisco address.
3. Compare `later-token` with `not-substring`: `market` finds the later token in `123 Market Street`, while `arket` is not arbitrary substring completion.
4. Try `airport-alias`, `accent-folding`, `mixed-case-whitespace` and `curated-turkish-alias`. SFO returns one document despite multiple aliases. The Istanbul case demonstrates curated Latin aliases, not full Turkish normalization. Leading and repeated internal spaces preserve the prefix match. Compare `trailing-space-completes-token`: on the pinned version, `ferry b ` treats `b` as a complete token and returns no Ferry Building hit. Applications that want prefix behavior after trailing spaces need to trim input before constructing the query.
5. Inspect `no-location`: there is no geo filter, function or sort. Compare `market-radius` with `larger-radius`; widening is explicit. Contradictory filters return no hits and are never relaxed. `literal-special-text` passes `* OR *` as analyzed text rather than query syntax.
6. Compare the three `ranking-*` requests using `ranking test p`. Identical text isolates location: balanced and distance place near before far; text uses ID ordering for equal scores. Distance sorts by meters first; balanced multiplies text scores by bounded signals and does not promise globally nearest first.
7. Compare `popularity-*`: text scores tie, while balanced scoring favors the high-popularity record. `stable-tie` uses place ID to order equal scores, putting `sf-tie-a` before `sf-tie-b`.
8. Compare `completed-token-fuzzy` and `completed-token-no-fuzzy`: `fery b` is rescued only by completed-token fuzziness. `final-token-not-fuzzy` shows `ferry builx` is not rescued.

All three ranking modes keep restrictions in `bool.filter`. Balanced uses the multiplier `min(5, 1 + 3*geoDecay + 0.2*log10(1+popularity))`; `log1p` is base ten in Elasticsearch. The 5 km decay scale is independent of the hard radius. Curated aliases can contribute terms across array values, so this baseline does not promise phrase semantics confined to one alias.

## Inspect and mutate

Use [internals](../../postman/collections/elasticsearch-practice/address-search/internals/) to analyze both the custom analyzer and generated prefix field, explain Ferry Building, profile retrieval, and inspect mappings/settings/storage. The prefix analyzer produces edge prefixes; profile is a diagnostic cost and belongs outside latency measurement.

Select `manual/invalid-latitude` explicitly: latitude 100 should be rejected with HTTP 400. It is an expected-error exercise, not an automated baseline search.

For migration, reset first and pause writes. Run `migration-create-target`, `migration-reindex`, `migration-target-count`, `migration-target-relevance`, `migration-atomic-cutover`, then `migration-verify-aliases` in order. The target has explicit mappings/settings. Reindex failures, counts and target relevance are checked before one atomic alias switch. The old index remains for rollback while writes remain paused. Concurrent updates/deletes need replay and a cutover protocol; atomic aliases alone do not solve freshness. Reset removes both owned generations and restores v1.

## Interview exercises

- Explain why a geo filter does not inherently select shards, and why a small `size` does not bound all retrieval work.
- Change the radius and decay scale independently. Which documents become candidates, and which merely change score?
- Explain how language-specific analysis, transliteration and address conventions would extend ASCII folding. Why is blanket `St → Street` unsafe?
- Describe a source-of-truth place feed, duplicate-ID handling, popularity freshness, delete propagation and rebuilding the search projection.
- Contrast this known-place lookup with postal parsing, interpolation, reverse geocoding, deliverability and routing. None is implemented here.
- Design a measured large-data experiment with separate benchmark indices, fixed workload distribution, warmup, hardware, shard counts, errors and latency percentiles. These small fixtures establish semantics only.
