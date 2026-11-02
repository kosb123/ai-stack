# OmniRoute 콤보 자동 조정 (omni-autotune)

Hermes와 Hindsight가 쓰는 OmniRoute 콤보 세 개의 후보 목록을 실시간 데이터로 다시 짜는 도구.
목표(2026-10-29 사용자): **결제(antigravity Google AI Pro 계정)와 무료 할당량을 모두 오류 없이 최대한 쓰는 것.**

- `hermes-fast-agent`: Hermes 작업용(이름 고정, Hermes 설정은 콤보 이름만 참조).
- `hindsight-structured`: Hindsight 전용(`~\.hindsight\hindsight.env`의 `HINDSIGHT_API_LLM_MODEL`, Hindsight 공식
  설정 `docs/developer/performance.md`). 같은 점수 순서에서 Hindsight 형식 JSON 시험(`probe_structured`: 대화 수 KB +
  strict json_schema 배열, 비스트리밍, 90초 이내, 2회 연속)을 통과한 후보만 넣는다. north-mini가 Hindsight의
  json_schema 요청 64건 중 14건에서 300초 멈춰서 만든 레인(Hermes 스트리밍 요청은 265/265 정상이라 작업용에는 유지).
  최근 48시간에 멈춤(499, 90초+)이 3번 이상(`structured_hang_limit`)인 모델은 시험 결과와 상관없이 이 레인에서 뺀다(`hang_pattern`;
  1순위 고정 medium은 제외 — 큰 consolidation에 2~5분 걸려 `HINDSIGHT_API_CONSOLIDATION_LLM_TIMEOUT=540`으로 대응:
  north-mini는 시험을 운 좋게 통과할 수 있음 — 실제 성공률 78%).
- `hindsight-refresh`: Hindsight 페이지 갱신(reflect) 전용. Hindsight가 토큰을 가장 많이 쓰는 작업이라(하루 입력 약 570만
  토큰) 작고 빠른 `agy/gemini-3-flash`를 1순위로 둔다. 후보 조건은 `hindsight-structured`와 같다. Hindsight 설정에서
  `HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_MODEL`을 `hindsight-refresh`로 지정해야 이 콤보를 쓴다(autotune은 Hindsight
  설정을 바꾸지 않는다).

### 역할별 기능 격리 (2026-11-02)

`role_exclusions`는 실제 기능 시험에서 실패한 모델을 해당 콤보에서만 제외한다. `kiro/auto`는 Hermes에서,
Cline의 dots-3-note-preview와 nemotron-3-ultra는 두 Hindsight 콤보에서 제외한다. 시간 경과로 자동 복귀하지 않으며
실패한 기능을 다시 시험해 통과한 뒤 수동 해제한다. 원본 picks와 다른 역할의 후보는 유지한다.

### 연결 고정과 레인 규칙 (2026-10-30 사용자)

- `route_pinned_providers` (`["agy"]`): 이 공급자의 콤보 단계에 `allowedConnectionIds`로 그 공급자의 활성 연결 id만
  넣는다. 고정이 없을 때 `agy/gemini-3.8-flash-medium`이 같은 Google 계정의 antigravity 연결(ef405c09)로도 가서 거의
  매번 429(24시간 435회, 회당 약 20초)를 받았고, agy 연결은 200으로 답했다. 활성 연결이 하나도 없으면 필드를 빼고
  경고를 남긴다. OmniRoute는 빈 목록인 단계를 건너뛰기 때문이다(`src/lib/combos/steps.ts` `implicitPinAllowlist`,
  `src/sse/handlers/chat.ts`). 콤보가 그대로인지 판단할 때 모델 순서와 함께 단계별 `allowedConnectionIds`도 비교한다.
