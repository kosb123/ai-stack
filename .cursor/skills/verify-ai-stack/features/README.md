# ai-stack verification map

This is the maintained source of user-visible verification paths for jun's installed Windows stack. Read the relevant feature before driving it. The controller lives at `.cursor/skills/verify-ai-stack/scripts/control_stack.py`.

## Baseline preconditions

- Run from `C:/Users/jun/Documents/GitHub/ai-stack` with host Python and PyYAML.
- Core installed services must be available on local ports 18434, 20128 and 8888. Port 18435 must be closed and the Qwen model must have one process. The manual dashboard on 20140 is required only for its own journey. Paperclip on 3100 and FreeLLMAPI on 3001 are checked only when already running.
- Run Launch and Doctor from the parent SKILL.md. Use its `$ctl` and `$run` variables for commands below.
- The launch creates fresh `C:/vh/<UUID>/` scratch state; CLI profiles and memory bank are owned by that UUID. Production profiles, credentials and `jun` are not cloned or changed.
- Run one driver at a time. A busy model is a reason to defer inference, not to launch another server.

## Proof conventions

Read `manifest.json`, raw artifacts and `coverage.json` under `evidence/ai-stack/runs/<UUID>/`. Never promote API/CLI/configuration proof to UI, hook execution, restore or fresh-install proof. `VERIFIED`, `NOT VERIFIED`, `INCONCLUSIVE` and unexecuted entries stay distinct. Cleanup removes owned scratch and preserves evidence.

| Feature | Commands | Real observable boundary |
| --- | --- | --- |
| [Local execution and blog connection](local-execution.md) | `local-tool`, `blog-direct` | Real file nonce/final answer and deployed authenticated blog client on 18434 |
| [Cloud routing and tool work](routing.md) | `combos`, `hermes-tool`, `settings` | Strict JSON/tool calls, actual served primary, real cloud file tool |
| [Shared memory](memory.md) | `memory`, `plugin-memory`, `settings` | API persistence and separate-session Hermes plugin retain/recall |
| [Dashboard](dashboard.md) | `dashboard` | Live document, all seven data routes and four periods, collector freshness |
| [Operations and configuration](operations.md) | `doctor`, `settings`, `operations`, `autotune`, `services` | Process identity/auth, deployed guards, canonical task actions, optional service metadata, DB durability and readable recent backup |

Use `audit` for the read-only environment path. `all` also performs actual inference and isolated memory writes. Desktop model selection/resume, rendered dashboard controls, Codex/Claude hook execution, automatic memory, Paperclip login/agent work, fallback-attempt traces, disposable backup restoration and fresh-PC provisioning are mapped gaps. `report` always shows these; an `all` pass covers implemented commands only.
