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
- `docker-compose.yml` — 현재: server mem_limit 1536m, db 512m, pids_limit 2048, app 포트 127.0.0.1:3100, db 포트 127.0.0.1:5433 (5432는 Hindsight 전용)
- `.env` — BETTER_AUTH_SECRET 등 시크릿 원본 (187B). 이 파일 없으면 server 기동 불가.
- 백업: `docker-compose.yml.bak-before-memlimit-20261103`, `docker-compose.yml.bak-before-ram-20261104`

## 4. WSL 메모리 상한 (C:\Users\jun\.wslconfig)
```
[wsl2]
memory=2560MB
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
