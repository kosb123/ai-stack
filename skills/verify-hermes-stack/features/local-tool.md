# Hermes local tool turn

A user runs the same file-reading task against the self-hosted Qwen model.

## Sub-features

- `local-wake` obtains an actual inference response, including waking the model if asleep.
- `local-file-read` performs the file tool call and returns the unknown file value.
- `local-route` uses the explicit 18434 endpoint with cloud fallback disabled.

## How to get to it (user POV)

Choose the Local model in Hermes or invoke its local alias in the CLI.

## Driving it with verify_stack.py

Preconditions: the installed local model server is available and another user request is not using it. Do not drive it concurrently. The helper checks `/metrics` for both `llamacpp:requests_processing` and `llamacpp:requests_deferred` equal to zero, saving the counters as evidence. Missing or busy counters leave the journey INCONCLUSIVE. It does not poll `/slots`, which can wake a sleeping model.

Run `python $verifier local-tool --run $run`. Inspect the actual CLI events, final answer, exit code and isolated route evidence. A successful health response while asleep is only a prerequisite.

## Gotchas

This one short task cannot prove long-context stability or repeated sleep/wake reliability. Do not report the earlier memory-allocation crash as fixed by a smoke test. No server restart or context adjustment is part of this journey.
