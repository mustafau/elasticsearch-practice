#!/usr/bin/env python3
"""Local resource lifecycle, validation, Postman selection and client-side RRF."""

import argparse
from collections import Counter
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlsplit

from http_client import Client, LabError, bulk_complete

ROOT = Path(__file__).resolve().parents[1]
COLLECTION_REL = Path("postman/collections/elasticsearch-practice")
SLUGS = ("address-search", "ecommerce-search", "log-search", "document-search", "hybrid-search")
MIN_COUNTS = dict(zip(SLUGS, (40, 30, 120, 24, 20)))
ID_FIELDS = dict(zip(SLUGS, ("place_id", "product_id", "event_id", "document_id", "passage_id")))
GROUPS = ("core", "variations", "internals")
RRF_CONSTANT = 60


def require(condition, reason):
    if not condition:
        raise LabError(reason)


def local_path(value, parent=None):
    parent = ROOT if parent is None else parent
    require(isinstance(value, str), "path must be text")
    result = (parent / value).resolve()
    require(result.is_relative_to(parent.resolve()), f"path escapes {parent}: {value}")
    require(result.exists(), f"missing local path: {result}")
    return result


def read_json(path):
    return json.loads(path.read_text(), parse_constant=lambda s: (_ for _ in ()).throw(LabError(f"nonfinite JSON: {s}")))


def read_yaml(path):
    try:
        import yaml
    except ImportError as exc:
        raise LabError("Install requirements: python3 -m pip install -r requirements.txt") from exc
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise LabError(f"invalid native YAML {path}: {exc}") from exc


def load_manifest(slug):
    require(slug in SLUGS, f"scenario outside checked-in allowlist: {slug}")
    base = ROOT / "scenarios" / slug
    m = read_json(local_path("scenario.json", base))
    prefix = "lab-" + slug
    require(m["slug"] == slug and m["id_field"] == ID_FIELDS[slug], f"invalid identity: {slug}")
    require(m["postman_folder"] == f"{COLLECTION_REL.as_posix()}/{slug}", "invalid Postman folder")
    local_path(m["postman_folder"])
    require(m["expected_count"] >= MIN_COUNTS[slug], f"insufficient fixture count: {slug}")
    require(m["seed_mode"] == ("create" if slug == "log-search" else "index"), "invalid seed mode")
    if slug == "log-search":
        expected = [("pipeline", prefix + "-ingest"), ("template", prefix + "-template"),
                    ("stream", prefix + "-events"), ("lifecycle", prefix + "-events")]
        require(m["target"] == prefix + "-events", "invalid stream target")
        require(m["manual_indices"] == [], "unexpected log indices")
    else:
        expected = [("index", prefix + "-v1-000001"), ("aliases", prefix)]
        require(m["target"] == prefix + "-write" and m["read_target"] == prefix + "-read", "invalid aliases")
        allowed_manual = [prefix + "-v2-000001"]
        if slug == "ecommerce-search":
            allowed_manual.append(prefix + "-object-demo")
        require(m["manual_indices"] == allowed_manual, "invalid manual resource allowlist")
    require([(r["kind"], r["name"]) for r in m["resources"]] == expected, "invalid owned resources/order")
    for r in m["resources"]:
        files = {"index": "index", "aliases": "aliases", "pipeline": "ingest-pipeline",
                 "template": "index-template", "stream": "data-stream", "lifecycle": "lifecycle"}
        require(r["body"] == f"setup/{files[r['kind']]}.json", "unexpected setup path")
        body = read_json(local_path(r["body"], base))
        if r["kind"] == "aliases":
            expected_aliases = {"actions": [
                {"add": {"index": prefix + "-v1-000001", "alias": prefix + "-read"}},
                {"add": {"index": prefix + "-v1-000001", "alias": prefix + "-write", "is_write_index": True}}]}
            require(body == expected_aliases, "alias actions outside exact owned topology")
        elif r["kind"] == "index":
            require(set(body) == {"settings", "mappings"}, "index setup must not create additional resources")
            require(body["mappings"]["dynamic"] == "strict", "mapping must be strict")
            require(body["settings"]["number_of_shards"] == 1 and body["settings"]["number_of_replicas"] == 0, "expected one primary and zero replicas")
        elif r["kind"] == "template":
            require(body["index_patterns"] == [prefix + "-events*"] and body["data_stream"] == {}, "template pattern outside owned stream")
            require(body["template"]["mappings"]["dynamic"] == "strict", "mapping must be strict")
            require(body["template"]["settings"]["index.default_pipeline"] == prefix + "-ingest", "unexpected log pipeline")
        elif r["kind"] == "pipeline":
            require(all(set(p) <= {"fail", "date", "lowercase"} for p in body["processors"]), "unexpected ingest operation")
        elif r["kind"] == "stream":
            require(body == {}, "stream setup must be empty")
        elif r["kind"] == "lifecycle":
            require(body == {"data_retention": "7d"}, "unexpected lifecycle")
    require(m["fixtures"].startswith("data/"), "fixture path must live in data/")
    local_path(m["fixtures"], base)
    return m


