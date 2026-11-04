# Local execution and the blog connection

The mapped default path selects local Qwen in Hermes or uses the blog client's default authenticated llama.cpp server at 18434. The full blog writer also supports `BLOG_LLM_URL`; this journey does not validate every external override.

## Sub-features

- `local-tool`: Hermes actually reads an unknown file value and returns it through the local route.
- `blog-auth`: the deployed blog request client authenticates directly to 18434 and receives a real response.
- `single-endpoint`: no listener remains on 18435 and only one model process exists.
- `desktop-resume`: local model selection survives a new Desktop chat and resume; separate UI gap.

## How to get to it (user POV)

- Run `hermes chat --oneshot -q` with `-m local`.
- Use the Desktop model picker to choose the local Qwen model, send a message, create another chat, and resume it.
- The blog user runs `E:/blog-rank-project/blog-rank/writer/simple_write.py`; its request boundary is exercised separately without photos or article side effects.

## Driving it with control_stack.py

Preconditions:

- Launch and Doctor pass, the model is idle, and the deployed blog helper exists.

- **File tool.** Run `python $ctl local-tool --run $run`. The controller first requires the installed provider URL to be `http://127.0.0.1:18434/v1`. Require a real `read_file` tool event, matching result, one successful final result with the unknown nonce, and that endpoint in the invocation-scoped log. The disposable profile disables fallback.
- **Blog authentication.** Run `python $ctl blog-direct --run $run`. Require deployed helper equality, HTTP 200 with an actual `OK` answer, and refusal on 18435. Read `blog-direct-response.json`.
- **Desktop entry.** No Desktop automation is implemented by this controller. In an explicitly owned UI test chat, select local Qwen, ask it to read a fresh unknown test-file value, create/resume the chat, and capture actions/screenshots plus matched request logs. Leave this entry unverified until those artifacts exist; CLI success does not cover it.

## Gotchas

- Model health can succeed while wake or inference fails. Never use `/slots` to poll this build.
- A busy local model yields INCONCLUSIVE; do not interrupt the user's work to turn it green.
- A correct answer from a cloud fallback is not local-route proof.
- `blog-direct` proves the deployed request/auth boundary only. It does not process photos, run search APIs, write articles or prove search ranking.
- The Desktop test must not reuse or alter the user's running conversation.
