# Shared memory

Hermes stores and recalls durable facts through Hindsight. The settings journey checks for Hindsight references in Codex and Claude hook configuration; active hook execution needs separate evidence.

## Sub-features

- `api-memory`: asynchronous retain completes and a linked fact can be recalled through the API.
- `hermes-memory`: one real Hermes session retains a fact and another session recalls the unpredictable value.
- `memory-isolation`: every test write targets its generated bank, never `jun` or an additional bank.
- `coding-agent-hooks`: Codex/Claude hook configuration is checked; new-session runtime is a mapped gap.

## How to get to it (user POV)

- Ask Hermes to remember something, then ask a different Hermes session to recall it.
- Codex and Claude use their installed Hindsight plugin hooks around ordinary sessions.
- The direct Hindsight API is at `http://127.0.0.1:8888/v1/default/banks/`.

## Driving it with control_stack.py

Preconditions:

- Doctor passes for the native Hindsight owner. The installed Hermes Hindsight plugin and configured cloud routes must be available.

- **API path.** Run `python $ctl memory --run $run`. Require the retain operation to finish, a positive-unit document with generated tags and a linked fact, and recall that references that document and its unknown value.
- **Plugin path.** Run `python $ctl plugin-memory --run $run`. The isolated profile uses explicit memory tools, not the default automatic memory mode. Require actual retain/recall tool events in different Hermes session IDs, one generated bank only, persisted linked data, and unchanged installation fingerprints. Read the two session JSONL artifacts and linked document evidence.
- **Hook configuration.** Run `python $ctl settings --run $run`. This reads known hook configuration files and effective memory routing; it does not execute hooks.
- **Codex/Claude runtime.** This controller does not launch coding agents or mutate their hooks. A separately owned session needs a unique marker, the actual hook log, a persisted linked document and a later real recall. Keep the entry unverified until all evidence is available.

## Gotchas

- Submission acknowledgement is not persistence, and a query echoed by a model is not retrieval.
- API success never substitutes for actual Hermes plugin success. Explicit memory tool success does not prove automatic retain/recall in normal chats or Codex/Claude hook execution.
- The plugin reuses installed code via an owned profile junction; it never updates or installs dependencies during verification.
- Cleanup preserves the generated bank. Do not remove test documents from `jun` or delete unrelated banks.
- Plugin persistence polling is bounded at 180 seconds. An acknowledged retain without linked retrieval in that window is INCONCLUSIVE, not proof that the memory can never persist. Later API retrieval does not replace the unexecuted second Hermes session.
