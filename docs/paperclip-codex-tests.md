# Paperclip 컨테이너에서 Codex가 직접 테스트를 실행하게 하기

(JUN-8, 2026-11-04)

## 문제

policy-radar의 JUN-6 리뷰에서 Codex가 컨테이너에서 테스트를 실행하지 못했다.
원인을 확인한 결과:

- 컨테이너(Debian 13, Python 3.13.5)의 시스템 python에는 `scrapling`이 없었고
  `pip`/`ensurepip`도 없었다(`python3-venv` 패키지 미설치).
- 워크스페이스에 있는 `.venv/`는 Windows 호스트용 가상 환경(`Scripts/`)이라
  Linux 컨테이너에서 실행할 수 없다.
- 즉 Codex는 `import scrapling` 실패로 테스트를 돌릴 수도, 의존성을 설치할
  수도 없었다.

## 수정

1. `/workspace/.venvs/<프로젝트이름>/` 에 Linux 가상 환경을 만든다.
   `/workspace`는 Windows 호스트 E:\projects의 바인드 마운트이므로 컨테이너
   재생성後も 유지된다. 첫 설치:

   ```
   docker exec -u root docker-server-1 sh -c \
     'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv'
   docker exec -u node docker-server-1 bash /workspace/.infra/provision-workspace-venv.sh policy-radar
   ```

   컨테이너 fs는 일시적이므로 새 컨테이너에서는 root apt-get 단계가 다시
   필요하다(venv 자체는 유지됨). 스크립트는 `scripts/provision-workspace-venv.sh`
   에 정본으로 보관하고, 컨테이너에는 `/workspace/.infra/` 에 설치한다.

2. 컨테이너 Codex 전역 지침 `paperclip/codex-global-instructions.md`(정본)를
   `/paperclip/.codex/AGENTS.md` 로 설치한다. Codex CLI는 `$CODEX_HOME/AGENTS.md`
   (컨테이너에서는 HOME=/paperclip) 를 전역 지침으로 읽고, 프로젝트 루트의
   `AGENTS.md` 와 결합한다. 공식 문서:
   https://github.com/openai/codex/blob/main/docs/agents_md.md

3. 정책 레포의 테스트 명령:

   - 컨테이너: `cd /workspace/policy-radar && /workspace/.venvs/policy-radar/bin/python -m unittest tests.test_youth_seoul -v`
   - Windows 호스트: `.venv\Scripts\python -m unittest tests.test_youth_seoul -v`

## 확인

- 컨테이너에서 위 명령 실행: 15 tests, OK (JUN-6 회귀 테스트 포함).
- 스크립트 idempotent 확인: 두 번째 실행도 exit 0, venv 재사용.

## 컨테이너 재생성 후 복구 절차

1. root apt-get 단계(위 1번 첫 명령)
2. `bash /workspace/.infra/provision-workspace-venv.sh policy-radar`
3. `/paperclip/.codex/AGENTS.md` 가 없으면 이 저장소의
   `paperclip/codex-global-instructions.md` 로 다시 설치
   (`/paperclip` 볼륨이 지워진 경우만).
