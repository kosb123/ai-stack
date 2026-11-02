# 2026-10-29: 바탕화면 '로컬 LLM 서버 켜기' 바로가기용. 창 없이 서버를 켜고 결과만 알림창으로 보여준다.
# 실제 서버 실행은 Start-External-LlamaCpp.ps1 이 한다 (이미 떠 있으면 아무것도 안 함).
$ErrorActionPreference = 'Stop'

$runtimeRoot = 'C:\Users\jun\AppData\Local\hermes\runtimes\llamacpp'
$startScript = Join-Path $runtimeRoot 'Start-External-LlamaCpp.ps1'
$healthUrl = 'http://127.0.0.1:18434/health'
$shell = New-Object -ComObject WScript.Shell

function Test-Health {
    try {
        (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $healthUrl).StatusCode -eq 200
    } catch {
        $false
    }
}

if (Test-Health) {
    # 64 = 정보 아이콘, 5초 뒤 자동으로 닫힘
    $null = $shell.Popup('로컬 LLM 서버가 이미 켜져 있습니다.', 5, '로컬 LLM 서버', 64)
    exit 0
}

try {
    & $startScript
} catch {
    # 16 = 오류 아이콘
    $null = $shell.Popup("서버를 켜지 못했습니다.`n$($_.Exception.Message)", 0, '로컬 LLM 서버', 16)
    exit 1
}

# 모델을 GPU에 올리는 데 보통 1분 안팎 걸린다.
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 3
    if (Test-Health) {
        $null = $shell.Popup("로컬 LLM 서버가 켜졌습니다.`n3분 동안 쓰지 않으면 GPU 메모리에서 자동으로 내려갑니다.", 8, '로컬 LLM 서버', 64)
        exit 0
    }
}

$null = $shell.Popup("3분이 지나도 서버가 응답하지 않습니다.`n로그: $runtimeRoot\external-server.stderr.log", 0, '로컬 LLM 서버', 48)
exit 1
