#!/usr/bin/env python
# OVERNIGHT REPORT (one-shot cron, 07:15 KST): counts the OVERNIGHT window only
# (baseline snapshot taken at setup), so historical events don't pollute the verdict.
# Deterministic per data window. Self-contained Korean summary for the morning.
import os, re, json, time, statistics, urllib.request

HERMES = os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\jun'), 'AppData', 'Local', 'hermes')
LOG = os.path.join(HERMES, 'logs', 'agent.log')
GLOG = os.path.join(HERMES, 'logs', 'gateway.log')
BASE = os.path.join(HERMES, 'cache', 'scratch', 'overnight_baseline.json')
APIKEY = os.path.join(HERMES, 'runtimes', 'llamacpp', '.api_key')
CTX_LIMIT = 65536

def strip_ansi(s): return re.sub(r'\x1b\[[0-9;]*m', '', s)

def window(path, offset):
    if not os.path.exists(path): return ''
    with open(path, 'rb') as f:
        f.seek(max(0, min(offset, os.path.getsize(path))))
        return strip_ansi(f.read().decode('utf-8', 'ignore'))

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
    base = {}
    try:
        with open(BASE) as f: base = json.load(f)
    except Exception: pass
    newa = window(LOG, base.get('agent', 0))
    newg = window(GLOG, base.get('gateway', 0))

    cyc = re.findall(r'"event":"compression_attempt".*?\}', newa)
    committed = sum(1 for c in cyc if '"commit_status":"committed"' in c or '"commit_status":"in_place_committed"' in c)
    c400 = len(re.findall(r'"status_code":\s*40[0-9]', newa))
    stops = len(re.findall(r'Cannot compress further|compression_exhausted|insufficient progress', newa))
    g_fatal = len(re.findall(r'Gateway hit a non-retryable|Gateway exiting|Fatal .* adapter error', newg))
    lat = [float(m) for m in re.findall(r'model=Qwen3\.8-27B[^ ]* provider=custom [^"]*?latency=([\d.]+)s', newa)]
    med = statistics.median(sorted(lat)) if lat else 0
    p90 = sorted(lat)[int(len(sorted(lat))*0.9)] if lat else 0
    tt = omni_ttft()

    issues = []
    if stops: issues.append(f"압축중지/멈춤 {stops}회 발생 (원인: agent.log 'Cannot compress further' 주변 확인)")
    if c400: issues.append(f"보조(압축)모델 400 오류 {c400}회 — 옴니라우트 20128 상태 점검 필요")
    if g_fatal: issues.append(f"게이트웨이 fatal {g_fatal}회 — gateway.log 확인 필요")
    if isinstance(tt, str) or (isinstance(tt, float) and tt > 5): issues.append(f"옴니라우트 응답 지연/장애 (TTFT={tt})")
    elif lat and med > 60: issues.append(f"메인모델 응답 지연 (중간값 {med:.0f}s) — 컨텍스트 크기에 따른 prefill")

    lines = []
    lines.append("== 옴니라우트 + 헤르메스 야간 최종 리포트 (08:30~07:15) ==")
    lines.append(f"[1] 옴니라우트 auto: TTFT={tt}s  (정상 2~3s 이하)")
    lines.append(f"[2] 메인 27B 호출 {len(lat)}건: median={med:.1f}s p90={p90:.1f}s"
                 + (f" max={max(lat):.0f}s" if lat else ""))
    lines.append(f"[3] 압축: 시도={len(cyc)} committed={committed} 400={c400} 중지={stops}")
    lines.append(f"[4] 게이트웨이 fatal={g_fatal}")
    lines.append(f"[판정] {'정상 유지 — 중단 없음' if not issues else '주의: ' + ' / '.join(issues)}")
    lines.append("(참고: '대화 길어서 중단' = 압축 실패 시에만. 이번 빌드는 턴당 3회 재시도 후 다음 턴에 자동 재개되며,")
    lines.append(" 압축은 옴니라우트 auto로 라우팅되어 있어 옴니라우트가 살아있으면 중단 불가. 중단 시 위 [3]에 기록됨)")
    print('\n'.join(lines))

if __name__ == '__main__':
    main()
