"""Bounded, single-attempt JSON/NDJSON HTTP calls to Elasticsearch."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class LabError(RuntimeError):
    pass


def complete(body, operation="response"):
    if not isinstance(body, dict):
        raise LabError(f"{operation}: expected a JSON object")
    if body.get("timed_out") or body.get("_shards", {}).get("failed", 0):
        raise LabError(f"{operation}: timeout or failed shards: {body}")
    if body.get("error"):
        raise LabError(f"{operation}: {body['error']}")
    if body.get("acknowledged") is False or body.get("shards_acknowledged") is False:
        raise LabError(f"{operation}: resource change was not acknowledged: {body}")
    if "responses" in body:
        for response in body["responses"]:
            complete(response, operation)
    return body


def bulk_complete(body, expected):
    complete(body, "bulk")
    items = body.get("items", [])
    failures = []
    for item in items:
        if len(item) != 1:
            failures.append(item)
            continue
        result = next(iter(item.values()))
        if result.get("error") or not 200 <= result.get("status", 0) < 300:
            failures.append(result)
    if body.get("errors") or failures or len(items) != expected:
        raise LabError(f"bulk: expected {expected} items, received {len(items)}; failures={failures}")


class Client:
    def __init__(self, base, timeout=30):
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise LabError("ES_URL must be an HTTP(S) base URL without query or fragment")
        self.base = base.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, body=None, *, missing_ok=False, ndjson=False):
        if not path.startswith("/") or path.startswith("//"):
            raise LabError(f"invalid Elasticsearch path: {path}")
        raw = None if body is None else (body.encode() if ndjson else json.dumps(body, allow_nan=False).encode())
        request = Request(self.base + path, data=raw, method=method,
                          headers={"Content-Type": "application/x-ndjson" if ndjson else "application/json"})
        operation = f"{method} {path}"
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                return complete(json.loads(content) if content else {}, f"{operation}: HTTP {response.status}")
        except HTTPError as exc:
            with exc:
                content = exc.read().decode(errors="replace")
            if missing_ok and exc.code == 404:
                return None
            try:
                reason = json.loads(content).get("error", content)
            except ValueError:
                reason = content
            raise LabError(f"{operation}: HTTP {exc.code}: {reason}") from exc
        except (URLError, socket.timeout, TimeoutError, OSError, ValueError) as exc:
            raise LabError(f"{operation}: {exc}") from exc
