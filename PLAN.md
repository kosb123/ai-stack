# 통합 작업 계획

- [x] Read the Principles section of the poteto-mode skill.
- [x] Phase A: Frame.
- [x] Phase B: Design the workflow.
- [x] Phase C: Run the loop.
- [x] Phase D: Keep the audit trail.
- [x] Phase E: Verify and hand back.

완료 기준은 현재 운영 소스와 관련 원격 브랜치의 작업을 잃지 않고, 비밀값과 런타임 데이터를 제외한 단일 비공개 저장소를 푸시하는 것이다. GitHub 원격 SHA와 로컬 SHA가 일치하고, 가져온 코드의 오프라인 테스트와 설정·비밀정보 검사가 통과해야 한다. 관련 기존 저장소는 이관 링크와 기존 브랜치·PR을 보존한 채 아카이브한다.

1. 관련 로컬 경로, 설정, 실행 대상, GitHub 저장소와 미커밋 변경을 조사한다.
2. 비밀 제거와 파일 허용 목록을 검증할 스냅샷 도구를 만든다.
3. Hermes, OmniRoute, Hindsight, 대시보드, Claude/Codex 연동과 검증 도구를 복사한다.
4. 새 경로와 로컬 비밀변수 사용을 검증한다. 운영 원본은 바꾸지 않는다.
5. 현재 파일과 출처 해시, 실제 비밀값 대조, 테스트를 독립 검토한다.
6. 새 비공개 저장소를 푸시하고 기존 두 저장소에 이관 안내 후 아카이브한다.
7. 원격 상태와 CI 결과, 기존 서비스 응답을 확인한다.

Blocking first steps는 원본과 미반영 변경의 식별, 비밀 제거 검사다. Independent workstreams는 읽기 전용 운영 경로 조사, GitHub 조사, 독립적인 문서 작성이다. Shared mutable state는 새 저장소의 파일을 담당자별로 분리하고 최종 export·후처리·커밋을 한 작업자가 순차 수행한다. Smallest safe decomposition은 저장소 한 개에 세 서비스의 관리 파일을 배치하고 실제 배포 경로를 유지하는 것이다.
