"""Before/after numbers for one combo, from OmniRoute's call log and from Hermes-shaped round trips (2026-10-30).

  python measure.py logs  --since 2026-10-30T09:00 [--until ...] [--combo hermes-fast-agent]
  python measure.py bench --runs 10 [--combo hermes-fast-agent] [--gap 20]
  python measure.py verify reports/verify/2026-10-30-1900.md ...   (round-trip seconds from verify_cycle reports)
  python measure.py hermes --since "2026-10-30 16:00"   (Hermes agent.log, local time)
  python measure.py trial mistral/mistral-small-latest llm7/...   (round trip per model, providers in parallel)

`logs` groups call_logs rows by correlation_id: one Hermes request = all its attempts (fallbacks included);
latency = first attempt start to last attempt end. `bench` sends verify_cycle's round trip (20k-token prompt,
real tool call, tool result, streamed Korean answer, 45 s limit) to the combo itself, so OmniRoute's routing
is part of what is timed. Times are UTC like call_logs.timestamp.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path.home() / ".omniroute" / "storage.sqlite"
QUOTA = (429, 402)


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "")[:23])


def from_logs(combo: str, since: str, until: str | None, min_tokens: int) -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    rows = con.execute("""select timestamp, coalesce(correlation_id, id), requested_model, status, duration,
                                 coalesce(tokens_in,0), coalesce(connection_id,''), coalesce(error_summary,'')
                          from call_logs where combo_name = ? and timestamp >= ? and timestamp < ?
                          order by timestamp""", (combo, since, until or "9999")).fetchall()
    reqs = defaultdict(list)
    for r in rows:
        reqs[r[1]].append(r)
    lat_ok, outcomes = [], Counter()
    # min_tokens keeps agent-sized turns only; a request with no answer has no token count and always stays
    reqs = {k: v for k, v in reqs.items() if max(a[5] for a in v) >= min_tokens or all(a[3] != 200 for a in v)}
    rows = [a for v in reqs.values() for a in v]
    for attempts in reqs.values():
        start = min(ts(a[0]) for a in attempts)
        end = max(ts(a[0]) + timedelta(milliseconds=a[4] or 0) for a in attempts)
        ok = any(a[3] == 200 for a in attempts)
        outcomes["ok" if ok else f"fail {attempts[-1][3]}"] += 1
        if ok:
            lat_ok.append((end - start).total_seconds())
    status = Counter(r[3] for r in rows)
    return {
        "combo": combo, "since": since, "until": until, "min_tokens": min_tokens, "requests": len(reqs), "attempts": len(rows),
        "success_rate": round(outcomes["ok"] / len(reqs), 3) if reqs else None,
        "p50_s": pct(lat_ok, 50), "p95_s": pct(lat_ok, 95),
        "quota_errors": sum(status[c] for c in QUOTA), "status": dict(status), "outcomes": dict(outcomes),
        "tokens_in_M": round(sum(r[5] for r in rows) / 1e6, 2),
        "by_model": {f"{m} {s} {c[:8]}": n for (m, s, c), n in
                     Counter((r[2], r[3], r[6]) for r in rows).most_common(12)},
    }


def bench(combo: str, runs: int, gap: float) -> dict:
    import verify_cycle as vc
    results = []
    for i in range(runs):
        r = vc.round_trip(combo)
        results.append(r)
        print(f"run {i + 1}: {'OK' if r['ok'] else 'FAIL'} {r['secs']:.1f}s {' '.join(r['detail'].split())[:90]}", flush=True)
        if i + 1 < runs:
            time.sleep(gap)
    ok = [r["secs"] for r in results if r["ok"]]
    return {"combo": combo, "runs": runs, "ok": len(ok), "success_rate": round(len(ok) / runs, 3),
            "p50_s": pct(ok, 50), "p95_s": pct(ok, 95),
            "quota_errors": sum(1 for r in results if re.search(r"HTTP (429|402)", r["detail"])),
            "results": [{k: (round(v, 1) if k == "secs" else v) for k, v in r.items()} for r in results]}


HERMES_LOGS = Path.home() / "AppData" / "Local" / "hermes" / "logs"
CALL = re.compile(r"^(\S+ \S+),\d+ .*API call #\d+: model=(\S+) provider=(\S+) .*latency=([\d.]+)s")
FAIL = re.compile(r"^(\S+ \S+),\d+ .*API call failed .*error_type=(\S+) .*provider=(\S+) base_url=(\S+) model=(\S+)(?: summary=HTTP (\d+))?")
FALLBACK = re.compile(r"^(\S+ \S+),\d+ .*Fallback activated: (\S+) → (\S+)")


def from_hermes(since: str, until: str | None) -> dict:
    """What Hermes itself saw (agent.log*, local time): per-call latency, failed calls, fallback activations."""
    lat, fails, fallbacks = defaultdict(list), Counter(), Counter()
    for f in sorted(HERMES_LOGS.glob("agent.log*")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line[:19]
            if t < since or (until and t >= until):
                continue
            if m := CALL.match(line):
                lat[f"{m[2]} ({m[3]})"].append(float(m[4]))
            elif m := FAIL.match(line):
                fails[f"{m[5]} @ {m[4]} {m[2]} {m[6] or ''}".strip()] += 1
            elif m := FALLBACK.match(line):
                fallbacks[f"{m[2]} -> {m[3]}"] += 1
    return {"since_local": since, "until_local": until,
            "calls": {k: {"n": len(v), "p50_s": pct(v, 50), "p95_s": pct(v, 95)} for k, v in sorted(lat.items())},
            "failed_calls": dict(fails.most_common()), "fallbacks": dict(fallbacks)}


def trial(models: list[str]) -> dict:
    """verify_cycle's round trip per model; providers run in parallel, a provider's models in order until one passes."""
    import verify_cycle as vc
    from concurrent.futures import ThreadPoolExecutor
    by_prov = defaultdict(list)
    for m in models:
        by_prov[m.split("/")[0]].append(m)

    def run(ms):
        out = []
        for m in ms:
            r = vc.round_trip(m)
            out.append((m, r))
            print(f"{m}: {'OK' if r['ok'] else 'FAIL'} {r['secs']:.1f}s {' '.join(r['detail'].split())[:100]}", flush=True)
            if r["ok"]:
                break
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [x for res in ex.map(run, by_prov.values()) for x in res]
    return {"passed": [m for m, r in rows if r["ok"]],
            "results": {m: {"ok": r["ok"], "secs": round(r["secs"], 1), "detail": r["detail"]} for m, r in rows}}


def from_verify(paths: list[str]) -> dict:
    per_model, failures, quota = defaultdict(list), Counter(), 0
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\| (?:\d+|combo) \| (\S+) \| (OK|FAIL|transient) \| ([\d.]+) \| (.*) \|$", line)
            if not m:
                continue
            model, verdict, secs, detail = m.groups()
            if verdict == "OK":
                per_model[model].append(float(secs))
            else:
                failures[model] += 1
                quota += bool(re.search(r"HTTP (429|402)", detail))
    combo = per_model.pop("hermes-fast-agent", [])
    combo_fail = failures.pop("hermes-fast-agent", 0)
    allsecs = [s for xs in per_model.values() for s in xs]
    total = len(allsecs) + sum(failures.values())
    return {"reports": len(paths), "combo_round_trips": len(combo) + combo_fail, "combo_ok": len(combo),
            "combo_p50_s": pct(combo, 50), "combo_p95_s": pct(combo, 95),
            "round_trips": total, "ok": len(allsecs),
            "success_rate": round(len(allsecs) / total, 3) if total else None,
            "p50_s": pct(allsecs, 50), "p95_s": pct(allsecs, 95), "quota_errors": quota,
            "passed_models": sorted(per_model), "failures": dict(failures),
            "per_model_median_s": {m: round(statistics.median(xs), 1) for m, xs in sorted(per_model.items())}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["logs", "bench", "verify", "hermes", "trial"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--combo", default="hermes-fast-agent")
    ap.add_argument("--since", default=(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).isoformat(timespec="minutes"))
    ap.add_argument("--until")
    ap.add_argument("--min-tokens", type=int, default=0)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--gap", type=float, default=20)
    a = ap.parse_args()
    out = {"logs": lambda: from_logs(a.combo, a.since, a.until, a.min_tokens),
           "bench": lambda: bench(a.combo, a.runs, a.gap),
           "verify": lambda: from_verify(a.paths),
           "hermes": lambda: from_hermes(a.since, a.until),
           "trial": lambda: trial(a.paths)}[a.mode]()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
