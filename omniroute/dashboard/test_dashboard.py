import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api
import collect
import server

SECRET = "SECRET-DO-NOT-" + "COPY-9f3a"
SECRET_COLUMNS = ("access_token", "refresh_token", "api_key", "id_token", "provider_specific_data")
CONN_A, CONN_B = "conn-a", "conn-b"

SOURCE_SCHEMA = """
CREATE TABLE usage_history(id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, model TEXT, connection_id TEXT,
  account_key TEXT, account_label TEXT, tokens_input INT, tokens_output INT, tokens_cache_read INT,
  tokens_cache_creation INT, tokens_reasoning INT, status TEXT, success INT, latency_ms INT, ttft_ms INT,
  error_code TEXT, timestamp TEXT, combo_strategy TEXT, endpoint TEXT);
CREATE TABLE call_logs(id TEXT PRIMARY KEY, timestamp TEXT, status INT, model TEXT, requested_model TEXT,
  provider TEXT, account TEXT, connection_id TEXT, duration INT, tokens_in INT, tokens_out INT,
  tokens_cache_read INT, tokens_cache_creation INT, tokens_reasoning INT, cache_source TEXT, combo_name TEXT,
  error_summary TEXT, error_type TEXT);
CREATE TABLE quota_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, connection_id TEXT,
  window_key TEXT, remaining_percentage REAL, is_exhausted INT, next_reset_at TEXT, created_at TEXT);
CREATE TABLE provider_connections(id TEXT PRIMARY KEY, provider TEXT, name TEXT, email TEXT, is_active INT,
  test_status TEXT, error_code TEXT, last_error TEXT, last_error_at TEXT, rate_limited_until TEXT,
  last_used_at TEXT, display_name TEXT, access_token TEXT, refresh_token TEXT, api_key TEXT, id_token TEXT,
  provider_specific_data TEXT, sort_order INT);
CREATE TABLE combos(id TEXT, name TEXT, data TEXT, sort_order INT);
"""


def iso(dt):
    return collect.utc_iso(dt)


