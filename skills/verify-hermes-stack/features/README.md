# Stack verification map

This map tracks the actual user-facing paths through Hermes, OmniRoute and Hindsight. Run the skill's Launch and Doctor first. Use a new UUID run and sequentially drive shared service instances. Credentials stay in owned scratch state, and memory tests use a generated bank rather than `jun`.

| Feature | Command | Observable proof |
|---|---|---|
| [OmniRoute combos](combos.md) | `combos` | Strict JSON and forced tool calls, requested versus served route. |
| [Hermes cloud tool turn](hermes-tool.md) | `hermes-tool` | Actual file tool event, result and exact unknown file value. |
| [Hermes local tool turn](local-tool.md) | `local-tool` | Same tool proof through the explicit local endpoint. |
| [Hindsight memory](memory.md) | `memory`, `plugin-memory` | Completed API retain, plus actual Hermes plugin storage and recall in a different session. |
| [Session routing](session-routing.md) | Separate UI journey | Model switch followed by resume reaches the selected provider. |

The fifth feature is tracked as a coverage gap until its real Desktop entry point is driven. API health does not cover it. Every entry names what was not exercised; INCONCLUSIVE and NOT VERIFIED are not passes. Cleanup preserves proof files.
