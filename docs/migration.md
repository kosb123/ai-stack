# 출처와 이전 작업

`snapshot-manifest.json`은 로컬 원본과 저장소 대상 경로를 연결한다. `provenance/sources.json`은 수집 출처를 기록한다. 외부 코드와 스킬 스냅샷을 수정하거나 재배포할 때 원래 라이선스와 저작권 고지를 유지한다.

| 원본 | 이 저장소의 관리 대상 |
| --- | --- |
| `C:/Users/jun/AppData/Local/Hermes` | `hermes/`의 설정 예시와 운영 파일 |
| `C:/Users/jun/Desktop/TEST/omni-autotune` | `omniroute/autotune/`의 수집 당시 로컬 작업 내용 |
| `C:/Users/jun/Desktop/TEST/omni-autotune-dashboard/dashboard` | `omniroute/dashboard/` |
| `E:/hindsight` 및 `~/.hindsight` | `hindsight/`의 서로 다른 환경 예시와 운영 파일 |
| 기존 Claude Code 연동 저장소 | `integrations/claude-code/` |

## 기존 저장소와 남은 작업

기존 [kosb123/omni-autotune](https://github.com/kosb123/omni-autotune)의 `main` 기준 커밋은 `818180b`였다. `trial/argon-hindsight-first`의 `5a13771`, `feat/dashboard`의 `3c43df4`, `fix/lane-pin-cooldown`의 `7542ba1`과 기존 [PR 1](https://github.com/kosb123/omni-autotune/pull/1), [PR 2](https://github.com/kosb123/omni-autotune/pull/2), [PR 3](https://github.com/kosb123/omni-autotune/pull/3)은 기존 저장소에서 추적한다. 현재 사용 중인 autotune에는 커밋되지 않은 로컬 수정이 있었으므로 `main`만으로 현재 사본을 재현할 수 없다. 패치는 최신 코드와 겹칠 수 있다. PR 3이 병합됐다고 가정하지 말고 실제 상태와 차이를 확인한다.

기존 `omniroute-claude-setup`은 비공개 `master` 저장소이며 확인 당시 PR이 없었다. 기존 저장소를 archive하기 전에 새 저장소의 파일, 미완료 PR, 브랜치와 참조 링크를 검토한다. Archive는 별도의 운영 작업이다. 빈 `claudecode`와 관련 없는 `my-full-stack`은 이 통합 범위에 포함하지 않는다.

Dashboard 자동 시작은 과거 거부된 상태가 기록돼 있다. 현재 자동 시작 상태는 확인되지 않았으므로 변경 전에 다시 확인한다.
