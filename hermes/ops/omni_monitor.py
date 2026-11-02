#!/usr/bin/env python
# OVERNIGHT WATCHDOG (no_agent cron): prints ONLY when an issue is detected.
# Empty stdout = healthy = nothing is delivered (watchdog pattern).
# Reads agent.log / gateway.log incrementally (state file) so each tick is cheap
# and deterministic; fires on compression stops, aux 400s, gateway fatals,
# main-model stalls/degradation, or OmniRoute TTFT problems.
import os, re, json, time, urllib.request, statistics

HERMES = os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\jun'), 'AppData', 'Local', 'hermes')
LOG = os.path.join(HERMES, 'logs', 'agent.log')
GLOG = os.path.join(HERMES, 'logs', 'gateway.log')
APIKEY = os.path.join(HERMES, 'runtimes', 'llamacpp', '.api_key')
STATE = os.path.join(HERMES, 'cache', 'scratch', 'monitor_state.json')

def tail_since(path, offset):
    if not os.path.exists(path): return '', offset
    sz = os.path.getsize(path)
    if sz < offset: offset = 0  # log rotated/truncated
    with open(path, 'rb') as f:
        f.seek(offset)
        data = f.read().decode('utf-8', 'ignore')
    return data, sz

def omni_ttft():
    try:
        env = {}
        with open(os.path.join(HERMES, '.env')) as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1); env[k.strip()] = v.strip().strip('"').strip("'")
        ok = env.get('OMNIROUTE_API_KEY', '')
        body = json.dumps({"model": "auto", "messages": [{"role": "user", "content": "OK"}],
                           "max_tokens": 3, "stream": True}).encode()
        req = urllib.request.Request("http://127.0.0.1:20128/v1/chat/completions", data=body,
                                     headers={"Authorization": "Bearer " + ok, "Content-Type": "application/json"})
        t0 = time.time(); ttft = None
        with urllib.request.urlopen(req, timeout=90) as r:
            for raw in r:
                line = raw.decode('utf-8', 'ignore').strip()
                if line.startswith('data:') and line[5:].strip() != '[DONE]':
                    if ttft is None: ttft = time.time() - t0
        return round(ttft, 2) if ttft else None
    except Exception as e:
        return f'ERR {type(e).__name__}'

def main():
    st = {}
    try:
        with open(STATE) as f: st = json.load(f)
    except Exception: pass
    newa, noff = tail_since(LOG, st.get('agent_off', 0))
    newg, ngoff = tail_since(GLOG, st.get('goff', 0))

    issues = []
    stops = len(re.findall(r'Cannot compress further|insufficient progress|compression_exhausted', newa))
    if stops: issues.append(f"압축중지={stops}회")
    c400 = len(re.findall(r'"status_code":\s*40[0-9]', newa))
    if c400: issues.append(f"보조모델_400={c400}회")
    g_fatal = len(re.findall(r'Gateway hit a non-retryable|Gateway exiting|Fatal .* adapter error', newg))
    if g_fatal: issues.append(f"게이트웨이_FATAL={g_fatal}회")
    lat = [float(m) for m in re.findall(r'model=Qwen3\.8-27B[^ ]* provider=custom [^"]*?latency=([\d.]+)s', newa)]
    stalls = [x for x in lat if x > 300]
    if stalls: issues.append(f"메인모델_멈춤={len(stalls)}회(max {max(stalls):.0f}s)")
    elif len(lat) >= 2:
        med = statistics.median(lat)
        if med > 120: issues.append(f"메인모델_지연_악화(중간값 {med:.0f}s, {len(lat)}호출)")
    tt = omni_ttft()
    if isinstance(tt, str) or (isinstance(tt, float) and tt > 5):
        issues.append(f"옴니라우트_장애(ttft={tt})")

    st.update(agent_off=noff, goff=ngoff)
    try:
        with open(STATE, 'w') as f: json.dump(st, f)
    except Exception: pass

    if issues:
        print("옴니라우트/헤르메스 모니터링 경고: " + ", ".join(issues) +
              f" | 메인호출={len(lat)} 옴니TTFT={tt}s — 원인 확인 필요 (agent.log/gateway.log 확인 후 해결)")

if __name__ == '__main__':
    main()