- 레인 항목의 선택 키 (`CONFIG["lanes"]`):
  - `"primary": "<모델>"`: 그 모델이 레인 목록에 있으면 맨 앞으로 옮긴다(품질·공급자 규칙 없음).
  - `"pinned": [{"model": "<모델>", "rank": n}]`: 그 레인에만 n순위로 넣는다. 고른 모델이 아니어도 넣고, 속도·성공률
    시험은 적용하지 않는다(전역 `pinned_models`와 같은 방식). 다만 쓸 수 없을 때(한도 소진, 잠금, 연결 장애, 새 호출 실패)는
    `preferred_models`와 같은 쿨다운으로 그 레인에서 임시로 빠지고(`state.json`의 `preferred_status`), 한도 회복 뒤 실제 호출
    시험(`probe`)을 통과하면 원래 n순위로 돌아온다(`lane_pin_candidates`; 2026-11-01 사용자: 한도가 바닥난 argon이 1순위로
    남아 429를 먼저 받고 2순위가 20-40초 늦게 답했음). 3분 점검도 이 모델의 한도 소진·회복·새 호출 실패를 감시하되, 속도 문제로 재선정을 일으키지 않는다.
    공용 선정의 공급자별 개수 제한(`max_per_provider_override`)에는 들어가지 않으므로 다른 후보를 밀어내지 않는다.
  - 첫 레인(`hermes-fast-agent`)은 `preferred_models`·`pinned_models`로 순서가 정해지므로 두 키는 그 뒤 레인에 쓴다.
- `hindsight-structured`는 `agy/gemini-4-argon-high`를 1순위로 고정한다(arena 0.98, 자체 주간 한도, 도구·구조화 시험 통과,
  고른 모델이 아니라 그동안 안 쓰였음). antigravity 묶음(medium, gemini-3-flash)에 argon이 더해져도 레인에서 빠지지 않는다.
  처음에는 2순위였지만 호출이 0건이었다. priority는 1순위가 실패해야 2순위로 내려가는데, agy 연결 고정 뒤로 medium이
  거의 실패하지 않았다. 2026-10-30 18:32부터 1순위 시험 중(브랜치 `trial/argon-hindsight-first`).
  2026-10-31 사용자(추론은 전부 low): 같은 모델의 `agy/gemini-4-argon-low`로 바꿨다. 구조화 시험 4회씩 번갈아 low 4/4
  중간값 23.6초, high 4/4 24.1초.
- `selected_models.json`의 `picks.antigravity`는 `__none__`이다(`_note_20261030`, 2026-11-04 이후 다시 판단).

### 메인 콤보 hedging (2026-10-30 사용자)

- `main_combo_config`는 `hermes-fast-agent`의 OmniRoute 콤보 `config`에만 넣고 30분마다 맞춘다(`config_drift`).
  레인 콤보에는 넣지 않는다.
  - `hedging` + `hedgeDelayMs: 10000` + `zeroLatencyOptimizationsEnabled`: 1순위가 10초 안에 답하지 않으면 다음 단계를 함께
    시작하고 먼저 온 답을 쓴다(OmniRoute `open-sse/services/comboConfig.ts`, `combo/comboAttemptLoop.ts`). 고정 1순위
    medium은 Hermes 크기 요청에 3-5초 아니면 22-50초로 답했고, reasoning_effort(없음/low/high)와 무관했다(실측).
  - `disableSessionStickiness: true`: 켜 두면 OmniRoute가 첫 메시지 해시로 대화를 한 연결에 15분 묶는다
    (`combo/sessionStickiness.ts`). hedge에서 2순위가 한 번 이기자 같은 대화의 다음 턴이 medium을 건너뛰었다.
- 메인 콤보만 2순위를 1순위와 다른 공급자 묶음에서 고른다(`second_from_other_group`, 1순위가 고정 모델이어도).
  hedge의 두 번째 요청이 1순위와 같은 Google 계정·한도로 가지 않게 하려는 것이다. 레인은 예전 순서를 그대로 받는다.

### 전수 시험과 extra_picks (2026-10-30 사용자: 쓸 수 있는 후보 최대 확보)