def utc_date(value):
    require(isinstance(value, str) and value.endswith("Z"), f"expected UTC date: {value}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LabError(f"invalid date: {value}") from exc


def vector(value):
    require(isinstance(value, list) and len(value) == 4, "vector requires four dimensions")
    require(all(type(x) in (int, float) and math.isfinite(x) for x in value), "vector values must be finite numbers")
    require(sum(x*x for x in value) > 0, "zero vector is invalid for cosine")


def validate_fields(doc, mapping, context):
    require(isinstance(doc, dict), f"{context}: expected object")
    props = mapping["properties"]
    require(not set(doc) - set(props), f"{context}: unknown fields: {set(doc)-set(props)}")
    for key, value in doc.items():
        if value is None:
            continue
        kind = props[key]["type"]
        values = value if isinstance(value, list) and kind not in ("nested", "dense_vector") else [value]
        for entry in values:
            if kind in ("keyword", "text", "search_as_you_type"):
                require(isinstance(entry, str), f"{context}.{key}: expected text")
            elif kind in ("integer", "long"):
                require(type(entry) is int, f"{context}.{key}: expected integer")
            elif kind in ("float", "double"):
                require(type(entry) in (int, float) and math.isfinite(entry), f"{context}.{key}: expected finite number")
            elif kind == "date":
                utc_date(entry)
            elif kind == "geo_point":
                require(isinstance(entry, dict) and set(entry) == {"lat", "lon"}, "use named lat/lon")
                require(all(type(n) in (int, float) and math.isfinite(n) for n in entry.values()), "invalid coordinate type")
                require(-90 <= entry["lat"] <= 90 and -180 <= entry["lon"] <= 180, "coordinate out of range")
            elif kind == "dense_vector":
                vector(entry)
            elif kind == "nested":
                require(isinstance(entry, list) and 1 <= len(entry) <= 5, "expected 1–5 variants")
                skus = []
                for child in entry:
                    require(set(child) == set(props[key]["properties"]), "variant fields missing")
                    validate_fields(child, props[key], context + "." + key)
                    require(child["price_cents"] >= 0 and child["stock_qty"] >= 0, "negative price/stock")
                    skus.append(child["sku"])
                require(len(skus) == len(set(skus)), "duplicate SKU")
            elif kind == "flattened":
                require(isinstance(entry, dict), "labels must be an object")


def fixtures(m):
    base = ROOT / "scenarios" / m["slug"]
    docs = [json.loads(line) for line in local_path(m["fixtures"], base).read_text().splitlines() if line.strip()]
    setup = read_json(local_path(m["resources"][1 if m["slug"] == "log-search" else 0]["body"], base))
    mapping = setup["template"]["mappings"] if m["slug"] == "log-search" else setup["mappings"]
    ids = []
    for doc in docs:
        require(isinstance(doc, dict), "fixture lines must be source objects")
        require(isinstance(doc.get(m["id_field"]), str) and doc[m["id_field"]], "missing/nontext ID")
        ids.append(doc[m["id_field"]])
        validate_fields(doc, mapping, ids[-1])
        required = set(mapping["properties"]) - ({"allowed_principals"} if m["slug"] == "document-search" else set())
        require(required <= doc.keys(), f"{ids[-1]}: missing required fields {required-doc.keys()}")
        require(all(v is not None or k in ("name", "postal_code") for k,v in doc.items()), "unexpected null")
        if m["slug"] == "address-search":
            texts = doc["search_text"]
            require(isinstance(texts, list) and texts and texts[0] == doc["label"] and len(texts) == len(set(texts)), "invalid search_text")
            require(all(isinstance(t, str) and t.strip() for t in texts), "empty search string")
            require(doc["place_type"] in ("address", "airport", "station", "landmark", "business"), "invalid place type")
            require(bool(re.fullmatch("[A-Z]{2}", doc["country_code"])) and doc["city_id"], "invalid country/city")
            require(0 <= doc["popularity"] <= 1000000, "invalid popularity")
        if m["slug"] in ("document-search", "hybrid-search"):
            require(isinstance(doc.get("allowed_principals", []), list), "allowlist must be an array")
        if m["slug"] == "hybrid-search":
            require(doc["embedding_model"] == "fixture-v1", "wrong vector space")
    require(len(ids) == len(set(ids)) == m["expected_count"], f"fixture count/duplicate ID mismatch: {m['slug']}")
    return docs


def settings_match(actual, expected):
    """Elasticsearch normalizes numeric settings to strings and nests under index."""
    def normalize(value):
        if isinstance(value, dict):
            # GET settings/templates expand index.* keys and normalize values to strings.
            expanded = {}
            for key, entry in value.items():
                node = expanded
                parts = key.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = normalize(entry)
            if set(expanded) == {"index"} and isinstance(expanded["index"], dict):
                return expanded["index"]
            if isinstance(expanded.get("index"), dict):
                index_settings = expanded.pop("index")
                expanded.update(index_settings)
            return expanded
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return str(value).lower() if isinstance(value, bool) else str(value)
    def contains(a, e):
        if isinstance(e, dict):
            return isinstance(a, dict) and all(k in a and contains(a[k], v) for k,v in e.items())
        return a == e
    return contains(normalize(actual), normalize(expected))


def setup_body(m, resource):
    return read_json(local_path(resource["body"], ROOT / "scenarios" / m["slug"]))


def bootstrap(client, m):
    for resource in m["resources"]:
        kind, name = resource["kind"], resource["name"]
        body = setup_body(m, resource)
        if kind == "index":
            old = client.request("GET", "/" + name, missing_ok=True)
            if old is None:
                client.request("PUT", "/" + name, body)
            else:
                state = old[name]
                require(settings_match(state["mappings"], body["mappings"]), f"incompatible mapping: {name}; reset required")
                # Additional application fields can alter semantics: reject extra top-level properties.
                require(set(state["mappings"]["properties"]) == set(body["mappings"]["properties"]), f"extra fields: {name}")
                require(settings_match(state["settings"]["index"], body["settings"]), f"incompatible settings: {name}; reset required")
        elif kind == "aliases":
            missing = False
            for action in body["actions"]:
                target = action["add"]
                old = client.request("GET", "/_alias/" + target["alias"], missing_ok=True)
                if old is not None:
                    require(set(old) == {target["index"]}, f"alias topology differs: {target['alias']}; inspect migration")
                    expected = {k:v for k,v in target.items() if k not in ("index", "alias")}
                    require(old[target["index"]]["aliases"][target["alias"]] == expected, "alias attributes differ")
                else:
                    missing = True
            if missing:
                client.request("POST", "/_aliases", body)
        elif kind in ("pipeline", "template"):
            path = ("/_ingest/pipeline/" if kind == "pipeline" else "/_index_template/") + name
            old = client.request("GET", path, missing_ok=True)
            if old is None:
                client.request("PUT", path, body)
            else:
                current = old[name] if kind == "pipeline" else old["index_templates"][0]["index_template"]
                require(settings_match(current, body), f"incompatible {kind}: {name}; reset required")
        elif kind == "stream":
            if client.request("GET", "/_data_stream/" + name, missing_ok=True) is None:
                client.request("PUT", "/_data_stream/" + name)
        elif kind == "lifecycle":
            path = "/_data_stream/" + name + "/_lifecycle"
            old = client.request("GET", path)
            current = old["data_streams"][0].get("lifecycle", {})
            if not current.get("data_retention"):
                client.request("PUT", path, body)
            else:
                require(settings_match(current, body), "incompatible lifecycle; reset required")
    print(f"bootstrap {m['slug']}: compatible")


def batches(docs, target, id_field, mode):
    batch, size = [], 0
    for doc in docs:
        action = json.dumps({mode: {"_index": target, "_id": doc[id_field]}}, separators=(",", ":"))
        text = action + "\n" + json.dumps(doc, allow_nan=False, separators=(",", ":")) + "\n"
        length = len(text.encode())
        require(length <= 5*1024*1024, "single document exceeds bulk budget")
        if batch and (len(batch) == 500 or size + length > 5*1024*1024):
            yield "".join(batch), len(batch)
            batch, size = [], 0
        batch.append(text)
        size += length
    if batch:
        yield "".join(batch), len(batch)


def canonical_sources(client, m, docs):
    ids = [d[m["id_field"]] for d in docs]
    result = client.request("POST", "/" + m["read_target"] + "/_search", {
        "size": len(ids) + 1, "track_total_hits": True, "query": {"ids": {"values": ids}}})
    require(result["hits"]["total"]["relation"] == "eq", "inexact fixture count")
    return result["hits"]["total"]["value"], result["hits"]["hits"]


def seed(client, m):
    docs = fixtures(m)  # Validate everything before the first write.
    if m["seed_mode"] == "create":
        count, hits = canonical_sources(client, m, docs)
        if count:
            expected = {d[m["id_field"]]:d for d in docs}
            require(count == len(docs) and len({h['_id'] for h in hits}) == len(docs) and
                    all(h["_source"] == expected[h["_id"]] for h in hits),
                    "partial, duplicate or changed stream fixtures; reset log-search")
            print("seed log-search: matching canonical events already present")
            return
    suffix = "&require_alias=true" if m["seed_mode"] == "index" else ""
    for payload, count in batches(docs, m["target"], m["id_field"], m["seed_mode"]):
        bulk_complete(client.request("POST", "/_bulk?refresh=wait_for" + suffix, payload, ndjson=True), count)
    count, hits = canonical_sources(client, m, docs)
    expected = {d[m["id_field"]]:d for d in docs}
    require(count == len(docs) and all(h["_source"] == expected[h["_id"]] for h in hits), "post-seed source mismatch")
    print(f"seed {m['slug']}: {count} canonical documents verified")


def reset(client, m):
    # These names have already been compared against a code-owned exact allowlist.
    if m["slug"] == "log-search":
        for path in ("/_data_stream/lab-log-search-events", "/_index_template/lab-log-search-template", "/_ingest/pipeline/lab-log-search-ingest"):
            client.request("DELETE", path, missing_ok=True)
    else:
        for name in [m["resources"][0]["name"], *m["manual_indices"]]:
            client.request("DELETE", "/" + name, missing_ok=True)
    bootstrap(client, m)
    seed(client, m)


def request_body(relative):
    path = local_path(relative, ROOT / COLLECTION_REL)
    require(path.name.endswith(".request.yaml"), "expected native request")
    request = read_yaml(path)
    require(request["body"]["type"] == "json", "CLI supports native raw JSON only")
    text = request["body"]["content"]
    require("{{" not in text, "CLI branch bodies must be literal JSON; unseen query/vector substitutions are unsupported")
    body = json.loads(text)
    url = request["url"]
    require(url.startswith("{{esUrl}}/"), "request must target collection esUrl")
    return request["method"], url[len("{{esUrl}}"):], body


def fuse(branches, constant=RRF_CONSTANT):
    require(type(constant) is int and constant > 0, "fusion constant must be positive")
    rows = {}
    for name, ids in branches.items():
        seen = set()
        for rank, identity in enumerate(ids, 1):
            if identity in seen:
                continue
            seen.add(identity)
            row = rows.setdefault(identity, {"passage_id": identity, "score": 0.0, "ranks": {}})
            row["ranks"][name] = rank
            row["score"] += 1 / (constant + rank)
    return sorted(rows.values(), key=lambda r: (-r["score"], r["passage_id"]))


def hybrid_demo(client):
    branches = {}
    for branch in ("lexical", "vector"):
        method, path, body = request_body(f"hybrid-search/core/{branch}.request.yaml")
        result = client.request(method, path, body)
        branches[branch] = [h["_id"] for h in result["hits"]["hits"]]
    print(json.dumps(fuse(branches), indent=2))


def check():
    count = 0
    for slug in SLUGS:
        m = load_manifest(slug)
        docs = fixtures(m)
        for setup in (ROOT / "scenarios" / slug / "setup").glob("*.json"):
            read_json(setup)
        if slug == "hybrid-search":
            cases = read_json(ROOT / "scenarios" / slug / "data/query-cases.json")
            require(len(cases) >= 3, "three committed vector cases required")
            for case in cases:
                vector(case["vector"])
                require(case["embedding_model"] == "fixture-v1" and case["query"].strip(), "invalid query case")
        if slug == "document-search":
            principals = read_json(ROOT / "scenarios" / slug / "data/principals.json")
            for name, p in principals.items():
                require("user:" + name in p["effective_principals"], "principal identity missing")
                require("tenant:" + p["tenant_id"] + ":all" in p["effective_principals"], "tenant token missing")
                require(all(not token.startswith("tenant:") or token == f"tenant:{p['tenant_id']}:all" for token in p["effective_principals"]), "cross-tenant principal")
        folder = ROOT / m["postman_folder"]
        require(all((folder / g).is_dir() for g in (*GROUPS, "manual")), "missing practice group")
        for file in folder.rglob("*.request.yaml"):
            request = read_yaml(file)
            require(request["$kind"] == "http-request" and request["method"] in ("GET", "POST", "PUT", "DELETE"), f"invalid native request: {file}")
            require(request["url"].startswith("{{esUrl}}/"), f"nonlab URL: {file}")
            params = request.get("queryParams", [])
            require(isinstance(params, list) and all(
                isinstance(p, dict) and isinstance(p.get("key"), str) and
                (p.get("value") is None or isinstance(p["value"], str)) for p in params),
                f"invalid native queryParams: {file}")
            enabled = [(p["key"], p.get("value") or "") for p in params if not p.get("disabled", False)]
            require(Counter(parse_qsl(urlsplit(request["url"]).query, keep_blank_values=True)) == Counter(enabled),
                    f"FMT209: URL query and queryParams differ: {file}")
            require(type(request.get("order")) in (int, float) and request.get("description"), f"missing order/description: {file}")
            require(request.get("scripts"), f"missing semantic assertions: {file}")
            for script in request["scripts"]:
                require(script.get("type") in ("beforeRequest", "afterResponse") and
                        script.get("language") == "text/javascript" and isinstance(script.get("code"), str),
                        f"unsupported native script: {file}")
            body = request.get("body")
            if body:
                text = body["content"]
                # Dynamic state is a typed JSON scalar or array set by the preceding sequence step.
                text = re.sub(r'"{{(?:log|document)PitId}}"', '"validation-only"', text)
                text = re.sub(r'{{(?:log|document)SearchAfter}}', '[0,"validation-only",0]', text)
                require("{{" not in text, f"unsupported body variable: {file}")
                if body["type"] == "json":
                    json.loads(text)
                else:
                    require(body["type"] == "text" and text.endswith("\n"), "NDJSON requires final newline")
                    for line in text.splitlines():
                        json.loads(line)
            count += 1
        for group in (*GROUPS, "manual"):
            requests = [read_yaml(p) for p in (folder / group).glob("*.request.yaml")]
            orders = [r["order"] for r in requests]
            require(len(orders) == len(set(orders)), f"duplicate execution order: {slug}/{group}")
        print(f"check {slug}: {len(docs)} valid fixtures")
    # A directory metadata kind is also 'collection' in the native format; only the root has collection identity/variables.
    collection = ROOT / COLLECTION_REL
    definition_path = collection / ".resources/definition.yaml"
    definition = read_yaml(definition_path)
    require(definition["name"] == "Elasticsearch Practice Lab" and definition["variables"]["esUrl"] == "http://localhost:9200", "invalid collection root")
    for metadata in collection.rglob("definition.yaml"):
        value = read_yaml(metadata)
        require(value["$kind"] == "collection", f"invalid native folder metadata: {metadata}")
        if metadata != definition_path:
            require("variables" not in value, "scenario must not define an independent collection")
    print(f"check: {count} native requests; one collection")


def postman_test(slugs, executable):
    require(shutil.which(executable), f"Postman CLI not found: {executable}; see README")
    args = [executable, "collection", "run", COLLECTION_REL.as_posix(), "--env-var", "esUrl=" + os.environ.get("ES_URL", "http://localhost:9200"), "--timeout-request", "30000"]
    for slug in slugs:
        m = load_manifest(slug)
        for group in GROUPS:
            # Native nested folder paths are supported by -i; always run from the shared root.
            args.extend(["-i", str(Path(m["postman_folder"]).relative_to(COLLECTION_REL) / group)])
    subprocess.run(args, cwd=ROOT, check=True)


def integration_test(client, executable):
    """Explicitly disposable scenarios; preserve an unrelated sentinel through every reset."""
    sentinel = "lab-unrelated-sentinel"
    existing = client.request("GET", "/" + sentinel, missing_ok=True)
    if existing is None:
        client.request("PUT", "/" + sentinel, {"settings": {"number_of_shards": 1, "number_of_replicas": 0}})
        client.request("PUT", "/" + sentinel + "/_doc/sentinel?refresh=wait_for", {"marker": "preserve"})
    before = client.request("GET", "/" + sentinel + "/_search?size=100&sort=_doc")
    try:
        for slug in SLUGS:
            m = load_manifest(slug)
            reset(client, m)
            seed(client, m)
            count = client.request("GET", "/" + m["read_target"] + "/_count")["count"]
            require(count == m["expected_count"], "reseed changed baseline count")
        postman_test(SLUGS, executable)
        hybrid_demo(client)
    finally:
        after = client.request("GET", "/" + sentinel + "/_search?size=100&sort=_doc")
        require(before["hits"] == after["hits"], "unrelated sentinel changed during scenario checks")
    print("test: all scenarios passed; unrelated sentinel preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wait", "bootstrap", "seed", "setup-all", "status", "reset", "check", "hybrid-demo", "postman-test", "test"))
    parser.add_argument("--scenario", choices=SLUGS)
    parser.add_argument("--postman", default="postman")
    args = parser.parse_args()
    try:
        if args.command == "check":
            check()
            return
        slugs = [args.scenario] if args.scenario else SLUGS
        if args.command == "postman-test":
            postman_test(slugs, args.postman)
            return
        client = Client(os.environ.get("ES_URL", "http://localhost:9200"))
        if args.command == "wait":
            deadline = time.monotonic() + 180
            while True:
                try:
                    health = client.request("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
                    require(health["status"] in ("yellow", "green"), "cluster not ready")
                    print("Elasticsearch ready")
                    return
                except LabError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2)
        if args.command == "hybrid-demo":
            hybrid_demo(client)
            return
        if args.command == "test":
            integration_test(client, args.postman)
            return
        require(args.scenario or args.command == "setup-all", "select --scenario for resource operations")
        for slug in slugs:
            m = load_manifest(slug)
            if args.command in ("bootstrap", "setup-all"):
                bootstrap(client, m)
            if args.command in ("seed", "setup-all"):
                seed(client, m)
            if args.command == "reset":
                reset(client, m)
            if args.command == "status":
                target = m["read_target"]
                paths = ["/" + target + "/_count", "/" + target + "/_settings", "/" + target + "/_mapping"]
                if slug == "log-search":
                    paths.extend(["/_data_stream/" + target, "/_data_stream/" + target + "/_lifecycle", "/" + target + "/_lifecycle/explain"])
                else:
                    paths.append("/_alias/" + target)
                for path in paths:
                    print(path, json.dumps(client.request("GET", path), indent=2))
    except (LabError, KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"lab: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
