winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements

Write-Host "설치 완료. 새 터미널을 열어서 node/git/python --version 으로 확인하세요." -ForegroundColor Green
Write-Host "그 다음 다음 명령으로 OmniRoute와 Claude Code를 설치하세요:" -ForegroundColor Yellow
Write-Host "  npm install -g omniroute"
Write-Host "  npm install -g @anthropic-ai/claude-code"
