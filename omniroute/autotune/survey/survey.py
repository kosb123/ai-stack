"""Candidate survey for the Hermes and Hindsight combos (2026-10-30): real calls, not catalog listings.

  python survey/survey.py select            -> survey/plan.json (which models get called, and why the rest do not)
  python survey/survey.py run [--only p,..]  -> survey/results.jsonl (appends; models already done are skipped)
  python survey/survey.py hindsight          -> runs hs_probe.py inside the hindsight container for Hermes passers
  python survey/survey.py report             -> survey/report.md

Per model: ping (tiny chat) -> Hermes round trip (20k-token prompt, both turns STREAMED like Hermes, real tool call,
tool result, Korean answer, 45 s) -> Hindsight probes with Hindsight's own client (survey/hs_probe.py).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("autotune", HERE.parent / "autotune.py")
at = importlib.util.module_from_spec(spec)
spec.loader.exec_module(at)
spec = importlib.util.spec_from_file_location("verify_cycle", HERE.parent / "verify_cycle.py")
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)

PLAN, RESULTS, HS_RESULTS = HERE / "plan.json", HERE / "results.jsonl", HERE / "hindsight.jsonl"
PER_PROVIDER = 6
ACCOUNT_BROKEN_PER_PROVIDER = 2  # an account-wide 401/402/403 only needs a second model to confirm it
NOT_LLM = {"context7", "exa-search", "serper-search", "tavily-search", "ollama-search", "jina-ai"}
NON_CHAT = re.compile(r"embed|rerank|whisper|tts|speech|audio|image|dall-e|flux|stable-diffusion|sdxl|veo|sora|"
                      r"video|moderation|realtime|transcribe|ocr|guard|orpheus|playai|lyria|imagen", re.I)
VARIANT = re.compile(r"-(xhigh|high|medium|low|max|minimal|thinking|none|fast|1m|500k|batch|rp|tee)(?=-|$)")
ACCOUNT_WIDE = re.compile(r"fund|balance|credit|budget|unauthor|invalid.*key|expired|revoked|authentication|subscription", re.I)


def variant_key(prov: str, mid: str) -> str:
    """Models that differ only by effort/context/batch suffix share a key (one is enough to learn the model works).
    agy/antigravity keep every name: each Antigravity model id is its own quota bucket."""
    if prov in ("agy", "antigravity"):
        return mid
    name = mid.lower().split("/", 1)[1]
    while (new := VARIANT.sub("", name)) != name:
        name = new
    return at.norm(name)
FREE = re.compile(r"(:|-)free\b|free$", re.I)


def user_exclusions() -> dict[str, str]:
    sel = json.loads((HERE.parent / "selected_models.json").read_text(encoding="utf-8"))
    out = {p: "user: not_used on the picker page" for p in sel.get("not_used", [])}
    out.update({p: "autotune exclude_providers (Claude subscription)" for p in at.CONFIG["exclude_providers"]})
    out.update({p: "search/embedding service, not a chat model" for p in NOT_LLM})
    return out


def recent_ok(con) -> Counter:
    since = (at.now_utc() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    return Counter(dict(con.execute("select requested_model, count(*) from call_logs where status = 200 and timestamp >= ? "
                                    "group by requested_model", (since,)).fetchall()))


def last_account_error(con, provider: str) -> str | None:
    row = con.execute("""select status, substr(coalesce(error_summary,''),1,120) from call_logs
                         where requested_model like ? and timestamp >= ? order by timestamp desc limit 1""",
                      (provider + "/%", (at.now_utc() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"))).fetchone()
    return f"{row[0]} {row[1]}" if row and row[0] in (401, 402, 403) and ACCOUNT_WIDE.search(row[1]) else None


def select() -> dict:
    con = at.db()
    active = sorted({p for (p,) in con.execute("select provider from provider_connections where is_active = 1")})
    inactive = sorted({p for (p,) in con.execute("select provider from provider_connections where is_active = 0")} - set(active))
    excl = user_exclusions()
    ok7 = recent_ok(con)
    cat = json.loads((HERE / "catalog.json").read_text())
    plan = {"providers": {}, "excluded_providers": {}, "inactive_providers": inactive}
    for prov in active:
        if prov in excl:
            plan["excluded_providers"][prov] = excl[prov]
            continue
        rows, skipped = [], Counter()
        for m in cat:
            mid = m["id"]
            if not mid.startswith(prov + "/"):
                continue
            ctx = m.get("context_length") or 0
            if (m.get("capabilities") or {}).get("tool_calling") is False:
                skipped["no tool calling"] += 1
            elif NON_CHAT.search(mid) or "text" not in (m.get("output_modalities") or ["text"]):
                skipped["not a chat model"] += 1
            elif ctx and ctx < at.CONFIG["min_context"]:
                skipped[f"context < {at.CONFIG['min_context'] // 1000}k"] += 1
            elif (at.tier_quality(mid) or 0.5) < at.CONFIG["min_quality"]:
                skipped["quality tier < 0.40 (tiny model)"] += 1
            else:
                rows.append(mid)
        best: dict[str, tuple] = {}
        for mid in rows:
            key = variant_key(prov, mid)
            rank = (ok7.get(mid, 0) > 0, ok7.get(mid, 0), bool(FREE.search(mid)), at.tier_quality(mid) or 0.5)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, mid)
        ordered = [mid for _, mid in sorted(best.values(), reverse=True)]
        broken = last_account_error(con, prov)
        cap = ACCOUNT_BROKEN_PER_PROVIDER if broken else PER_PROVIDER
        if prov in ("agy", "antigravity"):
            cap = 30  # the paid Google AI Pro pool: every distinct model is worth knowing
        plan["providers"][prov] = {"test": ordered[:cap], "catalog_eligible": len(ordered),
                                   "not_tested_beyond_cap": len(ordered) - min(cap, len(ordered)),
                                   "skipped": dict(skipped), "recent_account_error": broken}
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v["test"]) for v in plan["providers"].values())
    print(f"{len(plan['providers'])} providers, {n} models to call -> {PLAN}")
    return plan


def classify(status: int, text: str) -> str:
    t = text.lower()
    if status == 0 and ("timed out" in t or "timeout" in t):
        return "timeout"
    if status in (401,) or re.search(r"unauthor|invalid.*(key|token)|re-?auth|refresh token|expired", t):
        return "auth"
    if status in (402, 429) or re.search(r"quota|exhaust|insufficient|credit|balance|rate.?limit|out of|daily|"
                                         r"too many|limit reached|subscri|plan", t):
        return "quota/plan"
    if status == 403:
        return "forbidden (account/region/plan)"
    if status in (400, 404, 410, 422) or re.search(r"not (found|supported|exist)|unknown model|invalid model|retired", t):
        return "model/request unsupported"
    if status >= 500 or status == 0:
        return "upstream/network error"
    return f"HTTP {status}"


def ping(model: str) -> dict:
    status, data, secs = at.api("POST", "/v1/chat/completions",
                                {"model": model, "max_tokens": 40, "stream": False,
                                 "messages": [{"role": "user", "content": "Reply with the single word OK."}]},
                                timeout=60, headers={"x-omniroute-no-memory": "true"})
    content = ""
    if status == 200:
        msg = ((data or {}).get("choices") or [{}])[0].get("message") or {}
        content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    ok = status == 200 and bool(content)
    err = "" if ok else (str((data or {}).get("error", ""))[:200] if status != 200 else "empty answer")
    return {"ok": ok, "status": status, "secs": round(secs, 1), "err": err,
            "cause": "" if ok else classify(status, err)}


def stream_turn(body: dict, timeout: int) -> tuple[str, list[dict]]:
    """One streamed chat turn like Hermes' openai SDK call: returns (text, tool_calls assembled from deltas)."""
    req = urllib.request.Request(at.BASE + "/v1/chat/completions", data=json.dumps({**body, "stream": True}).encode(),
                                 headers={"Authorization": f"Bearer {at.os.environ.get('OMNIROUTE_API_KEY', '')}",
                                          "Content-Type": "application/json", "User-Agent": at.CLIENT_USER_AGENT,
                                          "x-omniroute-no-memory": "true"})
    text, calls = "", defaultdict(lambda: {"id": "", "name": "", "arguments": ""})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                delta = json.loads(line[5:])["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError):
                continue
            text += delta.get("content") or ""
            for tc in delta.get("tool_calls") or []:
                c = calls[tc.get("index", 0)]
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["name"] = fn.get("name") or c["name"]
                c["arguments"] += fn.get("arguments") or ""
    return text, [calls[i] for i in sorted(calls)]


def hermes(model: str) -> dict:
    """verify_cycle's round trip with both turns streamed (Hermes streams every turn, tool calls included)."""
    t0 = time.perf_counter()
    msgs = [{"role": "system", "content": vc.PAD}, {"role": "user", "content": vc.USER}]
    try:
        text, calls = stream_turn({"model": model, "messages": msgs, "tools": vc.TOOLS, "max_tokens": 600}, 120)
        calls = [c for c in calls if c["name"] in ("read_file", "list_dir")]
        if not calls:
            return {"ok": False, "secs": round(time.perf_counter() - t0, 1), "cause": "tool call missing/as text",
                    "detail": f"turn 1: no streamed tool call ({text[:80]!r})"}
        json.loads(calls[0]["arguments"] or "{}")
        tc = {"id": calls[0]["id"] or "call_0", "type": "function",
              "function": {"name": calls[0]["name"], "arguments": calls[0]["arguments"] or "{}"}}
        msgs += [{"role": "assistant", "content": text or "", "tool_calls": [tc]},
                 {"role": "tool", "tool_call_id": tc["id"], "content": vc.TOOL_RESULT}]
        text, _ = stream_turn({"model": model, "messages": msgs, "tools": vc.TOOLS, "max_tokens": 600}, 120)
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", "ignore")
        return {"ok": False, "secs": round(time.perf_counter() - t0, 1), "cause": classify(e.code, body),
                "detail": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"ok": False, "secs": round(time.perf_counter() - t0, 1), "cause": classify(0, str(e)),
                "detail": f"{type(e).__name__}: {str(e)[:160]}"}
    secs = round(time.perf_counter() - t0, 1)
    if not any("가" <= ch <= "힣" for ch in text):
        return {"ok": False, "secs": secs, "cause": "bad answer", "detail": f"turn 2: not Korean ({text[:60]!r})"}
    if "4" not in text and "네" not in text:
        return {"ok": False, "secs": secs, "cause": "bad answer", "detail": f"turn 2: ignored tool result ({text[:60]!r})"}
    if secs > at.CONFIG["max_latency_s"]:
        return {"ok": False, "secs": secs, "cause": "too slow", "detail": f"{secs}s > {at.CONFIG['max_latency_s']}s"}
    return {"ok": True, "secs": secs, "cause": "", "detail": text.strip()[:60]}


def done_models(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(l)["model"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def run(only: set[str] | None) -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    done = done_models(RESULTS)
    at.budgets = at.free_budgets(at.db()) if hasattr(at, "free_budgets") else {}

    def per_provider(prov: str, models: list[str]) -> None:
        account_dead = 0
        for m in models:
            if m in done:
                continue
            rec = {"model": m, "provider": prov, "ts": at.now_utc().isoformat()}
            if account_dead >= 2:
                rec["ping"] = {"ok": False, "status": None, "secs": 0, "err": "skipped: account-wide error on 2 models",
                               "cause": "account (inferred)"}
            else:
                rec["ping"] = ping(m)
                if rec["ping"]["status"] in (401, 402, 403):
                    account_dead += 1
            if rec["ping"]["ok"]:
                if at.probe_affordable(prov):
                    rec["hermes"] = hermes(m)
                    if not rec["hermes"]["ok"] and rec["hermes"]["cause"] in ("upstream/network error", "quota/plan"):
                        time.sleep(20)
                        rec["hermes_retry"] = hermes(m)
                else:
                    rec["hermes"] = {"ok": None, "cause": "not checked: small free budget", "detail": ""}
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            h = rec.get("hermes_retry") or rec.get("hermes") or {}
            print(f"{m}: ping {'OK' if rec['ping']['ok'] else rec['ping']['cause']} "
                  f"hermes {h.get('ok')} {h.get('secs', '')}s {h.get('cause', '')}", flush=True)

    jobs = {p: v["test"] for p, v in plan["providers"].items() if not only or p in only}
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda kv: per_provider(*kv), jobs.items()))


def hermes_ok(rec: dict) -> bool:
    return bool((rec.get("hermes_retry") or {}).get("ok") or (rec.get("hermes") or {}).get("ok"))


def hindsight(models: list[str] | None) -> None:
    recs = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = models or [r["model"] for r in recs if r["ping"]["ok"]]
    todo = [m for m in todo if m not in done_models(HS_RESULTS)]
    print(f"hindsight probes for {len(todo)} models", flush=True)
    subprocess.run(["docker", "cp", str(HERE / "hs_probe.py"), "hindsight:/tmp/hs_probe.py"], check=True)
    p = subprocess.Popen(["docker", "exec", "-i", "-w", "/app/api", "hindsight", "/app/api/.venv/bin/python",
                          "/tmp/hs_probe.py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    p.stdin.write("\n".join(todo) + "\n")
    p.stdin.close()
    with HS_RESULTS.open("a", encoding="utf-8") as f:
        for line in p.stdout:
            if line.startswith("{"):
                f.write(line)
                f.flush()
                r = json.loads(line)
                print(f"{r['model']}: retain {r['retain']['ok']} {r['retain']['secs']}s | reflect {r['reflect']['ok']} "
                      f"{r['reflect']['secs']}s", flush=True)
    p.wait()


def main(argv: list[str]) -> None:
    cmd = argv[0]
    if cmd == "select":
        select()
    elif cmd == "run":
        only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
        run(only)
    elif cmd == "hindsight":
        hindsight(argv[1:] or None)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
