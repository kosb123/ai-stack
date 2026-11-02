# Hermes cloud tool turn

A user asks Hermes to read a local file and answer using its contents through OmniRoute.

## Sub-features

- `cloud-file-read` emits an actual `read_file` call and corresponding result.
- `cloud-answer` returns the exact random value present only in the file.
- `cloud-route` uses the explicit provider in an isolated home with fallback disabled.

## How to get to it (user POV)

Ask Hermes CLI to read a file using the OmniRoute model. The analogous Desktop chat entry point is separate UI coverage.

## Driving it with verify_stack.py

Preconditions: Doctor passes, a writable scratch home exists and the selected OmniRoute provider is configured.

Run `python $verifier hermes-tool --run $run`. Inspect saved stream-json call, result and terminal result alongside the process exit. Check the request-specific isolated log or route metadata. The prompt supplies a filename but never the file's secret value.

To test a direct candidate, run `python $verifier hermes-tool --run $run --model kiro/auto`. This changes only the model in the isolated cloud profile and records the requested model in route metadata. The check exercises the actual Hermes CLI file tool call and answer; the requested model alone does not establish the downstream upstream model.

## Gotchas

A model naming a tool is not a tool invocation. Global agent or server log activity can belong to the user and cannot prove this run. The isolated minimal configuration does not cover every production skill, hook or plugin.