class FakeSource:
    def __init__(self, path, now):
        self.path, self.now = path, now
        self.db = sqlite3.connect(path)
        self.db.executescript(SOURCE_SCHEMA)
        self.seed()

    def at(self, **delta):
        return iso(self.now - timedelta(**delta))

    def usage(self, provider, model, conn, ok, hours_ago, tin=100, tout=10, cache=50, reason=5, status=None, code=None, lat=2000):
        self.db.execute(
            "INSERT INTO usage_history(provider,model,connection_id,account_label,tokens_input,tokens_output,"
            "tokens_cache_read,tokens_cache_creation,tokens_reasoning,status,success,latency_ms,ttft_ms,error_code,timestamp)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (provider, model, conn, "acct", tin, tout, cache, 0, reason, status or ("200" if ok else "429"),
             int(ok), lat, 100, code, self.at(hours=hours_ago)))

    def call(self, cid, status, hours_ago, text=None, provider="antigravity", model="gemini-3-flash", **delta):
        ts = self.at(hours=hours_ago, **delta)
        self.db.execute("INSERT INTO call_logs(id,timestamp,status,model,provider,connection_id,duration,tokens_in,"
                        "tokens_out,tokens_cache_read,combo_name,error_summary,error_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, ts, status, model, provider, CONN_A, 900, 10, 1, 0, "combo-x", text, "x"))

    def seed(self):
        for _ in range(6):
            self.usage("antigravity", "gemini-3-flash", CONN_A, True, 1)
        self.usage("antigravity", "gemini-3-flash", CONN_A, False, 2, status="429", code="upstream_429")
        self.usage("antigravity", "gemini-3-flash", CONN_A, False, 3, status="429", code="upstream_429")
        for _ in range(3):
            self.usage("nvidia", "nvidia/nemotron", CONN_B, True, 5, tin=1000, cache=0)
        self.usage("openrouter", "free/model", None, True, 24 * 3, tin=7)
        self.call("c1", 200, 1)
        self.call("c2", 429, 2, text="Resource has been exhausted (e.g. check quota)")
        self.call("c3", 429, 3, text="Resource has been exhausted (e.g. check quota)")
        self.call("c4", 401, 4, text="[401]: upstream error", provider="nvidia")
        self.db.execute("INSERT INTO quota_snapshots(provider,connection_id,window_key,remaining_percentage,"
                        "is_exhausted,next_reset_at,created_at) VALUES('antigravity',?,?,?,?,?,?)",
                        (CONN_A, "gemini-3-flash", 80.0, 0, self.at(hours=-5), self.at(hours=30)))
        self.db.execute("INSERT INTO quota_snapshots(provider,connection_id,window_key,remaining_percentage,"
                        "is_exhausted,next_reset_at,created_at) VALUES('antigravity',?,?,?,?,?,?)",
                        (CONN_A, "gemini-3-flash", 12.5, 0, self.at(hours=-5), self.at(hours=1)))
        self.db.execute("INSERT INTO quota_snapshots(provider,connection_id,window_key,remaining_percentage,"
                        "is_exhausted,next_reset_at,created_at) VALUES('nvidia',?,?,?,?,?,?)",
                        (CONN_B, "daily", 0.0, 1, self.at(hours=-2), self.at(hours=1)))
        for cid, prov, label in ((CONN_A, "antigravity", "alice@example.com"), (CONN_B, "nvidia", "nv key")):
            self.db.execute(
                "INSERT INTO provider_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, prov, label, None, 1, "active", None, None, None, None, None, None,
                 SECRET, SECRET, SECRET, SECRET, SECRET, 1))
        combo = {"name": "combo-x", "strategy": "priority", "models": [
            {"id": "1", "kind": "model", "model": "agy/gemini-3-flash", "providerId": "agy", "allowedConnectionIds": [CONN_A]},
            {"id": "2", "kind": "model", "model": "nvidia/nvidia/nemotron", "providerId": "nvidia", "allowedConnectionIds": []},
        ]}
        self.db.execute("INSERT INTO combos VALUES('1','combo-x',?,1)", (json.dumps(combo),))
        self.db.commit()


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        self.src_path, self.dst_path = tmp / "src.sqlite", tmp / "dash.sqlite"
        self.selected = tmp / "selected.json"
        self.selected.write_text(json.dumps({"picks": {"agy": "agy/gemini-3-flash", "x": "__none__"},
                                             "extra_picks": ["nvidia/nvidia/nemotron"]}), encoding="utf-8")
        self.src = FakeSource(str(self.src_path), self.now)
        self.addCleanup(self.src.db.close)

    def collect(self):
        return collect.collect(self.src_path, self.dst_path, self.selected)

    def table_counts(self):
        db = sqlite3.connect(self.dst_path)
        try:
            return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("usage", "calls", "quota", "connections", "candidates")}
        finally:
            db.close()

    def get(self, path, rng="24h"):
        status, body = api.handle(self.dst_path, path, {"range": rng}, now=self.now)
        self.assertEqual(status, 200, body)
        return body

    def test_collect_backfills_then_is_a_noop(self):
        first = self.collect()
        self.assertEqual((first["usage"], first["calls"], first["quota"]), (12, 4, 3))
        before = self.table_counts()
        second = self.collect()
        self.assertEqual((second["usage"], second["calls"], second["quota"]), (0, 0, 0))
        self.assertEqual(self.table_counts(), before)

    def test_collect_picks_up_new_rows_and_late_calls(self):
        self.collect()
        self.src.usage("antigravity", "gemini-3-flash", CONN_A, True, 0)
        self.src.call("c-late", 200, 0, minutes=0)
        self.src.call("c-late-old", 200, 0, minutes=5)
        self.src.db.commit()
        second = self.collect()
        self.assertEqual((second["usage"], second["calls"]), (1, 2))
        self.assertEqual(self.collect()["calls"], 0)

    def test_secret_columns_never_reach_dash_db(self):
        self.collect()
        db = sqlite3.connect(self.dst_path)
        try:
            columns = {r[1] for t in ("connections", "usage", "calls", "quota", "candidates", "meta")
                       for r in db.execute(f"PRAGMA table_info({t})")}
            self.assertFalse(columns & set(SECRET_COLUMNS))
            dump = "\n".join(db.iterdump())
        finally:
            db.close()
        self.assertNotIn(SECRET, dump)

    def test_source_connection_is_read_only(self):
        src = collect.open_src(self.src_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                src.execute("DELETE FROM usage_history")
        finally:
            src.close()

    def test_candidates_table_keeps_combo_order_and_autotune_picks(self):
        self.collect()
        db = sqlite3.connect(self.dst_path)
        rows = db.execute("SELECT combo, rank, model FROM candidates ORDER BY combo, rank").fetchall()
        db.close()
        self.assertEqual(rows, [("autotune-picks", 1, "agy/gemini-3-flash"), ("autotune-picks", 2, "nvidia/nvidia/nemotron"),
                                ("combo-x", 1, "agy/gemini-3-flash"), ("combo-x", 2, "nvidia/nvidia/nemotron")])

    def test_overview_totals(self):
        self.collect()
        d = self.get("/api/overview")
        self.assertEqual(d["requests"], 11)
        self.assertAlmostEqual(d["ok_rate"], 9 / 11)
        self.assertEqual(d["tok_in"], 6 * 100 + 2 * 100 + 3 * 1000)
        self.assertAlmostEqual(d["cache_ratio"], d["tok_cache_read"] / d["tok_in"])
        self.assertEqual(d["p50_latency_ms"], 2000)
        self.assertIsNotNone(d["last_collect_at"])
        self.assertEqual(self.get("/api/overview", "all")["requests"], 12)

    def test_timeseries_fills_every_bucket(self):
        self.collect()
        d = self.get("/api/timeseries")
        self.assertEqual(d["bucket"], "hour")
        self.assertGreaterEqual(len(d["points"]), 24)
        self.assertEqual(sum(p["requests"] for p in d["points"]), 11)
        self.assertEqual(sum(p["errors"] for p in d["points"]), 2)
        self.assertEqual(self.get("/api/timeseries", "30d")["bucket"], "day")

    def test_models_carry_candidate_badges_and_quota(self):
        self.collect()
        by_model = {m["model"]: m for m in self.get("/api/models")["models"]}
        agy = by_model["gemini-3-flash"]
        self.assertEqual(agy["requests"], 8)
        self.assertEqual(agy["candidates"], [{"combo": "autotune-picks", "rank": 1}, {"combo": "combo-x", "rank": 1}])
        self.assertEqual(agy["quota"]["remaining_pct"], 12.5)
        self.assertEqual(by_model["nvidia/nemotron"]["candidates"][0]["combo"], "autotune-picks")
        self.assertNotIn("free/model", by_model)
        self.assertIn("free/model", {m["model"] for m in self.get("/api/models", "7d")["models"]})

    def test_quota_latest_snapshot_with_history_and_label(self):
        self.collect()
        items = {i["window_key"]: i for i in self.get("/api/quota")["items"]}
        self.assertEqual(set(items), {"gemini-3-flash", "daily"})
        g = items["gemini-3-flash"]
        self.assertEqual((g["remaining_pct"], g["label"]), (12.5, "alice@example.com"))
        self.assertGreater(g["reset_in_sec"], 0)
        self.assertLessEqual(len(g["history"]), 60)
        self.assertTrue(items["daily"]["exhausted"])

    def test_candidates_list_members_in_rank_order(self):
        self.collect()
        d = self.get("/api/candidates")
        self.assertEqual([c["name"] for c in d["combos"]], ["combo-x"])
        first, second = d["combos"][0]["members"]
        self.assertEqual((first["rank"], first["requests_24h"], first["quota"]["remaining_pct"]), (1, 8, 12.5))
        self.assertEqual(first["connection"]["status"], "ok")
        self.assertEqual((second["rank"], second["requests_24h"]), (2, 3))
        self.assertEqual(len(d["picks"]), 2)

    def test_errors_grouped_by_kind_with_examples(self):
        self.collect()
        d = self.get("/api/errors")
        self.assertEqual(d["total"], 3)
        self.assertEqual(sum(k["count"] for k in d["kinds"]), 3)
        top = d["kinds"][0]
        self.assertEqual(top["count"], 2)
        self.assertEqual(top["examples"][0], {"text": "Resource has been exhausted (e.g. check quota)", "count": 2})
        for k in d["kinds"]:
            self.assertTrue(k["title"] and k["cause"] and k["action"])
        self.assertEqual(sum(x["count"] for x in d["daily"]), 3)
        self.assertEqual(d["attempt_failures"]["total"], 2)

    def test_connections_endpoint_has_no_secrets(self):
        self.collect()
        body = json.dumps(self.get("/api/connections"))
        self.assertNotIn(SECRET, body)
        self.assertEqual(len(self.get("/api/connections")["connections"]), 2)

    def test_bad_range_and_unknown_path(self):
        self.collect()
        self.assertEqual(api.handle(self.dst_path, "/api/overview", {"range": "5y"})[0], 400)
        self.assertEqual(api.handle(self.dst_path, "/api/nope", {})[0], 404)

    def test_http_server_serves_page_and_json(self):
        self.collect()
        httpd, stop = server.serve(self.dst_path, self.src_path, port=0, collector=False)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        self.assertIn("OmniRoute 대시보드", page)
        data = json.load(urllib.request.urlopen(base + "/api/overview?range=24h"))
        self.assertEqual(data["requests"], 11)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(base + "/not-a-file")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
