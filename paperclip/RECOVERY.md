# Paperclip Docker 복구 카드 (2026-11-03)

## 1. 이미지만 날아간 경우 (DB 데이터는 안전)
```
docker pull ghcr.io/paperclipai/paperclip:latest
docker pull postgres:17-alpine
docker compose up -d
```
이미지는 코드일 뿐. 볼륨은 건드리지 않음.
주의: paperclip 태그는 `latest`라 다시 pull하면 새 버전이 올 수 있음.

## 2. 컨테이너만 죽은 경우
```
docker compose up -d
```

## 3. 구성 파일 (Docker 밖, E:\paperclip\docker)
- `docker-compose.yml` — 현재: server mem_limit 3072m, db 512m, pids_limit 2048, app 포트 127.0.0.1:3100, db 포트 127.0.0.1:5433 (5432는 Hindsight 전용)
- `.env` — BETTER_AUTH_SECRET 등 시크릿 원본 (187B). 이 파일 없으면 server 기동 불가.
- 백업: `docker-compose.yml.bak-before-memlimit-20261103`, `docker-compose.yml.bak-before-ram-20261104`

## 4. WSL 메모리 상한 (C:\Users\jun\.wslconfig)
```
[wsl2]
memory=4096MB
swap=0
processors=8

[experimental]
autoMemoryReclaim=gradual
```
변경 후 적용: `wsl --shutdown` (Docker Desktop이 VM 자동 재기동)
백업: `C:\Users\jun\.wslconfig.bak-before-ram-20261104`
`settings-store.json` 백업: C:\Users\jun\AppData\Roaming\Docker\settings-store.json.bak-before-memlimit-20261103

## 5. 데이터 위치
- 볼륨 `docker_pgdata`, `docker_paperclip-data` → E:\DockerData\DockerDesktopWSL (WSL distro)
- 미사용 볼륨 `paperclip_pg18`, `paperclip_home` — 이전 회사(korail, jun_company) 데이터. 2026-11-04 불필요 판단, 삭제 가능.
- `docker volume prune`, `docker system prune --volumes` 금지 — 익명 볼륨 `ccd523a3...`에 Paperclip이 아닌 앱 데이터(falkordb, rabbitmq, valkey, postgres)가 있음.
- 이전 소스: `E:\paperclip-old-20261104` (compose는 이미지만 쓰므로 불필요, `E:\paperclip`에는 `docker\`만 남김)
- 이미지 삭제/컨테이너 삭제는 볼륨에 영향 없음
- E:\DockerData 자체가 망가진 경우에만 DB 데이터 손실. 현재 별도 백업 없음.

## 6. 에이전트 조직 구성 (2026-11-04)
- 작업 폴더: `E:\projects`만 컨테이너 `/workspace`로 연결. E드라이브 전체를 연결하지 않음.
- 변경 전 백업: `docker-compose.yml.bak-before-paperclip-org-20261104`, `C:\Users\jun\.wslconfig.bak-before-paperclip-org-20261104`, DB `E:\paperclip-backups\pre-org-20261104\paperclip.dump`
- Hermes: `paperclip` 프로필(로컬 Qwen, fallback 없음, cwd `E:/projects`). 접속 주소는 `http://127.0.0.1:8642/p/paperclip`이고 `hermes-relay` 컨테이너가 Windows의 8642 포트로 중계함.
- 중계가 필요한 이유: Paperclip 검증은 평문 HTTP 경고를 실패로 처리하므로 `host.docker.internal`을 직접 쓰면 Hermes가 `verifying`에서 멈춤.
- `docker-server-1`을 다시 만들면 `hermes-relay`도 함께 다시 시작해야 함: `docker compose up -d`
- 완료 보고 키 `PAPERCLIP_BRIDGE_API_KEY`는 프로젝트 단위로 제한됨. 프로젝트를 추가하면 범위를 넓힌 키로 다시 발급해야 함.
- Hermes API 키: default와 paperclip 프로필 `.env`의 `API_SERVER_KEY`. 서로 다른 값이어야 함.

## 8. Gemini 에이전트 (2026-11-04)
- Hermes `google` 프로필의 기본 모델은 OmniRoute 콤보 `gemini-agent`임. 순서는 `gemini-3.8-flash-medium`, `gpt-oss-120b-medium`, `gemini-4-argon-medium`이고 모두 agy 연결에 고정함.
- 할당량 풀은 Gemini, Claude/GPT, Argon으로 나뉨. Antigravity의 Claude 모델은 할당량이 남아 있어도 429를 반환했고, 이 실패가 쌓이면 공급자 전체 차단으로 Gemini까지 막힘. 그래서 Claude는 콤보와 autotune 후보에서 뺌.
- `Provider antigravity circuit breaker is open`이 나오면 원인 요청을 먼저 확인한 뒤 `POST /api/resilience/reset`으로 해제.
