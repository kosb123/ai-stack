# OmniRoute combo responses

Hermes and Hindsight request different combos while OmniRoute selects an eligible provider and model.

## Sub-features

- `combo-schema` returns strict schema output with the unique nonce.
- `combo-forced-tool` returns a real `search_observations` function call with the requested nonce.
- `combo-route` distinguishes configured primary, served model and fallback.

## How to get to it (user POV)

Select `hermes-fast-agent` in Hermes, or use the configured `hindsight-structured` and `hindsight-refresh` endpoints through Hindsight.

## Driving it with verify_stack.py

Preconditions: authenticated OmniRoute is running on 20128 and all three combos exist.

Run `python $verifier combos --run $run`. Inspect each combo's strict JSON response, forced tool-call arguments and selected response headers. Require an HTTP success and correct content in each case. Read current runtime membership rather than assuming that the configured preferred model is currently eligible.

## Gotchas

An HTTP 200 or a short pong does not prove tool compatibility. A missing served-model header is unknown upstream identity. A failed primary followed by a successful fallback must not be called a primary pass. These short probes do not establish latency optimality or large-context output reliability.
