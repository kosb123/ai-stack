"""Read-only JSON handlers over dash.sqlite. Each handler takes an open connection and returns a dict."""
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from catalog import classify

RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
KST = timezone(timedelta(hours=9))
PROVIDER_ALIAS = {"agy": "antigravity"}
STALE_QUOTA_SEC = 24 * 3600
EXAMPLE_LEN = 200
HISTORY_POINTS = 60
HISTORY_DAYS = 7
BUCKET_SQL = {
    "hour": "strftime('%Y-%m-%dT%H:00', ts, '+9 hours')",
    "3h": "strftime('%Y-%m-%dT', ts, '+9 hours') || printf('%02d', (CAST(strftime('%H', ts, '+9 hours') AS INT) / 3) * 3) || ':00'",
    "day": "strftime('%Y-%m-%d', ts, '+9 hours')",
}
BUCKET_STEP = {"hour": timedelta(hours=1), "3h": timedelta(hours=3), "day": timedelta(days=1)}
BUCKET_FOR_RANGE = {"24h": "hour", "7d": "3h", "30d": "day", "all": "day"}


def connect(path):
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _since(rng, now):
    delta = RANGES.get(rng, RANGES["24h"])
    return "" if delta is None else _iso(now - delta)


def _meta(db, key):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, max(0, -(-len(sorted_vals) * p // 100) - 1))]


def _ratio(num, den):
    return None if not den else num / den


def _norm_provider(p):
    return PROVIDER_ALIAS.get(p, p)


def _clip(s, n=EXAMPLE_LEN):
    return s if len(s) <= n else s[: n - 1] + "…"


def overview(db, rng="24h", now=None):
    now = now or _utcnow()
    since = _since(rng, now)
    r = db.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok, COALESCE(SUM(tok_in),0) tok_in,
             COALESCE(SUM(tok_cache_read),0) tok_cache_read, COALESCE(SUM(tok_cache_write),0) tok_cache_write,
             COALESCE(SUM(tok_out),0) tok_out, COALESCE(SUM(tok_reasoning),0) tok_reasoning
           FROM usage WHERE ts >= ?""", (since,)).fetchone()
    lat = sorted(x[0] for x in db.execute(
        "SELECT latency_ms FROM usage WHERE ts >= ? AND ok = 1 AND latency_ms > 0", (since,)))
    return {
        "range": rng,
        "requests": r["n"],
        "ok_rate": _ratio(r["ok"], r["n"]),
        "tok_in": r["tok_in"],
        "tok_cache_read": r["tok_cache_read"],
        "tok_cache_write": r["tok_cache_write"],
        "tok_out": r["tok_out"],
        "tok_reasoning": r["tok_reasoning"],
        # tokens_input already counts cached prompt tokens (cache_read never exceeds it in usage_history)
        "cache_ratio": _ratio(r["tok_cache_read"], r["tok_in"]),
        "p50_latency_ms": _pct(lat, 50),
        "last_collect_at": _meta(db, "last_collect_at"),
        "last_collect_error": _meta(db, "last_collect_error") or None,
        "data_since": db.execute("SELECT MIN(ts) FROM usage").fetchone()[0],
    }


def _bucket_floor(dt, kind):
    if kind == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    step = 3 if kind == "3h" else 1
    return dt.replace(hour=dt.hour // step * step, minute=0, second=0, microsecond=0)


def _bucket_key(dt, kind):
    return dt.strftime("%Y-%m-%d" if kind == "day" else "%Y-%m-%dT%H:00")


def timeseries(db, rng="24h", now=None):
    now = now or _utcnow()
    since = _since(rng, now)
    kind = BUCKET_FOR_RANGE.get(rng, "hour")
    rows = {r["b"]: dict(r) for r in db.execute(
        f"""SELECT {BUCKET_SQL[kind]} b, SUM(tok_in) tok_in, SUM(tok_cache_read) tok_cache_read,
              SUM(tok_out) tok_out, SUM(tok_reasoning) tok_reasoning, COUNT(*) requests,
              SUM(CASE WHEN ok = 1 THEN 0 ELSE 1 END) errors
            FROM usage WHERE ts >= ? GROUP BY b""", (since,))}
    kst_now = now.astimezone(KST).replace(tzinfo=None)
    if since:
        start = _parse(since).astimezone(KST).replace(tzinfo=None)
    elif rows:
        start = datetime.strptime(min(rows), "%Y-%m-%d" if kind == "day" else "%Y-%m-%dT%H:00")
    else:
        start = kst_now
    cur, end, step = _bucket_floor(start, kind), _bucket_floor(kst_now, kind), BUCKET_STEP[kind]
    points = []
    while cur <= end:
        key = _bucket_key(cur, kind)
        row = rows.get(key, {})
        points.append({"t": key, **{k: row.get(k) or 0 for k in (
            "tok_in", "tok_cache_read", "tok_out", "tok_reasoning", "requests", "errors")}})
        cur += step
    return {"range": rng, "bucket": kind, "tz": "KST", "points": points}


def _load_candidates(db):
    out = []
    for r in db.execute("SELECT combo, rank, model, provider, connection_ids FROM candidates ORDER BY combo, rank"):
        model, provider = r["model"], r["provider"] or ""
        head = model.split("/", 1)[0] if "/" in model else ""
        names = {model}
        if head:
            names.add(model.split("/", 1)[1])
        out.append({
            "combo": r["combo"], "rank": r["rank"], "model": model, "provider": provider,
            "names": names,
            "providers": {_norm_provider(p) for p in (provider, head) if p},
            "connection_ids": set(json.loads(r["connection_ids"] or "[]")),
        })
    return out


def _match(cand, provider, model, conn_ids):
    """Strict match: same model name and (shared connection or same provider)."""
    if model not in cand["names"]:
        return False
    if cand["connection_ids"]:
        return bool(cand["connection_ids"] & conn_ids)
    return _norm_provider(provider) in cand["providers"]


def _latest_quota(db, now):
    rows = db.execute(
        """SELECT q.provider, q.connection_id, q.window_key, q.ts, q.remaining_pct, q.exhausted, q.next_reset
           FROM quota q JOIN (SELECT MAX(src_id) m FROM quota GROUP BY provider, connection_id, window_key) l
             ON q.src_id = l.m""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["age_sec"] = int((now - _parse(d["ts"])).total_seconds()) if d["ts"] else None
        d["reset_in_sec"] = int((_parse(d["next_reset"]) - now).total_seconds()) if d["next_reset"] else None
        d["stale"] = d["age_sec"] is None or d["age_sec"] > STALE_QUOTA_SEC
        out.append(d)
    return out


def _quota_for(cand_like, quota_rows, bare_names):
    """Remaining quota for the connections a candidate may use; the window named like the model wins."""
    fresh = [q for q in quota_rows if not q["stale"] and (
        q["connection_id"] in cand_like["connection_ids"] if cand_like["connection_ids"]
        else _norm_provider(q["provider"]) in cand_like["providers"])]
    if not fresh:
        return None
    exact = [q for q in fresh if q["window_key"] in bare_names]
    pick = min(exact or fresh, key=lambda q: (q["remaining_pct"] is None, q["remaining_pct"]))
    return {"remaining_pct": pick["remaining_pct"], "window_key": pick["window_key"],
            "exhausted": bool(pick["exhausted"]), "next_reset": pick["next_reset"],
            "model_specific": bool(exact)}


def models(db, rng="24h", now=None):
    now = now or _utcnow()
    since = _since(rng, now)
    groups = {}
    for r in db.execute(
            """SELECT provider, model, connection_id, COUNT(*) n, COALESCE(SUM(ok),0) ok,
                 COALESCE(SUM(tok_in),0) tok_in, COALESCE(SUM(tok_cache_read),0) tok_cache_read,
                 COALESCE(SUM(tok_out),0) tok_out, COALESCE(SUM(tok_reasoning),0) tok_reasoning, MAX(ts) last_ts
               FROM usage WHERE ts >= ? GROUP BY provider, model, connection_id""", (since,)):
        g = groups.setdefault((r["provider"], r["model"]), {
            "provider": r["provider"], "model": r["model"], "requests": 0, "ok": 0, "tok_in": 0,
            "tok_cache_read": 0, "tok_out": 0, "tok_reasoning": 0, "last_used": None, "conns": set()})
        g["requests"] += r["n"]
        g["ok"] += r["ok"]
        for k in ("tok_in", "tok_cache_read", "tok_out", "tok_reasoning"):
            g[k] += r[k]
        if r["last_ts"] and (g["last_used"] is None or r["last_ts"] > g["last_used"]):
            g["last_used"] = r["last_ts"]
        if r["connection_id"]:
            g["conns"].add(r["connection_id"])
    lat = defaultdict(list)
    for r in db.execute("SELECT provider, model, latency_ms FROM usage WHERE ts >= ? AND ok = 1 AND latency_ms > 0",
                        (since,)):
        lat[(r["provider"], r["model"])].append(r["latency_ms"])
    cands = _load_candidates(db)
    quota_rows = _latest_quota(db, now)
    out = []
    for key, g in groups.items():
        vals = sorted(lat.get(key, []))
        badges = [{"combo": c["combo"], "rank": c["rank"]} for c in cands
                  if _match(c, g["provider"], g["model"], g["conns"])]
        probe = {"connection_ids": set(g["conns"]), "providers": {_norm_provider(g["provider"])}}
        quota = _quota_for(probe, quota_rows, {g["model"]})
        out.append({
            "provider": g["provider"], "model": g["model"], "requests": g["requests"],
            "ok_rate": _ratio(g["ok"], g["requests"]), "tok_in": g["tok_in"],
            "tok_cache_read": g["tok_cache_read"], "tok_out": g["tok_out"], "tok_reasoning": g["tok_reasoning"],
            "p50_latency_ms": _pct(vals, 50), "p95_latency_ms": _pct(vals, 95),
            "last_used": g["last_used"], "candidates": badges, "quota": quota,
        })
    out.sort(key=lambda m: -m["requests"])
    return {"range": rng, "models": out}


def quota(db, rng="24h", now=None):
    now = now or _utcnow()
    labels = {r["id"]: r["label"] for r in db.execute("SELECT id, label FROM connections")}
    latest = _latest_quota(db, now)
    start = now - timedelta(days=HISTORY_DAYS)
    width = HISTORY_DAYS / HISTORY_POINTS
    history = defaultdict(list)
    for r in db.execute(
            """SELECT provider, connection_id, window_key,
                 MIN(CAST((julianday(ts) - julianday(?1)) / ?2 AS INT), ?3) b, AVG(remaining_pct) v
               FROM quota WHERE ts >= ?4 GROUP BY provider, connection_id, window_key, b ORDER BY b""",
            (_iso(start), width, HISTORY_POINTS - 1, _iso(start))):
        history[(r["provider"], r["connection_id"], r["window_key"])].append([r["b"], round(r["v"], 1)])
    items = []
    for q in latest:
        items.append({
            "provider": q["provider"], "connection_id": q["connection_id"],
            "label": labels.get(q["connection_id"]) or q["connection_id"], "window_key": q["window_key"],
            "ts": q["ts"], "remaining_pct": q["remaining_pct"], "exhausted": bool(q["exhausted"]),
            "next_reset": q["next_reset"], "reset_in_sec": q["reset_in_sec"], "stale": q["stale"],
            "history": history.get((q["provider"], q["connection_id"], q["window_key"]), []),
        })
    items.sort(key=lambda i: (i["stale"], i["remaining_pct"] is None, i["remaining_pct"] or 0))
    return {"history_points": HISTORY_POINTS, "history_days": HISTORY_DAYS, "items": items}


def _connection_status(conns, now_iso):
    if not conns:
        return {"status": "unknown", "connections": 0, "labels": [], "rate_limited_until": None, "last_error": None}
    usable = [c for c in conns if c["active"]]
    limited = [c for c in usable if c["rate_limited_until"] and c["rate_limited_until"] > now_iso]
    if not usable:
        status = "inactive"
    elif len(limited) == len(usable):
        status = "limited"
    elif all(c["test_status"] not in (None, "active") for c in usable):
        status = "error"
    else:
        status = "ok"
    errs = [c for c in conns if c["last_error"]]
    last = max(errs, key=lambda c: c["last_error_at"] or "") if errs else None
    return {
        "status": status, "connections": len(conns), "active": len(usable),
        "labels": [c["label"] for c in conns][:3],
        "rate_limited_until": max((c["rate_limited_until"] for c in limited), default=None),
        "last_error": _clip(last["last_error"]) if last else None,
        "last_error_at": last["last_error_at"] if last else None,
    }


def candidates(db, rng="24h", now=None):
    now = now or _utcnow()
    since = _iso(now - RANGES["24h"])
    now_iso = _iso(now)
    usage_groups = defaultdict(lambda: {"requests": 0, "ok": 0, "tok_in": 0, "tok_cache_read": 0,
                                        "tok_out": 0, "tok_reasoning": 0})
    for r in db.execute(
            """SELECT provider, model, COALESCE(connection_id,'') cid, COUNT(*) n, COALESCE(SUM(ok),0) ok,
                 COALESCE(SUM(tok_in),0) tok_in, COALESCE(SUM(tok_cache_read),0) tok_cache_read,
                 COALESCE(SUM(tok_out),0) tok_out, COALESCE(SUM(tok_reasoning),0) tok_reasoning
               FROM usage WHERE ts >= ? GROUP BY provider, model, cid""", (since,)):
        g = usage_groups[(r["provider"], r["model"], r["cid"])]
        g["requests"] += r["n"]
        g["ok"] += r["ok"]
        for k in ("tok_in", "tok_cache_read", "tok_out", "tok_reasoning"):
            g[k] += r[k]
    lat = defaultdict(list)
    for r in db.execute("""SELECT provider, model, COALESCE(connection_id,'') cid, latency_ms FROM usage
                           WHERE ts >= ? AND ok = 1 AND latency_ms > 0""", (since,)):
        lat[(r["provider"], r["model"], r["cid"])].append(r["latency_ms"])
    conns = [dict(r) for r in db.execute("SELECT * FROM connections")]
    quota_rows = _latest_quota(db, now)

    def member(c):
        strict = [k for k in usage_groups if _match(c, k[0], k[1], {k[2]})]
        keys = strict or [k for k in usage_groups if k[1] in c["names"]]
        agg = {"requests": 0, "ok": 0, "tok_in": 0, "tok_cache_read": 0, "tok_out": 0, "tok_reasoning": 0}
        vals = []
        for k in keys:
            for f in agg:
                agg[f] += usage_groups[k][f]
            vals.extend(lat.get(k, []))
        vals.sort()
        if c["connection_ids"]:
            mine = [x for x in conns if x["id"] in c["connection_ids"]]
        else:
            mine = [x for x in conns if _norm_provider(x["provider"]) in c["providers"]]
        return {
            "rank": c["rank"], "model": c["model"], "provider": c["provider"],
            "requests_24h": agg["requests"], "ok_rate": _ratio(agg["ok"], agg["requests"]),
            "tok_in": agg["tok_in"], "tok_cache_read": agg["tok_cache_read"], "tok_out": agg["tok_out"],
            "tok_reasoning": agg["tok_reasoning"], "p50_latency_ms": _pct(vals, 50),
            "matched_by": ("strict" if strict else "model") if keys else None,
            "quota": _quota_for(c, quota_rows, c["names"]),
            "connection": _connection_status(mine, now_iso),
        }

    grouped = defaultdict(list)
    for c in _load_candidates(db):
        grouped[c["combo"]].append(member(c))
    picks = grouped.pop("autotune-picks", [])
    return {"combos": [{"name": name, "members": members} for name, members in grouped.items()], "picks": picks}


def errors(db, rng="24h", now=None):
    now = now or _utcnow()
    since = _since(rng, now)
    kinds = {}
    totals = db.execute("SELECT COUNT(*) FROM calls WHERE ts >= ?", (since,)).fetchone()[0]
    for r in db.execute(
            """SELECT status, error_text, provider, COUNT(*) n, MIN(ts) first_ts, MAX(ts) last_ts
               FROM calls WHERE ts >= ? AND status >= 400 GROUP BY status, error_text, provider""", (since,)):
        kind = classify(r["status"], r["error_text"], r["provider"])
        k = kinds.setdefault(kind.key, {
            "key": kind.key, "title": kind.title, "cause": kind.cause, "action": kind.action,
            "severity": kind.severity, "count": 0, "providers": Counter(), "texts": Counter(),
            "statuses": Counter(), "first_seen": r["first_ts"], "last_seen": r["last_ts"]})
        k["count"] += r["n"]
        k["providers"][r["provider"] or "?"] += r["n"]
        k["statuses"][r["status"]] += r["n"]
        k["texts"][r["error_text"] or f"(본문 없음, 상태 {r['status']})"] += r["n"]
        k["first_seen"] = min(k["first_seen"], r["first_ts"])
        k["last_seen"] = max(k["last_seen"], r["last_ts"])
    out = []
    for k in sorted(kinds.values(), key=lambda k: -k["count"]):
        out.append({
            **{f: k[f] for f in ("key", "title", "cause", "action", "severity", "count", "first_seen", "last_seen")},
            "providers": [{"provider": p, "count": n} for p, n in k["providers"].most_common()],
            "statuses": [{"status": s, "count": n} for s, n in k["statuses"].most_common(5)],
            "examples": [{"text": _clip(t), "count": n} for t, n in k["texts"].most_common(3)],
        })
    daily = [{"day": r["d"], "count": r["n"]} for r in db.execute(
        """SELECT strftime('%Y-%m-%d', ts, '+9 hours') d, COUNT(*) n FROM calls
           WHERE ts >= ? AND status >= 400 GROUP BY d ORDER BY d""", (since,))]
    attempt_kinds = {}
    for r in db.execute(
            """SELECT status, error_code, provider, COUNT(*) n FROM usage
               WHERE ts >= ? AND ok = 0 GROUP BY status, error_code, provider""", (since,)):
        kind = classify(r["status"], r["error_code"], r["provider"])
        a = attempt_kinds.setdefault(kind.key, {"key": kind.key, "title": kind.title,
                                                "severity": kind.severity, "count": 0})
        a["count"] += r["n"]
    attempts = sorted(attempt_kinds.values(), key=lambda a: -a["count"])
    since_row = db.execute("SELECT MIN(ts) FROM calls").fetchone()[0]
    return {
        "range": rng, "total": sum(k["count"] for k in out), "calls_total": totals,
        "calls_since": since_row, "kinds": out, "daily": daily,
        "attempt_failures": {"total": sum(a["count"] for a in attempts), "kinds": attempts},
    }


def connections(db, rng="24h", now=None):
    rows = [dict(r) for r in db.execute(
        """SELECT id, provider, label, active, test_status, error_code, last_error, last_error_at,
             rate_limited_until, last_used_at FROM connections ORDER BY provider, label""")]
    for r in rows:
        r["active"] = bool(r["active"])
        if r["last_error"]:
            r["last_error"] = _clip(r["last_error"])
    return {"connections": rows}


ROUTES = {
    "/api/overview": overview, "/api/timeseries": timeseries, "/api/models": models, "/api/quota": quota,
    "/api/candidates": candidates, "/api/errors": errors, "/api/connections": connections,
}


def handle(db_path, path, params, now=None):
    fn = ROUTES.get(path)
    if fn is None:
        return 404, {"error": "not found"}
    rng = params.get("range", "24h")
    if rng not in RANGES:
        return 400, {"error": "range must be one of " + ", ".join(RANGES)}
    db = connect(db_path)
    try:
        return 200, fn(db, rng, now)
    finally:
        db.close()
