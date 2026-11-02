"""OmniRoute error kinds with plain Korean explanations.

Rules are tried in order and the first match wins, so specific texts sit above the generic status fallbacks.
The texts come from call_logs.error_summary as seen on 2026-10-24..31.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorKind:
    key: str
    title: str
    cause: str
    action: str
    severity: str  # quota | auth | config | transient | client | unknown


def _k(key, title, cause, action, severity):
    return ErrorKind(key, title, cause, action, severity)


QUOTA_AGY = _k("quota_antigravity", "Antigravity 한도 소진",
               "Google Antigravity 계정의 모델별 할당량(주간/5시간 창)을 다 써서 'Resource has been exhausted'로 거절됨. "
               "OmniRoute가 내부에서 최대 3번 재시도한 뒤 약 20초 후에 이 오류를 돌려준다.",
               "할당량 표에서 리셋 시각을 확인한다. 콤보의 다음 후보가 대신 답하므로 보통 조치는 필요 없다. "
               "자주 나면 다른 Google 계정을 추가하거나 덜 쓰는 모델을 1순위로 둔다.", "quota")
DAILY_LIMIT = _k("quota_daily", "일일 토큰 한도 초과",
                 "공급자의 하루 토큰 한도를 넘었다.", "자정(공급자 기준) 리셋을 기다리거나 다른 후보로 넘긴다.", "quota")
FREE_LIMIT = _k("quota_free", "무료 사용량 소진",
                "무료 등급의 사용량(포인트, 7일 창, 무료 모델 한도)을 다 썼다.",
                "창이 끝날 때까지 기다린다. 계속 필요하면 유료 전환이 필요하므로 직접 판단한다.", "quota")
NO_CREDIT = _k("quota_credit", "잔액·크레딧 부족",
               "선불 잔액이나 크레딧이 0이라 요청이 거절됐다(402, out of funds, insufficient balance).",
               "충전하지 않을 거라면 autotune이 이 공급자를 후보에서 빼도록 두면 된다. 충전은 사용자가 직접 한다.",
               "quota")
PLAN_GATE = _k("plan_required", "요금제에 없는 모델",
               "계정 요금제(Pro/VIP/구독)에 포함되지 않은 모델을 불렀다. 계정 전체가 아니라 그 모델만 막힌 것이다.",
               "그 모델을 후보에서 뺀다. 같은 공급자의 다른 모델은 쓸 수 있다.", "config")
RATE_LIMIT = _k("rate_limit", "속도 제한 (잠시 후 재시도)",
                "짧은 시간에 요청이 몰려 공급자가 잠시 막았다(429 rate limit, 동시 요청 한도, 대기열 시간 초과).",
                "일시적이다. 계속 반복되면 그 후보의 순위를 내린다.", "transient")
OVERLOADED = _k("overloaded", "공급자 과부하",
                "공급자 쪽 서버가 바쁘거나 일시 장애다(503 high demand, 500 internal error).",
                "일시적이다. 콤보가 다음 후보로 넘긴다.", "transient")
EMPTY = _k("empty_reply", "빈 응답",
           "모델이 내용 없이 응답을 끝냈다. Cursor는 사용량 소진 때 이렇게 응답하는 경우가 많다.",
           "반복되면 그 공급자의 사용량 한도를 확인한다.", "transient")
AUTH = _k("auth", "인증 실패",
          "API 키나 로그인 세션이 틀렸거나 만료됐다(401, invalid key, expired session cookie).",
          "OmniRoute 설정의 Providers에서 해당 연결을 다시 로그인하거나 키를 새로 넣는다(사용자 작업).", "auth")
ABUSE_GATE = _k("verification_required", "추가 인증 필요",
                "공급자가 무료 모델 남용을 막으려고 전화번호 인증 등을 요구한다.", "공급자 웹사이트에서 직접 인증한다.",
                "auth")
SCHEMA_NAME = _k("schema_name_missing", "구조화 출력 형식 오류",
                 "요청의 response_format json_schema에 이름(name)이 없거나 형식이 달라 Antigravity가 거절했다. "
                 "Hindsight처럼 JSON 스키마를 보내는 요청에서 난다.",
                 "보내는 쪽(Hindsight/OmniRoute 변환)의 문제다. 이 후보를 구조화 출력 레인에서 빼거나 OmniRoute를 업데이트한다.",
                 "client")
NO_STRUCTURED = _k("structured_unsupported", "구조화 출력 미지원 모델",
                   "이 모델은 JSON 스키마 강제 출력을 지원하지 않는다.", "hindsight-structured 레인에서 뺀다.", "config")
MODEL_GONE = _k("model_unavailable", "모델 없음·지원 안 함",
                "요청한 모델이 공급자에서 사라졌거나 이 계정에서 지원하지 않는다(400 not supported, 404, 410 Gone).",
                "후보 목록에서 그 모델을 뺀다. autotune이 다음 실행 때 반영한다.", "config")
LOCAL_RUNTIME = _k("local_runtime", "로컬 CLI·브리지 설정 문제",
                   "Devin/Auggie/Codex 같은 로컬 CLI 기반 공급자가 설치되지 않았거나 경로·토큰 설정이 비어 있다.",
                   "쓰지 않는 공급자면 OmniRoute에서 비활성화한다. 쓸 거면 해당 CLI를 설치하고 로그인한다.", "config")
NOT_TESTABLE = _k("test_unsupported", "연결 테스트 미지원",
                  "OmniRoute의 연결 상태 점검이 이 공급자를 지원하지 않는다. 실제 요청 실패가 아니다.",
                  "무시해도 된다.", "client")
CATALOG_FALLBACK = _k("catalog_fallback", "모델 목록 조회 실패",
                      "공급자의 모델 목록 API에 닿지 못해 저장된 목록을 썼다. 사용자 요청은 실패하지 않았다.",
                      "무시해도 된다. 오래 지속되면 그 공급자 연결을 확인한다.", "client")
ABORTED = _k("aborted", "요청 취소됨",
             "클라이언트가 응답 전에 요청을 끊었다. 대부분 헤징으로 다른 후보가 먼저 답해서 OmniRoute가 느린 쪽을 취소한 것이다.",
             "정상 동작이다.", "client")
NO_ROUTE = _k("no_route", "보낼 후보 없음",
              "콤보의 모든 후보가 사전 필터(한도 소진, 차단, 게이트)에 걸려 보낼 곳이 없었다.",
              "할당량 표와 후보 상태를 확인한다. 여러 후보가 동시에 막힌 시간대다.", "quota")
TIMEOUT = _k("timeout", "시간 초과",
             "정해진 시간 안에 공급자가 답하지 않았다(504, deadline exceeded).",
             "일시적이면 무시한다. 같은 후보에서 반복되면 순위를 내린다.", "transient")
BUDGET = _k("budget_exhausted", "OmniRoute 예산 한도",
            "OmniRoute에 설정한 키·공급자 예산을 다 써서 OmniRoute가 스스로 막았다.",
            "OmniRoute 예산 설정을 확인한다.", "quota")

UNKNOWN_BY_STATUS = {
    400: _k("http_400", "잘못된 요청 (400)", "공급자가 요청 형식을 거절했다. 자세한 문구가 없다.", "예시 문구를 확인한다.", "unknown"),
    401: AUTH,
    402: NO_CREDIT,
    403: _k("http_403", "접근 거부 (403)", "공급자가 이 계정의 요청을 거부했다. 지역·등급·한도 제한인 경우가 많다.",
            "반복되면 그 공급자 대시보드를 확인한다.", "unknown"),
    404: MODEL_GONE,
    410: MODEL_GONE,
    422: _k("http_422", "처리할 수 없는 요청 (422)", "요청 매개변수(예: 지원하지 않는 reasoning_effort 값)를 공급자가 받지 않았다.",
            "예시 문구의 매개변수를 확인한다.", "client"),
    429: RATE_LIMIT,
    499: ABORTED,
    500: OVERLOADED,
    502: _k("http_502", "공급자 응답 오류 (502)", "공급자가 깨진 응답을 보냈거나 연결이 끊겼다.", "일시적이면 무시한다.", "transient"),
    503: OVERLOADED,
    504: TIMEOUT,
}
UNKNOWN = _k("unknown", "분류 안 된 오류", "알려진 패턴에 맞지 않는 오류다.", "예시 문구를 확인한다.", "unknown")

_RULES = (
    (r"resource has been exhausted", QUOTA_AGY),
    (r"daily token limit", DAILY_LIMIT),
    (r"free usage limit|free points|免费点数|free tier|anytokens", FREE_LIMIT),
    (r"out of funds|insufficient balance|insufficient.*credit|credit limit|add credits|balance is insufficient|"
     r"credits exhausted|exceeds your budget|requires an? \w+ api key", NO_CREDIT),
    (r"requires a (pro|vip)|premium model requires|subscription does not have access|requires a subscription|"
     r"blocked at the organization level", PLAN_GATE),
    (r"phone verification", ABUSE_GATE),
    (r"'name' of a\(n\) 'json_schema'|contain field: 'json_schema'", SCHEMA_NAME),
    (r"does not support structured output", NO_STRUCTURED),
    (r"model is not supported|model is unavailable|requested model is not supported|\bgone\b|model_not_supported|不支持", MODEL_GONE),
    (r"provider test not supported", NOT_TESTABLE),
    (r"local cli runtime is not installed|devin_agentic_home|auggie|devin (cli|desktop)|codex app-server|cursor-agent", LOCAL_RUNTIME),
    (r"invalid api key|api key is invalid|unauthorized|session cookie|authentication|expired|revoked|"
     r"not able to authenticate", AUTH),
    (r"deadline exceeded|timed? ?out", TIMEOUT),
    (r"using (local|cached) catalog|no remote models discovered", CATALOG_FALLBACK),
    (r"empty (content|turn)|non-ping sse", EMPTY),
    (r"request aborted", ABORTED),
    (r"pre-dispatch filters|no compatible route|pipeline gate rejected", NO_ROUTE),
    (r"budget_exhausted", BUDGET),
    (r"rate limit|rate limited|concurrent requests|queue budget|queue_timeout|too many requests", RATE_LIMIT),
    (r"high demand|internal error|temporarily unavailable|overloaded", OVERLOADED),
)
_COMPILED = tuple((re.compile(p, re.I), kind) for p, kind in _RULES)

CATALOG = tuple(dict.fromkeys([kind for _, kind in _RULES] + list(UNKNOWN_BY_STATUS.values()) + [UNKNOWN]))


def classify(status, text, provider=None):
    for rx, kind in _COMPILED:
        if text and rx.search(text):
            return kind
    try:
        code = int(status)
    except (TypeError, ValueError):
        return UNKNOWN
    return UNKNOWN_BY_STATUS.get(code, UNKNOWN)
