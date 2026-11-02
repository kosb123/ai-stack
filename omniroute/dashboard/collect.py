"""Incremental, idempotent copy of OmniRoute's usage data into the dashboard's own sqlite file.

The OmniRoute DB is only ever opened read-only, and provider_connections is read through an explicit
column list so the token/key columns can never reach dash.sqlite.
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
DEFAULT_SRC = HOME / ".omniroute" / "storage.sqlite"
DEFAULT_DST = Path(os.environ.get("LOCALAPPDATA", str(HOME / "AppData" / "Local"))) / "omni-dash" / "dash.sqlite"
DEFAULT_SELECTED = Path(__file__).resolve().parents[1] / 'autotune' / 'selected_models.json'

BATCH = 5000
CALLS_OVERLAP = timedelta(minutes=10)
AUTOTUNE_COMBO = "autotune-picks"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  src_id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, model TEXT, connection_id TEXT, account TEXT,
  status INT, ok INT, latency_ms INT, ttft_ms INT, tok_in INT, tok_out INT, tok_cache_read INT,
  tok_cache_write INT, tok_reasoning INT, error_code TEXT);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_pm ON usage(provider, model);
CREATE INDEX IF NOT EXISTS usage_conn ON usage(connection_id, model);

CREATE TABLE IF NOT EXISTS calls(
  src_id TEXT PRIMARY KEY, ts TEXT, status INT, provider TEXT, model TEXT, requested_model TEXT,
  connection_id TEXT, combo TEXT, duration_ms INT, tok_in INT, tok_out INT, tok_cache_read INT,
  cache_source TEXT, error_type TEXT, error_text TEXT);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS calls_pm ON calls(provider, model);
CREATE INDEX IF NOT EXISTS calls_combo ON calls(combo);

CREATE TABLE IF NOT EXISTS quota(
  src_id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, connection_id TEXT, window_key TEXT,
  remaining_pct REAL, exhausted INT, next_reset TEXT);
CREATE INDEX IF NOT EXISTS quota_ts ON quota(ts);
CREATE INDEX IF NOT EXISTS quota_series ON quota(provider, connection_id, window_key, src_id);

CREATE TABLE IF NOT EXISTS connections(
  id TEXT PRIMARY KEY, provider TEXT, label TEXT, active INT, test_status TEXT, error_code TEXT,
  last_error TEXT, last_error_at TEXT, rate_limited_until TEXT, last_used_at TEXT, seen_at TEXT);

CREATE TABLE IF NOT EXISTS candidates(
  combo TEXT, rank INT, model TEXT, provider TEXT, connection_ids TEXT, PRIMARY KEY(combo, rank));
CREATE INDEX IF NOT EXISTS candidates_combo ON candidates(combo);

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

USAGE_SQL = """SELECT id, timestamp, provider, model, connection_id, account_label, status, success,
  latency_ms, ttft_ms, tokens_input, tokens_output, tokens_cache_read, tokens_cache_creation,
  tokens_reasoning, error_code FROM usage_history WHERE id > ? ORDER BY id LIMIT ?"""

QUOTA_SQL = """SELECT id, created_at, provider, connection_id, window_key, remaining_percentage,
  is_exhausted, next_reset_at FROM quota_snapshots WHERE id > ? ORDER BY id LIMIT ?"""

CALLS_SQL = """SELECT id, timestamp, status, provider, model, requested_model, connection_id, combo_name,
  duration, tokens_in, tokens_out, tokens_cache_read, cache_source, error_type, error_summary
  FROM call_logs WHERE (timestamp > ?1 OR (timestamp = ?1 AND id > ?2)) ORDER BY timestamp, id LIMIT ?3"""

CONNECTIONS_SQL = """SELECT id, provider, display_name, name, email, is_active, test_status, error_code,
  last_error, last_error_at, rate_limited_until, last_used_at FROM provider_connections"""


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def open_dst(dst_path):
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dst_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def open_src(src_path):
    uri = "file:" + Path(src_path).as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _meta_get(dst, key, default=None):
    row = dst.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _meta_set(dst, key, value):
    dst.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, None if value is None else str(value)))


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _copy_by_id(src, dst, sql, insert_sql, meta_key, mapper):
    cursor = int(_meta_get(dst, meta_key, 0) or 0)
    added = 0
    while True:
        rows = src.execute(sql, (cursor, BATCH)).fetchall()
        if not rows:
            break
        added += dst.executemany(insert_sql, [mapper(r) for r in rows]).rowcount
        cursor = rows[-1][0]
        _meta_set(dst, meta_key, cursor)
        dst.commit()
        if len(rows) < BATCH:
            break
    return added


def _usage_row(r):
    return (r[0], r[1], r[2], r[3], r[4], r[5], _int(r[6]), _int(r[7]), r[8], r[9],
            r[10], r[11], r[12], r[13], r[14], r[15])


def _quota_row(r):
    return (r[0], r[1], r[2], r[3], r[4], r[5], _int(r[6]), r[7])


def _copy_calls(src, dst):
    last_ts = _meta_get(dst, "calls_ts")
    if last_ts:
        start_ts = utc_iso(parse_iso(last_ts) - CALLS_OVERLAP)
    else:
        start_ts = ""
    start_id = ""
    max_ts = last_ts or ""
    added = 0
    while True:
        rows = src.execute(CALLS_SQL, (start_ts, start_id, BATCH)).fetchall()
        if not rows:
            break
        added += dst.executemany(
            "INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r[0], r[1], _int(r[2]), r[3], r[4], r[5], r[6], r[7], _int(r[8]), r[9], r[10], r[11],
              r[12], r[13], r[14]) for r in rows]).rowcount
        start_ts, start_id = rows[-1][1], rows[-1][0]
        if start_ts and start_ts > max_ts:
            max_ts = start_ts
        _meta_set(dst, "calls_ts", max_ts)
        dst.commit()
        if len(rows) < BATCH:
            break
    return added


def _sync_connections(src, dst):
    now = utc_iso(datetime.now(timezone.utc))
    rows = src.execute(CONNECTIONS_SQL).fetchall()
    out = []
    for r in rows:
        label = r[2] or r[3] or r[4] or r[0]
        out.append((r[0], r[1], label, _int(r[5]), r[6], r[7], r[8], r[9], r[10], r[11], now))
    dst.executemany(
        """INSERT INTO connections(id,provider,label,active,test_status,error_code,last_error,last_error_at,
             rate_limited_until,last_used_at,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET provider=excluded.provider,label=excluded.label,active=excluded.active,
             test_status=excluded.test_status,error_code=excluded.error_code,last_error=excluded.last_error,
             last_error_at=excluded.last_error_at,rate_limited_until=excluded.rate_limited_until,
             last_used_at=excluded.last_used_at,seen_at=excluded.seen_at""", out)
    dst.commit()
    return len(out)


def _provider_of(model, fallback=None):
    return model.split("/", 1)[0] if "/" in model else fallback


def _sync_candidates(src, dst, selected_path):
    rows = []
    for name, data in src.execute("SELECT name, data FROM combos ORDER BY sort_order, name").fetchall():
        try:
            members = json.loads(data).get("models") or []
        except (TypeError, ValueError):
            continue
        rank = 0
        for m in members:
            model = m.get("model") if isinstance(m, dict) else None
            if not isinstance(model, str):
                continue
            rank += 1
            rows.append((name, rank, model, m.get("providerId") or _provider_of(model),
                         json.dumps(m.get("allowedConnectionIds") or [])))
    rows.extend(_autotune_rows(selected_path))
    dst.execute("DELETE FROM candidates")
    dst.executemany("INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?)", rows)
    dst.commit()
    return len(rows)


def _autotune_rows(selected_path):
    try:
        with open(selected_path, encoding="utf-8") as f:
            sel = json.load(f)
    except (OSError, ValueError):
        return []
    models = [(prov, m) for prov, m in (sel.get("picks") or {}).items()
              if isinstance(m, str) and m != "__none__"]
    models += [(_provider_of(m), m) for m in (sel.get("extra_picks") or []) if isinstance(m, str)]
    return [(AUTOTUNE_COMBO, i, m, prov, "[]") for i, (prov, m) in enumerate(models, 1)]


def collect(src_path=DEFAULT_SRC, dst_path=DEFAULT_DST, selected_path=DEFAULT_SELECTED):
    dst = open_dst(dst_path)
    try:
        src = open_src(src_path)
        try:
            counts = {
                "usage": _copy_by_id(src, dst, USAGE_SQL, "INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     "usage_id", _usage_row),
                "quota": _copy_by_id(src, dst, QUOTA_SQL, "INSERT OR IGNORE INTO quota VALUES(?,?,?,?,?,?,?,?)",
                                     "quota_id", _quota_row),
                "calls": _copy_calls(src, dst),
                "connections": _sync_connections(src, dst),
                "candidates": _sync_candidates(src, dst, selected_path),
            }
        finally:
            src.close()
        _meta_set(dst, "last_collect_at", utc_iso(datetime.now(timezone.utc)))
        _meta_set(dst, "last_collect_error", "")
        dst.commit()
        return counts
    except Exception as e:
        dst.rollback()
        _meta_set(dst, "last_collect_error", f"{type(e).__name__}: {e}")
        dst.commit()
        raise
    finally:
        dst.close()


if __name__ == "__main__":
    t = time.time()
    print(json.dumps(collect(*(sys.argv[1:3]))), f"{time.time() - t:.1f}s")
