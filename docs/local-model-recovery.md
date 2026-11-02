# 로컬 모델 연결 복구와 단일 포트 운영

Hermes와 블로그 작성기는 `http://127.0.0.1:18434/v1`의 Qwen 모델 하나를 사용한다. 18435 모델과 임시 호환 프록시는 제거했다. 모델, medium 추론 수준, 163840 컨텍스트 설정은 유지했다.

## 복구한 문제

Desktop 세션 공급자를 `custom`으로 내보내면서 다음 대화의 Qwen 요청이 OmniRoute로 전달됐다. 설치 소스의 `gateway/session_local_inventory.py`가 원래 선택한 공급자 또는 endpoint의 식별자를 반환하도록 수정했다. 두 영향 대화는 공식 세션 모델 변경 API로 `llamacpp`에 복구했다.

같은 Qwen 모델이 두 포트에서 실행되며 GPU 메모리를 경쟁하던 문제도 제거했다. 시작 스크립트는 mutex로 중복 실행을 막는다. 블로그 작성기 두 개는 18434로 직접 연결하고 로컬 키 파일로 인증한다.

## 파일과 배포

- `hermes/llamacpp/Start-External-LlamaCpp.ps1`: 모델 한 개를 18434에서 시작한다.
- `hermes/llamacpp/Watch-LocalLlama.ps1`: 기존 감시 예약 작업에서 모델과 라우팅 패치를 확인한다.
- `hermes/llamacpp/ensure_route_patch.py`: 적용된 패치는 건너뛴다. 원래 hunk가 모두 일치할 때만 복원하고 재시작 marker를 남긴다. upstream 코드가 달라지면 덮어쓰지 않고 검토 필요를 기록한다.
- 감시는 marker가 있으면 공식 gateway restart를 시도한다. `--yes`를 사용하지 않아 실행 중인 사용자 작업을 강제 중단하지 않는다.
- `hermes/patches/installed-source.patch`: 기존 폴백 고정 방지와 공급자 식별 수정.
- `integrations/blog-writer/local_llm.py`: 블로그 작성기의 인증 요청. 기본 키는 `%LOCALAPPDATA%/Hermes/runtimes/llamacpp/.api_key`에서 읽는다. 키는 저장소에 넣지 않는다. 다른 호스트로 주소를 바꿔도 로컬 키를 자동 전송하지 않는다.
- `integrations/blog-writer/migrate_endpoint.py`: 기존 작성기 두 개의 주소와 요청 생성만 이관한다. 다른 로컬 수정은 보존한다.

승인된 실행 환경 변경은 수동 배포했다. 저장소 수정 자체가 서비스를 자동으로 바꾸지는 않는다.

```powershell
python integrations/blog-writer/migrate_endpoint.py --project E:/blog-rank-project --backup-dir C:/local-backups/blog-single-port
```

`BLOG_LLM_URL`은 기본 주소를 바꾼다. `BLOG_LLM_API_KEY_FILE`은 로컬 키 파일 경로를 바꾼다. `BLOG_LLM_API_KEY`를 명시하면 해당 값을 사용한다. 비밀 값은 명령줄이나 Git 파일에 넣지 않는다.

## 검증 범위

라우팅 회귀 7개를 통과했다. 중복 모델 제거 후 실제 Hermes 파일 읽기부터 최종 답변까지 5회 연속 통과했으며, run `bff60562-9249-4004-b310-26fc0d30d80b`에 증거가 남아 있다. 단위 검사 13개는 인증 요청, 원격 호스트로의 로컬 키 전송 방지, 반복 이관 시 무변경, 패치 충돌 시 원본 보존을 확인한다. 배포된 블로그 요청 모듈로 18434에 실제 요청해 HTTP 200과 `OK` 응답을 확인했다. 18435는 연결이 거부되고, 모델 수신 포트는 18434 하나다. 이 결과는 Desktop 클릭 경로 전체나 미래의 모든 장애를 보증하지 않는다.

공식 근거: [Hermes 로컬 모델](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/local-models.md), [llama.cpp 서버](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
