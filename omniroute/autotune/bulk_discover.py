"""One-off bulk discovery: probe every untried candidate model (tool calling, context >= 128k, known quality) in batches of 10.
Results go to state.json probes (the next scheduled tune uses them) and to logs/bulk/batch-NN.json.
Usage: python bulk_discover.py plan            # list candidates only
       python bulk_discover.py run [max_batches]
"""
import json
import sys
from datetime import timedelta

import autotune as a

PER_PROVIDER_CAP = 6
MIXED_FREE_PAID = {'llm7', 'kilocode', 'cheaperinference', 'electronhub'}


def candidates():
    state = a.load_state()
    con = a.db()
    cat, hist, q, conns, qual = a.catalog(), a.history(con), a.quotas(con), a.connections(con), a.quality(con)
    a.provider_block.clear(); a.provider_block.update(a.provider_blocks(con))
    tnow = a.now_utc()
    exhausted = {p.get("provider") for p in state["probes"].values() if a.provider_exhausted(p)
                 and tnow - (a.parse_ts(p["ts"]) or tnow) < timedelta(hours=a.CONFIG["reprobe_fail_hours"])}

    def usable(prov):
        if prov in a.CONFIG["exclude_providers"] or prov in exhausted:
            return False
        pb = a.provider_block.get(prov)
        if pb and pb["status"] == 401:
            return False
        cl = conns.get(prov, [])
        if not cl or not any(c["active"] and c["test"] != "banned" for c in cl):
            return False
        return not all((a.parse_ts(c["rl_until"]) or tnow) > tnow for c in cl)

    out = []
    for m, info in cat.items():
        if m in hist or m in state["probes"] or "/auto" in m or info.get("owned_by") in (None, "combo"):
            continue
        caps = info.get("capabilities") or {}
        qs = qual.get(a.norm(m))
        if not caps.get("tool_calling") or (info.get("context_length") or 0) < a.CONFIG["min_context"] or qs is None:
            continue
        prov = info["owned_by"]
        if prov in MIXED_FREE_PAID:  # probing a paid model marks the whole connection 'credits exhausted' (seen 2026-10-10 with llm7)
            continue
        if not usable(prov) or a.quota_for(prov, m, q)[1] is not None:
            continue
        out.append((m, prov, qs))
    out.sort(key=lambda x: -x[2])
    seen, per, picked = set(), {}, []
    for m, prov, qs in out:
        if a.norm(m) in seen or per.get(prov, 0) >= PER_PROVIDER_CAP:
            continue
        seen.add(a.norm(m)); per[prov] = per.get(prov, 0) + 1
        picked.append((m, prov, qs))
    return picked


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    picked = candidates()
    if mode == "plan":
        print(len(picked), "candidates")
        by = {}
        for m, p, qs in picked:
            by.setdefault(p, []).append(m)
        for p, ms in sorted(by.items(), key=lambda x: -len(x[1])):
            print(p, len(ms), ms[:3])
        return
    max_batches = int(sys.argv[2]) if len(sys.argv) > 2 else 999
    import os
    os.makedirs(a.Path(__file__).parent / "logs" / "bulk", exist_ok=True)
    dead = set()
    for bi in range(0, min(len(picked), max_batches * 10), 10):
        batch = picked[bi:bi + 10]
        rows = []
        state = a.load_state()
        for m, prov, qs in batch:
            if prov in dead:
                rows.append({"model": m, "provider": prov, "ok": None, "note": "skipped: provider exhausted earlier in this run"})
                continue
            res = a.probe(m)
            res["provider"] = prov
            state["probes"][m] = res
            if a.provider_exhausted(res):
                dead.add(prov)
            rows.append({"model": m, "provider": prov, "quality": qs, "ok": res["ok"], "latency": res["latency"], "status": res.get("status"), "err": (res.get("err") or "")[:110]})
        a.save_state(state)
        (a.Path(__file__).parent / "logs" / "bulk" / f"batch-{bi // 10 + 1:02d}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print("batch", bi // 10 + 1, "done", sum(1 for r in rows if r["ok"]), "ok", flush=True)


if __name__ == "__main__":
    main()
