"""OmniRoute combo auto-tuner for Hermes.

Rebuilds the candidate list of one OmniRoute combo (default: hermes-fast-agent) from live data:
call history (success rate, speed), quota snapshots, connection health and arena quality scores.
OmniRoute still picks per request among the candidates; this only refreshes WHO the candidates are.

Usage:
  python autotune.py tune [--dry-run]   full re-selection (scheduled every 30 min)
  python autotune.py check              cheap health check; runs `tune` early if the combo is failing
  python autotune.py rollback           restore the combo from the newest backup
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time
import traceback
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = {
    "combo_name": "hermes-fast-agent",
    "role_exclusions": {
        "hermes-fast-agent": {"kiro/auto": "agent-sized API refusal and Hermes tool call failure"},
        "hindsight-structured": {
            "cline/dots-studio/dots-3-note-preview:free": "structured extraction and required tool failures",
            "cline/nvidia/nemotron-3-ultra-550b-a55b:free": "structured extraction and required tool failures"},
        "hindsight-refresh": {
            "cline/dots-studio/dots-3-note-preview:free": "structured extraction and required tool failures",
            "cline/nvidia/nemotron-3-ultra-550b-a55b:free": "structured extraction and required tool failures"},
    },
    # User preference (2026-10-27): health/quota gates still apply; score cannot reorder these.
    # 2026-10-28 user: no pinned models any more; the combo is ordered purely by measured score
    # (was gemini-3.8-flash-medium, gpt-oss-120b-medium: both kept being re-admitted and then failing with 429/400)
    # 2026-10-29 user: antigravity/gemini-3.8-flash-medium is #1 again and the only antigravity model.
    # 2026-10-29 user (evening): #1 moved to the agy route of the same model and account. Today the antigravity
    # route answered 0/260 with 429 "Resource has been exhausted" while agy answered 667/669 at the same quota.
    # 2026-10-31 user: all reasoning low, so the low thinking tier of the same model. Hermes round trip, 5 each
    # interleaved: low 5/5 p50 27.4 s (24.6-28.8), medium 5/5 p50 30.6 s (25.4-60.2).
    "preferred_models": ["agy/gemini-3.8-flash-low"],
    # Pinned models are always in the combo at their preferred rank: no probes, no suspension, no latency limit.
    # A rate-limited pinned model is skipped by OmniRoute's own model lockout and the next model answers.
    "pinned_models": ["agy/gemini-3.8-flash-low"],
    # provider -> the only models of that provider the combo may use
    # 2026-10-29 user (later): goal is to use every quota (AI Pro paid + free); medium stays #1 but the other
    # antigravity models (Gemini pool and Claude/GPT pool) are candidates again. Was {"antigravity": [medium]}.
    "provider_only_models": {},
    # 2026-10-29 user: agy is the same provider as antigravity (OmniRoute runs agy on the antigravity executor with
    # the same Google accounts and shared gemini family cooldowns); provider limits and rules apply to the group
    "provider_groups": {"agy": "antigravity"},
    # 2026-10-30 user: a combo step of these providers is pinned to that provider's own active connections
    # (allowedConnectionIds). Unpinned, agy/gemini-3.8-flash-medium also went to the antigravity connection of the
    # same Google account, which answered 429 almost every time (24h: 435 attempts, ~20 s each) while agy answered 200.
    "route_pinned_providers": ["agy"],
    "preferred_retry_minutes": 30,
    # 2026-10-28: a preferred model is (re)admitted only after extra checks on top of the tool-call probe:
    # `json_schema` = Hindsight-style structured output must work (antigravity/gpt-oss returned 400 "name ... non-empty"
    # and the combo stopped, 12+ Hindsight fallbacks in one night); `repeats` = tool-call probe must pass this many
    # times in a row (gemini-3.8 passed once, then returned 429 for ~20 s per call within minutes).
    "preferred_admission": {"json_schema": True, "repeats": 2},
    "preferred_admission_fail_hours": 6,  # a deterministic json_schema failure is not retried every 30 min
    # 2026-10-23: was 20. With priority the tail is only reached after everything above it failed, and a long
    # tail of unverified models just adds failed round trips; keep a short list of verified fallbacks.
    "target_size": 8,
    # 2026-10-29 user: candidates are only the models the user picked, one per provider, on the picker page
    # https://claude.ai/artifact/88a5uLyVc6FeVDxx1zQPbg (copied to this file); no discovery of other models.
    # Every picked model that passes the health gates is used (target_size grows to the number of picks).
    "selected_models_file": "selected_models.json",
    # provider -> ISO time: ignore its call history before this. 2026-10-29: our probes sent urllib's User-Agent and
    # got 403 from these providers (OmniRoute #15632), so their history showed failures that real clients never had
    "history_reset": {"chatanywhere": "2026-10-29T06:20:00", "groq": "2026-10-29T06:20:00", "aion": "2026-10-29T06:20:00"},
    "min_size": 5,            # never shrink the combo below this; keep the old list instead
    # Free-pool budget is computed from past call logs; for these providers it is stale.
    # 2026-10-30 user: kiro credit 45.87/50 left (resets 11-01) but the log-derived free budget (25,000 tokens vs
    # 118,802 logged) wrongly marks the pool used up.
    "ignore_budget_providers": ["kiro"],
    "max_per_provider": 4,    # models of one provider share a quota pool
    "max_per_provider_override": {"antigravity": 3},  # antigravity+agy group: medium + up to 2 more (was 1, then 2)
    "min_context": 128_000,
    "history_hours": 48,
    "proven_days": 7,
    "agent_tokens": 15_000,   # a Hermes turn sends ~25k tokens; "agent-sized" requests
    "probes_per_run": 6,          # 2026-10-10 doubled (was 3); probes are ~20k tokens each
    "admission_probes_per_run": 30,   # 2026-10-10 user: widen further (was 12, 2026-10-09 was 6)
    "admission_fresh_hours": 6,
    # 2026-10-23: members without a success (real or probe) for this long are re-tested like newcomers; before,
    # a single old probe kept idle members in the list for weeks (6 of 14 had no call in 7 days)
    "incumbent_fresh_hours": 24,
    # 2026-10-23: a free pool that cannot carry this many Hermes turns goes behind the others (openrouter-free is
    # 40k tokens/day, i.e. one or two turns, but scored #4)
    "min_pool_turns": 5,
    "turn_tokens": 25_000,
    "probe_tokens": 20_000,   # close to a real Hermes turn (16k fixed prompt + history)
    "probe_effort": "low",    # what Hermes sends for hermes-fast-agent (agent.reasoning_overrides, 2026-10-31 user)
    # do not test-drive a pool with a small free budget: a probe must cost at most this share of what is left
    # (openrouter-free is 40k tokens/day = two 20k probes)
    "probe_max_budget_share": 0.10,
    "reprobe_ok_hours": 24,
    "reprobe_fail_hours": 6,
    "check_window_min": 10,
    "check_fail_ratio": 0.5,
    "check_min_samples": 4,
    "min_minutes_between_tunes": 5,
    # cache-optimized pins a whole conversation to one model, so reliability matters as much as quality
    "weights": {"quality": 0.35, "success": 0.35, "speed": 0.15, "quota": 0.15},
    "min_success": 0.50,      # user choice 2026-10-08
    "min_quality": 0.40,      # tiny models (7B etc.) are not good enough to drive the agent
    # "weighted" = every request is spread across the candidates at random in proportion to the weights we set
    # (quality-heavy score x remaining free budget), so per-minute limits of many free pools are used in parallel
    # instead of draining one model at a time. A failed pick falls through to the others.
    # 2026-10-08 user choice: minimise input tokens. "cache-optimized" pinned each prompt prefix to one target
    # by rendezvous hash — but it ignores our order AND weights (OmniRoute promptCacheAffinity.ts), and since
    # every Hermes conversation shares the same system-prompt prefix, 90% of traffic went to whichever model
    # the hash happened to pick, with a hash-ordered (not score-ordered) fallback. 2026-10-19: "lkgp" keeps the
    # same stickiness (last good provider first → prompt cache stays warm) but falls back in OUR score order.
    # 2026-10-19 later: with several lanes (below) lkgp drifts — once lane B falls onto lane A's model it stays
    # there even after B's own primary recovers, so all lanes converge on one quota pool. "priority" always
    # tries the lane's own #1 first; OmniRoute's model lockout (modelLockout.enabled) skips a failing #1 for
    # 2-30 min, so it is still one stable model per lane (cache stays warm) without retrying a dead one.
    "strategy": "priority",
    # Lanes: one combo per agent role, same candidate list, different #1. Two agents running at once (kanban
    # max_in_progress 2) then draw on different quota pools / rate limits, and a reviewer gets a different model
    # family from the worker it checks. When a lane's #1 runs out it falls through the whole list in score
    # order. The first lane is the main combo (unchanged name; workers and auxiliary tasks use it).
    # 2026-10-19 evening: the kanban profiles were deleted (only the default profile runs projects now), so only
    # the main lane is left. To bring lanes back, add entries such as
    # {"combo": "hermes-lane-review", "role": "review"} and point a profile's model.default at that combo.
    "lanes": [
        {"combo": "hermes-fast-agent", "role": "work"},
        # 2026-10-29 user: Hindsight gets its own combo (HINDSIGHT_API_LLM_MODEL) with only the candidates that pass
        # a Hindsight-shaped structured-output probe; north-mini hung on 14 of 64 of Hindsight's json_schema calls
        # Optional lane keys: "primary" moves that model to #1 when it is in the lane; "pinned" [{"model", "rank"}]
        # puts a model at that rank in this lane only (even if it is not a pick), with no probes or suspension.
        # 2026-10-30 user: gemini-4-argon-high (arena 0.98, its own weekly pool) passed the tool and structured probes
        # but is not a pick, so it was never used. Later the same day: at #2 it still got 0 calls, because priority
        # only reaches #2 when #1 fails and the agy connection pin removed medium's 429s; trial at #1.
        # 2026-10-31 user: all reasoning low. Structured probe, 4 each: argon-low 4/4 p50 23.6 s, argon-high 4/4 24.1 s.
        {"combo": "hindsight-structured", "role": "hindsight", "structured": True,
         "pinned": [{"model": "agy/gemini-4-argon-low", "rank": 1}]},
        # 2026-10-30 user: Hindsight page refresh / reflect is its biggest token consumer (~5.7M input tokens/day);
        # Hindsight's docs recommend a small fast model for it (HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_MODEL)
        {"combo": "hindsight-refresh", "role": "hindsight-refresh", "structured": True, "primary": "agy/gemini-3-flash"},
    ],
    "structured_max_s": 90,   # Hindsight-shaped json_schema probe must answer within this (Hindsight waits 300 s)
    # hung calls (48 h) that keep a model out of the Hindsight lane whatever its probe says; north-mini had 24, while
    # an occasional slow consolidation (medium 2, tiered 1, grok 1) is not a hang pattern. Pinned models are exempt.
    "structured_hang_limit": 3,
    "lane_min_quality": 0.60,  # a lane's #1 must be at least this good, else it shares the best model
    "weight_exponent": 4,     # score^4: a 0.76 model gets ~4x the traffic of a 0.53 one
    "unknown_budget": 0.8,    # remaining-budget fraction assumed when a pool publishes no number
    "combo_max_retries": 2,   # retries on one model before moving down the list (was 3 → ~20 s stuck per 429)
    # 2026-10-30 user: the pinned #1 (medium) answers a Hermes turn in 3-5 s or in 22-50 s, whatever the reasoning
    # effort (measured), and priority only reaches #2 when #1 fails. OmniRoute hedging (open-sse comboConfig.ts,
    # combo/comboAttemptLoop.ts) starts the next step when #1 is still silent after hedgeDelayMs and keeps the first
    # answer. Main combo only; #2 comes from another provider group (second_from_other_group) so the hedge does not
    # double the load on #1's account. Session stickiness (combo/sessionStickiness.ts) then pinned the whole
    # conversation to the step that won one hedged turn (next turn skipped medium), so it is off: priority already
    # tries the same #1 first on every turn.
    "main_combo_config": {"zeroLatencyOptimizationsEnabled": True, "hedging": True, "hedgeDelayMs": 10_000,
                          "disableSessionStickiness": True},
    "hang_s": 90,             # a 499 (client gave up) after this long = the model hung, not a hedge cancel
    "slow_success_s": 240,    # 2026-10-29: an answer slower than this rests the model like a hang (Hindsight waits 300 s)
    "max_latency_s": 45,      # user choice 2026-10-08; a pinned conversation pays this on every turn
    "reserve_below_pct": 10,  # a model with less quota than this left ...
    "reserve_days": 3,        # ... and a reset further away than this goes to the end of the list
    "check_last_n": 8,        # health check looks at the most recent N combo attempts
    "check_streak": 3,        # any candidate failing this many times in a row triggers an immediate re-tune
    # Subscription-backed connections that share limits with Claude Code / claude.ai: keep them out of
    # the free-model combo unless the user opts in.
    "exclude_providers": ["claude", "claude-web"],
}

# Capability tiers by model family (first match wins). Used for ordering; arena scores fill gaps.
QUALITY_TIERS = [
    (r"claude-(opus|sonnet)-(4[.-][5-9]|5)", 0.90),
    (r"gpt-5(\.\d+)?(-codex|-terra|-sol|-luna|$)", 0.88),
    (r"gemini-3(\.\d)?-pro", 0.85),
    (r"(^|/)o3(-pro)?$", 0.82),
    (r"kimi-k2\.[5-9]|glm-5|deepseek-v4|qwen3-coder-plus|grok-4", 0.76),
    (r"gemini-3(\.\d)?-flash(-high|-tiered|-preview|$)|gemini-3-flash", 0.72),
    (r"qwen3\.[5-9]-(max|plus|coder|397b)|qwen3-(max|coder)|kimi-k3|minimax-m2|mimo-v2\.[5-9]-pro|glm-4\.[5-9]", 0.70),  # 2026-10-10 added families
    (r"command-a(-plus|-reasoning)?-|command-a$|llama-4|mistral-(medium|large)|magistral|qwen3\.[5-9]-(27b|35b|122b)", 0.60),
    (r"nemotron-3-ultra", 0.70),
    (r"gemini-2\.5-pro|gpt-4\.1$", 0.68),
    (r"o3-mini|o4-mini", 0.65),
    (r"gpt-4o$", 0.64),
    (r"nemotron-3-super|gpt-oss-?:?120b|llama-3\.3-70b|codestral|mistral-large", 0.62),
    (r"flash-lite|flash-low|flash-medium", 0.56),
    (r"gpt-4o-mini|gpt-oss-?:?20b|gemma|ling-|apodex|nano|-mini", 0.46),
    (r"(^|[^0-9.])[1-9]b\b|qwen7b|small|tiny", 0.35),  # single-digit-billion models
]


def tier_quality(model: str) -> float | None:
    name = model.lower().split("/", 1)[-1]
    for pattern, score in QUALITY_TIERS:
        if re.search(pattern, name):
            return score
    return None
BASE = "http://127.0.0.1:20128"
DB = Path.home() / ".omniroute" / "storage.sqlite"
STATE = ROOT / "state.json"
LOCK = ROOT / ".lock"
HARD_ERRORS = {401, 402, 403, 404, 410, 426, 466}
KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------- utilities
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def kst(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s if "T" in s else s.replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def log(msg: str, file: str = "autotune.log") -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    with open(ROOT / "logs" / file, "a", encoding="utf-8") as fh:
        fh.write(f"[{kst()}] {msg}\n")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"probes": {}, "last_tune": None}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


class Lock:
    def __enter__(self):
        for _ in range(60):
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 900:  # stale lock from a killed run
                    LOCK.unlink(missing_ok=True)
                    continue
                time.sleep(2)
        # 2026-10-23: a skip, not a failure — the run holding the lock is doing the same work (the 3-hourly
        # verify_cycle holds it for ~15 min, and the 30-min tune then showed up as a failed task)
        log("skipped: another autotune run holds the lock")
        raise SystemExit(0)

    def __exit__(self, *exc):
        os.close(self.fd)
        LOCK.unlink(missing_ok=True)


CLIENT_USER_AGENT = "OpenAI/Python 2.24.0"


def api(method: str, path: str, body=None, timeout: int = 90, headers: dict | None = None):
    key = os.environ.get("OMNIROUTE_API_KEY", "")
    data = json.dumps(body).encode() if body is not None else None
    # 2026-10-29: OmniRoute 3.8.51 forwards the client's User-Agent upstream (issue #15632, fixed in 3.8.52) and
    # groq / chatanywhere / aion answer urllib's default "Python-urllib" with 403 while the same call with the
    # OpenAI SDK's agent succeeds. Probes must look like the real clients (Hermes and Hindsight: openai 2.24.0).
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": CLIENT_USER_AGENT, **(headers or {})})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, {"error": raw[:300]}, time.time() - t0
    except Exception as e:  # timeouts, resets
        return 0, {"error": f"{type(e).__name__}: {e}"[:300]}, time.time() - t0


def db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)


def is_pinned(model: str) -> bool:
    return model in CONFIG.get("pinned_models", [])


def update_allowed(n_new: int, n_old: int, forced: bool) -> bool:
    """Replace the combo list? min_size only protects an old list bigger than the new one (an old list already below
    min_size is replaced by any list at least as long, e.g. a locked-out member swapped for a live one); `forced` (a user rule
    removes members) allows any non-empty list."""
    return n_new >= min(CONFIG["min_size"], n_old) or (forced and n_new > 0)


def provider_group(provider: str) -> str:
    """Provider that shares accounts and quota with `provider` (CONFIG provider_groups), else `provider` itself."""
    return CONFIG.get("provider_groups", {}).get(provider, provider)


def selected_models() -> dict | None:
    """model -> provider from the user's picks (CONFIG selected_models_file), or None when no pick list is set."""
    name = CONFIG.get("selected_models_file")
    if not name:
        return None
    try:
        data = json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sel = {m: p for p, m in (data.get("picks") or {}).items() if m and m != "__none__"}
    # 2026-10-30 user: models that passed the sweep (sweep.py, 3 of 3) beside a provider's one pick; same gates as picks
    sel.update({m: m.split("/", 1)[0] for m in data.get("extra_picks") or []})
    return sel


