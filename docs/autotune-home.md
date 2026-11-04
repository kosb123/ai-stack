# Autotune 운영 원본

2026-11-04부터 `C:/Users/jun/Documents/GitHub/ai-stack/omniroute/autotune`이 실제 코드와 설정의 유일한 운영 원본이다. `autotune.py`의 CONFIG와 `selected_models.json`을 여기서 관리한다. 변경은 다음 예약 실행부터 사용된다. 기존 모델 우선순위와 콤보 설정을 바꾸지 않고 경로와 상태를 통합했다.

## 예약 작업과 소비자

| 작업 | 실행 파일 | 인수 |
| --- | --- | --- |
| OmniAutotune-Check | `omniroute/autotune/autotune.py` | `check` |
| OmniAutotune-Tune | `omniroute/autotune/autotune.py` | `tune` |
| OmniAutotune-Verify | `omniroute/autotune/verify_cycle.py` | 없음 |

세 작업은 기존 Python 3.12 `pythonw.exe`를 유지하고 작업 디렉터리도 실제 운영 원본으로 지정한다. 주기와 계정, 원래 활성화 상태는 유지한다. 경로 전환을 위해 잠시 비활성화한 후 원래 활성화 상태를 복원했다.

옛 `C:/Users/jun/Desktop/TEST/omni-autotune`은 이 폴더를 가리키는 디렉터리 정션이다. 옛 경로에서 파일을 수정해도 같은 파일이 변경된다. 튜너는 `__file__.resolve()`를 사용하므로 옛 경로와 새 경로가 같은 상태 파일과 잠금을 사용한다. 대시보드의 옛 후보 파일 경로도 동일한 파일을 읽는다. 새 작업과 명령은 새 경로를 직접 사용한다.

Hermes, Hindsight와 OmniRoute 서버의 설치 경로, 인증과 데이터베이스는 이번 통합으로 이동하지 않았다. 대시보드 서버 자체도 이전하지 않았다.

## 실행 데이터와 Git

`state.json`, `.lock`, `state.tmp`, `logs/`, `reports/`, `backups/`와 survey의 JSON, JSONL, 로그 및 todo 출력은 Git에서 제외한다. 코드와 비밀이 없는 선택 모델 설정은 추적한다. 인증 정보는 기존 외부 저장 위치를 유지한다.

`scripts/capture_local.py`와 `snapshot-manifest.json`은 autotune을 외부에서 다시 가져오지 않는다. 스냅샷 수집이 현재 운영 코드를 덮어쓰거나 source/output 겹침을 일으키지 않도록 했다. 최초 수집 당시의 autotune 출처 해시 10건은 `provenance/autotune-snapshot-sources.json`에 별도로 보존한다. 기존 패치도 이력으로 유지하며 현재 파일의 해시라고 간주하지 않는다. 이전 Git 출처는 `provenance/autotune-origin.json`에 기록했다.

## 확인한 범위

전환 중 기존 실행 인스턴스와 잠금 파일이 없음을 확인했다. 전환 직후 기존 원본과 CONFIG, QUALITY_TIERS, 코드, 선택 모델, 상태와 로그·백업·보고서·survey 파일을 대조했다. 실제 예약 작업의 실행 경로와 작업 디렉터리, 옛 경로의 실제 대상, Git 제외 규칙도 확인했다. 새 경로에서 튜너를 import하여 실제 ROOT 및 state 경로와 DB 읽기를 확인했고 콤보 설정의 전후 해시가 일치했다.

검사 도구는 읽기 전용이다. 원본 아카이브를 지정하여 실행한다.

```powershell
python scripts/check_autotune_home.py --source C:/Users/jun/Desktop/TEST/_archive/omni-autotune-before-ai-stack-20261104-112446
```

예약 실행 이후 상태와 로그는 정상적으로 달라질 수 있다. 이때는 정책·코드·경로 검사를 수행한다.

```powershell
python scripts/check_autotune_home.py --source C:/Users/jun/Desktop/TEST/_archive/omni-autotune-before-ai-stack-20261104-112446 --allow-state-changes
```

튜닝과 검증 사이클을 수동 실행하지 않았다. 이들은 실제 콤보를 재선정하고 모델 요청을 발생시킨다. 오프라인 검사와 import 성공을 모델 요청 성공으로 보고하지 않는다.

## 원본과 롤백

기존 폴더 전체와 `.git`, 미커밋 변경 및 오래된 백업은 `C:/Users/jun/Desktop/TEST/_archive/omni-autotune-before-ai-stack-20261104-112446`에 보존했다. 원본 예약 작업 XML과 전후 로컬 증거는 `.local/autotune-migration/`에 보관하며 Git에 포함하지 않는다.

롤백이 필요하면 세 예약 작업의 새 실행을 막고 진행 중 작업이 끝난 것을 확인한다. 새 위치에서 생성된 최신 상태와 기록을 먼저 보존한다. 옛 경로의 정션만 제거한 뒤 아카이브를 복원하고, 최신 실행 데이터를 반영한 뒤 보관한 XML의 실행 경로와 작업 디렉터리를 복구한다. 원래 활성화 상태로 되돌리고 한 원본만 실행되는지 확인한다. 정션 대상이나 데이터베이스를 삭제하지 않는다.

[OmniRoute 공식 문서](https://github.com/cheaper-inference/OmniRoute/blob/main/README.md)의 콤보 설정은 그대로 유지했다. [Microsoft 예약 작업 문서](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/set-scheduledtask?view=windowsserver2025-ps)에 따라 액션만 변경하며 실행 중인 인스턴스에 경로 변경이 소급 적용되지 않는 점을 고려했다.
