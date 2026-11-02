"""Three-hourly verification of the hermes-fast-agent combo (2026-10-23).

1. Run `autotune.py tune` 5 times in a row and compare the selections (is the ranking stable?).
2. Send every final candidate a Hermes-shaped round trip through OmniRoute: ~20k-token system prompt + tools
   -> a real tool call; then the tool result -> a streamed Korean answer that uses it. The autotune admission
   probe only checks the first tool call; Hermes also needs the tool-result turn and streaming to work.
3. A candidate that fails the round trip twice (rate limits excepted) is written to state.json as a failed
   probe, and one more tune drops it.
4. Write reports/verify/YYYY-MM-DD-HHMM.md and reports/verify-latest.md for the reviewing agent.

Usage: python verify_cycle.py           (scheduled task OmniAutotune-Verify, every 3 hours)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("autotune", ROOT / "autotune.py")
at = importlib.util.module_from_spec(spec)
spec.loader.exec_module(at)

RUNS = 5
PAD = ("You are Hermes Agent. Reference notes for the session follow. The operator keeps kanban notes, blog "
       "campaign schedules and server logs here; use tools for anything about files. ") * 330  # ~20k tokens
TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List a directory",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]
USER = "C:/notes/todo.txt 파일을 읽고 거기 적힌 할 일 개수를 한국어로 알려줘."
TOOL_RESULT = "1. 블로그 글 쓰기\n2. 캠페인 신청\n3. 서버 점검\n4. 백업 확인"


def chat(body: dict, stream: bool, timeout: int = 120):
    req = urllib.request.Request(at.BASE + "/v1/chat/completions", data=json.dumps({**body, "stream": stream}).encode(),
                                 headers={"Authorization": f"Bearer {os.environ.get('OMNIROUTE_API_KEY', '')}",
                                          "Content-Type": "application/json",
                                          # 2026-10-28: real Hermes requests get no OmniRoute memory_* tools
                                          # (0 of 458 logged); without this the check saw 4 extra tools
                                          "x-omniroute-no-memory": "true"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if not stream:
            return json.loads(r.read())
        text = ""
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                try:
                    text += (json.loads(line[5:])["choices"][0].get("delta") or {}).get("content") or ""
                except (ValueError, KeyError, IndexError):
                    pass
        return text


def round_trip(model: str) -> dict:
    """{'ok', 'transient', 'secs', 'detail'} for one Hermes-shaped tool round trip."""
    t0 = time.perf_counter()
    # 2026-10-29 user: the pinned model may be slow; give it as long as Hermes waits (stale_timeout_seconds 360)
    wait = 360 if at.is_pinned(model) else 120
    msgs = [{"role": "system", "content": PAD}, {"role": "user", "content": USER}]
    effort = at.CONFIG["probe_effort"]
    try:
        msg = chat({"model": model, "messages": msgs, "tools": TOOLS, "max_tokens": 600, "reasoning_effort": effort},
                   False, wait)["choices"][0]["message"]
        calls = [c for c in msg.get("tool_calls") or [] if (c.get("function") or {}).get("name") in ("read_file", "list_dir")]
        if not calls:
            return {"ok": False, "transient": False, "secs": time.perf_counter() - t0,
                    "detail": f"turn 1: no tool call ({(msg.get('content') or '')[:60]!r})"}
        json.loads(calls[0]["function"].get("arguments") or "{}")
        msgs += [{"role": "assistant", "content": msg.get("content") or "", "tool_calls": [calls[0]]},
                 {"role": "tool", "tool_call_id": calls[0].get("id") or "call_0", "content": TOOL_RESULT}]
        text = chat({"model": model, "messages": msgs, "tools": TOOLS, "max_tokens": 600, "reasoning_effort": effort},
                    True, wait)
    except urllib.error.HTTPError as e:
        return {"ok": False, "transient": e.code in (429, 503), "secs": time.perf_counter() - t0,
                "detail": f"HTTP {e.code}: {e.read()[:120].decode('utf-8', 'ignore')}"}
    except Exception as e:  # timeouts, bad tool arguments, broken streams
        # a 120 s silence stalls a Hermes conversation just the same, so a timeout is a real failure
        return {"ok": False, "transient": False, "secs": time.perf_counter() - t0,
                "detail": f"{type(e).__name__}: {str(e)[:120]}"}
    secs = time.perf_counter() - t0
    if not any("\uac00" <= ch <= "\ud7a3" for ch in text):
        return {"ok": False, "transient": False, "secs": secs, "detail": f"turn 2: not Korean ({text[:60]!r})"}
    if "4" not in text and "네" not in text:
        return {"ok": False, "transient": False, "secs": secs, "detail": f"turn 2: ignored the tool result ({text[:60]!r})"}
    # 2026-10-23: a correct but very slow answer passed (glm-4.5-flash took 128 s); Hermes waits that long on
    # every turn, so beyond autotune's own latency limit it counts as a failure
    if secs > at.CONFIG["max_latency_s"] and not at.is_pinned(model):
        return {"ok": False, "transient": False, "secs": secs, "detail": f"too slow ({secs:.0f}s > {at.CONFIG['max_latency_s']}s)"}
    return {"ok": True, "transient": False, "secs": secs, "detail": text.strip()[:60]}


def tune_once() -> str:
    res = subprocess.run([sys.executable, str(ROOT / "autotune.py"), "tune"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=1800)
    return (res.stdout or res.stderr).strip().splitlines()[-1] if (res.stdout or res.stderr).strip() else f"exit {res.returncode}"


def probe_lines(report: str) -> list[str]:
    """Admission/revival probe result lines of the latest tune report."""
    out, keep = [], False
    for line in report.splitlines():
        if line.startswith("## "):
            keep = line.startswith(("## Admission", "## Revival"))
        elif keep and line.startswith("- "):
            out.append(line[:160])
    return out


def main() -> int:
    started = datetime.now(at.KST)
    lines = [f"# hermes-fast-agent verification — {started:%Y-%m-%d %H:%M} (KST)", ""]
    selections, anomalies = [], []

    lines += ["## 1. Five tune runs", "", "| run | result | candidates in order |", "|---|---|---|"]
    probes = []
    for i in range(1, RUNS + 1):
        result = tune_once()
        order = [m["model"] for m in at.combo_get(at.CONFIG["combo_name"])["models"]]
        selections.append(order)
        probes += [f"run {i}: {p}" for p in probe_lines((ROOT / "reports" / "latest.md").read_text(encoding="utf-8"))]
        lines.append(f"| {i} | {result.split(';')[0]} | {' → '.join(order)} |")
    changed = [i + 1 for i in range(1, RUNS) if selections[i] != selections[i - 1]]
    if changed and changed[-1] >= RUNS - 1:
        anomalies.append(f"selection still changing in the last runs ({', '.join(map(str, changed))})")
    lines += ["", f"- selection changed in runs: {', '.join(map(str, changed)) or 'none'}", "", "### Probe results", ""]
    lines += [f"- {p}" for p in probes] or ["- (none)"]

    final = at.combo_get(at.CONFIG["combo_name"])["models"]
    if len(final) < at.CONFIG["min_size"]:
        anomalies.append(f"only {len(final)} candidates")
    if len(final) > 1 and final[0]["providerId"] == final[1]["providerId"]:
        anomalies.append(f"#1 and #2 share provider {final[0]['providerId']}")

    lines += ["", "## 2. Hermes-shaped round trip per final candidate", "",
              "| # | model | result | seconds | detail |", "|---|---|---|---|---|"]
    evicted = []
    for i, m in enumerate(final, 1):
        r = round_trip(m["model"])
        if not r["ok"] and not r["transient"]:
            time.sleep(30)
            r = round_trip(m["model"])  # one retry: evict only on a repeated, non-rate-limit failure
            if not r["ok"] and not r["transient"]:
                if at.is_pinned(m["model"]):
                    anomalies.append(f"pinned {m['model']} failed the round trip twice (kept: pinned by user)")
                else:
                    evicted.append((m, r))
        if i == 1 and r["ok"] and r["secs"] > 20 and not at.is_pinned(m["model"]):
            anomalies.append(f"#1 round trip slow ({r['secs']:.1f}s)")
        verdict = "OK" if r["ok"] else ("transient" if r["transient"] else "FAIL")
        detail = " ".join(r["detail"].split()).replace("|", "/")
        lines.append(f"| {i} | {m['model']} | {verdict} | {r['secs']:.1f} | {detail} |")

    # 2026-10-30: the rows above call each model directly; Hermes calls the combo, whose routing (priority,
    # hedging, lockouts) decides what it waits for
    r = round_trip(at.CONFIG["combo_name"])
    if not r["ok"] and not r["transient"]:
        anomalies.append(f"combo {at.CONFIG['combo_name']} round trip failed: {r['detail'][:80]}")
    detail = " ".join(r["detail"].split()).replace("|", "/")
    lines.append(f"| combo | {at.CONFIG['combo_name']} | {'OK' if r['ok'] else ('transient' if r['transient'] else 'FAIL')} "
                 f"| {r['secs']:.1f} | {detail} |")

    if evicted:
        with at.Lock():
            state = at.load_state()
            for m, r in evicted:
                state["probes"][m["model"]] = {"ts": at.now_utc().isoformat(), "ok": False, "status": 0,
                                               "latency": round(r["secs"], 1), "err": f"verify round trip: {r['detail']}"[:160],
                                               "provider": m["providerId"]}
            at.save_state(state)
        anomalies.append("round trip failed twice: " + ", ".join(m["model"] for m, _ in evicted))
        after = tune_once()
        lines += ["", f"- recorded {len(evicted)} round-trip failure(s) as failed probes; re-tune: {after}",
                  "- combo now: " + " → ".join(m["model"] for m in at.combo_get(at.CONFIG["combo_name"])["models"])]

    lines += ["", "## 3. Anomalies for review", ""] + ([f"- {a}" for a in anomalies] or ["- none"])
    lines += ["", f"_finished {datetime.now(at.KST):%H:%M:%S}_"]
    folder = ROOT / "reports" / "verify"
    folder.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    (folder / f"{started:%Y-%m-%d-%H%M}.md").write_text(text, encoding="utf-8")
    (ROOT / "reports" / "verify-latest.md").write_text(text, encoding="utf-8")
    at.log(f"verify: {len(anomalies)} anomalies; evicted {[m['model'] for m, _ in evicted]}", "verify.log")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        at.log("ERROR " + traceback.format_exc(), "verify.log")
        raise
