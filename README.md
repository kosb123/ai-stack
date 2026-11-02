# ai-stack

Hermes, OmniRoute, Hindsight의 설정 예시와 운영 스크립트, 검증 도구를 관리하는 저장소다. 기존 실행 경로는 그대로 사용한다. 저장소의 파일을 수정해도 실행 중인 서비스에 자동으로 반영되지 않는다.

## 시작하기

- [구성 요소와 실행 경로](docs/components.md)에서 파일과 로컬 서비스의 관계를 확인한다.
- [운영과 검증](docs/operations.md)에서 변경 및 검증 범위를 확인한다.
- [출처와 이전 작업](docs/migration.md)에서 기존 저장소와 미완료 작업을 확인한다.
- [비밀과 로컬 데이터](docs/secrets.md)에서 저장소에 넣지 않는 데이터를 확인한다.

`*.example.*` 파일의 `${LOCAL_SECRET}`은 자리 표시자다. 예시 파일은 바로 배포할 수 없으며 기존 설정을 덮어쓰는 데 사용하지 않는다. OAuth 인증은 해당 공급자의 공식 인증 절차를 사용한다.

이 저장소는 현재 운영 환경의 복원 이미지가 아니다. 데이터베이스, 인증 정보, 메모리와 모델 파일은 포함하지 않는다. 서비스의 현재 상태와 연결 여부는 별도로 확인해야 한다.

## 저장소 확인

```powershell
python -m pip install -r requirements-dev.txt
python scripts/verify_repo.py .
python -m unittest discover -s tests -v
python -m unittest discover -s omniroute/autotune -p test_preferred.py
python -m unittest discover -s omniroute/dashboard -p test_dashboard.py
python -m unittest discover -s skills/verify-hermes-stack/scripts -p test_verifier.py
```

대시보드는 `python omniroute/dashboard/server.py`로 실행한다. 기존 20140 서버가 실행 중이면 두 번째 서버를 시작하지 않는다. 실행하면 별도 로컬 DB가 생성된다.

`scripts/capture_local.py`는 이 PC의 현재 실행 경로에서 다시 스냅샷을 수집하는 명시적 작업이다. 수집 시 저장소의 관리 파일을 갱신하므로 먼저 로컬 변경을 커밋하고 차이를 검토한다. 일반적인 수정에 매번 실행하지 않는다. 원본 서비스의 설정은 바꾸지 않는다. 임시 설정 추출물은 `%LOCALAPPDATA%/ai-stack/capture-inputs`에 생기며 Git에 포함되지 않는다.

## 공식 문서

- [Hermes skills 문서](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)
- [OmniRoute README](https://github.com/cheaper-inference/OmniRoute/blob/main/README.md)
- [Hindsight README](https://github.com/vectorize-io/hindsight/blob/main/README.md)
