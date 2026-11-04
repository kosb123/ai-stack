# 출처와 이전 작업

2026-11-04에 autotune의 실제 실행 원본을 `omniroute/autotune/`으로 통합했다. 아래의 수집 경로는 최초 스냅샷 출처다. 현재 예약 작업, 상태 보존과 롤백 정보는 [autotune 운영 통합](autotune-home.md)에 있다.

`snapshot-manifest.json`은 로컬 원본과 저장소 대상 경로를 연결한다. `provenance/sources.json`은 수집 출처를 기록한다. 외부 코드와 스킬 스냅샷을 수정하거나 재배포할 때 원래 라이선스와 저작권 고지를 유지한다.

| 원본 | 이 저장소의 관리 대상 |
| --- | --- |
| `C:/Users/jun/AppData/Local/Hermes` | `hermes/`의 설정 예시와 운영 파일 |
| `C:/Users/jun/Desktop/TEST/omni-autotune` | `omniroute/autotune/`의 수집 당시 로컬 작업 내용 |
| `C:/Users/jun/Desktop/TEST/omni-autotune-dashboard/dashboard` | `omniroute/dashboard/` |
| `E:/hindsight` 및 `~/.hindsight` | `hindsight/`의 서로 다른 환경 예시와 운영 파일 |
| 기존 Claude Code 연동 저장소 | `integrations/claude-code/` |

## 기존 저장소와 남은 작업

기존 `kosb123/omni-autotune`의 `main` 기준 커밋은 `818180b`였다. `trial/argon-hindsight-first`의 `5a13771`, `feat/dashboard`의 `3c43df4`, `fix/lane-pin-cooldown`의 `7542ba1`과 PR 1~3은 이전 기록이다. 운영 당시 미커밋 수정은 별도 원본 아카이브에 보존했다. 패치는 최신 코드와 겹칠 수 있으며 PR 3이 병합됐다고 가정하지 않는다.

2026-11-04 사용자 요청으로 GitHub의 `kosb123/omni-autotune` 저장소를 삭제했다. 삭제 전 원격 Git mirror와 PR 3건, 리뷰·커밋·파일 정보 및 이슈 기록을 `.local/github-retirement/omni-autotune-20261104-115739/`에 보존했고 Git 무결성 검사를 통과했다. 삭제 후 기존 저장소 API의 404 응답, ai-stack 접근과 실제 autotune 실행 경로 검사를 확인했다. 현재 운영과 변경 관리는 `kosb123/ai-stack`에서만 수행한다. 로컬 백업은 Git에 올리지 않는다.

기존 `omniroute-claude-setup`은 비공개 `master` 저장소이며 확인 당시 PR이 없었다. 2026-11-02에 두 기존 저장소의 README와 소개·홈페이지에 새 주소를 안내하고 아카이브했다. 이후 `omni-autotune`만 위 절차로 삭제했다. `provenance/github-migration.json`에 이전 작업과 삭제 결과가 있다. 빈 `claudecode`와 관련 없는 `my-full-stack`은 변경하지 않았다.

이전 오류 보고와 일일 작업 기록 등 실행에 필요하지 않은 문서는 기존 아카이브에서 보존한다. 새 저장소에는 현재 설정과 실행 소스, 검증 도구를 선별해 담았다. 원래 Git 이력 전체를 새 저장소에 재작성하거나 업로드하지 않았다.

Dashboard 자동 시작은 과거 거부된 상태가 기록돼 있다. 현재 자동 시작 상태는 확인되지 않았으므로 변경 전에 다시 확인한다.
