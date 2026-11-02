# 구성 요소와 실행 경로

이 저장소는 로컬 실행 파일의 관리용 사본을 모은다. 아래 경로는 수집 당시 관찰한 경로이며 현재 서비스 상태를 보장하지 않는다.

| 구성 요소 | 저장소 | 실행 환경 |
| --- | --- | --- |
| Hermes | `hermes/` 설정 예시, 프로필, 운영 스크립트, llama.cpp 설정과 pstack 스냅샷 | `C:/Users/jun/AppData/Local/Hermes` |
| OmniRoute | `omniroute/` 설정 예시, autotune, dashboard, 시작 스크립트 | 상태는 `~/.omniroute/storage.sqlite`. autotune은 `C:/Users/jun/Desktop/TEST/omni-autotune` |
| Dashboard | `omniroute/dashboard/` | `C:/Users/jun/Desktop/TEST/omni-autotune-dashboard/dashboard`, 관찰 주소 `127.0.0.1:20140` |
| Hindsight | `hindsight/` 환경 예시와 운영 스크립트 | 네이티브 설치는 `E:/hindsight`. 별도의 레거시 클라이언트 설정은 `~/.hindsight` |
| 통합 검증 | `skills/verify-hermes-stack/` | 로컬 스택을 대상으로 하는 별도 검증 도구 |
| Claude Code 연동 | `integrations/claude-code/` | 기존 연동 저장소에서 가져온 파일 |

Dashboard는 로컬 소스를 읽으며 기록도 로컬에 둔다. 수집 당시 OmniRoute 포트는 `20128`, llama.cpp 포트는 `18434`, Hindsight 포트는 `8888`이었다. 네이티브 Hindsight는 `0.0.0.0:8888`에서 수신하는 것으로 관찰됐다. 이 주소는 로컬 전용 수신을 뜻하지 않는다. 현재 바인딩은 운영 환경에서 다시 확인해야 한다.

`hindsight/native.env.example`과 `hindsight/legacy-client.env.example`은 서로 다른 설정의 예시다. 어느 한쪽을 다른 쪽의 대체 파일로 사용하지 않는다.