def target_size() -> int:
    sel = selected_models()
    return max(CONFIG["target_size"], len(sel)) if sel else CONFIG["target_size"]


def provider_allows(provider: str, model: str) -> bool:
    """False for a model the user's rules exclude: not among the picks (selected_models_file), or a provider
    restricted to specific models (CONFIG provider_only_models). Pinned models are always allowed."""
    sel = selected_models()
    if sel is not None and model not in sel and not is_pinned(model):
        return False
    only = CONFIG.get("provider_only_models", {}).get(provider_group(provider))
    return only is None or model in only


def norm(name: str) -> str:
    """Comparable model name: drop provider prefixes, :free, dates, punctuation."""
    n = name.lower().split("/")[-1]
    n = re.sub(r":free$|-preview$|-latest$|-\d{8}$", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def hist_for(model: str, hist: dict) -> dict | None:
    """Call history of `model`; OmniRoute logs agy requests under the antigravity name (agy/x -> antigravity/x),
    so an agy pick had no history and ranked last (2026-10-29: agy/gemini-3-flash, 99% success, behind north-mini)."""
    if model in hist:
        return hist[model]
    head, _, rest = model.partition("/")
    for prov, group in CONFIG.get("provider_groups", {}).items():
        if head == prov and f"{group}/{rest}" in hist:
            return hist[f"{group}/{rest}"]
    return None


def base_model(name: str) -> str:
    """norm() without a reasoning-effort suffix: within one provider, 'grok-4.7-xhigh' is served by the same
    'grok-4.7' (2026-10-23 round-trip test), so two such entries add no fallback."""
    n = name.lower().split("/")[-1]
    return norm(re.sub(r"-(xhigh|high|medium|low|max|minimal|thinking)$", "", n))


# ---------------------------------------------------------------- data collection
def combo_get(name: str) -> dict:
    status, data, _ = api("GET", "/api/combos")
    if status != 200:
        raise RuntimeError(f"GET /api/combos failed: {status} {data}")
    return next(c for c in data["combos"] if c["name"] == name)


def catalog() -> dict:
    status, data, _ = api("GET", "/v1/models", timeout=120)
    if status != 200:
        log(f"catalog fetch failed: {status} {data}")
        return {}
    return {m["id"]: m for m in data.get("data", [])}


def history(con) -> dict:
    """Per requested_model stats from OmniRoute call logs."""
    h = CONFIG["history_hours"]
    rows = con.execute("""
        select requested_model, provider, status, duration, tokens_in, timestamp, coalesce(error_summary,'')
        from call_logs
        where timestamp >= datetime('now', ?) and path like '%chat/completions%'
          and requested_model like '%/%' and requested_model not like 'auto%'
        order by timestamp""", (f"-{CONFIG['proven_days']} days",)).fetchall()
    cutoff = now_utc() - timedelta(hours=h)
    stats: dict[str, dict] = {}
    for model, provider, status, dur, tin, ts, err in rows:
        since = CONFIG.get("history_reset", {}).get(provider)
        if since and ts < since:
            continue  # history polluted by our own broken probes (see CONFIG history_reset)
        if status == 499 and (dur or 0) < CONFIG["hang_s"] * 1000:
            continue  # hedged duplicate cancelled by the combo, not a model failure
        if status == 499:
            status = 504  # the client gave up after a long silence: the model hung — count it as a failure
            stats.setdefault(model, {"provider": provider, "n": 0, "ok": 0, "n7": 0, "ok7": 0, "ok_big7": 0,
                                     "durs": [], "durs_big": [], "durs_big7": [], "last": None, "last_ok": None})["last_hang"] = ts
        s = stats.setdefault(model, {"provider": provider, "n": 0, "ok": 0, "n7": 0, "ok7": 0, "ok_big7": 0,
                                     "durs": [], "durs_big": [], "durs_big7": [], "last": None, "last_ok": None})
        t = parse_ts(ts)
        ok = 200 <= (status or 0) < 300
        s["n7"] += 1
        s["ok7"] += ok
        if ok and (tin or 0) >= CONFIG["agent_tokens"]:
            s["ok_big7"] += 1
            s["durs_big7"].append((dur or 0) / 1000)
        if t and t >= cutoff:
            s["n"] += 1
            s["ok"] += ok
            if ok:
                s["durs"].append((dur or 0) / 1000)
                if (tin or 0) >= CONFIG["agent_tokens"]:
                    s["durs_big"].append((dur or 0) / 1000)
        s["last"] = {"ts": ts, "status": status, "err": err[:120]}
        if ok and (dur or 0) >= CONFIG["slow_success_s"] * 1000:
            # 2026-10-29: anyapi nemotron-nano alternated 280-300 s hangs with 130-260 s "successes"; each slow success
            # cleared the 60-min rest, so Hindsight kept timing out on it. A success this slow rests the model too.
            s["last_hang"] = ts
        elif ok:
            s["last_ok"] = ts
    return stats


def quotas(con) -> dict:
    """Latest fresh quota snapshot per (provider, window)."""
    out: dict[str, dict] = {}
    for prov, wk, rem, exh, reset, created in con.execute("""
        select q.provider, coalesce(q.window_key,'-'), q.remaining_percentage, q.is_exhausted, q.next_reset_at, q.created_at
        from quota_snapshots q join (
          select provider, coalesce(window_key,'-') wk, max(id) mid from quota_snapshots
          where created_at >= datetime('now','-6 hours') group by provider, wk) m on q.id = m.mid"""):
        out.setdefault(prov, {})[wk] = {"remaining": rem, "exhausted": bool(exh), "reset": reset}
    # Antigravity has multiple accounts on this PC. A model is exhausted only when
    # every active account's relevant quota is exhausted, not the last account polled.
    accounts = {cid: {} for (cid,) in con.execute("""select id from provider_connections
        where provider = 'antigravity' and is_active = 1 and coalesce(test_status,'') != 'banned'""")}
    if accounts:
        for cid, wk, rem, exh, reset in con.execute("""select q.connection_id,
            coalesce(q.window_key,'-'), q.remaining_percentage, q.is_exhausted, q.next_reset_at
            from quota_snapshots q join (select connection_id, coalesce(window_key,'-') wk, max(id) mid
            from quota_snapshots where provider = 'antigravity' and created_at >= datetime('now','-6 hours')
            group by connection_id, wk) m on q.id = m.mid"""):
            if cid in accounts:
                accounts[cid][wk] = {"remaining": rem, "exhausted": bool(exh), "reset": reset}
        out.setdefault("antigravity", {})["__accounts__"] = accounts
    return out


def connections(con) -> dict:
    out: dict[str, list] = {}
    for cid, prov, active, test, err_code, rl_until in con.execute(
            "select id, provider, is_active, test_status, error_code, rate_limited_until from provider_connections"):
        out.setdefault(prov, []).append({"id": cid, "active": bool(active), "test": test, "err": err_code,
                                         "rl_until": rl_until})
    return out


def live_resilience() -> dict:
    """OmniRoute's own live view (official API, not the DB): open circuit breakers and model lockouts.

    Returns {"open": {provider: [connection ids or None for provider-wide]}, "locked": {(provider, model): set(conn ids)}}.
    Both are in-memory in OmniRoute, so only the API can see them.
    """
    out = {"open": {}, "locked": {}}
    status, data, _ = api("GET", "/api/monitoring/health", timeout=30)
    if status == 200:
        for b in (data or {}).get("providerBreakers") or []:
            if b.get("state") != "OPEN" or not (b.get("retryAfterMs") or 0) > 0:
                continue
            prov, _, conn = (b.get("provider") or "").partition("::conn::")
            out["open"].setdefault(prov, []).append(conn or None)
    else:
        log(f"health fetch failed: {status}")
    status, data, _ = api("GET", "/api/resilience/model-cooldowns", timeout=30)
    if status == 200:
        for it in (data or {}).get("items") or []:
            if (it.get("remainingMs") or 0) > 0:
                key = (it.get("provider"), (it.get("model") or "").split("/")[-1].lower())
                out["locked"].setdefault(key, set()).add(it.get("connectionId"))
    else:
        log(f"model-cooldowns fetch failed: {status}")
    return out


resilience: dict = {"open": {}, "locked": {}}


def resilience_block(provider: str, model: str, conns: dict) -> str:
    """Why OmniRoute itself would skip this target right now ('' = it would not)."""
    active = [c["id"] for c in conns.get(provider, []) if c["active"]]
    opened = resilience["open"].get(provider)
    if opened and (None in opened or (active and set(active) <= set(opened))):
        return "circuit breaker OPEN"
    locked = resilience["locked"].get((provider, model.split("/")[-1].lower()))
    if locked and (None in locked or (active and set(active) <= locked)):
        return "model locked out by OmniRoute"
    return ""


def pool_budget(provider: str) -> dict:
    """Free-pool budget of `provider`, or {} when CONFIG ignore_budget_providers marks the log-derived number stale."""
    return {} if provider in CONFIG["ignore_budget_providers"] else (budgets.get(provider) or {})


def probe_affordable(provider: str) -> bool:
    """A ~probe_tokens test must not eat more than probe_max_budget_share of a pool's remaining free budget."""
    b = pool_budget(provider)
    if not b.get("budget") or b.get("remaining_frac") is None:
        return True  # no published budget: nothing to protect
    left = b["budget"] * b["remaining_frac"]
    return CONFIG["probe_tokens"] <= left * CONFIG["probe_max_budget_share"]


def free_budgets(con) -> dict:
    """Free-tier pools from OmniRoute's catalog (/api/free-models), with usage in the current window.

    Returns {provider: {"pool", "type", "budget", "used", "remaining_frac", "resets"}} — one pool per provider
    (OmniRoute's poolKey; all models of a pool share it, so it is counted once, as the dashboard does).
    """
    status, data, _ = api("GET", "/api/free-models", timeout=60)
    if status != 200:
        log(f"free-models fetch failed: {status}")
        return {}
    pools: dict[str, dict] = {}
    for m in data.get("models", []):
        p = pools.setdefault(m["provider"], {"pool": m.get("poolKey"), "type": m.get("freeType"),
                                              "monthly": 0, "credit": 0})
        p["monthly"] = max(p["monthly"], m.get("monthlyTokens") or 0)
        p["credit"] = max(p["credit"], m.get("creditTokens") or 0)
    now = now_utc()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = day0.replace(day=1)
    out = {}
    for prov, p in pools.items():
        t = p["type"]
        if t == "recurring-daily" and p["monthly"]:
            budget, since, resets = p["monthly"] / 30, day0, day0 + timedelta(days=1)
        elif t in ("recurring-monthly", "recurring-credit") and (p["monthly"] or p["credit"]):
            budget, since = p["monthly"] or p["credit"], month0
            resets = (month0 + timedelta(days=32)).replace(day=1)
        elif t == "one-time-initial" and p["credit"]:
            budget, since, resets = p["credit"], datetime(2000, 1, 1, tzinfo=timezone.utc), None
        else:
            out[prov] = {**p, "budget": None, "used": None, "remaining_frac": None, "resets": None}
            continue
        used = con.execute("""select coalesce(sum(coalesce(tokens_in,0) + coalesce(tokens_out,0)), 0) from call_logs
            where provider = ? and status between 200 and 299 and timestamp >= ?""",
                           (prov, since.strftime("%Y-%m-%dT%H:%M:%S"))).fetchone()[0]
        out[prov] = {**p, "budget": budget, "used": used, "remaining_frac": max(0.0, 1 - used / budget),
                     "resets": resets}
    return out


budgets: dict[str, dict] = {}


def quality(con) -> dict:
    scores: dict[str, list] = {}
    for model, score in con.execute("select model, cast(score as real) from model_intelligence where score is not null"):
        scores.setdefault(norm(model), []).append(score)
    return {k: statistics.mean(v) for k, v in scores.items()}


# ---------------------------------------------------------------- evaluation
GENERIC_WINDOWS = ("session", "weekly", "daily", "monthly", "credits", "balance", "free", "total", "api", "chat",
                   "available", "cash")


def quota_for(provider: str, model: str, q: dict):
    """(remaining 0-100 or None, exhausted_until or None, reason)."""
    wins = q.get(provider, {})
    if not wins:
        return None, None, ""
    accounts = wins.get("__accounts__")
    if accounts:
        results = [quota_for(provider, model, {provider: windows}) for windows in accounts.values()]
        available = [r for r in results if not r[2]]
        if available:
            return max(available, key=lambda r: r[0] if r[0] is not None else -1)
        return min(results, key=lambda r: r[1] or datetime.max.replace(tzinfo=timezone.utc))
    name = model.split("/")[-1].lower()
    relevant = []
    if name in wins:
        relevant.append((name, wins[name]))
    elif provider in ("antigravity", "agy"):
        # Match OmniRoute's selectAntigravityQuotaWindowNames: public Flash tiers
        # use the technical -tiered quota bucket if no exact model bucket exists.
        flash = re.fullmatch(r"(gemini-\d+(?:\.\d+)*-flash)(?:-(?:high|medium|low))?", name)
        alias = flash.group(1) + "-tiered" if flash else None
        if alias in wins:
            relevant.append((alias, wins[alias]))
    for key, w in wins.items():
        k = key.lower()
        if k == name or k.startswith("chat_") or k in ("premium_interactions", "completions"):
            continue
        family = re.fullmatch(r"([a-z_]+)_weekly", k)
        if family:  # e.g. gemini_weekly, claude_gpt_weekly: applies only to models of those families
            if any(f and f in name for f in family.group(1).split("_")):
                relevant.append((key, w))
        elif any(g in k for g in GENERIC_WINDOWS) and not any(ch.isdigit() for ch in k.replace("5h", "").replace("7d", "")):
            if k in ("balance", "credits", "credits_usd", "cash", "available", "voucher") and model.endswith(":free"):
                continue  # free models do not spend paid balance
            relevant.append((key, w))
    if not relevant:
        return None, None, ""
    worst_key, worst = min(relevant, key=lambda kv: kv[1]["remaining"] if kv[1]["remaining"] is not None else 100)
    rem = worst["remaining"]
    if worst["exhausted"] or (rem is not None and rem <= 0.5):
        until = parse_ts(worst["reset"])
        if until is None or until > now_utc():
            return rem, until, f"quota '{worst_key}' exhausted" + (f" until {kst(until)}" if until else "")
    return rem, None, ""


provider_block: dict[str, dict] = {}


def _resets_far(provider: str, model: str, q: dict) -> bool:
    """True when the binding quota window resets more than reserve_days away (or never reports a reset)."""
    wins = q.get(provider, {})
    resets = [parse_ts(w.get("reset")) for w in wins.values() if w.get("remaining") is not None]
    resets = [r for r in resets if r]
    return not resets or min(resets) - now_utc() > timedelta(days=CONFIG["reserve_days"])


MODEL_SCOPED_402 = re.compile(r"not included in your|requires an active subscription|not available on your plan", re.I)


def provider_blocks(con) -> dict:
    """Providers whose most recent non-499 call was a 401/402 with no success after it (auth / credit).
    2026-10-30: a 402 that names one model outside the plan (ollama-cloud deepseek-v4-pro "not included in your free
    usage") says nothing about the provider; it blocked ollama-cloud/gpt-oss:20b, which answers 200."""
    out, seen = {}, set()
    for prov, status, ts, err in con.execute("""
        select provider, status, timestamp, coalesce(error_summary,'') from call_logs
        where timestamp >= datetime('now','-7 days') and status != 499 and path like '%chat/completions%'
        order by timestamp desc"""):
        if prov in seen or (status == 402 and MODEL_SCOPED_402.search(err)):
            continue
        seen.add(prov)
        if status in (401, 402):
            out[prov] = {"status": status, "ts": ts, "err": err}
    return out


def evaluate(model: str, provider: str, hist: dict | None, cat: dict, q: dict, conns: dict, qual: dict, probes: dict):
    """Return (score or None, details dict). None = excluded, details['why'] explains."""
    d = {"model": model, "provider": provider}
    if provider in CONFIG["exclude_providers"]:
        return None, {**d, "why": "provider excluded by config (subscription shared with Claude)"}
    picked = model in (selected_models() or {})
    if ("/auto" in model or model.startswith("auto") or provider in ("combo", "auto")) and not picked:
        return None, {**d, "why": "router-of-routers"}  # a provider's own router is used only when the user picked it
    info = cat.get(model) or {}
    caps = info.get("capabilities") or {}
    if caps and caps.get("tool_calling") is False:
        return None, {**d, "why": "no tool calling"}
    ctx = info.get("context_length")
    if ctx and ctx < CONFIG["min_context"]:
        return None, {**d, "why": f"context {ctx:,} < {CONFIG['min_context']:,}"}
    cl = conns.get(provider, [])
    if cl and not any(c["active"] and c["test"] not in ("banned",) for c in cl):
        return None, {**d, "why": "connection inactive/banned"}
    if cl and all((parse_ts(c["rl_until"]) or now_utc()) > now_utc() for c in cl):
        return None, {**d, "why": "connection rate-limited"}
    rwhy = resilience_block(provider, model, conns)
    if rwhy:  # OmniRoute would skip it anyway; a pinned conversation must not fall onto it
        return None, {**d, "why": rwhy, "temporary": True}
    rem, until, qwhy = quota_for(provider, model, q)
    if until is not None or qwhy:
        return None, {**d, "why": qwhy, "temporary": True, "until": until.isoformat() if until else None}
    fb = pool_budget(provider)
    if fb.get("remaining_frac") is not None:
        if fb["remaining_frac"] <= 0.02:
            when = f" until {kst(fb['resets'])}" if fb.get("resets") else " (one-time credit spent)"
            return None, {**d, "why": f"free budget of pool '{fb['pool']}' used up{when}", "temporary": True,
                          "until": fb["resets"].isoformat() if fb.get("resets") else None}
        if rem is None:  # no live quota snapshot: use the free-tier budget as the remaining-quota signal
            rem = fb["remaining_frac"] * 100
    d["budget"] = fb.get("remaining_frac")

    probe = probes.get(model)
    h = hist or {}
    last = h.get("last") or {}
    probe_newer = probe and (parse_ts(probe["ts"]) or now_utc()) > (parse_ts(last.get("ts")) or datetime.min.replace(tzinfo=timezone.utc))
    if probe_newer and not probe["ok"]:
        return None, {**d, "why": f"probe failed: {probe.get('err','')[:80]}", "needs_action": probe.get("status") in HARD_ERRORS}
    if not probe_newer and last.get("status") in HARD_ERRORS and (h.get("last_ok") or "") < last.get("ts", ""):
        return None, {**d, "why": f"last call {last['status']}: {last['err'][:80]}", "needs_action": True}
    hang = parse_ts(h.get("last_hang"))
    if hang and now_utc() - hang < timedelta(minutes=60) and (h.get("last_ok") or "") < h["last_hang"]:
        return None, {**d, "why": f"hung (no answer for {CONFIG['hang_s']}s+) at {kst(hang)}; resting 60 min",
                      "temporary": True, "until": (hang + timedelta(minutes=60)).isoformat()}
    if not probe_newer and last.get("status") == 429 and (h.get("last_ok") or "") < last.get("ts", ""):
        t = parse_ts(last["ts"])
        if t and now_utc() - t < timedelta(minutes=30):
            return None, {**d, "why": "rate limited (429) in last 30 min", "temporary": True,
                          "until": (t + timedelta(minutes=30)).isoformat()}

    pb = provider_block.get(provider)
    if pb and not probe_newer and (pb["status"] == 401 or (pb["status"] == 402 and not model.endswith(":free"))):
        return None, {**d, "why": f"provider-wide {pb['status']}: {pb['err'][:80]}", "needs_action": True}

    n, ok = h.get("n", 0), h.get("ok", 0)
    n7, ok7, big7 = h.get("n7", 0), h.get("ok7", 0), h.get("ok_big7", 0)
    if probe and probe["ok"]:
        n, ok = n + 1, ok + 1
    if n == 0 and n7 == 0:
        return None, {**d, "why": "no evidence yet"}
    if big7 == 0 and not (probe and probe["ok"]):
        return None, {**d, "why": "never handled an agent-sized request"}
    # 7-day rate is the prior; the last 48h move it in proportion to how much recent evidence exists,
    # so 2 lucky calls cannot outrank 136 good ones, and a model that 429s all week stays low.
    prior = (ok7 + 1) / (n7 + 2)
    success = (ok + prior * 5) / (n + 5)
    # Speed is compared on agent-sized requests only (the probe is one too). Small-request timings made a model
    # with 3 quick small calls (3.3 s) outrank one measured on real turns (9 s) that was in fact twice as fast
    # on Hermes-sized requests (2026-10-23: 18.5 s vs 8.3 s).
    durs = h.get("durs_big") or h.get("durs_big7") or ([probe["latency"]] if probe and probe["ok"] else [])
    med = statistics.median(durs) if durs else 15.0
    speed = max(0.05, min(1.0, 1 - (med - 3) / 30))
    quota_score = (rem / 100) if rem is not None else 0.6
    qual_score = tier_quality(model) or qual.get(norm(model)) or 0.5
    w = CONFIG["weights"]
    score = w["success"] * success + w["speed"] * speed + w["quota"] * quota_score + w["quality"] * qual_score
    # Evidence confidence: 0 calls -> 0, ~10 calls -> 0.5, 100+ calls -> 1. Thinly tested models are pulled
    # toward a neutral 0.5 so one lucky call cannot outrank hundreds of good ones.
    conf = min(1.0, math.log10(1 + n7 + (1 if probe and probe["ok"] else 0)) / 2)
    score = 0.5 + (score - 0.5) * (0.5 + 0.5 * conf)
    d["confidence"] = round(conf, 2)
    reserve = rem is not None and rem < CONFIG["reserve_below_pct"] and _resets_far(provider, model, q)
    if reserve:
        score -= 1.0  # keep as last-resort only: do not burn the last few % of a monthly/weekly quota
        d["note"] = f"reserve: {rem:.0f}% left"
    if qual_score < CONFIG["min_quality"]:
        return None, {**d, "why": f"quality {qual_score:.2f} below {CONFIG['min_quality']}"}
    if success < CONFIG["min_success"] and n + n7 >= 3:
        return None, {**d, "why": f"success {success:.0%} (48h {ok}/{n}, 7d {ok7}/{n7})", "temporary": True}
    if med > CONFIG["max_latency_s"]:
        return None, {**d, "why": f"too slow (median {med:.0f}s)", "temporary": True}
    last_ok = parse_ts(h.get("last_ok"))
    if probe and probe["ok"]:
        last_ok = max(last_ok or parse_ts(probe["ts"]), parse_ts(probe["ts"]))
    return round(score, 4), {**d, "success": round(success, 3), "median_s": round(med, 1), "quota": rem,
                             "quality": round(qual_score, 3), "n48h": n, "ok_big7d": big7, "ctx": ctx,
                             "last_ok": last_ok.isoformat() if last_ok else None}


# ---------------------------------------------------------------- probing
FILLER = ("Hermes agent context padding. The quick brown fox jumps over the lazy dog while the "
          "operator reviews logs, tickets, and kanban notes for the nightly supervisor run. ") * 1


def probe(model: str) -> dict:
    pad = (FILLER * (CONFIG["probe_tokens"] * 4 // len(FILLER) + 1))[: CONFIG["probe_tokens"] * 4]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a test harness. Reference material follows.\n" + pad},
            {"role": "user", "content": "Call the report_status tool with status set to ok. Do not answer in text."},
        ],
        "tools": [{"type": "function", "function": {
            "name": "report_status", "description": "Report harness status",
            "parameters": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}}}],
        "max_tokens": 400, "stream": False, "reasoning_effort": CONFIG["probe_effort"],
    }
    # 2026-10-28: without the opt-out OmniRoute adds memory_* tools and some replies call memory_save instead of
    # report_status, failing a model that works (see probe_json_schema)
    status, data, secs = api("POST", "/v1/chat/completions", body, timeout=90,
                             headers={"x-omniroute-no-memory": "true"})
    msg = ((data or {}).get("choices") or [{}])[0].get("message", {}) if status == 200 else {}
    # 2026-10-23: a real call of the tool is required; before, a reply that merely mentioned report_status in
    # its text passed, which lets through models that cannot drive Hermes' tools
    tool_ok = any((c.get("function") or {}).get("name") == "report_status" for c in msg.get("tool_calls") or [])
    ok = status == 200 and tool_ok
    err = "" if ok else (str((data or {}).get("error", ""))[:160] if status != 200 else "no tool call in reply")
    return {"ts": now_utc().isoformat(), "ok": ok, "status": status, "latency": round(secs, 1), "err": err}


