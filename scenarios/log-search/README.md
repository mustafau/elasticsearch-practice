# Centralized log search

Interview prompt: design append-oriented log ingestion, text/time/trace search, error dashboards and retention. Explain how at-least-once ingestion, rollover and paging affect correctness.

Resources are an [ingest pipeline](setup/ingest-pipeline.json), [data-stream template](setup/index-template.json), and `lab-log-search-events`. There are no ordinary read/write aliases. The strict mapping has flattened `labels` to limit field explosion. The pipeline validates event/service/message presence, checks ISO timestamps without rewriting their precision, and lowercases severity; malformed items fail ingestion.

```sh
make bootstrap SCENARIO=log-search
make seed SCENARIO=log-search
make postman-test SCENARIO=log-search
make status SCENARIO=log-search
```

The [122 events](data/events.ndjson) use a fixed January 1, 2026 window with one just-before boundary fixture. No request depends on today's clock. Open [log-search](../../postman/collections/elasticsearch-practice/log-search/) within the shared collection.

## Walkthrough and expected results

1. `core/timeout-search` restricts payments/prod to `[12:00,12:05)` and matches timeout text: exactly `pay-00`, `pay-01`, `pay-02`.
2. `known-window-dashboard` uses the same structured filters without the text clause: 10 events, three errors, error fraction 0.3. There are two events per minute for five minutes; the before-start and exact-end boundary events are excluded.
3. Duration average is 1517.5 ms; p50/p95/p99 are approximate percentiles, not asserted as exact floating-point constants. All baseline events have duration. If duration were missing, duration metrics would ignore it by default while the event-count denominator would still include that event.
4. `variations/empty-minute-bounds` extends the histogram through 12:06 UTC with empty buckets. Bounds extend displayed buckets; they do not replace a range filter.
5. `cross-service-trace` retains environment/time restrictions and uses an exact trace ID across payments, identity and dispatch. `service-breakdown` deliberately uses a broader service scope.
6. The core PIT sequence opens on the stream, fetches five pages of two events, then closes the latest PIT. Tied timestamps test the paging protocol. Requests preserve query/sort, copy the complete final-hit sort array (including Elasticsearch's implicit PIT value) and assert no duplicate or missing IDs across the ten-event snapshot. Page URLs use `/_search`, not a stream URL plus PIT.

The stream has lifecycle retention `7d`, configured via the data-stream lifecycle API and inspected under `internals`. No ILM policy is attached. Retention runs asynchronously and is not an exact event-timestamp TTL. Tests do not wait seven days or assume the old fixture timestamps immediately expire a newly created backing index.

## Seeding and explicit manual experiments

Bulk events use `create` and `_id=event_id`. Re-seeding first searches canonical IDs anywhere in the stream. All matching canonical sources mean no-op; none permits creation; partial, changed or duplicated canonical sets require reset. A 409 is not silently accepted as a successful full load. IDs are not globally unique across backing indices, and this quiescent-local preflight is not exactly-once delivery.

Reset before each independent manual sequence:

- `bulk-ingestion-failures` sends an invalid timestamp and invalid numeric duration. HTTP 200 contains two failed bulk items and `errors=true`; neither item should enter normal fixtures. `missing-required-field` uses pipeline simulation to expose validation failure.
- Run `rollover`, `append-after-rollover`, then `inspect-stream-after-rollover`. The new backing index is captured from the response rather than hardcoded; the appended event must land there while the stream name stays constant. Reseeding after a rollover still scans the whole stream for canonical IDs.

Reset deletes the stream first, then its owned template and pipeline, and recreates the baseline. A dead-letter design needs durable storage, replay, observability and ownership; an ingest `on_failure` handler is not automatically a durable queue.

## Interview exercises

- Explain why tiny daily indices can be costly, when rollover size/age is preferable, and how shard fan-out affects long-range searches.
- Define backpressure, retry and duplicate handling for at-least-once ingestion. What changes after rollover or with late-arriving events?
- Compare logs, event dashboards and metrics-oriented TSDS. What aggregation problems arise with high-cardinality trace IDs?
- Describe hot/warm storage, restore/replay and an asynchronous retention SLA. Which system is the durable source of truth?
