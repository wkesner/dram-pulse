"""Minimal fake Airtable upsert server for offline exporter tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import unquote, urlparse


class FakeAirtableState:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = {}
        self.requests: list[dict[str, Any]] = []
        self.force_429_once = False
        self.fail_on_table: Optional[str] = None
        self._id_seq = 0

    def reset(self) -> None:
        self.tables.clear()
        self.requests.clear()
        self.force_429_once = False
        self.fail_on_table = None
        self._id_seq = 0

    def next_id(self) -> str:
        self._id_seq += 1
        return f"recFAKE{self._id_seq:06d}"


STATE = FakeAirtableState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def do_PATCH(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = urlparse(self.path).path
        # /v0/{base}/{table}
        parts = [unquote(p) for p in path.strip("/").split("/")]
        table = parts[-1] if parts else ""
        records = body.get("records") or []
        merge = (body.get("performUpsert") or {}).get("fieldsToMergeOn") or []

        STATE.requests.append({"table": table, "n": len(records), "merge": merge, "body": body})

        if STATE.force_429_once:
            STATE.force_429_once = False
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"RATE_LIMIT"}')
            return

        if STATE.fail_on_table and table == STATE.fail_on_table:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"fail"}')
            return

        if len(records) > 10:
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"BATCH_TOO_LARGE"}')
            return

        if not merge:
            self.send_response(422)
            self.end_headers()
            return

        merge_field = merge[0]
        store = STATE.tables.setdefault(table, {})
        created: list[str] = []
        updated: list[str] = []
        out_records: list[dict[str, Any]] = []

        for rec in records:
            fields = dict(rec.get("fields") or {})
            key = fields.get(merge_field)
            if key is None:
                continue
            key_s = str(key)
            if key_s in store:
                # Merge fields but preserve human-owned keys already present
                existing = store[key_s]
                human = {
                    "Review",
                    "Enrich?",
                    "Side",
                    "Product Family",
                    "Signal Type",
                    "Domain",
                    "Enriched At",
                }
                merged_fields = dict(existing["fields"])
                for k, v in fields.items():
                    if k in human and k in merged_fields:
                        continue  # exporter shouldn't send these; if it did, still protect
                    merged_fields[k] = v
                # Also: if exporter omitted human fields, keep them
                existing["fields"] = merged_fields
                updated.append(existing["id"])
                out_records.append(existing)
            else:
                rid = STATE.next_id()
                stored = {"id": rid, "fields": fields}
                store[key_s] = stored
                created.append(rid)
                out_records.append(stored)

        payload = {
            "createdRecords": created,
            "updatedRecords": updated,
            "records": out_records,
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_fake_airtable() -> tuple[HTTPServer, str]:
    STATE.reset()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/v0"
