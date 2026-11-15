"""Tool contracts that don't attempt to emulate Elasticsearch search semantics."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
from http_client import Client, LabError, bulk_complete, complete


class Response(BytesIO):
    status = 200


class ResponseContracts(unittest.TestCase):
    def test_http_error_contains_operation_status_and_reason(self):
        error = HTTPError("http://localhost:9200/bad", 400, "Bad Request", {},
                          BytesIO(b'{"error":{"reason":"invalid latitude"}}'))
        with patch("http_client.urlopen", side_effect=error):
            with self.assertRaisesRegex(LabError, "POST /bad: HTTP 400.*invalid latitude"):
                Client("http://localhost:9200").request("POST", "/bad", {})

    def test_socket_failure_is_readable_and_not_retried(self):
        with patch("http_client.urlopen", side_effect=URLError("connection refused")) as call:
            with self.assertRaisesRegex(LabError, "GET /index.*connection refused"):
                Client("http://localhost:9200").request("GET", "/index")
            self.assertEqual(call.call_count, 1)

    def test_http_200_timeout_is_failure(self):
        with patch("http_client.urlopen", return_value=Response(b'{"timed_out":true}')):
            with self.assertRaisesRegex(LabError, "HTTP 200.*timeout"):
                Client("http://localhost:9200").request("POST", "/index/_search", {})

    def test_partial_shards_and_multisearch_errors_fail(self):
        for body in ({"_shards": {"failed": 1}}, {"responses": [{"error": "rejected"}]},
                     {"acknowledged": False}, {"shards_acknowledged": False}):
            with self.subTest(body=body), self.assertRaises(LabError):
                complete(body)

    def test_invalid_json_is_not_an_empty_result(self):
        with patch("http_client.urlopen", return_value=Response(b"not JSON")):
            with self.assertRaisesRegex(LabError, "GET /index"):
                Client("http://localhost:9200").request("GET", "/index")

    def test_only_explicit_missing_ok_accepts_404(self):
        def error(*args, **kwargs):
            raise HTTPError("url", 404, "missing", {}, BytesIO(b'{"error":"missing"}'))
        with patch("http_client.urlopen", side_effect=error):
            self.assertIsNone(Client("http://localhost:9200").request("GET", "/index", missing_ok=True))
            with self.assertRaises(LabError):
                Client("http://localhost:9200").request("GET", "/index")

    def test_bulk_checks_each_item_even_when_error_flag_lies(self):
        with self.assertRaisesRegex(LabError, "rejected"):
            bulk_complete({"errors": False, "items": [{"index": {"status": 429, "error": "rejected"}}]}, 1)

    def test_bulk_rejects_missing_items_and_conflicts(self):
        for body in ({"items": []}, {"items": [{"create": {"status": 409}}]}):
            with self.subTest(body=body), self.assertRaises(LabError):
                bulk_complete(body, 1)
        bulk_complete({"errors": False, "items": [{"index": {"status": 201}}]}, 1)


class FixtureContracts(unittest.TestCase):
    def test_committed_fixtures_satisfy_all_five_models(self):
        for slug in lab.SLUGS:
            with self.subTest(slug=slug):
                manifest = lab.load_manifest(slug)
                self.assertEqual(len(lab.fixtures(manifest)), manifest["expected_count"])

    def test_invalid_vectors_rejected_before_loading(self):
        for value in ([1, 0], [0, 0, 0, 0], [True, 0, 0, 0], [float("nan"), 1, 0, 0], [float("inf"), 0, 0, 0]):
            with self.subTest(value=value), self.assertRaises(LabError):
                lab.vector(value)
        lab.vector([1, 0, 0, 0])

    def test_wrong_integer_and_coordinates_are_rejected(self):
        mapping = {"properties": {"count": {"type": "integer"}, "location": {"type": "geo_point"}}}
        for doc in ({"count": True}, {"count": "3"}, {"location": {"lat": 91, "lon": 0}}, {"location": [0, 0]}):
            with self.subTest(doc=doc), self.assertRaises(LabError):
                lab.validate_fields(doc, mapping, "test")

    def test_utc_dates_require_real_calendar_values(self):
        for value in ("2026-02-30T00:00:00Z", "2026-01-01T00:00:00+00:00", "bad"):
            with self.subTest(value=value), self.assertRaises(LabError):
                lab.utc_date(value)

    def test_bulk_has_action_source_pairs_and_final_newline(self):
        docs = [{"id": str(i), "value": "é"} for i in range(501)]
        batches = list(lab.batches(docs, "owned-write", "id", "index"))
        self.assertEqual([n for _, n in batches], [500, 1])
        for payload, count in batches:
            self.assertTrue(payload.endswith("\n"))
            lines = [json.loads(line) for line in payload.splitlines()]
            self.assertEqual(len(lines), count * 2)
            for action in lines[::2]:
                self.assertEqual(action["index"]["_index"], "owned-write")


class LifecycleSafety(unittest.TestCase):
    def test_path_traversal_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "escape").symlink_to("/etc")
            for value in ("../", "escape/hosts", "/etc/hosts"):
                with self.subTest(value=value), self.assertRaises(LabError):
                    lab.local_path(value, base)

    def test_unknown_scenario_fails_without_resource_access(self):
        for slug in ("../../etc", "*", "_all", "address-search-other"):
            with self.subTest(slug=slug), self.assertRaises(LabError):
                lab.load_manifest(slug)

    def test_alias_setup_cannot_delete_an_unrelated_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(lab.ROOT / "scenarios/address-search", root / "scenarios/address-search")
            (root / lab.COLLECTION_REL / "address-search").mkdir(parents=True)
            aliases = root / "scenarios/address-search/setup/aliases.json"
            aliases.write_text(json.dumps({"actions": [{"remove_index": {"index": "unrelated-sentinel"}}]}))
            with patch.object(lab, "ROOT", root):
                with self.assertRaisesRegex(LabError, "alias actions outside"):
                    lab.load_manifest("address-search")

    def test_elasticsearch_normalized_settings_are_compatible(self):
        self.assertTrue(lab.settings_match(
            {"settings": {"index": {"number_of_shards": "1", "default_pipeline": "owned"}}},
            {"settings": {"number_of_shards": 1, "index.default_pipeline": "owned"}}))
        self.assertFalse(lab.settings_match({"number_of_shards": "2"}, {"number_of_shards": 1}))

    def test_partial_or_changed_stream_seed_never_writes(self):
        manifest = lab.load_manifest("log-search")
        docs = lab.fixtures(manifest)
        for response in ((1, [{"_id": docs[0]["event_id"], "_source": docs[0]}]),
                         (len(docs), [{"_id": d["event_id"], "_source": {**d, "message": "changed"}} for d in docs])):
            with self.subTest(count=response[0]), patch.object(lab, "canonical_sources", return_value=response):
                client = unittest.mock.Mock()
                with self.assertRaisesRegex(LabError, "reset log-search"):
                    lab.seed(client, manifest)
                client.request.assert_not_called()

    def test_complete_stream_reseed_is_noop(self):
        m = lab.load_manifest("log-search")
        docs = lab.fixtures(m)
        client = unittest.mock.Mock()
        with patch.object(lab, "canonical_sources", return_value=(len(docs), [{"_id": d["event_id"], "_source": d} for d in docs])):
            lab.seed(client, m)
        client.request.assert_not_called()


class RankFusion(unittest.TestCase):
    def test_known_rank_arithmetic_and_missing_ids(self):
        rows = lab.fuse({"lexical": ["a", "b"], "vector": ["b", "c"]})
        self.assertEqual([r["passage_id"] for r in rows], ["b", "a", "c"])
        self.assertAlmostEqual(rows[0]["score"], 1/62 + 1/61)
        self.assertEqual(rows[0]["ranks"], {"lexical": 2, "vector": 1})
        self.assertAlmostEqual(rows[2]["score"], 1/62)

    def test_fused_ties_use_passage_id(self):
        rows = lab.fuse({"lexical": ["z"], "vector": ["a"]})
        self.assertEqual([r["passage_id"] for r in rows], ["a", "z"])

    def test_duplicate_id_cannot_gain_extra_contribution(self):
        rows = lab.fuse({"branch": ["a", "a", "b"]})
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["score"], 1/61)
        self.assertEqual(rows[1]["ranks"], {"branch": 3})

    def test_empty_branches_and_invalid_constant(self):
        self.assertEqual(lab.fuse({"lexical": [], "vector": []}), [])
        with self.assertRaises(LabError):
            lab.fuse({"branch": ["a"]}, 0)


if __name__ == "__main__":
    unittest.main()