def probe_json_schema(model: str) -> dict:
    """Structured-output probe shaped like Hindsight's calls (response_format json_schema, strict, name set)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Return a JSON object with the field ok set to true."}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "response", "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"], "additionalProperties": False}}},
        "max_tokens": 200, "stream": False,
    }
    # OmniRoute injects its own memory_* tools into requests; on a one-line prompt some models call them instead of
    # answering (gemini-3.8: 2 of 3 replies). `x-omniroute-no-memory` is OmniRoute's documented opt-out
    # (open-sse/handlers/chatCore/headers.ts).
    status, data, secs = api("POST", "/v1/chat/completions", body, timeout=90,
                             headers={"x-omniroute-no-memory": "true"})
    content = ((data or {}).get("choices") or [{}])[0].get("message", {}).get("content") if status == 200 else None
    ok = False
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            ok = isinstance(parsed, dict) and parsed.get("ok") is True
        except ValueError:
            ok = False
    err = "" if ok else (str((data or {}).get("error", ""))[:160] if status != 200 else "reply is not the requested JSON")
    return {"ts": now_utc().isoformat(), "ok": ok, "status": status, "latency": round(secs, 1), "err": err}


def probe_required_tool(model: str) -> dict:
    """Hindsight's reflect call shape: one tool and tool_choice="required" (Hindsight turns a named tool_choice into
    this). 2026-10-29: kilocode nemotron answered it with the call written as plain text 4 of 4 times, so Hindsight's
    page refresh failed with "produced no usable tool call" whenever the combo fell through to it."""
    pad = (FILLER * (4000 * 4 // len(FILLER) + 1))[: 4000 * 4]
    body = {
        "model": model, "max_tokens": 400, "stream": False, "tool_choice": "required",
        "messages": [{"role": "system", "content": "You answer questions about the user from their memory bank.\n" + pad},
                     {"role": "user", "content": "What operating system does the user use?"}],
        "tools": [{"type": "function", "function": {
            "name": "search_observations", "description": "Search consolidated observations about the user",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "reason": {"type": "string"}},
                           "required": ["query"]}}}],
    }
    status, data, secs = api("POST", "/v1/chat/completions", body, timeout=120,
                             headers={"x-omniroute-no-memory": "true", "x-omniroute-no-cache": "true"})
    choice = ((data or {}).get("choices") or [{}])[0] if status == 200 else {}
    ok = False
    if choice.get("finish_reason") != "length":
        for call in choice.get("message", {}).get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") != "search_observations":
                continue
            try:
                args = json.loads(function.get("arguments", ""))
                query = args.get("query") if isinstance(args, dict) else None
                ok = isinstance(query, str) and any(c.isalnum() for c in query.strip())
            except (ValueError, TypeError):
                pass
            if ok:
                break
    err = "" if ok else (str((data or {}).get("error", ""))[:160] if status != 200 else "tool call written as text, not tool_calls")
    return {"ts": now_utc().isoformat(), "ok": ok, "status": status, "latency": round(secs, 1), "err": err}


def probe_structured(model: str) -> dict:
    """Hindsight retain-shaped call: a few KB of conversation, strict json_schema with an array of facts, non-streaming.
    The small probe_json_schema passes models that then take minutes (or never finish) on real extraction."""
    convo = "\n".join(f"user: 오늘 {i}번째 작업으로 가계부 CLI의 월별 합계 기능을 고쳤고 테스트 {i * 3}개가 통과했다.\n"
                      f"assistant: 확인했습니다. ledger.py의 monthly_total 함수를 수정했고 pytest 결과를 기록했습니다."
                      for i in range(1, 25))
    body = {
        "model": model, "max_tokens": 2000, "stream": False, "temperature": 0.1,
        "messages": [{"role": "system", "content": "Extract durable facts from the conversation. Answer only with JSON."},
                     {"role": "user", "content": convo}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "facts", "strict": True, "schema": {
            "type": "object", "additionalProperties": False, "required": ["facts"],
            "properties": {"facts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["what", "when", "who"],
                "properties": {"what": {"type": "string"}, "when": {"type": "string"}, "who": {"type": "string"}}}}}}}},
    }
    timeout = CONFIG["structured_max_s"] + 30
    status, data, secs = api("POST", "/v1/chat/completions", body, timeout=timeout,
                             headers={"x-omniroute-no-memory": "true", "x-omniroute-no-cache": "true"})
    content = ((data or {}).get("choices") or [{}])[0].get("message", {}).get("content") if status == 200 else None
    ok, err = False, ""
    try:
        facts = json.loads(content).get("facts") if isinstance(content, str) else None
        ok = (isinstance(facts, list) and bool(facts) and all(
            isinstance(f, dict) and set(f) == {"what", "when", "who"}
            and all(isinstance(v, str) for v in f.values()) and bool(f["what"].strip()) for f in facts))
    except (ValueError, AttributeError):
        ok = False
    if status != 200:
        err = str((data or {}).get("error", ""))[:160] or f"no answer in {round(secs)}s"
    elif not ok:
        err = "reply is not the requested JSON"
    elif secs > CONFIG["structured_max_s"]:
        ok, err = False, f"too slow ({round(secs)}s > {CONFIG['structured_max_s']}s)"
    return {"ts": now_utc().isoformat(), "ok": ok, "status": status, "latency": round(secs, 1), "err": err}


def recent_hangs(con, hours: int = 48) -> dict:
    """requested_model -> number of hung calls (client gave up after hang_s, 499) in the last `hours`, any combo."""
    rows = con.execute("""select requested_model, count(*) from call_logs where status = 499 and duration >= ?
                          and timestamp >= ? group by requested_model""",
                       (CONFIG["hang_s"] * 1000, (now_utc() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")))
    return dict(rows.fetchall())


def hang_pattern(model: str, hangs: dict) -> bool:
    """Keeps a model out of the Hindsight lane: structured_hang_limit hung calls in 48 h (pinned models exempt)."""
    return hangs.get(model, 0) >= CONFIG["structured_hang_limit"] and not is_pinned(model)


def structured_ok(state: dict, model: str, dry_run: bool) -> tuple[bool, str]:
    """Cached probe_structured gate for the Hindsight lane (2 of 2 must pass); cached like required_tool_ok."""
    cache = state.setdefault("structured", {})
    entry, tnow = cache.get(model) or {}, now_utc()
    ts = parse_ts(entry.get("ts"))
    if ts and tnow - ts < timedelta(hours=CONFIG["reprobe_ok_hours" if entry.get("ok") else "reprobe_fail_hours"]):
        return entry["ok"], entry.get("err", "")
    if dry_run:
        return False, "not checked (dry run)"
    for _ in range(2):
        res = probe_structured(model)
        if res["status"] in (402, 429, 503):
            return False, f"not checked ({res['status']})"  # no verdict: stays out of the lane until it can be tested
        if not res["ok"]:
            break
    cache[model] = {"ok": res["ok"], "ts": res["ts"], "err": res["err"], "latency": res["latency"]}
    log(f"structured probe {model} -> {res}")
    return res["ok"], res["err"]


def required_tool_ok(state: dict, model: str, dry_run: bool) -> tuple[bool, str]:
    """Cached probe_required_tool gate (2 of 2 must pass). Passes are trusted for reprobe_ok_hours, failures are
    retried after reprobe_fail_hours. A rate-limited / unpaid answer says nothing about the model: not cached."""
    cache = state.setdefault("required_tool", {})
    entry, tnow = cache.get(model) or {}, now_utc()
    ts = parse_ts(entry.get("ts"))
    if ts and tnow - ts < timedelta(hours=CONFIG["reprobe_ok_hours" if entry.get("ok") else "reprobe_fail_hours"]):
        return entry["ok"], entry.get("err", "")
    if dry_run:
        return True, "not checked (dry run)"
    # 2026-10-29: kilocode nemotron failed 4 of 4 in the morning and passed 2 of 2 in the afternoon; a model that
    # failed within 7 days must pass 3 in a row (Hermes turns break on a tool call written as text)
    last_fail = parse_ts(entry.get("last_fail") or (entry.get("ts") if entry and not entry.get("ok") else None))
    passes = 3 if last_fail and tnow - last_fail < timedelta(days=7) else 2
    for _ in range(passes):
        res = probe_required_tool(model)
        if res["status"] in (402, 429, 503) or (res["status"] == 0):
            return True, f"not checked ({res['status']})"  # quota/availability is judged elsewhere
        if not res["ok"]:
            break
    cache[model] = {"ok": res["ok"], "ts": res["ts"], "err": res["err"],
                    "last_fail": res["ts"] if not res["ok"] else (last_fail.isoformat() if last_fail else None)}
    log(f"required-tool probe {model} -> {res}")
    return res["ok"], res["err"]


def admission_checks(model: str) -> tuple[str, bool]:
    """Extra gate for a preferred model that just passed the tool-call probe: (failure text or "", deterministic).

    deterministic=True means the model cannot do it at all (retry after preferred_admission_fail_hours);
    False means it is flaky/limited right now (retry after preferred_retry_minutes).
    """
    cfg = CONFIG.get("preferred_admission") or {}
    if cfg.get("json_schema"):
        js = probe_json_schema(model)
        if not js["ok"]:
            return f"json_schema probe failed ({js['status']}): {js['err']}", js["status"] in (400, 422)
    for n in range(2, int(cfg.get("repeats", 1)) + 1):
        again = probe(model)
        if not again["ok"]:
            return f"probe {n}/{cfg['repeats']} failed ({again['status']}): {again['err']}", False
        if again["latency"] > CONFIG["max_latency_s"]:
            return f"probe {n}/{cfg['repeats']} too slow ({again['latency']}s)", False
    return "", False


def provider_exhausted(res: dict) -> bool:
    """OmniRoute answered for the whole provider (all accounts out of quota / credits), not one model."""
    err = (res.get("err") or "").lower()
    return not res.get("ok") and ("all " in err and ("exhausted" in err or "accounts" in err)
                                  or "billing_error" in err or "payment_required" in err)


def pick_probes(hist, cat, q, conns, qual, probes) -> list[tuple[str, str]]:
    """Revival probes for proven-but-excluded models first, then discovery of strong untried models."""
    due = []
    tnow = now_utc()
    recently_exhausted = {p.get("provider") for p in probes.values() if provider_exhausted(p)
                          and tnow - (parse_ts(p["ts"]) or tnow) < timedelta(hours=CONFIG["reprobe_fail_hours"])}

    def provider_usable(prov):
        if prov in CONFIG["exclude_providers"] or prov in recently_exhausted or not probe_affordable(prov):
            return False
        pb = provider_block.get(prov)
        # 2026-10-23: a 401 used to block probing for good — nothing else calls a dead provider, so its last call
        # stayed 401 even after a re-login. Re-test it once the fail-reprobe interval has passed.
        if pb and pb["status"] == 401 and tnow - (parse_ts(pb["ts"]) or tnow) < timedelta(hours=CONFIG["reprobe_fail_hours"]):
            return False
        cl = conns.get(prov, [])
        if not cl or not any(c["active"] and c["test"] != "banned" for c in cl):
            return False
        return not all((parse_ts(c["rl_until"]) or tnow) > tnow for c in cl)

    # 2026-10-23: a provider whose last 3 probes in 24h all failed is skipped by discovery for now — it has many
    # high-tier model ids (Claude variants on devin/github/electronhub) and would otherwise take a slot every run
    recent = sorted((p for p in probes.values() if p.get("provider")
                     and tnow - (parse_ts(p.get("ts")) or tnow) < timedelta(hours=24)), key=lambda p: p["ts"])
    probe_runs: dict[str, list] = {}
    for p in recent:
        probe_runs.setdefault(p["provider"], []).append(p["ok"])
    failing = {prov for prov, oks in probe_runs.items() if len(oks) >= 3 and not any(oks[-3:])}

    def unpaid(prov, m):
        # 2026-10-23: a paid model on a provider whose last call was 402 cannot pass until a top-up; testing it
        # only wastes one of the few probe slots (kilocode/cline Claude models took them all)
        return (provider_block.get(prov) or {}).get("status") == 402 and not m.endswith(":free")

    def due_for(m):
        p = probes.get(m)
        if not p:
            return True
        age = tnow - (parse_ts(p["ts"]) or tnow)
        return age > timedelta(hours=CONFIG["reprobe_ok_hours"] if p["ok"] else CONFIG["reprobe_fail_hours"])

    for m, h in hist.items():
        if not due_for(m) or not provider_usable(h["provider"]):
            continue
        score, d = evaluate(m, h["provider"], h, cat, q, conns, qual, {})
        if score is not None:
            continue
        if h["ok_big7"] and d.get("needs_action"):
            due.append((m, h["provider"], 1.0))  # proven model that broke: has it come back?
        elif (d.get("needs_action") and (provider_block.get(h["provider"]) or {}).get("status") == 401
              and (tier_quality(m) or qual.get(norm(m)) or 0) >= CONFIG["min_quality"]):
            # 2026-10-23: auth failure on a model never proven at agent size — re-test it so a re-login is
            # noticed. Only 401: an unpaid 402 does not heal by itself and would crowd out discovery probes.
            due.append((m, h["provider"], tier_quality(m) or qual.get(norm(m))))
        elif (d["why"] == "never handled an agent-sized request" and h["ok7"]
              and (tier_quality(m) or qual.get(norm(m)) or 0.5) >= CONFIG["min_quality"]):
            # worked on small requests but never tried at agent size: untested, not broken
            # (2026-10-23: below min_quality it could never be selected, so the probe was wasted — qwen7b)
            due.append((m, h["provider"], tier_quality(m) or qual.get(norm(m)) or 0.5))
    known = set(hist)
    # 2026-10-23: an effort variant of a model this provider already serves fine teaches nothing new (and is
    # deduplicated out of the combo anyway); grok-4.7-low/-medium/-high took a probe slot every run
    working = ({(h["provider"], base_model(m)) for m, h in hist.items() if h["ok7"]}
               | {(p.get("provider"), base_model(m)) for m, p in probes.items() if p.get("ok")})
    for m, info in cat.items():
        if m in known or "/auto" in m or info.get("owned_by") in (None, "combo"):
            continue
        if (info["owned_by"], base_model(m)) in working:
            continue
        caps = info.get("capabilities") or {}
        # 2026-10-23: our own family tiers count too; with the arena score alone, new models without one
        # (most recent releases) were never discovered
        qs = tier_quality(m) or qual.get(norm(m))
        if (not caps.get("tool_calling") or (info.get("context_length") or 0) < CONFIG["min_context"]
                or qs is None or qs < CONFIG["min_quality"]):
            continue
        prov = info["owned_by"]
        if not provider_usable(prov) or not due_for(m) or unpaid(prov, m) or prov in failing:
            continue
        if quota_for(prov, m, q)[1] is not None:
            continue
        due.append((m, prov, qs))
    due.sort(key=lambda x: -x[2])
    seen_norm, seen_prov, picked = set(), set(), []
    for m, prov, _ in due:
        if norm(m) in seen_norm or prov in seen_prov:
            continue  # one probe per provider per run (variants share a quota pool), aliases once
        if not provider_allows(prov, m):
            # 2026-10-30: filtered here, not after the cut; six unpicked models filled every slot and the
            # user's pick kiro/auto was never probed
            continue
        rem = quota_for(prov, m, q)[0]
        if rem is not None and rem < 15:
            continue  # do not spend a nearly empty quota on tests
        seen_norm.add(norm(m))
        seen_prov.add(prov)
        picked.append((m, prov))
        if len(picked) >= CONFIG["probes_per_run"]:
            break
    return picked


# ---------------------------------------------------------------- combo update
def pinned_candidate(model: str, h: dict, cat, q, conns, qual, probes: dict) -> dict:
    """Candidate for a user-pinned model: kept whatever its speed or recent failures, with the fields the report prints."""
    provider = model.split("/", 1)[0]
    score, d = evaluate(model, provider, h, cat, q, conns, qual, probes)
    if score is None:  # excluded on statistics (slow, failing)
        p = probes.get(model) or {}
        score = 0.5
        d = {"success": round(h.get("ok", 0) / h["n"], 3) if h.get("n") else 0.0, "median_s": p.get("latency") or 0,
             "quota": quota_for(provider, model, q)[0], "quality": tier_quality(model) or qual.get(norm(model)) or 0.5,
             "n48h": h.get("n", 0), "ok_big7d": h.get("ok_big7", 0), "last_ok": h.get("last_ok"), **d, "why": ""}
    return {**d, "model": model, "provider": provider, "score": score}


def preferred_candidates(state, cat, hist, q, conns, qual, dry_run=False, models=None, availability_only=False):
    """Keep user order, but never revive a suspended model without a successful live probe.

    `models` replaces CONFIG preferred_models; `availability_only` keeps a model that passes the quota, connection,
    failure and recovery gates at its rank whatever its speed or success rate (the lane pins' cooldown).

    Suspension survives restarts. Quota windows gate the affected model/family (Gemini and
    Claude/GPT have separate windows); a provider-wide gate is only used if OmniRoute says so.
    Unknown reset times are retried every preferred_retry_minutes, not on every request.
    """
    selected, excluded, notes = [], [], []
    statuses = state.setdefault("preferred_status", {})
    tnow = now_utc()
    for rank, model in enumerate(CONFIG.get("preferred_models", []) if models is None else models, 1):
        provider = model.split("/", 1)[0]
        entry = statuses.setdefault(model, {})
        h = hist_for(model, hist) or {}
        p = state["probes"].get(model) or {}
        if is_pinned(model):
            # 2026-10-29 user: keep it at its rank whatever its speed or recent failures
            entry.clear()
            selected.append({**pinned_candidate(model, h, cat, q, conns, qual, state["probes"]),
                             "preferred_rank": rank, "note": f"pinned by user #{rank}"})
            continue
        last = h.get("last") or {}
        success_times = [t for t in (parse_ts(h.get("last_ok")),
                                    parse_ts(p.get("ts")) if p.get("ok") else None) if t]
        last_ok = max(success_times, default=datetime.min.replace(tzinfo=timezone.utc))

        def suspend(why, until=None, event=None):
            entry.update({"suspended": True, "reason": why})
            # Preserve an unknown-reset retry deadline instead of pushing it out every tune.
            if until:
                entry["retry_at"] = until.isoformat()
            elif not entry.get("retry_at") or (event and entry.get("event") != event):
                entry["retry_at"] = (tnow + timedelta(minutes=CONFIG["preferred_retry_minutes"])).isoformat()
            if event:
                entry["event"] = event
            excluded.append({"model": model, "provider": provider, "why": "preferred: " + why,
                             "temporary": True, "until": entry.get("retry_at")})

        remaining, reset, quota_why = quota_for(provider, model, q)
        if quota_why:
            suspend(quota_why, reset)
            continue
        active = [c for c in conns.get(provider, []) if c["active"] and c["test"] != "banned"]
        blocked = ("provider excluded by config" if provider in CONFIG["exclude_providers"] else
                   "connection inactive/banned" if not active else
                   "connection rate-limited" if all((parse_ts(c["rl_until"]) or tnow) > tnow for c in active) else
                   resilience_block(provider, model, conns))
        if blocked:
            suspend(blocked)
            continue
        # A new failed production call or verification result must beat an earlier successful probe.
        failures = []
        if last and not 200 <= (last.get("status") or 0) < 300:
            failures.append((parse_ts(last.get("ts")), last.get("status"), last.get("err", "call failed")))
        if p and not p.get("ok"):
            failures.append((parse_ts(p.get("ts")), p.get("status"), p.get("err", "probe failed")))
        failures = [f for f in failures if f[0] and f[0] > last_ok]
        if failures:
            failed_at, status, err = max(failures, key=lambda f: f[0])
            event = failed_at.isoformat()
            if entry.get("event") != event:
                hours = CONFIG["reprobe_fail_hours"] if status in (401, 402, 403) else CONFIG["preferred_retry_minutes"] / 60
                entry.update({"suspended": True, "reason": f"last failure {status}: {err[:100]}",
                              "event": event, "retry_at": (failed_at + timedelta(hours=hours)).isoformat()})
        due = parse_ts(entry.get("retry_at"))
        if (entry.get("suspended") and entry.get("reason", "").startswith("quota '")
                and remaining is not None and remaining > 0.5):
            due = tnow  # fresh quota telemetry can recover before the old advertised reset
        if entry.get("suspended") and due and due > tnow:
            suspend(entry["reason"])
            continue
        stale = tnow - last_ok >= timedelta(hours=CONFIG["incumbent_fresh_hours"])
        if entry.get("suspended") or stale:
            if dry_run:
                suspend("needs recovery/admission probe")
                continue
            p = probe(model)
            p["provider"] = provider
            state["probes"][model] = p
            notes.append(f"- preferred #{rank} {model}: {'PASS' if p['ok'] else 'FAIL'} {p['latency']}s {p['err']}")
            deterministic = False
            if p["ok"] and (availability_only or p["latency"] <= CONFIG["max_latency_s"]):
                # 2026-10-28: passing one tool-call probe is not enough (see CONFIG["preferred_admission"])
                why, deterministic = admission_checks(model)
                if why:
                    p.update(ok=False, err=why)
                    notes.append(f"- preferred #{rank} {model}: admission FAIL {why}")
            if not p["ok"] or (not availability_only and p["latency"] > CONFIG["max_latency_s"]):
                if p["ok"]:
                    p.update(ok=False, err=f"too slow ({p['latency']}s)")
                delay = (timedelta(hours=CONFIG["reprobe_fail_hours"]) if p.get("status") in (401, 402, 403)
                         else timedelta(hours=CONFIG["preferred_admission_fail_hours"]) if deterministic
                         else timedelta(minutes=CONFIG["preferred_retry_minutes"]))
                suspend(p["err"] or "probe failed", tnow + delay, event=p["ts"])
                continue
            entry.clear()
        if availability_only:
            entry.clear()
            selected.append({**pinned_candidate(model, h, cat, q, conns, qual, state["probes"]), "note": "lane pin"})
            continue
        score, d = evaluate(model, provider, h, cat, q, conns, qual, state["probes"])
        if score is None:
            # User preference overrides statistical ranking, not capability or health exclusions.
            # A fresh live success allows a recovering model with a poor historical average back in.
            if not (p.get("ok") and str(d.get("why", "")).startswith(("success ", "too slow "))):
                suspend(d["why"])
                continue
            score = 0.5
            d.update(success=1.0, median_s=p["latency"], quota=quota_for(provider, model, q)[0],
                     quality=tier_quality(model) or qual.get(norm(model)) or 0.5,
                     n48h=h.get("n", 0), ok_big7d=h.get("ok_big7", 0), last_ok=p["ts"])
        entry.clear()
        selected.append({**d, "score": score, "preferred_rank": rank, "note": f"user priority #{rank}"})
    return selected, excluded, notes


def lane_pin_models() -> list[str]:
    """Models pinned inside a lane (CONFIG lanes[].pinned), once each."""
    return list(dict.fromkeys(pin["model"] for lane in CONFIG["lanes"] for pin in lane.get("pinned", [])))


def lane_pin_candidates(state, cat, hist, q, conns, qual, dry_run=False):
    """(candidates of lane pins that may hold their rank now, models cooling down, excluded entries, probe notes).
    2026-11-01 user: a lane pin stays first by design, but gets the preferred models' cooldown: out while its quota is
    exhausted or it keeps failing, back at its rank after a passing recovery probe."""
    models = lane_pin_models()
    ready, out, notes = preferred_candidates(state, cat, hist, q, conns, qual, dry_run, models, availability_only=True)
    return {d["model"]: d for d in ready}, {m for m in models if m not in {d["model"] for d in ready}}, out, notes


def preferred_check_reasons(state, con, current):
    """Cheap 3-minute check: notice quota loss/recovery even when fallback traffic succeeds."""
    q, reasons, tnow = quotas(con), [], now_utc()
    lane_only = [m for m in lane_pin_models() if m not in CONFIG.get("preferred_models", [])]
    cooling = {m for m in lane_pin_models() if (state.get("preferred_status", {}).get(m) or {}).get("suspended")}
    if cooling:
        status, data, _ = api("GET", "/api/combos")
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("combos"), list):
            reasons.append("cannot verify cooling lane pins")
        else:
            combos = {c["name"]: c for c in data["combos"]}
            for lane in CONFIG["lanes"]:
                pins = {p["model"] for p in lane.get("pinned", [])} & cooling
                if pins and lane["combo"] in combos and any(m["model"] in pins for m in combos[lane["combo"]].get("models", [])):
                    reasons.append(f"cooling pin still in lane {lane['combo']}")
    for model in CONFIG.get("preferred_models", []) + lane_only:
        if is_pinned(model):
            continue  # a re-tune never moves a pinned model, so its failures are no reason to run one
        provider = model.split("/", 1)[0]
        remaining, _, why = quota_for(provider, model, q)
        entry = state.get("preferred_status", {}).get(model) or {}
        active = model in current or (model in lane_only and not entry.get("suspended"))  # a lane pin is in unless cooling
        if active and why:
            reasons.append(f"preferred {model}: {why}")
        elif not active:
            due = parse_ts(entry.get("retry_at"))
            refilled = entry.get("reason", "").startswith("quota '") and remaining is not None and remaining > 0.5
            if not why and (refilled or not due or due <= tnow):
                reasons.append(f"preferred {model}: recovery check due")
        if active:
            last = con.execute("""select status, timestamp from call_logs
                where requested_model = ? and path like '%chat/completions%'
                and (status != 499 or duration >= ?) order by timestamp desc limit 1""",
                (model, CONFIG["hang_s"] * 1000)).fetchone()
            p = state.get("probes", {}).get(model) or {}
            if last and not 200 <= (last[0] or 0) < 300:
                failed_at = parse_ts(last[1])
                successful_probe = parse_ts(p.get("ts")) if p.get("ok") else None
                if failed_at and (not successful_probe or failed_at > successful_probe):
                    reasons.append(f"preferred {model}: last call failed ({last[0]})")
    return reasons


def order_for_fallback(selected: list[dict], probes: dict) -> list[dict]:
    """Score order, adjusted so the fallback chain can actually carry Hermes (2026-10-23):
    a free pool with fewer than min_pool_turns Hermes turns left goes behind the rest, and #2 comes from a
    different provider than #1 — two models of one provider share its quota and fail together (since 10-20
    every success came from antigravity, which held both #1 and #2)."""
    def pool_left(d):
        b = pool_budget(d["provider"])
        if not b.get("budget") or b.get("remaining_frac") is None:
            return None
        return b["budget"] * b["remaining_frac"]

    need = CONFIG["min_pool_turns"] * CONFIG["turn_tokens"]
    roomy = [d for d in selected if pool_left(d) is None or pool_left(d) >= need]
    small = [d for d in selected if d not in roomy]
    for d in small:
        d["note"] = (d.get("note", "") + "; " if d.get("note") else "") + f"small free pool: {pool_left(d):,.0f} tokens left"
    ordered = roomy + small

    if not any(d.get("preferred_rank") for d in ordered):
        ordered = second_from_other_group(ordered, probes)
    preferred = sorted((d for d in ordered if d.get("preferred_rank")), key=lambda d: d["preferred_rank"])
    return preferred + [d for d in ordered if not d.get("preferred_rank")]


def second_from_other_group(ordered: list[dict], probes: dict) -> list[dict]:
    """Move the best proven candidate of another provider group to #2 when #1 and #2 share one."""
    if len(ordered) < 3 or provider_group(ordered[1]["provider"]) != provider_group(ordered[0]["provider"]):
        return ordered

    def proven(d):
        p = probes.get(d["model"]) or {}
        fresh = p.get("ok") and now_utc() - (parse_ts(p.get("ts")) or now_utc()) < timedelta(hours=CONFIG["incumbent_fresh_hours"])
        return d.get("ok_big7d", 0) > 0 or fresh

    alt = next((d for d in ordered[2:] if provider_group(d["provider"]) != provider_group(ordered[0]["provider"])
                and proven(d)), None)
    if not alt:
        return ordered
    alt["note"] = (alt.get("note", "") + "; " if alt.get("note") else "") + "#2 from another provider"
    return [ordered[0], alt] + [d for d in ordered[1:] if d is not alt]


def assign_weights(selected: list[dict]) -> None:
    """Traffic share per candidate (integers summing to ~100) = score^k x remaining-budget factor."""
    if not selected:
        return
    raw = []
    for s in selected:
        budget = s["quota"] / 100 if s.get("quota") is not None else CONFIG["unknown_budget"]
        # budget only tilts the share (0.5x..1x); quality-heavy score decides who gets most traffic
        raw.append(max(s["score"], 0.01) ** CONFIG["weight_exponent"] * (0.5 + 0.5 * max(0.0, min(1.0, budget))))
    total = sum(raw)
    for s, r in zip(selected, raw):
        s["weight"] = max(1, round(100 * r / total))


def route_pins(conns: dict) -> dict[str, list[str]]:
    """provider -> ids of its active connections, for each CONFIG route_pinned_providers entry that has any."""
    pins = {}
    for prov in CONFIG.get("route_pinned_providers", []):
        ids = [c["id"] for c in conns.get(prov, []) if c["active"]]
        if ids:
            pins[prov] = ids
        else:
            # OmniRoute skips a step whose allow-list is empty, so an unpinned step is the only safe fallback
            log(f"WARNING route pin: no active {prov} connection; its combo steps stay unpinned")
    return pins


def build_models(selected: list[dict], pins: dict[str, list[str]], name: str | None = None) -> list[dict]:
    name = name or CONFIG["combo_name"]
    out = []
    for i, s in enumerate(selected, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", s["model"].lower()).strip("-")
        step = {"id": f"{name}-model-{i}-{slug}"[:120], "kind": "model", "model": s["model"],
                "providerId": s["provider"], "weight": s.get("weight", 0)}
        if pins.get(s["provider"]):
            step["allowedConnectionIds"] = list(pins[s["provider"]])
        out.append(step)
    return out


def step_routes(models: list[dict]) -> list[tuple[str, tuple[str, ...]]]:
    """What a combo's steps route to: (model, allowed connection ids); compared to decide whether a PUT is needed."""
    return [(m["model"], tuple(sorted(m.get("allowedConnectionIds") or ()))) for m in models]


def config_drift(combo: dict) -> bool:
    """True when the main combo's live config differs from what CONFIG sets (maxRetries, main_combo_config)."""
    cfg = combo.get("config") or {}
    return (cfg.get("maxRetries") != CONFIG["combo_max_retries"]
            or any(cfg.get(k) != v for k, v in CONFIG["main_combo_config"].items()))


def put_combo(combo: dict, models: list[dict], strategy: str | None = None) -> tuple[int, dict]:
    config = dict(combo.get("config") or {})
    config["candidatePool"] = [m["providerId"] for m in models]
    if strategy is None:  # a rollback (explicit strategy) restores the saved config untouched
        config["maxRetries"] = CONFIG["combo_max_retries"]
        if combo["name"] == CONFIG["combo_name"]:
            config.update(CONFIG["main_combo_config"])
    body = {"name": combo["name"], "models": models, "strategy": strategy or CONFIG["strategy"], "config": config}
    status, data, _ = api("PUT", f"/api/combos/{combo['id']}", body)
    return status, data


def verify_combo(name: str | None = None) -> tuple[bool, str]:
    body = {"model": name or CONFIG["combo_name"], "max_tokens": 60, "stream": False,
            "messages": [{"role": "user", "content": f"Call the report_status tool with status ok. Request {uuid.uuid4().hex}"}],
            "tools": [{"type": "function", "function": {"name": "report_status", "description": "report",
                       "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}}]}
    for attempt in (1, 2):
        # 2026-10-28: same memory opt-out as probe(); without it OmniRoute adds memory_* tools and the reply can
        # call memory_save instead of report_status (the 23:14 tune run's verify request got 39 memories injected)
        status, data, secs = api("POST", "/v1/chat/completions", body, timeout=120,
                                 headers={"x-omniroute-no-memory": "true", "x-omniroute-no-cache": "true"})
        if status == 200 and isinstance(data, dict):
            choices = data.get("choices") or []
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") or {}
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if call.get("type") != "function" or function.get("name") != "report_status":
                    continue
                try:
                    arguments = json.loads(function.get("arguments", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(arguments, dict) and arguments.get("status") == "ok" and choice.get("finish_reason") != "length":
                    return True, f"served by {data.get('model')} in {secs:.1f}s (attempt {attempt})"
            last = "200 without a complete report_status(status=ok) tool call"
        else:
            last = f"{status} {str((data or {}).get('error',''))[:120]}"
    return False, last


def model_family(model: str) -> str:
    """'kilocode/nvidia/nemotron-3-super-120b-a12b:free' -> 'nemotron', 'antigravity/gemini-3-flash' -> 'gemini'."""
    name = model.lower().split("/")[-1]
    m = re.match(r"[a-z]+\d*", name)  # letters up to the first '-' / '.': gemini, nemotron, glm, grok, o3, hy4
    return m.group(0) if m else name


def role_candidates(name: str, rows: list[dict]) -> list[dict]:
    excluded = CONFIG.get("role_exclusions", {}).get(name, {})
    return [row for row in rows if row["model"] not in excluded]


def apply_lane_rules(lane: dict, order: list[dict], selected: list[dict], lane_pins: dict[str, dict],
                     cooling: frozenset | set = frozenset()) -> list[dict]:
    """The lane's own "primary" (to #1 if present) and "pinned" (forced in at its rank) on top of `order`;
    a pinned model in `cooling` is left out of the lane until it recovers."""
    first = next((d for d in order if d["model"] == lane.get("primary")), None)
    if first:
        order = [first] + [d for d in order if d is not first]
    for pin in lane.get("pinned", []):
        if pin["model"] in cooling:
            order = [x for x in order if x["model"] != pin["model"]]
            continue
        d = next((x for x in selected if x["model"] == pin["model"]), None) or lane_pins.get(pin["model"])
        if d is None:
            continue
        order = [x for x in order if x is not d]
        order.insert(pin["rank"] - 1, d)
    return role_candidates(lane["combo"], order)


def lane_orders(selected: list[dict], lane_pins: dict[str, dict] | None = None,
                cooling: frozenset | set = frozenset()) -> list[tuple[dict, list[dict]]]:
    """[(lane, ordered candidates)]: each lane after the first gets as #1 the best candidate on a provider and
    model family no earlier lane starts with; the rest of the list follows in score order. `lane_pins` holds the
    candidates of lane-pinned models (model -> candidate) that are not in `selected`; `cooling` holds the pinned
    models that must not hold a lane rank right now."""
    lane_pins = lane_pins or {}
    out, used_prov, used_fam = [], set(), set()
    for i, lane in enumerate(CONFIG["lanes"]):
        if lane.get("structured"):
            # Hindsight lane: same score order (pinned #1 kept), only candidates that passed probe_structured
            order = apply_lane_rules(lane, [d for d in selected if d.get("structured_ok")], selected, lane_pins, cooling)
            if order:
                out.append((lane, order))
            continue
        if i == 0 or not selected:
            primary = selected[0] if selected else None
        else:
            good = [d for d in selected if d["quality"] >= CONFIG["lane_min_quality"]]
            primary = (next((d for d in good if provider_group(d["provider"]) not in used_prov
                             and model_family(d["model"]) not in used_fam), None)
                       or next((d for d in good if provider_group(d["provider"]) not in used_prov), None)
                       or selected[0])
        if primary is None:
            continue
        order = apply_lane_rules(lane, [primary] + [d for d in selected if d is not primary], selected, lane_pins, cooling)
        if not order:
            continue
        used_prov.add(provider_group(order[0]["provider"]))
        used_fam.add(model_family(order[0]["model"]))
        out.append((lane, order))
    return out


def sync_lane(name: str, order: list[dict], combos: list[dict], pins: dict[str, list[str]]) -> str:
    """Create or update one lane combo to `order`; verify with a live call, roll back on failure."""
    combo = next((c for c in combos if c["name"] == name), None)
    models = build_models(order, pins, name)
    if combo is None:
        status, data, _ = api("POST", "/api/combos", {"name": name, "models": models, "strategy": CONFIG["strategy"],
                                                      "config": {"maxRetries": CONFIG["combo_max_retries"],
                                                                 "candidatePool": [m["providerId"] for m in models]}})
        if status not in (200, 201):
            return f"CREATE FAILED {status} {str(data)[:120]}"
        ok, msg = verify_combo(name)
        return f"created; verify: {msg}" if ok else f"created but VERIFY FAILED: {msg}"
    if (step_routes(combo["models"]) == step_routes(models) and combo.get("strategy") == CONFIG["strategy"]
            and (combo.get("config") or {}).get("maxRetries") == CONFIG["combo_max_retries"]):
        return "unchanged"
    backup = ROOT / "backups" / f"{name}-{datetime.now(KST).strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(json.dumps(combo, ensure_ascii=False, indent=1), encoding="utf-8")
    status, data = put_combo(combo, models)
    if status != 200:
        return f"UPDATE FAILED {status} {str(data)[:120]}"
    ok, msg = verify_combo(name)
    if ok:
        return f"updated; verify: {msg}"
    put_combo(combo, combo["models"], strategy=combo.get("strategy"))
    return f"ROLLED BACK (verify failed: {msg})"


def remove_cooling_lane_pins(lane: dict, cooling: set, combos: list[dict], dry_run: bool) -> tuple[str, list[dict]]:
    combo = next((c for c in combos if c["name"] == lane["combo"]), None)
    if combo is None:
        return "cannot update: lane missing", []
    models = [m for m in combo["models"] if m["model"] not in cooling]
    if len(models) == len(combo["models"]):
        return "unchanged", models
    if not models:
        return "cannot update: no surviving models", []
    if dry_run:
        return "would remove cooling pins (dry run)", models
    backup = ROOT / "backups" / f"{combo['name']}-{datetime.now(KST).strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(json.dumps(combo, ensure_ascii=False, indent=1), encoding="utf-8")
    body = {key: combo[key] for key in ("name", "strategy", "config") if key in combo}
    body["models"] = models
    status, data, _ = api("PUT", f"/api/combos/{combo['id']}", body)
    if status != 200:
        return f"UPDATE FAILED {status} {str(data)[:120]}", models
    ok, msg = verify_combo(combo["name"])
    if ok:
        return f"updated; verify: {msg}", models
    body["models"] = combo["models"]
    rollback_status, _, _ = api("PUT", f"/api/combos/{combo['id']}", body)
    if rollback_status != 200:
        return f"ROLLBACK FAILED {rollback_status} (verify failed: {msg})", models
    return f"ROLLED BACK (verify failed: {msg})", models


# ---------------------------------------------------------------- reports
def write_report(kind: str, lines: list[str], record: dict) -> Path:
    day = datetime.now(KST).strftime("%Y-%m-%d")
    folder = ROOT / "reports" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(KST).strftime('%H%M%S')}-{kind}.md"
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    (ROOT / "reports" / "latest.md").write_text(text, encoding="utf-8")
    with open(ROOT / "reports" / "history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": kst(), "kind": kind, **record}, ensure_ascii=False) + "\n")
    return path


def fmt_q(v):
    return "  -" if v is None else f"{v:3.0f}%"


# ---------------------------------------------------------------- commands
def cmd_tune(dry_run: bool = False, reason: str = "scheduled") -> int:
    state = load_state()
    con = db()
    combo = combo_get(CONFIG["combo_name"])
    old = [m["model"] for m in combo["models"]]
    cat, hist, q, conns, qual = catalog(), history(con), quotas(con), connections(con), quality(con)
    provider_block.clear()
    provider_block.update(provider_blocks(con))
    budgets.clear()
    budgets.update(free_budgets(con))
    resilience.update(live_resilience())
    pins = route_pins(conns)

    preferred, preferred_out, probe_lines = preferred_candidates(state, cat, hist, q, conns, qual, dry_run)
    lane_pins, lane_cooling, lane_pin_out, lane_pin_lines = lane_pin_candidates(state, cat, hist, q, conns, qual, dry_run)
    probe_lines += lane_pin_lines
    if not dry_run:
        for model, prov in pick_probes(hist, cat, q, conns, qual, state["probes"]):
            if model in CONFIG.get("preferred_models", []) or model in lane_cooling:
                continue  # preferred admission/recovery has its own quota gate and retry clock
            res = probe(model)
            res["provider"] = prov
            state["probes"][model] = res
            probe_lines.append(f"- {model}: {'OK' if res['ok'] else 'FAIL'} {res['latency']}s {res['err']}")
            log(f"probe {model} -> {res}")

    universe = {m: h["provider"] for m, h in hist.items()}
    for m, p in (selected_models() or {}).items():
        universe.setdefault(m, p)  # a pick with no history yet is still evaluated (and admission-probed)
    for m, p in state["probes"].items():
        universe.setdefault(m, p.get("provider") or (cat.get(m) or {}).get("owned_by") or m.split("/")[0])
    scored, excluded = [], list(preferred_out) + lane_pin_out
    for m, prov in universe.items():
        if m in CONFIG.get("preferred_models", []) or m in lane_cooling:
            continue  # cooling models cannot sneak back in via ordinary ranking
        if not provider_allows(prov, m):
            continue
        score, d = evaluate(m, prov, hist_for(m, hist), cat, q, conns, qual, state["probes"])
        (scored if score is not None else excluded).append({**d, "score": score})
    scored.sort(key=lambda d: -d["score"])

    selected = list(preferred)
    per_prov, seen, admission_lines, admissions = {}, set(), [], 0
    for d in selected:
        g = provider_group(d["provider"])
        per_prov[g] = per_prov.get(g, 0) + 1
        seen.add((g, base_model(d["model"])))
    fresh = now_utc() - timedelta(hours=CONFIG["admission_fresh_hours"])
    member_fresh = now_utc() - timedelta(hours=CONFIG["incumbent_fresh_hours"])
    # providers OmniRoute reported as wholly exhausted / unpaid in the last reprobe window, unless a later
    # probe of the same provider succeeded
    window = timedelta(hours=CONFIG["reprobe_fail_hours"])
    last_ok_probe = {}
    for p in state["probes"].values():
        if p.get("ok") and p.get("provider"):
            g = provider_group(p["provider"])
            last_ok_probe[g] = max(last_ok_probe.get(g, ""), p["ts"])
    blocked_now = {provider_group(p["provider"]) for p in state["probes"].values()
                   if provider_exhausted(p) and parse_ts(p["ts"]) and now_utc() - parse_ts(p["ts"]) < window
                   and last_ok_probe.get(provider_group(p.get("provider")), "") < p["ts"]}
    # 2026-10-29: a hang rests the model, not just the alias that hung (kilocode/... hung for 300 s on Hindsight
    # calls and kc/... of the same provider and model was admitted in its place a minute later)
    resting = {}
    for m, h in hist.items():
        hang = parse_ts(h.get("last_hang"))
        if hang and now_utc() - hang < timedelta(minutes=60) and (h.get("last_ok") or "") < h["last_hang"]:
            resting[(h.get("provider"), base_model(m))] = hang
    for d in scored:
        key = base_model(d["model"])
        group = provider_group(d["provider"])
        if group in blocked_now:
            continue
        if (d["provider"], key) in resting:
            excluded.append({**d, "why": f"same model hung at {kst(resting[(d['provider'], key)])}; resting 60 min",
                             "temporary": True})
            continue
        # a picked model is the user's choice even when it is a reasoning variant of a model already in the list
        dup = (group, key) in seen and d["model"] not in (selected_models() or {})
        if per_prov.get(group, 0) >= CONFIG["max_per_provider_override"].get(group, CONFIG["max_per_provider"]) or dup:
            continue
        stale = not d.get("last_ok") or parse_ts(d["last_ok"]) < (fresh if d["model"] not in old else member_fresh)
        if stale:
            # Admission test: a newcomer without a recent success must pass a live tool-call probe first,
            # and so must a member that has not succeeded for a day (it would otherwise ride on an old probe).
            if not probe_affordable(d["provider"]):
                continue  # testing it would burn a large share of a small free pool
            if dry_run:
                d["note"] = "needs admission probe"
            elif admissions >= CONFIG["admission_probes_per_run"]:
                continue
            else:
                admissions += 1
                res = probe(d["model"])
                res["provider"] = d["provider"]
                state["probes"][d["model"]] = res
                admission_lines.append(f"- {d['model']}: {'PASS' if res['ok'] else 'FAIL'} {res['latency']}s {res['err']}")
                log(f"admission probe {d['model']} -> {res}")
                if provider_exhausted(res):
                    blocked_now.add(group)
                if not res["ok"] or res["latency"] > CONFIG["max_latency_s"]:
                    continue
                d["median_s"] = d["median_s"] if d.get("n48h") else res["latency"]
        selected.append(d)
        per_prov[group] = per_prov.get(group, 0) + 1
        seen.add((group, key))
        if len(selected) >= target_size():
            break
    selected = [d for d in selected if d.get("preferred_rank") or provider_group(d["provider"]) not in blocked_now]
    selected = order_for_fallback(selected, state["probes"])
    # lanes keep the plain fallback order (hindsight-structured is the user's to change); only the main combo hedges
    lane_base = list(selected)
    selected = role_candidates(CONFIG["combo_name"], selected)
    selected = second_from_other_group(selected, state["probes"])
    assign_weights(selected)
    new = [d["model"] for d in selected]
    models = build_models(selected, pins)
    routes_changed = step_routes(models) != step_routes(combo["models"])
    old_weights = {m["model"]: m.get("weight", 0) for m in combo["models"]}
    weights_changed = any(abs(old_weights.get(d["model"], -99) - d["weight"]) >= 3 for d in selected)
    added, removed = [m for m in new if m not in old], [m for m in old if m not in new]

    action, verify_msg = "unchanged", ""
    # Do not preserve a known-bad preferred model solely to meet the usual minimum list size.
    removing_preferred = any(m in old and m not in new for m in CONFIG.get("preferred_models", []))
    # 2026-10-29: a 402 member is skipped by OmniRoute itself: 402 was added to the official model lockout codes
    # (settings modelLockout.errorCodes, docs/architecture/RESILIENCE_GUIDE.md section 3) instead of dropping it here
    # 2026-10-29: a member the user's provider rules now forbid (agy/* after agy joined the antigravity group) is
    # removed even when that leaves fewer than min_size models
    dropping_restricted = [m["model"] for m in combo["models"]
                           if not provider_allows(m.get("providerId") or m["model"].split("/")[0], m["model"])
                           and m["model"] not in new]
    # 2026-10-29: min_size only protects a bigger old list; an old list already below min_size and no longer than the new one
    # (2 models after agy joined the antigravity group, grok-4.7 out of daily usage) is not worth keeping
    safe_to_update = update_allowed(len(selected), len(old), bool(removing_preferred or dropping_restricted or len(role_candidates(CONFIG["combo_name"], combo["models"])) < len(old)))
    if not safe_to_update:
        action = f"kept old list (only {len(selected)} eligible < min {CONFIG['min_size']})"
    elif (routes_changed or weights_changed or combo.get("strategy") != CONFIG["strategy"]
          or config_drift(combo)) and not dry_run:
        backup = ROOT / "backups" / f"{CONFIG['combo_name']}-{datetime.now(KST).strftime('%Y%m%d-%H%M%S')}.json"
        backup.write_text(json.dumps(combo, ensure_ascii=False, indent=1), encoding="utf-8")
        status, data = put_combo(combo, models)
        if status != 200:
            action = f"UPDATE FAILED {status} {str(data)[:150]}"
        else:
            ok, verify_msg = verify_combo()
            if ok:
                action = "updated"
            else:
                put_combo(combo, combo["models"], strategy=combo.get("strategy"))
                action = f"ROLLED BACK (verify failed: {verify_msg})"
        log(f"tune: {action}; +{added} -{removed}")
    elif dry_run and (routes_changed or combo.get("strategy") != CONFIG["strategy"]):
        action = "would update (dry run)"

    lane_lines = []
    if safe_to_update or lane_cooling:
        st, data, _ = api("GET", "/api/combos")
        combos = (data or {}).get("combos", []) if st == 200 else []
        if safe_to_update:
            if any(lane.get("structured") for lane in CONFIG["lanes"]):
                hangs = recent_hangs(con)
                for d in lane_base:
                    if hang_pattern(d["model"], hangs):
                        d["structured_ok"], d["structured_why"] = False, f"hung {hangs[d['model']]}x in 48h"
                    else:
                        req_ok, req_why = required_tool_ok(state, d["model"], dry_run)
                        if req_ok:
                            d["structured_ok"], d["structured_why"] = structured_ok(state, d["model"], dry_run)
                        else:
                            d["structured_ok"], d["structured_why"] = False, f"tool_choice=required: {req_why}"
            lane_work = lane_orders(lane_base, lane_pins, lane_cooling)
        else:
            lane_work = [(lane, []) for lane in CONFIG["lanes"]
                         if any(pin["model"] in lane_cooling for pin in lane.get("pinned", []))]
        for lane, order in lane_work:
            if lane["combo"] == CONFIG["combo_name"]:
                continue
            if st != 200:
                lane_lines.append(f"| {lane['combo']} | {lane['role']} | - | - | - | combo list unavailable |")
                continue
            if safe_to_update:
                res = "dry run" if dry_run else sync_lane(lane["combo"], order, combos, pins)
                first = order[0] if order else {}
            else:
                cooled = {pin["model"] for pin in lane.get("pinned", [])} & lane_cooling
                res, retained = remove_cooling_lane_pins(lane, cooled, combos, dry_run)
                first = retained[0] if retained else {}
            if not dry_run:
                log(f"lane {lane['combo']}: {res}")
            model = first.get("model", "-")
            provider = first.get("provider") or first.get("providerId") or "-"
            lane_lines.append(f"| {lane['combo']} | {lane['role']} | {model} | {provider} | "
                              f"{model_family(model)} | {res} |")
        if selected:
            lane_lines.insert(0, f"| {CONFIG['combo_name']} | {CONFIG['lanes'][0]['role']} | {selected[0]['model']} | "
                                 f"{selected[0]['provider']} | {model_family(selected[0]['model'])} | {action} |")

    needs_action = sorted({d["provider"]: d["why"] for d in excluded if d.get("needs_action")}.items())
    temporary = [d for d in excluded if d.get("temporary")]
    lines = [f"# {CONFIG['combo_name']} auto-tune — {kst()} (KST)", "",
             f"- reason: {reason}{' (DRY RUN)' if dry_run else ''}",
             f"- result: **{action}**" + (f" — verify: {verify_msg}" if verify_msg else ""),
             f"- candidates: {len(selected)}/{CONFIG['target_size']} (evaluated {len(universe)}, eligible {len(scored)})",
             f"- added: {', '.join(added) or '-'}", f"- removed: {', '.join(removed) or '-'}", "",
             f"## Selected — strategy `{CONFIG['strategy']}`: "
             + ("every request tries #1 first and falls through in **this order**; OmniRoute's model lockout skips a "
                "failing model for 2-30 min (*traffic %* is informational, priority ignores weights)"
                if CONFIG["strategy"] == "priority" else
                "traffic sticks to the last provider that answered (prompt cache stays warm); on failure it falls "
                "through in **this order** (# = fallback rank; *traffic %* is informational, lkgp ignores weights)"
                if CONFIG["strategy"] == "lkgp" else
                "each conversation sticks to one of these (prompt-cache reuse); *traffic %* is the weight we set"
                if CONFIG["strategy"] == "cache-optimized" else
                "each request goes to one of these at random, in proportion to *traffic %*"), "",
             "| # | model | provider | traffic % | score | success | median s | quota/budget left | quality | 48h calls |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, d in enumerate(selected, 1):
        lines.append(f"| {i} | {d['model']}{' *(' + d['note'] + ')*' if d.get('note') else ''} | {d['provider']} | "
                     f"**{d.get('weight', 0)}** | {d['score']:.3f} | {d['success']:.0%} | {d['median_s']} | "
                     f"{fmt_q(d['quota'])} | {d['quality']:.2f} | {d['n48h']} |")
    budget_rows = [(p, b) for p, b in sorted(budgets.items()) if b.get("budget") and p in {d["provider"] for d in selected}
                   | {d["provider"] for d in excluded}]
    if budget_rows:
        lines += ["", "## Free-tier budgets (OmniRoute /api/free-models, one row per shared pool)", "",
                  "| provider | pool | type | budget this window | used | left |", "|---|---|---|---|---|---|"]
        for p, b in budget_rows:
            lines.append(f"| {p} | {b['pool']} | {b['type']} | {b['budget']:,.0f} | {b['used']:,} | "
                         f"{b['remaining_frac']:.0%} |")
    if lane_lines:
        lines += ["", "## Lanes — one combo per agent role: same list, different #1 (then everything else in score order)", "",
                  "| combo | role | #1 model | #1 provider | family | result |", "|---|---|---|---|---|---|"] + lane_lines
    if admission_lines:
        lines += ["", "## Admission tests (newcomers without a success in the last "
                  f"{CONFIG['admission_fresh_hours']}h, members without one in {CONFIG['incumbent_fresh_hours']}h)", ""] + admission_lines
    if probe_lines:
        lines += ["", "## Revival / discovery probes this run", ""] + probe_lines
    if temporary:
        lines += ["", "## Temporarily out (comes back automatically)", ""]
        lines += [f"- {d['model']} — {d['why']}" for d in temporary[:30]]
    if needs_action:
        lines += ["", "## Needs your action (re-login / top-up); re-probed every "
                  f"{CONFIG['reprobe_fail_hours']}h", ""]
        lines += [f"- {p}: {w}" for p, w in needs_action[:30]]
    path = write_report("tune" if not dry_run else "dryrun", lines,
                        {"action": action, "reason": reason, "selected": new, "added": added, "removed": removed,
                         "verify": verify_msg})
    if not dry_run:
        state["last_tune"] = now_utc().isoformat()
        # comeback times of temporarily excluded models: the health check re-tunes as soon as one passes
        # only real quota/budget resets (not 30-min 429 cool-offs, which the regular run covers) of models that
        # have actually handled agent-sized requests or passed a probe — anything else would not be picked anyway
        comebacks = {}
        for d in excluded:
            u = parse_ts(d.get("until"))
            proven = (d["model"] in CONFIG.get("preferred_models", []) or
                      (hist.get(d["model"]) or {}).get("ok_big7") or (state["probes"].get(d["model"]) or {}).get("ok"))
            if d.get("temporary") and u and u > now_utc() and "429" not in d["why"] and proven:
                key = u.replace(second=0, microsecond=0).isoformat()
                comebacks.setdefault(key, []).append(d["model"])
        state["resets"] = [{"at": at, "models": ms[:5], "count": len(ms)} for at, ms in sorted(comebacks.items())][:100]
        save_state(state)
    print(f"{action}; {len(selected)} candidates; report {path}")
    return 0 if not action.startswith(("UPDATE FAILED", "ROLLED BACK")) else 1


def log_stray_auto(con, state: dict) -> None:
    """Nothing we manage should still call the old auto* combos; record any that do (time, first message, size)
    in logs/stray-auto.log so a recurring caller can be identified by its timing and payload."""
    import gzip
    since = state.get("stray_auto_seen") or (now_utc() - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = con.execute("""select timestamp, requested_model, status, artifact_relpath from call_logs
        where requested_model like 'auto%' and combo_name = requested_model and timestamp > ? order by timestamp""",
                       (since,)).fetchall()
    for ts, model, status, rel in rows:
        first, shape = "", ""
        try:
            raw = (Path.home() / ".omniroute" / "call_logs" / rel).read_bytes()
            body = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).get("requestBody") or {}
            body = body.get("body") if isinstance(body.get("body"), dict) else body
            msgs = body.get("messages") or []
            c = msgs[0].get("content") if msgs else ""
            first = (c if isinstance(c, str) else json.dumps(c))[:80].replace("\n", " ")
            shape = f"msgs={len(msgs)} tools={len(body.get('tools') or [])} max_tokens={body.get('max_tokens')}"
        except Exception:
            pass
        log(f"{kst(parse_ts(ts))} {model} {status} first={first!r} {shape}", "stray-auto.log")
    if rows:
        state["stray_auto_seen"] = rows[-1][0]
        save_state(state)


def cmd_check() -> int:
    """Look at the most recent combo attempts (not a time window: a burst of earlier successes must not
    hide a rank-1 model that has just started failing on every request)."""
    state = load_state()
    con = db()
    names = [lane["combo"] for lane in CONFIG["lanes"]]  # all lanes share one candidate list: watch them together
    ph = ",".join("?" * len(names))
    rows = con.execute(f"""
        select requested_model, case when status = 499 then 504 else status end from call_logs
        where combo_name in ({ph}) and requested_model not in ({ph}) and timestamp >= datetime('now', ?)
          and (status != 499 or duration >= ?) order by timestamp desc limit ?""",
        (*names, *names, f"-{CONFIG['check_window_min']} minutes",
         CONFIG["hang_s"] * 1000, CONFIG["check_last_n"])).fetchall()
    # a single hang (no answer for hang_s, client gave up) stalls a whole pinned conversation: react at once
    hung = [(m, ts) for (m, ts) in con.execute(f"""
        select requested_model, timestamp from call_logs where combo_name in ({ph}) and requested_model not in ({ph})
          and status = 499 and duration >= ? and timestamp >= ? order by timestamp desc limit 5""",
        (*names, *names, CONFIG["hang_s"] * 1000,
         (now_utc() - timedelta(minutes=CONFIG["check_window_min"])).strftime("%Y-%m-%dT%H:%M:%S")))]
    # a pinned model stays whatever happens, so its slow or failed calls must not trigger a re-tune loop
    lane_pinned = {pin["model"] for lane in CONFIG["lanes"] for pin in lane.get("pinned", [])}
    rows = [r for r in rows if not (is_pinned(r[0]) or r[0] in lane_pinned)]
    hung = [(m, ts) for m, ts in hung if not (is_pinned(m) or m in lane_pinned)]
    n = len(rows)
    fails = sum(1 for _, s in rows if not (200 <= (s or 0) < 300))
    # longest current failure streak of any single candidate (rows are newest first)
    streaks, done = {}, set()
    for model, s in rows:
        if model in done:
            continue
        if 200 <= (s or 0) < 300:
            done.add(model)
        else:
            streaks[model] = streaks.get(model, 0) + 1
    worst, streak = max(streaks.items(), key=lambda kv: kv[1], default=(None, 0))
    last = parse_ts(state.get("last_tune"))
    recent_tune = last and now_utc() - last < timedelta(minutes=CONFIG["min_minutes_between_tunes"])
    # 2026-10-29: only a hang after the last tune counts (that tune already rested the model), and it bypasses the
    # minimum gap: a tune 16 s before north-mini hung held the reaction back 6 min and it hung twice more
    hung = [m for m, ts in hung if not last or (parse_ts(ts) or now_utc()) > last]
    reasons = []
    current = {m["model"] for m in combo_get(CONFIG["combo_name"])["models"]}
    reasons.extend(preferred_check_reasons(state, con, current))
    if streak >= CONFIG["check_streak"]:
        reasons.append(f"{worst} failed {streak}x in a row")
    if n >= CONFIG["check_min_samples"] and fails / n >= CONFIG["check_fail_ratio"]:
        reasons.append(f"{fails}/{n} of last attempts failed")
    # out of credit / auth expired never heals by itself: drop such a candidate on first sight
    dead = sorted({m for m, s in rows if s in (401, 402)})
    if dead:
        reasons.append(f"401/402 (auth or credit) from {', '.join(dead[:3])}")
    if hung:
        reasons.append(f"hang >{CONFIG['hang_s']}s on {', '.join(sorted(set(hung))[:3])}")
    due = [r for r in state.get("resets", []) if (parse_ts(r["at"]) or now_utc()) <= now_utc()
           and (not last or parse_ts(r["at"]) > last)]
    if due:
        names = sorted({m for r in due for m in r["models"]})
        reasons.append(f"quota/limit reset passed for {sum(r['count'] for r in due)} model(s): {', '.join(names[:4])}")
    trigger = bool(reasons) and (not recent_tune or bool(hung))
    log_stray_auto(con, state)
    log(f"check: last {n} attempts {fails} failed; worst streak {streak} ({worst})"
        + (f" -> TUNE ({'; '.join(reasons)})" if trigger else ""), "check.log")
    if trigger:
        return cmd_tune(reason="health check: " + "; ".join(reasons))
    return 0


def cmd_rollback() -> int:
    backups = sorted((ROOT / "backups").glob(f"{CONFIG['combo_name']}-*.json"))
    if not backups:
        print("no backup")
        return 1
    saved = json.loads(backups[-1].read_text(encoding="utf-8"))
    combo = combo_get(CONFIG["combo_name"])
    status, data = put_combo({**combo, "config": saved.get("config", combo.get("config"))}, saved["models"],
                             strategy=saved.get("strategy", "auto"))
    print(f"rollback to {backups[-1].name}: {status}")
    log(f"rollback to {backups[-1].name}: {status}")
    return 0 if status == 200 else 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tune"
    try:
        with Lock():
            if cmd == "tune":
                return cmd_tune(dry_run="--dry-run" in argv, reason="manual" if sys.stdin and sys.stdin.isatty() else "scheduled")
            if cmd == "check":
                return cmd_check()
            if cmd == "rollback":
                return cmd_rollback()
        print(__doc__)
        return 2
    except SystemExit:
        raise
    except Exception:
        log("ERROR " + traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv))
