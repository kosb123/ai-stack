# Cloud routing and tool work

The user chooses OmniRoute's Hermes combo and expects real tools to work while routing respects current primary, fallback and quota policy.

## Sub-features

- `combo-structure`: Hindsight roles return strict structured data, and all three production combos return real forced tool calls. The additional Hermes JSON response is diagnostic.
- `served-route`: requested combo, persisted primary and both provider/model response headers agree. This does not expose the complete fallback-attempt trace.
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
- **Three combos.** Run `python $ctl combos --run $run`. Require strict JSON for Hindsight roles and an actual `search_observations` tool call with the generated nonce for every combo. The extra Hermes JSON probe is diagnostic and cannot exclude a healthy Hermes tool route. JSON probes use 4096 output tokens; tool probes use 1024 and low reasoning. Hindsight combos use `tool_choice="required"`; the Hermes probe uses the named tool. These exercise capabilities rather than replaying an entire production prompt. Compare both response provider and model with the persisted primary. Quota-sharing groups such as agy/antigravity do not prove exact route identity. Unknown identity remains INCONCLUSIVE.
- **User tool work.** Run `python $ctl hermes-tool --run $run`. Require matching real file tool/result/final nonce and invocation-scoped endpoint evidence.

## Gotchas

- Quota exhaustion can legitimately suspend a preferred model. Do not restore it just to pass verification.
- A successful request served by a different or unidentified provider is INCONCLUSIVE for primary identity. Matching served identity does not establish that no intermediate attempts failed.
- Tests call live providers and may consume quota. They do not create providers, switch OAuth accounts or change combo order.
- Configuration reads do not prove the scheduled tuner executed or that recovery after quota exhaustion works.
