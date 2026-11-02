"""Model sweep across every connected OmniRoute provider (2026-10-30): which models really work for Hermes and Hindsight.

  python sweep.py                      # candidates chosen per provider (picks, 7-day successes, best catalog models)
  python sweep.py --models a/b c/d     # only these
  python sweep.py --providers kiro agy # only these providers

Stage 1 (cheap, ~300 tokens): one tool + tool_choice auto. A non-200 here is classified and the model stops.
Stage 2 on a 200 (the same checks autotune and verify_cycle use, so the verdicts match what the tuner admits):
  hermes     verify_cycle.round_trip: ~20k-token prompt, real tool call, tool result, streamed Korean answer, <= 45 s
  structured autotune.probe_structured: Hindsight retain shape, strict json_schema array, non-streaming, <= 90 s
  required   autotune.probe_required_tool: Hindsight reflect shape, one tool + tool_choice="required"
Hermes-fit = hermes ok. Hindsight-fit = structured ok and required ok.
Writes reports/sweep/<stamp>.json and .md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("verify_cycle", ROOT / "verify_cycle.py")
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)
at = vc.at

NOT_LLM = {"context7", "exa-search", "serper-search", "tavily-search", "ollama-search", "jina-ai"}
SUBSCRIPTION = set(at.CONFIG["exclude_providers"])
NON_CHAT = re.compile(r"embed|whisper|tts|rerank|guard|moderation|image|video|ocr|orpheus|voxtral|flux|transcribe|"
                      r"vision|translate|fim|audio|speech|kokoro|piper|diffusion|-vl\b", re.I)
EFFORT_VARIANT = re.compile(r"-(none|low|medium|high|xhigh|max|ultra|fast)$")
FREE_AGGREGATORS = {"cline", "kilocode", "openrouter", "opencode", "requesty", "anyapi", "electronhub", "eurouter",
                    "fastrouter", "cheaperinference", "llm7", "free-ai", "aihorde"}
PER_PROVIDER = 5

CAUSES = [  # (status pattern, message pattern, cause); first match wins
    (r"401", r"", "auth: re-login / key invalid"),
    (r"402", r"", "credits or payment exhausted"),
    (r"403", r"plan|pro\b|subscri|upgrade|tier", "plan does not include the model"),
    (r"403", r"run out|quota|limit|points|exhaust", "quota exhausted"),
    (r"403", r"", "forbidden / upstream refused"),
    (r"400|422", r"tool_choice", "tool_choice unsupported by the model"),
    (r"40[04]|410|422", r"not found|not supported|does not exist|retired|no longer|unknown model|invalid model|不支持|unavailable",
     "model not served on this route"),
    (r"400|422", r"tool", "tools unsupported / rejected"),
    (r"400|422", r"response_format|json_schema|schema", "structured output rejected"),
    (r"400|422", r"", "request format rejected"),
    (r"413", r"", "request too large for this tier"),
    (r"429", r"", "rate limit / quota window"),
    (r"5\d\d", r"", "upstream error"),
    (r"0", r"timeout|timed out", "timeout / hang"),
    (r"0", r"", "connection error"),
]


def cause(status: int, msg: str) -> str:
    for sp, mp, label in CAUSES:
        if re.fullmatch(sp, str(status)) and (not mp or re.search(mp, msg or "", re.I)):
            return label
    return f"HTTP {status}"


def stage1(model: str) -> dict:
    body = {"model": model, "max_tokens": 300, "stream": False,
            "messages": [{"role": "user", "content": "Call the report_status tool with status set to ok."}],
            "tools": [{"type": "function", "function": {"name": "report_status", "description": "Report status",
                       "parameters": {"type": "object", "properties": {"status": {"type": "string"}},
                                      "required": ["status"]}}}]}
    status, data, secs = at.api("POST", "/v1/chat/completions", body, timeout=90, headers={"x-omniroute-no-memory": "true"})
    msg = ((data or {}).get("choices") or [{}])[0].get("message", {}) if status == 200 else {}
    tool = any((c.get("function") or {}).get("name") == "report_status" for c in msg.get("tool_calls") or [])
    err = "" if status == 200 else str((data or {}).get("error", ""))[:200]
    text = (msg.get("content") or "")[:80] if status == 200 else ""
    return {"status": status, "secs": round(secs, 1), "tool": tool, "text": text, "err": err,
            "cause": "" if status == 200 else cause(status, err)}


def run_model(model: str) -> dict:
    prov = model.split("/", 1)[0]
    r = {"model": model, "provider": prov, "stage1": stage1(model)}
    if r["stage1"]["status"] != 200:
        r["hermes_fit"] = r["hindsight_fit"] = False
        r["why"] = r["stage1"]["cause"] + ": " + r["stage1"]["err"][:120]
        return r
    if at.probe_affordable(prov):
        h = vc.round_trip(model)
        r["hermes"] = {"ok": h["ok"], "secs": round(h["secs"], 1), "detail": " ".join(h["detail"].split())[:140]}
    else:
        r["hermes"] = {"ok": None, "secs": 0, "detail": "skipped: free pool too small for a 20k-token round trip"}
    r["structured"] = at.probe_structured(model)
    r["required"] = at.probe_required_tool(model)
    r["hermes_fit"] = bool(r["hermes"]["ok"])
    r["hindsight_fit"] = bool(r["structured"]["ok"] and r["required"]["ok"])
    why = []
    if not r["hermes_fit"]:
        why.append("hermes: " + r["hermes"]["detail"])
    if not r["structured"]["ok"]:
        why.append(f"structured: {cause(r['structured']['status'], r['structured']['err']) if r['structured']['status'] != 200 else r['structured']['err']}")
    if not r["required"]["ok"]:
        why.append(f"required tool: {cause(r['required']['status'], r['required']['err']) if r['required']['status'] != 200 else r['required']['err']}")
    r["why"] = "; ".join(why)
    return r


def candidates(only_providers: list[str] | None) -> dict[str, list[str]]:
    cat = at.catalog()
    con = at.db()
    active = {p for p, cs in at.connections(con).items() if any(c["active"] for c in cs)}
    picks = {p: m for m, p in (at.selected_models() or {}).items()}
    pinned = at.CONFIG["pinned_models"] + [x["model"] for lane in at.CONFIG["lanes"] for x in lane.get("pinned", [])]
    ok7 = defaultdict(list)
    for prov, m, n in con.execute("""select provider, requested_model, sum(status=200) from call_logs
            where timestamp >= datetime('now','-7 days') and path like '%chat/completions%' and requested_model like '%/%'
            group by provider, requested_model having sum(status=200) > 0 order by 3 desc"""):
        ok7[m.split("/", 1)[0]].append(m)
    out = {}
    for prov in sorted(active - NOT_LLM - SUBSCRIPTION):
        if only_providers and prov not in only_providers:
            continue
        ids = [k for k in cat if k.startswith(prov + "/") and not NON_CHAT.search(k)]
        chosen = [m for m in pinned if m.startswith(prov + "/")]
        if prov in picks:
            chosen.append(picks[prov])
        chosen += [m for m in ok7.get(prov, []) if m in cat][:3]
        # one model per effort family: the plain name when the catalog has it, else its -medium (or first) variant
        families = defaultdict(list)
        for m in ids:
            families[EFFORT_VARIANT.sub("", m)].append(m)
        reps = [base if base in fam else next((m for m in fam if m.endswith("-medium")), fam[0])
                for base, fam in families.items()]
        ranked = sorted(reps,
                        key=lambda m: (-(at.tier_quality(m) or 0.5),
                                       0 if (prov not in FREE_AGGREGATORS or "free" in m.lower()) else 1))
        if prov in FREE_AGGREGATORS:
            ranked = [m for m in ranked if "free" in m.lower()] + [m for m in ranked if "free" not in m.lower()]
        cap = 9 if prov in ("agy",) else PER_PROVIDER
        for m in ranked:
            if len(dict.fromkeys(chosen)) >= cap:
                break
            chosen.append(m)
        if chosen:
            out[prov] = list(dict.fromkeys(chosen))[:cap]
    return out


def write(results: list[dict], stamp: str) -> Path:
    d = ROOT / "reports" / "sweep"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    yn = lambda v: "-" if v is None else ("O" if v else "X")
    lines = [f"# OmniRoute model sweep {stamp} (KST)", "",
             "| model | stage1 | hermes | s | structured | s | required | hermes-fit | hindsight-fit | why |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (not r["hindsight_fit"], not r["hermes_fit"], r["model"])):
        s1 = r["stage1"]
        s1t = f"{s1['status']} {'tool' if s1['tool'] else 'no-tool'} {s1['secs']}s" if s1["status"] == 200 else f"{s1['status']}"
        h, st, rq = r.get("hermes", {}), r.get("structured", {}), r.get("required", {})
        lines.append(f"| {r['model']} | {s1t} | {yn(h.get('ok'))} | {h.get('secs', '')} | {yn(st.get('ok'))} | "
                     f"{st.get('latency', '')} | {yn(rq.get('ok'))} | {yn(r['hermes_fit'])} | {yn(r['hindsight_fit'])} | "
                     f"{r.get('why', '')[:160].replace('|', '/')} |")
    p = d / f"{stamp}.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*")
    ap.add_argument("--providers", nargs="*")
    ap.add_argument("--list", action="store_true", help="print the candidate list and exit")
    a = ap.parse_args()
    plan = defaultdict(list)
    if a.models:
        for m in a.models:
            plan[m.split("/", 1)[0]].append(m)
    else:
        plan = candidates(a.providers)
    if a.list:
        print(json.dumps(plan, indent=1))
        return 0
    stamp = datetime.now(at.KST).strftime("%Y%m%d-%H%M")

    def per_provider(models):
        out = []
        for m in models:
            r = run_model(m)
            out.append(r)
            print(f"{m}: s1={r['stage1']['status']} H={r['hermes_fit']} HS={r['hindsight_fit']} {r.get('why', '')[:110]}",
                  flush=True)
            time.sleep(2)
        return out

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = [r for rs in ex.map(per_provider, plan.values()) for r in rs]
    print(write(results, stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
