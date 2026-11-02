$env:ANTHROPIC_BASE_URL = "http://localhost:20128/v1"
if ([string]::IsNullOrWhiteSpace($env:ANTHROPIC_API_KEY)) { throw 'Set ANTHROPIC_API_KEY locally before running Claude.' }
$env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = "60000"
claude --permission-mode auto --tools "Read Write Edit Bash Glob Grep" --model "auto/cheap" @args
