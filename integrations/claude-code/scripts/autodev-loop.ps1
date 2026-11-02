param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectDir,

    [Parameter(Mandatory=$true)]
    [string]$Goal,

    [string]$OmniRouteApiKey = "여기에-OmniRoute-API-키",

    [int]$DelaySeconds = 30
)

$env:ANTHROPIC_BASE_URL = "http://localhost:20128/v1"
$env:ANTHROPIC_API_KEY = $OmniRouteApiKey
$env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = "60000"

if (-not (Test-Path $ProjectDir)) {
    New-Item -ItemType Directory -Force -Path $ProjectDir | Out-Null
}
Set-Location $ProjectDir

$logFile = Join-Path $ProjectDir "autodev.log"

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

$prompt = @"
목표: $Goal

이 프로젝트 폴더에서 계속 작업 중이야.
1. PROGRESS.md 파일이 있으면 읽어서 지금까지 뭘 했는지, 다음에 뭘 해야 하는지 확인해.
   없으면 새로 만들어서 목표를 이루기 위한 계획을 세워.
2. 이번 턴에는 다음 할 일 중 딱 하나(작고 한 턴에 끝날 분량)만 실제로 구현/수정/테스트해.
3. 작업이 끝나면 PROGRESS.md를 갱신해:
   - 완료된 것 전체 목록
   - 다음에 할 일 1~3개 (구체적으로)
   - 막힌 문제가 있으면 "문제점" 섹션에 적어놔
4. 이미 목표를 완전히 달성했다고 판단되면 PROGRESS.md 맨 위에 "## 상태: 완료"라고 적어.
"@

Log "===== 자동 개발 루프 시작 — 목표: $Goal ====="

$iteration = 0
while ($true) {
    $iteration++
    Log "----- 반복 $iteration 시작 -----"

    $progressPath = Join-Path $ProjectDir "PROGRESS.md"
    if (Test-Path $progressPath) {
        $firstLines = Get-Content $progressPath -TotalCount 3 -ErrorAction SilentlyContinue
        if ($firstLines -match "상태: 완료") {
            Log "PROGRESS.md에 '완료' 표시 발견 — 루프를 종료합니다."
            break
        }
    }

    try {
        $output = claude --print --permission-mode auto --tools "Read Write Edit Bash Glob Grep" --model "auto/cheap" $prompt 2>&1
        Log $output
    } catch {
        Log "에러 발생: $_"
    }

    Log "----- 반복 $iteration 종료, ${DelaySeconds}초 대기 -----"
    Start-Sleep -Seconds $DelaySeconds
}
