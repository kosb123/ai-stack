# Hermes·OmniRoute·Hindsight 운영 점검

2026-11-03 실제 설치 환경에서 검사했다. 이 문서는 결과 요약이며, 원시 응답·세션·로그·백업은 저장소에 포함하지 않는다. 계정 가용성과 콤보 구성은 이후 자동조정으로 바뀔 수 있다.

## 확인된 동작

- 로컬 llama.cpp 경로와 OmniRoute 경로 각각에서 격리된 Hermes CLI가 파일 도구를 호출하고 임의 값을 읽었다. fallback은 껐다.
- Hindsight API의 저장·검색, 실제 Hermes 플러그인의 저장과 다른 세션에서의 회수, reflect 및 mental model 생성·갱신을 확인했다. 운영 기억 은행에는 테스트 데이터를 쓰지 않았다.
- 운영의 `hermes-fast-agent`, `hindsight-structured`, `hindsight-refresh` 콤보는 수정 후 실제 필수 도구 호출 요청을 통과했다.

## 후보별 결과

| 후보 | 실제 Hermes 파일 도구 | Hindsight 재시험 |
|---|---|---|
| agy/gemini-3.8-flash-low | 직접 경로 503으로 미확인 | 직접 경로 503으로 미확인 |
| kiro/qwen3-coder-next | 통과 | 해당 레인 후보 아님 |
| kiro/glm-5 | 통과 | 해당 레인 후보 아님 |
| electronhub/gemini-2.5-flash-lite | 통과 | 해당 레인 후보 아님 |
| cline/dots-studio/dots-3-note-preview:free | 통과 | 해당 레인 후보 아님 |
| kiro/deepseek-3.2 | 통과 | 해당 레인 후보 아님 |
| cheaperinference/nemotron-3.5-lightning-free | 통과 | 해당 레인 후보 아님 |
| kilocode/cohere/north-mini-code:free | 통과 | 필수 도구 및 구조화 추출 2회 통과 |
| anyapi/google/gemini-3.6-flash | 시간초과, 공급자 402 재확인 | 해당 레인 후보 아님 |
| requesty/mistral/leanstral-1-5 | 4회 반복 한도 소진, 8회 한도에서 통과 | 필수 도구 통과, 구조화 추출 2회 시간초과 |

AGY 직접 경로의 503과 달리 운영 Hermes 콤보의 성공 응답 헤더에는 `gemini-3.8-flash-low` / `antigravity`가 기록됐다. 콤보에는 계정 제한 설정이 있으므로 두 경로의 가용성이 같다고 단정하지 않는다. 계정별 후보 기능 판정은 남아 있다. 기존 AGY 선호 설정과 계정은 유지했다.

AnyAPI는 예산 초과 402를 재확인하여 Hermes 콤보에서 제외했다. Leanstral은 현재 Hindsight 추출 작업의 시간 제한을 충족하지 못해 두 Hindsight 콤보에서만 제외했다. Hermes에서는 유지했다. 선호 순서·전략·재시도·헤징 설정은 보존하고 후보 목록 변경을 재조회했다. Leanstral의 이전 구조화 성공 캐시는 무효화했으며 영구 실패 캐시는 만들지 않았다. 향후 재검증을 통한 복귀 정책은 유지한다.

작은 파일 작업 통과는 복잡한 개발 작업의 품질을 보증하지 않는다. API 도구 시험과 실제 Hermes CLI 시험은 별도로 판정했다.

## 수정한 원인과 검증

- 콤보 검증의 60토큰 응답이 추론 중 잘리는 현상을 4회 재현했다. 응답 한도를 1,024로 늘리고 low 추론과 필수 도구 호출을 지정했다. 도구 이름과 `status=ok` 검증은 유지했다.
- Hindsight 추출 시험의 한도를 2,000에서 4,096으로 늘렸다. 잘림·연결 실패·5xx를 모델 기능 실패로 캐시하지 않도록 했다.
- Hindsight 고정 후보도 역할별 기능 관문을 통과하도록 하고 중복 시험을 줄였다. 레인 갱신 실패가 성공 종료 코드로 감춰지지 않도록 했다.
- Windows 파일 경로를 정규화한 뒤 정확히 비교하도록 검증기를 수정했다. 슬래시·대소문자 차이는 허용하며 다른 파일이나 접미사는 거부한다.
- 자동조정기 111개, 경로 검증기 14개, 저장소 20개, 대시보드 14개 테스트가 통과했다. 수정 소스는 백업 후 운영 경로에도 반영했다.

## 남은 범위

예약 작업 Check·Tune·Verify는 원래 Enabled 상태로 복구했다. 테스트 정리는 부분 완료 상태다. unserved 처리된 테스트 프로필 하나의 `state.db`를 공유 Hermes gateway가 계속 열고 있어 Windows 오류 32로 삭제가 막혔다. Windows Restart Manager로 소유 프로세스를 확인했다. 다른 작업이 실행 중인 공유 gateway는 재시작하지 않았다. 테스트 DB와 scratch를 보존했으며, gateway가 파일을 닫은 뒤 해당 실행의 cleanup을 다시 수행해야 한다. 전체 cleanup은 통과로 보고하지 않는다.

Desktop 모델 전환·재개, 후크 실행, 재부팅 후 복구와 장기 부하 안정성은 이번 검사 범위에 포함되지 않는다. 로컬 모델은 실제 도구 요청을 통과했으나 과거 GPU 초기화 경고까지 해소됐다는 증거는 없다.

독립 검토는 사용자 규칙에 따라 `gpt-6.1-terra` / `high`로 수행했다. 다른 모델 계열의 검토는 수행하지 않았다.

## 공식 자료

- [OmniRoute 콤보 라우팅](https://github.com/cheaper-inference/OmniRoute/blob/v3.8.51/docs/routing/AUTO-COMBO.md)
- [Hermes CLI](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md)
- [Hindsight Hermes 연동](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs-integrations/hermes.md)
- [Hindsight mental models](https://github.com/vectorize-io/hindsight/blob/main/skills/hindsight-docs/references/developer/api/mental-models.md)
- [llama.cpp 서버](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
