---
name: verify-hermes-stack
description: Verify jun's installed Hermes CLI, OmniRoute combos and Hindsight API with isolated state, real tool calls, linked memory facts and preserved evidence. Use after changing their settings or diagnosing integration failures.
---

# Verify the installed Hermes stack

Run commands from the repository root. Resolve these relative paths from that directory.

Use `skills/verify-hermes-stack/scripts/verify_stack.py`. Read [features/README.md](features/README.md) before choosing a journey. This skill drives the installed Windows services. It does not provision a fresh PC or prove the Desktop UI through CLI results.

## Launch

The installed dependencies are Python with PyYAML, `C:/Users/jun/AppData/Local/Hermes/bin/hermes.exe`, the user's configured provider credentials, and existing services on ports 18434, 20128 and 8888. Reuse those instances sequentially. Do not launch a second production stack or restart a service as a verification step.

From PowerShell, create an empty evidence directory and working directory. Scratch work uses `C:/vh/<run UUID>/`. CLI journeys create owned `verify-...` profiles under the installed Hermes `profiles/` directory and record their exact paths in the manifest. Existing profiles and their credentials are not cloned.

CLI journeys disable automatic installs through `HERMES_DISABLE_LAZY_INSTALLS=1` and `security.allow_lazy_installs: false` in the disposable profile. They use the active dependency environment selected by `facts.json`. The runtime preflight imports Hermes's own `hermes_yaml` module. The helper's separate host Python still needs PyYAML. A pending source update and stale PM input hash are reported separately from the feature result. This verification mode does not prove an ordinary startup can complete that update. Missing required imports or refused dependencies fail the feature without installing anything.

The plugin journey places a directory junction in its own profile pointing to the already installed Hindsight plugin code. Configuration, sessions and its bank remain isolated. The code directory is reused rather than copied or updated. Before any memory write, the actual provider must resolve exactly one generated bank and no additional banks.

```powershell
$verifier = 'skills\verify-hermes-stack\scripts\verify_stack.py'
$run = (python $verifier launch | ConvertFrom-Json).run_id
python $verifier doctor --run $run
```

Launch prepares scratch state only. Doctor establishes connectivity. A live feature establishes usability. If a prerequisite fails, record it and repair the specific cause before claiming that feature works. The configuration deliberately reuses the selected installed provider definitions in an isolated home, so it does not test unrelated production customizations.

## Doctor

Run `python $verifier doctor --run $run`. Doctor reads health, authenticated model metadata and installed prerequisites. It does not perform inference. A sleeping llama-server may answer health while failing its next wake. Do not use `/slots` as a harmless polling endpoint on this build, since it can wake the server.

Run the separate settings audit to compare the four Hermes configurations, the native Hindsight listener's effective environment against `E:/hindsight/hindsight.env`, and the current runtime combos against their automation policy. The audit uses psutil from Hermes's active installed environment without installing dependencies, verifies the listener runs `E:/hindsight/run_api.py`, and records only allowlisted non-secret settings.

```powershell
python 'skills\verify-hermes-stack\scripts\audit_settings.py'
```

Its non-secret snapshot and per-boundary checks are saved to `evidence/settings-audit.json`. In particular, compression uses the lower of the ratio threshold and the absolute token cap.

Check current combos and their configured primaries separately from the model that actually answered. The user's preferred Argon model may be temporarily suspended while its quota is exhausted. Its desired rank remains in `omni-autotune/autotune.py`; the runtime membership is the effective state.

## Drive

Run one feature at a time. Each command saves its checks and evidence under the same UUID run.

```powershell
python $verifier combos --run $run
python $verifier hermes-tool --run $run
python $verifier local-tool --run $run
python $verifier memory --run $run
python $verifier plugin-memory --run $run
```

`python $verifier all --run $run` runs the six implemented journeys and retains individual failures instead of stopping at the first one. Inspect the result scope. `memory` proves the Hindsight API path. `plugin-memory` separately proves an actual Hermes retain tool call, a tagged positive-unit document with a linked fact, and a different Hermes session recalling the unknown value through the recall tool. It also compares the installation facts, pending marker and install stamp before and after execution.

The helper's exit code is 0 for VERIFIED, 1 for NOT VERIFIED and 2 for INCONCLUSIVE. A skipped or unreachable entry point is not covered by another feature's pass.

## Evidence

Evidence lives in `evidence/runs/<run UUID>/`. `manifest.json` indexes the run, its checks and owned scratch state. Read the actual request and response artifacts or stream-json events before accepting a verdict.

- Require an actual tool call, matching result and exact value read from a file that the prompt did not disclose.
- Require strict structured output and an actual forced function call from each combo. Text spelling out a function call is a failure.
- Record requested combo, current primary and actual response model separately. A successful fallback is not a healthy primary.
- Require an async retain operation to complete, a positive-unit document with the expected tags, and a recall result linked to that document containing both the random identifier and value. Query echo is not a retrieved fact.
- Keep CLI, API and Desktop proof separate. The Desktop model-switch and resume path requires its own UI exercise and remains unverified through this helper.
- Never store API keys, copied credential configuration or a full environment dump in evidence. Scratch provider settings can contain credentials and are removed by cleanup.

## Cleanup

After every attempt, including a failed journey, run the following command.

```powershell
python $verifier cleanup --run $run
```

Cleanup parks and unserves only manifest-owned profiles through the official gateway control socket, requires the acknowledgement to name each correct profile, and preserves the host gateway. A profile that was never served or was already released is accepted only when the exact not-served response names it and a fresh gateway status confirms it is absent. It stops an owned standalone gateway only after checking its PID and creation time. It then removes the validated temporary profiles and `C:/vh/<run UUID>/` scratch directory. A junction is removed with its owning profile, while its installed target remains intact.

Evidence, the manifest and the generated `verify-hermes-<UUID>` bank remain. Cleanup never deletes bank documents or touches `jun`. Missing ownership or a missing profile-release acknowledgement fails cleanup and preserves the remaining scratch state. Test bank deletion is a separate explicitly authorized action. Confirm the manifest and evidence remain after cleanup. A legacy failed attempt under `evidence/runs/<UUID>/scratch` must be checked and cleaned as its own owned scratch path rather than treated as proof of a successful new cleanup.

## Helpers

The executable helpers are `scripts/verify_stack.py` and `scripts/audit_settings.py`. Their commands are shown above. Run the assertion tests with:

```powershell
python -m unittest discover -s 'skills\verify-hermes-stack\scripts' -p 'test_verifier.py' -v
```

Use `/maintain-verification-skill` when endpoints, CLI flags, routing policy or feature entry points change. Update the feature map and rerun the affected real journey.

Official references are [Hermes local models](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/local-models.md), [Hermes CLI commands](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md), [profile-scoped gateway lifecycle](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md), [no-install startup policy](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/venv_sync.py), [Hindsight Hermes integration](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs-integrations/hermes.md) and [OmniRoute routing](https://github.com/cheaper-inference/OmniRoute/blob/v3.8.51/docs/routing/AUTO-COMBO.md). Installed source and the captured Hindsight OpenAPI define the exact tested contract where main-branch documentation differs.
