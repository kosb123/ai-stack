# 구성 요소와 실행 경로

이 저장소는 로컬 실행 파일의 관리용 사본을 모은다. 아래 경로는 2026-11-04에 확인한 경로이며 현재 서비스 상태를 보장하지 않는다. 현재 상태는 `scripts/status.ps1`로 확인한다.

## 서비스

| 서비스 | 역할 | 실행 위치 | 주소 | 자동시작 | 저장소 |
| --- | --- | --- | --- | --- | --- |
| Hermes | 작업 에이전트 | `C:/Users/jun/AppData/Local/hermes` | 게이트웨이 프로세스 | 앱 | `hermes/` |
| OmniRoute | 클라우드 모델 라우터 | npm 전역, 상태는 `~/.omniroute/storage.sqlite` | `127.0.0.1:20128` | 작업 `OmniRouteServer` | `omniroute/` |
| Autotune | OmniRoute 콤보 후보 관리 | 이 저장소의 `omniroute/autotune` | OmniRoute API와 읽기 전용 DB | 작업 `OmniAutotune-Check`, `OmniAutotune-Tune`, `OmniAutotune-Verify` | 실제 실행 원본 |
| FreeLLMAPI | 예비 라우터 | `C:/Users/jun/Desktop/TEST/freellmapi` | `127.0.0.1:3001` | 작업 `FreeLLMAPI` | 없음 |
| llama-server | 로컬 모델 | `C:/Users/jun/AppData/Local/Hermes/runtimes/llamacpp` | `127.0.0.1:18434` | 작업 `Local LlamaCpp Server (Qwen3.8 27B)` | `hermes/llamacpp/` |
| Hindsight | 공용 기억 | `E:/hindsight` (Docker 아님) | `127.0.0.1:8888`, DB `5432` | 작업 `Hindsight API` | `hindsight/` |
| Paperclip | 에이전트 관리 | Docker, `E:/paperclip/docker` | `127.0.0.1:3100`, DB `127.0.0.1:5433` | 수동 | `paperclip/` |
| Dashboard | OmniRoute 대시보드 | `C:/Users/jun/Desktop/TEST/omni-autotune-dashboard/dashboard` | `127.0.0.1:20140` | 수동 | `omniroute/dashboard/` |

## 실행 위치를 나눈 이유

- Hindsight는 Docker에서 메모리 부담이 커서 `E:/hindsight`의 네이티브 실행으로 옮겼다. 다시 Docker로 넣지 않는다.
- Paperclip은 Windows 네이티브에서 구독 로그인이 동작하지 않아(`termios` 부재) Docker로 실행한다.
- 포트 `5432`는 Hindsight 전용이다. Paperclip DB는 `5433`을 쓴다.
- llama-server는 10분 유휴 시 모델을 VRAM에서 내린다(`--sleep-idle-seconds 600`). 프로세스는 계속 떠 있고 다음 요청에 깨어난다. 이 상태는 장애가 아니다.

## 설정 파일

- Hindsight의 실제 설정은 `E:/hindsight/hindsight.env` 하나다.
- `~/.hindsight/`에는 연동 플러그인 설정(`coding-agent.json`)과 백업 스크립트가 있다.
- Paperclip의 시크릿은 `E:/paperclip/docker/.env`에 있고 저장소에는 `paperclip/.env.example`만 둔다.
- 저장소 안의 검증 도구는 `skills/verify-hermes-stack/`, Claude Code 연동은 `integrations/claude-code/`에 있다.

Dashboard는 로컬 소스를 읽으며 기록도 로컬에 둔다.

Autotune의 실제 코드와 CONFIG는 이 저장소에서 직접 관리한다. 옛 `Desktop/TEST/omni-autotune` 경로는 같은 폴더를 가리키는 정션이다. 대시보드의 옛 후보 파일 참조도 같은 파일을 읽는다. 자세한 보존 경로와 검증 범위는 [autotune 운영 통합](autotune-home.md)을 참고한다.
