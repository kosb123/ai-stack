# 워크스페이스 Linux 가상 환경 준비 (provision-workspace-venv.sh)

Paperclip 서버 컨테이너(Linux)에서 프로젝트 테스트를 돌리는 방법. 다른 프로젝트에도
같은 절차가 적용된다. 첫 사례 기록은 [컨테이너 Codex 테스트](paperclip-codex-tests.md)
(JUN-8)에 있다.

## 배경

- 저장소의 `.venv/`는 Windows 가상 환경(`Scripts/` 레이아웃)이다. Linux 컨테이너에서
  실행할 수 없다. 컨테이너 안에서는 항상 `/workspace/.venvs/<name>/`의 Linux venv를 쓴다.
- 컨테이너(Debian 13, Python 3.13)의 시스템 python에는 `pip`/`ensurepip`가 없을 수
  있다. `python3-venv` 패키지가 없기 때문이다.
- `/workspace`는 Windows 호스트 `E:\projects`의 바인드 마운트다. 컨테이너가 재생성돼도
  venv는 남는다. 사라지는 것은 컨테이너 내부의 `python3-venv` 패키지뿐이다.

## 전제

1. 컨테이너가 실행 중이고(예: `docker-server-1`), 프로젝트가 `/workspace/<프로젝트>/`
   아래에 있다.
2. 프로젝트에 `requirements.txt`가 있다. 없으면 venv만 만들고 의존성 설치는 건너뛴다.
3. 최초 또는 컨테이너 재생성 후, root 단계가 한 번 필요하다:

   ```
   docker exec -u root docker-server-1 sh -c \
     'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv'
   ```

   컨테이너 fs는 일시적이므로 이 단계는 컨테이너가 새로 생기면 다시 실행한다.
   venv 자체는 바인드 마운트에 있어 유지된다.

## 사용법

스크립트 정본은 이 저장소의 `scripts/provision-workspace-venv.sh`다. 컨테이너에는
`/workspace/.infra/`로 복사해서 쓴다:

```
cp scripts/provision-workspace-venv.sh /workspace/.infra/
```

실행:

```
docker exec -u node docker-server-1 bash /workspace/.infra/provision-workspace-venv.sh <name> [project-dir]
```

- `<name>`: `/workspace/.venvs/<name>` 이라는 venv 디렉터리 이름. 보통 프로젝트
  이름과 같이 잡는다.
- `[project-dir]`: `requirements.txt`가 있는 프로젝트 디렉터리. 생략하면
  `/workspace/<name>`이다. 프로젝트 디렉터리 이름과 venv 이름을 달리 잡거나
  서브디렉터리 프로젝트라면 명시한다.

스크립트 동작:

- `python3-venv`가 없으면 root로 실행 중일 때 자동 설치, 아니면 root 명령을
  안내하고 끝난다.
- venv가 정상이면 재사용한다. `bin/python`이나 `pip`이 없으면 지우고 새로 만든다.
- `requirements.txt`가 있으면 `pip install -r`을 실행한다. 반복 실행해도 안전하다
  (idempotent, exit 0).
- 마지막에 venv python으로 `venv ready: ...` 한 줄을 출력해 결과를 확인시켜 준다.

## 예시

프로젝트 디렉터리 이름 = venv 이름인 일반 경우:

```
docker exec -u root docker-server-1 sh -c 'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv'
docker exec -u node docker-server-1 bash /workspace/.infra/provision-workspace-venv.sh policy-radar
```

venv 이름을 따로잡는 경우:

```
bash /workspace/.infra/provision-workspace-venv.sh my-venv /workspace/some-project/sub
```

프로젝트가 이 구조와 다르면(예: 별도 컨테이너, 다른 바인드 마운트) 스크립트 헤더
주석의 경로를 고쳐 쓰면 된다. 로직은 경로와 무관하다.

## 테스트 실행

- 컨테이너: `cd /workspace/<프로젝트> && /workspace/.venvs/<name>/bin/python -m unittest <모듈> -v`
- Windows 호스트: `.venv\Scripts\python -m unittest <모듈> -v`

두 환경은 다른 venv를 쓰므로 결과도 각자의 의존성 설치 상태에 의존한다. 같은
`requirements.txt`라면 동일해야 하고, 다르면 설치 상태를 먼저 의심한다.

## 컨테이너 재생성 후 복구

1. root apt-get 단계(위 전제 3번)를 다시 실행한다.
2. `bash /workspace/.infra/provision-workspace-venv.sh <name>`을 다시 실행해
   venv가 그대로인지 확인한다. venv는 유지되어 있어 대부분 즉시 exit 0이다.
3. Codex 전역 지침 `/paperclip/.codex/AGENTS.md`가 지워진 경우만 이 저장소의
   `paperclip/codex-global-instructions.md`로 다시 설치한다.

## 주의

- 컨테이너 안에서 저장소의 Windows `.venv/`를 쓰지 않는다.
- 자격증명이나 비밀 값을 venv, `/workspace`, 컨테이너에 넣지 않는다.
- 스크립트는 `docker volume prune` 같은 볼륨 제거를 하지 않는다. 볼륨 정리 수동
  금지 정책도 유지한다.
