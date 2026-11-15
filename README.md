# Elasticsearch Practice Lab

Five hands-on exercises for learning Elasticsearch through system design problems. Run Elasticsearch locally with Docker, load the sample data, and explore queries in Postman. Each exercise includes mappings, synthetic data, requests with assertions, and a walkthrough explaining the tradeoffs.

## Scenarios

| Scenario | What you'll practice |
| --- | --- |
| [Address search](scenarios/address-search/README.md) | Autocomplete, aliases, geographic filters, distance and relevance ranking |
| [Ecommerce search](scenarios/ecommerce-search/README.md) | Nested product variants, facets, price sorting and inventory updates |
| [Log search](scenarios/log-search/README.md) | Ingest pipelines, data streams, dashboards, pagination and retention |
| [Document search](scenarios/document-search/README.md) | Tenant and permission filters, highlighting, pagination and permission changes |
| [Hybrid search](scenarios/hybrid-search/README.md) | Lexical and vector retrieval, score weighting and reciprocal rank fusion |

Start with address search and work down the table. The [design notes](docs/elasticsearch-practice-lab.md) provide more detail on the data models and query choices.

## Getting started

You'll need Python 3.11+, Docker with Compose v2, and Postman desktop v12 or later. The collection uses Postman's native v3 YAML format. For command-line runs, install the [Postman CLI](https://learning.postman.com/docs/postman-cli/postman-cli-installation/) with native YAML support; Newman doesn't support this format.

Elasticsearch and optional Kibana are pinned to **9.5.6**. Elasticsearch has a 2 GiB container memory limit, so allow additional Docker memory for overhead and Kibana if enabled. The first start after pulling a new image can take several minutes on Docker Desktop.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
make up
make setup-all
```

In Postman, open this repository using **Files → Open Folder**, switch to **Local View**, and expand **Elasticsearch Practice Lab** in the **Collections** sidebar. Open a scenario's walkthrough and run its `core` requests first.

Elasticsearch is available at `http://localhost:9200`. Security is disabled for local practice, and ports are bound to loopback. To start optional Kibana:

```sh
docker compose --profile kibana up -d
```

Then open `http://localhost:5601`.

## Working through an exercise

Each scenario groups requests into:

- **core** — the main search behavior and expected results.
- **variations** — changes to filters, ranking and edge cases.
- **internals** — analyzers, explanations, profiling, mappings and statistics.
- **manual** — updates, failures and migrations that change the sample data.

Read the query and its assertions together. Change one parameter, predict the result, then run it. Use `_analyze`, `_explain` and profiling to understand what happened.

Run manual sequences individually, following the scenario walkthrough. Reset before each independent experiment and after finishing; the whole manual folder isn't a single runnable suite.

## Commands

Run these from the repository root with your virtual environment active.

| Command | Purpose |
| --- | --- |
| `make up` | Start Elasticsearch and wait for it to become ready. |
| `make setup-all` | Create all five scenarios and load their fixtures. |
| `make postman-test` | Run every scenario's core, variations and internals requests. |
| `make postman-test SCENARIO=address-search` | Run one scenario's automated requests. |
| `make reset SCENARIO=address-search` | Delete that scenario's resources and restore its sample data. |
| `make status SCENARIO=log-search` | Inspect a scenario's resources and counts. |
| `make hybrid-demo` | Run lexical and vector searches and print fused ranks. |
| `make check tooling-test postman-lint` | Validate fixtures and collection files, and run Python tests. |
| `make test` | Reset all five scenarios and run the full integration checks. **Deletes existing lab experiments.** |
| `make down` | Stop services, preserving the Docker volume. |

To set up one scenario, use `make bootstrap SCENARIO=address-search` followed by `make seed SCENARIO=address-search`. Re-seeding restores canonical records in ordinary indices while preserving added records. Log re-seeding requires unchanged, complete fixtures; reset the log scenario if those fixtures have changed.

To use another endpoint, export `ES_URL` for Python and Make commands, and set the collection's `esUrl` variable for desktop requests:

```sh
export ES_URL=http://localhost:9201
```

## Scope and limitations

This is a local learning lab. Document and hybrid requests demonstrate permission filters using synthetic identities; callers can edit those filters. A production system needs trusted identity and authorization enforcement.

Hybrid search uses hand-authored four-dimensional vectors to illustrate retrieval mechanics. No embedding model, paid endpoint or trial license is required. These fixtures don't demonstrate semantic search quality on real text.
