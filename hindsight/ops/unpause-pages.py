# Re-enable knowledge pages whose automatic refresh a failed refresh has paused.
# Community workaround for vectorize-io/hindsight#6077 (paused state is invisible, open) and #5561:
# the server pauses a page after a failed refresh until a manual refresh succeeds, and nothing reports it.
# Rules taken from those reports: send one manual refresh per paused page, at most once per 12 h per page,
# and skip a page whose failure is fresh or whose refresh is already in flight. Hourly via Task Scheduler.
import json, pathlib, urllib.request
from datetime import datetime, timedelta, timezone

B = "http://127.0.0.1:8888/v1/default/banks/jun"
HOME = pathlib.Path.home() / ".hindsight"
STATE, LOG = HOME / "unpause-state.json", HOME / "unpause.log"
COOLDOWN, SETTLE = timedelta(hours=12), timedelta(minutes=15)
now = datetime.now(timezone.utc)


def call(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def log(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now.astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M} {msg}\n")


ts = lambda s: datetime.fromisoformat(s) if s else None
try:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    busy = {o.get("mental_model_id") for st in ("pending", "processing")
            for o in call("GET", f"/operations?status={st}&type=refresh_mental_model&limit=100").get("operations") or []}
    for m in call("GET", "/mental-models?limit=100").get("items") or []:
        failed, ok = ts(m.get("last_refresh_failed_at")), ts(m.get("last_refreshed_at"))
        if not failed or (ok and ok >= failed):
            continue  # not paused
        last_try = ts(state.get(m["id"]))
        if now - failed < SETTLE or m["id"] in busy or (last_try and now - last_try < COOLDOWN):
            continue
        call("POST", f"/mental-models/{m['id']}/refresh", {})
        state[m["id"]] = now.isoformat()
        log(f"refresh sent: {m['name']} (paused since {m['last_refresh_failed_at'][:16]}Z)")
    STATE.write_text(json.dumps(state), encoding="utf-8")
except Exception as e:  # Hindsight down or restarting: try again next hour
    log(f"skipped: {type(e).__name__}: {e}")