- `python sweep.py`: 연결된 공급자마다 고른 모델, 7일 성공 모델, 카탈로그 상위 모델을 골라(약 200개) 실제로 호출한다.
  1단계 짧은 도구 호출이 200이면 `verify_cycle.round_trip`(Hermes), `probe_structured`, `probe_required_tool`(Hindsight)을
  돌리고 실패 원인을 분류한다(`CAUSES`). 결과는 `reports\sweep\`.
- `selected_models.json`의 `extra_picks`: 공급자당 하나인 `picks` 옆에 sweep을 3번 연속 통과한 모델을 더한다. 시험·멈춤·한도
  관문은 picks와 똑같이 적용된다.
- `provider_blocks`: "이 모델은 플랜에 없음" 같은 모델 한정 402는 공급자 전체를 막지 않는다(ollama-cloud deepseek-v4-pro의
  402가 정상인 gpt-oss:20b까지 막았음).

### 후보 = 사용자가 고른 모델 (2026-10-29)

- 후보는 사용자가 [선택 페이지](https://claude.ai/artifact/88a5uLyVc6FeVDxx1zQPbg)에서 공급자마다 하나씩 고른 모델뿐이다
  (`selected_models.json`의 `picks`, 원본은 `selected_models.json.user-picks-20261029`). 다른 모델을 스스로 찾지 않는다.
- 고른 모델이 시험에서 떨어지면 같은 공급자의 다른 모델을 시험해 교체를 제안하고, 사용자 승인 뒤 바꾼다
  (`replaced_by_claude`). 이 환경에서 쓸 수 없는 공급자는 `dropped`에 이유와 함께 둔다.
- 고른 모델에는 '같은 모델의 추론 강도 변형은 하나만', '공급자 자체 auto 모델 제외' 규칙을 적용하지 않는다.
  상태 확인(인증·한도·멈춤·도구 호출·Hindsight 형식)은 그대로 적용한다. `target_size`는 고른 모델 수까지 커진다.
- agy 요청은 OmniRoute가 `antigravity/...` 이름으로 기록하므로 agy 모델의 기록은 antigravity 이름에서 찾는다(`hist_for`).

## 동작

### 고정 1순위 모델 (2026-10-29 사용자 결정, 2026-10-31 low로 변경)

- 2026-10-31 사용자(추론은 전부 low): 1순위를 같은 모델의 `agy/gemini-3.8-flash-low`로 바꿨다. Hermes 왕복 5회씩
  번갈아 low 5/5 중간값 27.4초(24.6-28.8), medium 5/5 30.6초(25.4-60.2). 두 변형 모두 약 20초 바닥이 있는데,
  Antigravity가 429(Resource has been exhausted)를 약 20초 뒤에 돌려주고 같은 모델 재시도가 3초에 답하는 경우다
  (OmniRoute `open-sse/executors/antigravity.ts`의 429 자동 재시도 `MAX_AUTO_RETRIES = 3`으로 추정). 메인 콤보의
  10초 hedging이 이 경우 2순위를 함께 띄운다. `probe_effort`와 verify 왕복도 Hermes와 같은 `low`를 보낸다.
- 아래 설명의 medium은 그 전 결정이다.
- `antigravity/gemini-3.8-flash-medium`을 항상 1순위로 고정한다(`pinned_models`). 시험·임시 제외·속도 제한
  (45초 기준, 90초 멈춤 판정, verify 왕복 판정)을 적용하지 않는다. 한도 초과(429) 중에는 OmniRoute 자체의
  모델 잠금이 이 모델을 잠시 건너뛰고 2순위가 답한다. 3분 점검은 이 모델의 실패로 재선정을 일으키지 않는다.
- agy는 antigravity와 같은 공급자로 묶는다(`provider_groups`: 같은 실행기·같은 Google 계정·같은 쿨다운). 묶음에서
  최대 3개 모델(`max_per_provider_override`). 할당량을 다 쓰기 위해 medium 외 antigravity 모델도 후보다
  (오전의 'antigravity는 medium만' 규칙은 같은 날 사용자 결정으로 해제).
- antigravity의 Claude·GPT 한도 묶음은 OmniRoute 3.8.51에서 못 쓴다: `claude-sonnet-5-5-*`는 응답 없음(100초 재현),
  `claude-sonnet-4-6`은 "no longer available" 문장을 200으로 돌려줌, `gpt-oss-120b-medium`은 json_schema 400.
  공식 수정은 3.8.52(CHANGELOG "Antigravity Claude Opus 5.5 / Sonnet 5.5 work again")지만 3.8.52는 #18048(모델 동기화가
  CPU 독점, 2026-10-28 실제로 멈춤)로 못 쓰므로 3.8.53을 기다린다. 도구 호출 시험이 이 모델들을 자동으로 거른다.
- 느린 응답이 오류가 되지 않도록 OmniRoute 첫 토큰 대기를 180초(최대 300초,
  `~\.omniroute\.env`의 `STREAM_READINESS_*`), Hermes 프로필의 omni/custom `stale_timeout_seconds`를 360초로 늘렸다.
- 2026-10-28에는 고정 모델을 해제했었다(gemini-3.8-flash-medium, gpt-oss-120b-medium이 429/400으로 반복 이탈).
- 2026-10-29 추가 규칙(고정 모델이 한도 초과일 때 넘겨받는 후보의 품질):
  - `tool_choice="required"` 시험(`probe_required_tool`): Hindsight reflect 형식(도구 1개 + required)에 도구 호출을
    텍스트로 쓰는 모델은 넣지 않는다(kilocode nemotron 4/4 실패 → Hindsight 페이지 갱신 실패). 통과는 24시간, 실패는
    6시간 캐시(`state.json`의 `required_tool`).
  - 시험 요청의 User-Agent는 실제 클라이언트와 같은 `OpenAI/Python 2.24.0`(Hermes·Hindsight의 openai SDK).
    OmniRoute 3.8.51은 클라이언트 User-Agent를 그대로 넘겨서(이슈 #15632, 3.8.52 수정) groq·chatanywhere·aion이
    urllib 기본값에만 403을 줬고, 그 때문에 이 공급자들이 몇 주간 '조치 필요'로 잘못 빠져 있었다.
  - `tool_choice="required"` 시험에서 7일 안에 실패한 적 있는 모델은 3번 연속 통과해야 한다(kilocode nemotron:
    오전 4/4 실패, 오후 2/2 통과).
  - 멈춤(hang) 휴식은 같은 공급자의 같은 모델 별칭에도 적용한다(kilocode/… 멈춘 뒤 kc/… 재입장 방지).
  - `slow_success_s`(240초)보다 느린 성공은 멈춤처럼 휴식시킨다(느린 성공이 휴식을 풀던 문제).
  - 402(크레딧·모델 예산 소진)는 OmniRoute 공식 모델 잠금으로 처리한다: 설정 `modelLockout.errorCodes`에 402 추가
    (Resilience Guide §3, 백업 `~\.omniroute\modelLockout.bak-20261029.json`). 402를 낸 모델은 2~30분 건너뛴다.
    (한때 autotune에 넣었던 '402 모델이면 `min_size` 무시하고 갱신' 예외는 공식 설정으로 바꾸며 제거)
  - 사용자 공급자 규칙으로 금지된 모델(agy 묶음 이후의 agy/…)을 빼는 경우에도 `min_size`보다 적어져도 갱신한다.
  - `min_size`(5)는 더 큰 기존 목록만 지킨다(`update_allowed`): 기존 목록이 이미 5개 미만이면 크기가 같거나 큰 새 목록으로
    바꾼다(agy 묶음 뒤 2개짜리 목록에서 일일 한도가 바닥난 grok-4.7을 north-mini로 못 바꾸던 문제).
  - `check`: 마지막 튜닝 이후에 생긴 멈춤은 튜닝 최소 간격(5분)을 무시하고 바로 튜닝한다. 마지막 튜닝 이전의 멈춤은
    이미 휴식 처리됐으므로 다시 튜닝하지 않는다(10:44:30 튜닝 16초 뒤 north-mini 멈춤 → 6분 대기 동안 2번 더 멈춤).
- 전체 후보 수는 최대 8개.
- 아래 규칙은 고정이 아닌 `preferred_models`에만 적용된다:
- 한도 소진, 잠금, 연결 장애 또는 새 호출 실패가 있으면 해당 우선 모델은 임시 제외한다.
  Gemini와 Claude/GPT 한도 그룹은 구분하며, Medium의 정확한 한도 창이 없으면
  OmniRoute와 동일하게 해당 Flash의 `-tiered` 한도 창을 사용한다.
- 알려진 초기화 시각 또는 새로 확인된 한도 회복 후 실제 도구 호출 시험이 성공해야
  원래 사용자 순위로 복귀한다. 복구 시험 실패는 30분 뒤 재시도하고, 인증/잔액/403은
  기존 6시간 재시험 간격을 사용한다. 초기화 시각을 모르면 30분 간격으로 재평가한다.
- 3분 점검은 우선 모델 소진/실패 및 복귀 예정 시각도 감시한다. 기존 재선정 최소 간격
  5분과 OmniRoute 자체의 요청별 잠금/다음 후보 전환은 유지한다.
- 성공 근거가 24시간 이상 오래되면 재시험한다. 컨텍스트·도구 지원, 연결 상태 및
  기존 45초 시험 제한도 유지한다. 지정은 모델의 건강 상태 검사를 생략한다는 뜻이 아니다.
- `state.json`의 `preferred_status`에 임시 제외 이유와 재평가 시각을 저장하므로
  프로세스 재시작 이후에도 유지된다. `--dry-run`은 복구 시험이나 콤보 변경을 하지 않는다.
- 검증: `python -m unittest test_preferred -v` (HTTP/운영 DB 변경 없는 회귀 시험).

- **30분마다 재선정** (예약 작업 `OmniAutotune-Tune` → `autotune.py tune`)
  1. OmniRoute DB(읽기 전용): 호출 기록, 남은 한도(`quota_snapshots`), 연결 상태, 품질 점수.
     OmniRoute `/api/free-models`: **무료 토큰 예산** — 풀(poolKey) 단위로 이번 기간(일별=오늘, 월별=이번 달,
     가입 크레딧=전체) 사용량을 호출 기록에서 합산해 남은 비율 계산. 다 쓴 풀은 초기화까지 제외.
  2. 제외: 도구 호출 미지원 / 컨텍스트 < 128k / `*/auto` 2차 라우터 / 한도·무료예산 소진 / 인증·잔액 오류 /
     성공률 < 50% / 중간 응답 > 45초 / 품질 < 0.40(소형 모델) / Claude 구독 연결(`claude`, `claude-web`).
     (대화 하나가 한 모델에 붙는 cache-optimized 방식이라 불안정·느린 모델은 그 대화 전체를 망가뜨림)
  3. 점수 = 품질 35% + 성공률 35%(7일 기록을 기본으로 최근 48시간 보정) + 속도 15% + 남은 한도·예산 15%.
     **근거 신뢰도**: 호출 기록이 적은 모델은 점수를 중간값(0.5) 쪽으로 깎음(0회→절반 반영, 10회→75%, 100회 이상→100%).
     남은 한도 10% 미만 + 초기화 3일 이상 남은 모델은 맨 뒤 예비.
  2-1. OmniRoute 공식 API로 **지금 OmniRoute가 건너뛸 대상**도 제외(2026-10-19): 차단기 OPEN
     (`/api/monitoring/health` 의 `providerBreakers`), 모델 잠금(`/api/resilience/model-cooldowns`).
     둘 다 OmniRoute 메모리에만 있어서 DB로는 안 보임.
  4. 상위 최대 **8개**(2026-10-23, 이전 20개; 프로바이더당 최대 4개, antigravity 는 3개).
     모자라면 억지로 채우지 않음(보통 5개 미만이면 변경 안 함. 단, 장애 난 우선 모델을 제거할 때는
     정상 후보가 1개 이상 있으면 최소 개수 때문에 장애 모델을 유지하지 않음).
  4-1. **선정 기준 보정**(2026-10-23, 실측 근거는 `reports\2026-10-23\` 참고):
     - 속도는 **Hermes 크기 요청(1.5만 토큰 이상)** 기록만으로 비교(최근 48시간 → 없으면 7일 → 없으면 입장 시험 지연).
       작은 요청 3건(3.3초)으로 뽑힌 1순위가 실제 Hermes 요청에서는 18.5초로 2순위(8.3초)보다 느렸음.
     - **기존 후보도 24시간 안에 성공(실전 또는 시험)이 없으면 다시 입장 시험**. 오래된 시험 통과 하나로 몇 주씩 남던 문제 해결
       (14개 중 6개가 7일간 호출 0건이었음).
     - **2순위는 1순위와 다른 공급자**(Hermes 크기 요청 실적이나 24시간 내 시험 통과가 있는 모델 중 최고점). 같은 공급자는 한도를 같이 써서 함께 막힘.
     - 남은 무료 한도가 **Hermes 5턴(12.5만 토큰) 미만**인 풀은 맨 뒤로(openrouter 무료 하루 4만 토큰 등).
     - **시험 규칙 보정**: 시험 통과는 `report_status` 도구를 **실제로 호출**해야 함(답에 이름만 적어도 통과하던 문제).
       401(인증 만료) 공급자는 6시간 뒤 다시 시험(다시 로그인하면 자동 복귀). 402(잔액 없음) 공급자는 `:free` 모델만 시험.
       신규 발견은 OmniRoute 품질 점수가 없어도 계열 품질표(`QUALITY_TIERS`)로 판단. 24시간 동안 시험 3번 연속 실패한 공급자는 발견 대상에서 잠시 제외.
       품질 하한(0.40) 미만 모델은 어떤 경로로도 시험하지 않음.
     - 같은 공급자 안에서 **추론 강도만 다른 모델은 하나만**(`grok-4.7-xhigh` 는 실제로 `grok-4.7` 이 처리 → 예비 효과 없음).
     - 검증(2026-10-23): 5회 연속 실행에서 1~3순위 동일, 4회차부터 변화 없음. 최종 후보 8개 모두 Hermes 방식 왕복
       (2만 토큰 + 도구 호출 → 도구 결과 → 한국어 스트리밍 답) 통과, 실제 Hermes 작업은 1순위가 처리.
  4-2. OmniRoute **`auto` 전략을 쓰지 않는 이유**(2026-10-23 확인): auto 는 연결된 모든 계정을 후보로 넣고(잔액·인증 문제 계정 포함),
     계정마다 기본 모델 1개만 쓰며, 실제 성공률(`reliability`)은 기본 가중치 0이라 기록 없는 후보를 만점으로 침(공식 문서
     `docs/routing/AUTO-COMBO.md`). 이 PC 실측: auto 9/27~10/13 시도 33,471회 성공 19%(48개 공급자), priority 99%.
  5. 새로 들어갈 모델 중 최근 6시간 성공 기록이 없으면 **입장 테스트**(실전과 같은 약 2만 토큰 + 노력 max +
     도구 호출 요청, 45초 이내)를 통과해야 함. 회차당 입장 테스트 최대 30개.
     "계정 전체 소진" 응답이 나온 프로바이더는 6시간 동안 통째로 제외.
     **무료 예산이 작은 풀은 시험 안 함**(2026-10-19): 시험 1번(2만 토큰)이 남은 예산의 10%를 넘으면 건너뜀
     (openrouter 하루 4만·arcee·bazaarlink·navy 등). 이미 실전에서 성공 중인 모델은 시험 없이 계속 후보.
  6-0. **레인**(2026-10-19 오후): 같은 후보 목록으로 콤보 3개를 관리 — `hermes-fast-agent`(작업) / `hermes-lane-review`(검토) /
     `hermes-lane-plan`(계획). 두 번째 레인부터는 앞 레인과 **다른 공급자·다른 모델 계열**의 최고점 후보(품질 0.60 이상)를
     1순위로, 나머지는 점수 순. 동시에 도는 에이전트가 서로 다른 한도를 쓰고, 검토자가 작업자와 다른 계열이 됨.
     없으면 새로 만들고, 바뀔 때마다 백업 → 변경 → 실제 호출 확인 → 실패 시 되돌림. 3분 점검도 세 레인을 함께 봄.
     전략은 **`priority`**: lkgp 는 콤보 단위로 "마지막 성공"에 붙어서, B 레인이 한 번 A 모델로 넘어가면 돌아오지 않고
     레인들이 한 모델로 모여 버림. priority 는 늘 자기 1순위부터 시도하고, 실패한 모델은 OmniRoute 모델 잠금이 2~30분 건너뜀.
  6. (이전 기록) 콤보 전략 **`lkgp`**(2026-10-19 오전, 이전 `cache-optimized`): 마지막으로 성공한 프로바이더를 맨 앞에 두고
     계속 붙어 있으므로 프롬프트 캐시가 유지되고, 실패하면 **이 도구가 정한 점수 순서대로** 다음 후보로 넘어감.
     `cache-optimized` 는 프롬프트 앞부분 해시로만 고르고 순서·가중치를 무시했음(OmniRoute `promptCacheAffinity.ts`).
     Hermes 대화는 시스템 프롬프트가 같아 해시가 한 곳에 몰려 48시간 트래픽 90%가 한 모델, 대체 순서는 해시 순이었음.
     보고서의 traffic % 는 참고용(lkgp 는 가중치를 안 씀). 재시도 `maxRetries: 2`.
  7. 변경 전 `backups\` 백업 → 변경 → 실제 대화로 확인 → 실패하면 즉시 되돌림.
  8. 회차마다 최대 6개 시험(프로바이더당 1개, 남은 한도 15% 미만·작은 무료 풀 제외): 죽었던 모델 복구 확인 +
     작은 요청에만 쓰였던 모델을 Hermes 크기로 시험 + 품질 좋은 새 모델 발견.
- **3분마다 점검** (예약 작업 `OmniAutotune-Check` → `autotune.py check`) — 아래 중 하나면 즉시 재선정(재선정 간 최소 5분):
  - 어느 후보든 **연속 3번 실패**
  - 어느 후보든 **401(인증 만료)·402(잔액 없음)** 한 번이라도 → 즉시 (스스로 회복되지 않는 오류)
  - 최근 8번 시도 중 절반 이상 실패
  - 기억해 둔 **한도/무료예산 초기화 시각**이 지남 (검증된 모델의 실제 한도 초기화만; 429 대기는 정기 재선정이 처리)
  - 덤: 옛 `auto*` 콤보로 들어온 요청을 `logs\stray-auto.log` 에 기록(누가 아직 쓰는지 추적용)

- **3시간마다 검증** (2026-10-23, 예약 작업 `OmniAutotune-Verify` → `verify_cycle.py`, 00:40부터 3시간 간격)
  1. `tune` 5번 연속 실행 → 순위가 안정적인지 비교
  2. 최종 후보마다 Hermes 방식 왕복 시험: 약 2만 토큰 + 도구 → 도구 호출 → 도구 결과 → 한국어 스트리밍 답
     (입장 시험은 첫 도구 호출까지만 봄). 두 번 연속 실패(429·503 제외, 120초 무응답은 실패)하면 state.json 에
     실패로 기록하고 재선정해서 뺌
  3. 보고서 `reports\verify-latest.md`, `reports\verify\YYYY-MM-DD-HHMM.md`
  - Claude 예약 작업 `omni-autotune-3h-review`(01:10부터 3시간 간격)가 이 보고서와 실제 호출 기록을 검토해서 명백한 버그는
    백업 후 고치고, 기준값·전략 변경은 `reports\review\` 에 제안으로만 남김.

## 파일
| 경로 | 내용 |
|---|---|
| `reports\latest.md` | 가장 최근 결과 (후보·가중치·점수·무료 예산·제외 사유·조치 필요 목록) |
| `reports\YYYY-MM-DD\HHMMSS-tune.md` | 회차별 보고서 |
| `reports\history.jsonl` | 회차별 한 줄 요약 |
| `logs\autotune.log` / `logs\check.log` / `logs\stray-auto.log` | 시험·변경 상세 / 점검 / 옛 auto 콤보 호출 |
| `backups\` | 변경 직전 콤보 백업 (`combos-all-*.json` 은 최초 전체 백업) |
| `state.json` | 시험 결과·복귀 예정 시각 기억 |

## 명령
```
python autotune.py tune --dry-run   # 바꾸지 않고 선정 결과만 보고서로
python autotune.py tune             # 지금 바로 재선정
python autotune.py check            # 점검 한 번
python autotune.py rollback         # 가장 최근 백업으로 콤보 되돌리기
```
API 키는 사용자 환경변수 `OMNIROUTE_API_KEY` 를 쓴다(파일에 저장하지 않음).

## 끄기 / 켜기
```
schtasks /Change /TN OmniAutotune-Tune /DISABLE
schtasks /Change /TN OmniAutotune-Check /DISABLE
```
(다시 켜려면 `/ENABLE`). 원래 7개 구성(auto 전략)은 `backups\combos-all-20261008-020412.json` 의 `hermes-fast-agent` 항목.

## 조정하고 싶을 때
`autotune.py` 맨 위 `CONFIG`(후보 수, 가중치, 전략, 기준값, 제외 프로바이더)와 `QUALITY_TIERS`(모델 계열별 품질 등급).
다음 회차부터 반영된다. 콤보를 OmniRoute 화면에서 직접 고쳐도 다음 회차에 덮어쓰므로 바꾸고 싶은 건 여기서.

## OmniRoute 쪽에서 함께 바꾼 설정 (2026-10-19, 근거: OmniRoute docs/architecture/RESILIENCE_GUIDE.md)
- **모델 잠금 `modelLockout.enabled = true`**(기본값 꺼짐, 나머지 기본값: 403/404/429/502/503/504 → 2분부터 최대 30분). 2026-10-29: 402 추가.
  lkgp 는 프로바이더 단위로 붙으므로, 같은 프로바이더의 1순위 모델이 429 를 내면 OmniRoute 가 그 모델만 잠그고
  같은 프로바이더의 다음 모델이나 다음 후보로 바로 넘어감(매 요청마다 죽은 모델부터 다시 시도하지 않음).
- `comboCooldownWait`(짧은 429 는 기다렸다 재시도)는 이미 켜져 있었음. 그대로.
- 느린 스트림 감시(`STREAM_THROUGHPUT_WATCHDOG_ENABLED`)는 **안 켬**: 환경변수 + 재시작이 필요하고, effort max 모델이
  오래 생각하는 동안 출력이 없어 끊길 위험. 멈춤은 이 도구의 90초 hang 감지가 처리.
- 되돌리기: 변경 전 설정·콤보 사본은 `Desktop\TEST\_archive\2026-10-19-cleanup.zip` 의 `omni-autotune/backups/`
  (`settings-20261019-140504.json`, `pre-lkgp-combo-20261019-140504.json`).
- 정리 규칙(2026-10-19): `backups\` 는 콤보마다 최근 5개 + 최초 전체 백업 `combos-all-*` 만 둔다. 오래된 보고서·로그는 압축 보관.
  모델 잠금만 끄기: `PATCH /api/settings` 본문 `{"modelLockout":{"enabled":false}}`.

## 함께 바꾼 Hermes 쪽 토큰 절약 설정 (2026-10-08, 근거: Hermes 공식 문서 configuration.md / features/tool-search.md)
- omni 기본·보조 모델(제목·압축·리뷰·비전·MCP) = `hermes-fast-agent`
- `compression.threshold_tokens: 50000`, `tools.tool_search.defer` 에 금고·TTS·스킬편집·비전·칸반 등 추가,
  `tool_output.max_bytes: 20000`, `file_read_max_chars: 40000`, `agent.disabled_toolsets: [video_gen, image_gen, tts]`
- 백업: `%LOCALAPPDATA%\hermes\config.yaml.bak-pre-*`

- 최소 후보 수 보호가 작동하는 동안 레인 고정 모델이 대기 상태면 기존 레인에서 해당 모델만 제거하고 나머지 순서, 가중치, 연결 및 설정을 유지한다. 남는 모델이 없으면 변경하지 않고 재시도한다. 콤보 검증은 실제 `report_status` 도구 호출과 `status: ok` 인자를 확인한다.
- Hindsight의 `tool_choice=required` 및 구조화 출력 검사는 Hindsight 레인에서만 적용한다. Hermes의 일반 도구 왕복이 정상인 후보를 Hindsight 조건 때문에 전역 제외하지 않는다.
