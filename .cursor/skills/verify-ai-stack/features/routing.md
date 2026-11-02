# Cloud routing and tool work

The user chooses OmniRoute's Hermes combo and expects real tools to work while routing respects current primary, fallback and quota policy.

## Sub-features

- `combo-structure`: all three production combos return strict structured data and real forced tool calls.
- `served-route`: requested combo, persisted primary and response headers agree with no fallback.
- `cloud-tool`: actual Hermes cloud tool invocation returns an unknown file value.
- `quota-policy`: effective runtime membership agrees with suspended preferred-model status; configuration proof only.

## How to get to it (user POV)

- Select `omni` in Hermes, or issue a real chat request to `http://127.0.0.1:20128/v1/chat/completions` naming `hermes-fast-agent`.
- Hindsight uses `hindsight-structured` and `hindsight-refresh` through the same gateway.
- The tuner manages the candidate list; do not run tuning as a verification action.

## Driving it with control_stack.py

Preconditions:

- Doctor establishes the known OmniRoute owner and authenticated models metadata. Provider credentials and quota must exist.

- **Provider policy.** Run `python $ctl settings --run $run`. Inspect `settings-audit.json` for effective combos, desired pins and suspension status.
- **Three combos.** Run `python $ctl combos --run $run`. Require strict JSON and an actual `search_observations` tool call for each combo, with arguments containing its generated nonce. Require the served model to equal the current persisted primary and `fallback` to be false.
- **User tool work.** Run `python $ctl hermes-tool --run $run`. Require matching real file tool/result/final nonce and invocation-scoped endpoint evidence.

## Gotchas

- Quota exhaustion can legitimately suspend a preferred model. Do not restore it just to pass verification.
- A successful fallback is INCONCLUSIVE for primary health and never silently counts as the primary.
- Tests call live providers and may consume quota. They do not create providers, switch OAuth accounts or change combo order.
- Configuration reads do not prove the scheduled tuner executed or that recovery after quota exhaustion works.
