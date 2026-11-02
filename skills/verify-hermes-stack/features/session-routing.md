# Model selection and resumed sessions

When a user switches a conversation to the local model and resumes it, the next request must reach that selected provider.

## Sub-features

- `desktop-model-switch` changes the provider in an existing Desktop conversation.
- `desktop-resume` resumes the same conversation and verifies the actual request route.
- `fallback-turn-scope` avoids persisting a temporary fallback as the conversation's permanent provider.

## How to get to it (user POV)

Open a Desktop conversation, use the model picker, send a message and reopen the same conversation. CLI `--resume` is an additional entry point, not a replacement for the model picker.

## Driving it with verify_stack.py

Preconditions: a dedicated disposable conversation and a supported Desktop control surface are available.

The current helper does not automate this entry point. Record INCONCLUSIVE until the actual UI action and its session-correlated request route are captured. Verify the stored route by that exact session ID in a read-only second view. Do not change user sessions to manufacture a pass.

## Gotchas

An issue number appearing in source comments does not prove that a patch is active. A missing stored endpoint is unknown, not implicitly local. CLI success cannot establish Desktop model-switch behavior.
