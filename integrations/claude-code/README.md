# OmniRoute + Claude Code 무료 세팅 가이드

Claude Code CLI를 [OmniRoute](https://github.com/diegosouzapw/OmniRoute)라는 무료 AI 게이트웨이에 연결해서,
여러 무료 LLM(Groq, Gemini, OpenRouter, NVIDIA 등)을 자동으로 오가며 **비용 없이** 코딩 에이전트를 돌리기 위한 설정입니다.

어떤 Windows 컴퓨터에서든 이 저장소 하나로 동일하게 재현할 수 있습니다.

---

## 0. 미리 준비할 것

아래 프로바이더에 가입해서 **무료 API 키**를 미리 받아두세요 (전부 무료 티어 있음):

| 프로바이더 | 가입 링크 |
|---|---|
| Groq | https://console.groq.com/keys |
| Google Gemini | https://aistudio.google.com/apikey |
| OpenRouter | https://openrouter.ai/keys |
| NVIDIA | https://build.nvidia.com |
| Cohere | https://dashboard.cohere.com/api-keys |
| (선택) Mistral | https://console.mistral.ai |

---

## 1. 필수 프로그램 설치 (관리자 권한 PowerShell)

`scripts/install.ps1`을 실행하거나, 아래 명령을 직접 입력하세요:

```powershell
winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
```

설치 후 **새 터미널 창**을 열어서 확인:
```powershell
node --version
git --version
python --version
```

> Git 설치가 중요한 이유: Windows에서는 Git Bash가 없으면 Claude Code의 `Bash` 도구 자체가 동작하지 않습니다.

---

## 2. OmniRoute + Claude Code CLI 설치

```powershell
npm install -g omniroute
npm install -g @anthropic-ai/claude-code
```

---

## 3. OmniRoute 서버 실행 및 로그인

```powershell
omniroute
```

- 대시보드: http://localhost:20128
- 기본 비밀번호 `CHANGEME` 입력 후 로그인 → 새 비밀번호로 변경

---

## 4. 프로바이더 연결

대시보드 → **공급자(Providers)** → 이름 검색 → API 키 입력 → 저장.
0번에서 준비한 키를 여기에 붙여넣으면 됩니다.

- Groq
- Gemini
- OpenRouter
- NVIDIA
- Cohere
- (선택) Mistral — OmniRoute 연동이 불안정할 수 있음, 참고용

---

## 5. OmniRoute 자체 API 키 생성

대시보드 → **API 키 관리** → "API 키 생성" → 이름 아무거나 → 생성된 키 복사.
(이 키는 로컬 인스턴스 전용이라 매번 새로 설치할 때마다 새로 만들어야 합니다.)

---

## 6. 실행 스크립트 설정

`scripts/` 폴더의 3개 파일을 편한 위치(예: `C:\Users\사용자이름\`)로 복사하고,
각 파일 안의 `여기에-OmniRoute-API-키` 부분을 5번에서 만든 키로 교체하세요.

- **claude-free.ps1** — 일상 작업용. 연결된 프로바이더 중 가장 저렴한(=무료) 걸 자동 선택하고, 하나가 막히면 자동으로 다음 걸로 전환.
- **claude-best.ps1** — 고난이도 작업용. NVIDIA의 DeepSeek V4.1 모델 고정 (품질 최상위권, 무료).
- **autodev-loop.ps1** — 24시간 자동 개발용. 아래 참고.

### 사용법
```powershell
powershell -File "C:\Users\사용자이름\claude-free.ps1" --print "여기에 작업 내용"
powershell -File "C:\Users\사용자이름\claude-best.ps1" --print "여기에 어려운 작업"
```

---

## 7. 24시간 자동 개발 루프 (선택)

무료 모델의 작은 컨텍스트 한도 때문에 대화를 계속 이어갈 수 없으니, 프로젝트 폴더의 `PROGRESS.md` 파일에
"지금까지 한 것 / 다음에 할 것"을 스스로 기록하게 하고, 매 반복마다 그 파일을 읽고 이어서 작업하게 만드는 방식입니다.

```powershell
powershell -File "C:\Users\사용자이름\autodev-loop.ps1" -ProjectDir "C:\Users\사용자이름\MyProject" -Goal "여기에 목표" -OmniRouteApiKey "5번에서-만든-키"
```

- 진행 상황: `MyProject\PROGRESS.md`, 로그: `MyProject\autodev.log`
- 스스로 "완료"라고 판단하면 자동으로 멈춤 (안 멈추면 터미널에서 `Ctrl+C`)

> ⚠️ **주의**: `--permission-mode auto`는 명령 실행까지 전부 자동 승인하는 모드입니다.
> 방치해두면 AI가 프로젝트 폴더 안에서 스스로 판단해 파일 삭제·수정·명령 실행을 계속합니다.
> 반드시 새로 만든 격리된 프로젝트 폴더에서만 사용하고, 중요 파일이 있는 폴더에서는 쓰지 마세요.
> 컴퓨터를 재부팅해도 무인으로 자동 재시작되게 만드는 설정(작업 스케줄러 자동등록 등)은 일부러 포함하지
> 않았습니다 — 터미널을 직접 열어서 시작 여부를 그때그때 판단하는 걸 권장합니다.

---

## 왜 이렇게 설정했는지 (핵심 이유)

- **`--permission-mode auto`가 핵심** — 파일 편집뿐 아니라 실제 명령 실행(Bash)까지 자동 승인되게 함. `acceptEdits`만 쓰면 Bash 실행이 막힘.
- **`--tools "Read Write Edit Bash Glob Grep"`로 도구 개수 제한** — 안 그러면 Claude Code 기본 도구 전체(시스템 프롬프트 포함 약 3만 토큰)가 Groq 같은 무료 모델의 분당 토큰 한도(8,000)를 넘겨서 매번 에러가 남.
- **`CLAUDE_CODE_MAX_CONTEXT_TOKENS=60000`** — 너무 작으면(3만 이하) 대화가 자꾸 조기 압축되며 오작동, 너무 크면 무료 모델 분당 한도 초과. 6만이 실측으로 안정적이었던 값.
- 프로바이더별 특징: Groq(분당 리셋이라 하루종일 버팀) · Gemini(품질 준수) · OpenRouter(하루 50건이라 금방 소진) · NVIDIA(품질 최고, DeepSeek/Mistral-Large 등 대형 모델 무료 제공) · Mistral(OmniRoute 연동이 불안정했음).

---

## 보안 참고

- 이 저장소에는 실제 API 키가 들어있지 않습니다. 각자 발급받은 키를 로컬 스크립트에만 채워 넣으세요.
- `scripts/*.ps1`에 본인 키를 채운 뒤에는 그 파일을 실수로 커밋하지 않도록 주의하세요.
