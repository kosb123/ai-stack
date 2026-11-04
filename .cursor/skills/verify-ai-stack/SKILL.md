---
name: verify-ai-stack
description: Check jun's installed ai-stack environment, canonical autotune tasks, Paperclip and FreeLLMAPI metadata, Hermes tools, OmniRoute routing and Hindsight memory. Use audit for read-only environment checks and explicit journeys for live inference or isolated memory writes.
---

# Verify the installed ai-stack

Read [features/README.md](features/README.md), then the relevant feature recipe. Run from `C:/Users/jun/Documents/GitHub/ai-stack`. This skill controls the installed Windows environment; the repository contains configuration snapshots and adapters, not a fresh-machine installer.

## Launch

Use host Python with the repository's `requirements-dev.txt` dependencies already installed. The active Hermes runtime must already exist at `%LOCALAPPDATA%/Hermes`, the native Hindsight runtime at `E:/hindsight`, and OmniRoute at 20128. The manual dashboard at 20140 is optional for core preflight and required for its own HTTP/UI journey. Model files, credentials, databases and service provisioning are deliberately absent from Git. Do not copy example credentials into live configuration. Autotune code and CONFIG under `omniroute/autotune/` are the actual scheduled runtime source.

```powershell
Set-Location C:\Users\jun\Documents\GitHub\ai-stack
$ctl = '.cursor\skills\verify-ai-stack\scripts\control_stack.py'
$run = (python $ctl launch | ConvertFrom-Json).run_id
python $ctl doctor --run $run
```

## Read-only environment review

```powershell
python $ctl audit --run $run
python $ctl cleanup --run $run
python $ctl report --run $run
```

`audit` runs Doctor, settings, operations, canonical autotune task checks, optional-service health and the dashboard HTTP check. It does not run inference, write memories, tune combos, start services or create backups. Paperclip checks cover its HTTP health and loopback binding, not browser login or agent work. FreeLLMAPI uses `/livez`; `/health` can return the SPA HTML with status 200 and is not its health contract.

Launch creates an empty, owned verification workspace at `C:/vh/<run UUID>/` and a new evidence directory. It attaches to the existing services; it never starts a second production stack, wakes the model with `/slots`, restarts a service, edits configuration, runs the tuner, or creates a backup. Doctor establishes whether the identities and authentication are worth driving. The selected feature establishes usability. A launch/doctor-only run is not a feature pass.

For CLI journeys the reused harness creates fresh, owned Hermes profiles and initializes their session databases through the real CLI. Memory journeys create a UUID-scoped bank and seed unpredictable linked facts. They preserve production profiles, conversations and `jun`. Missing credentials, services or seed preconditions fail or mark the journey inconclusive; do not invent substitute credentials or launch an extra model to pass.

This launch does **not** prove migrations, model downloads or seed restoration on a blank PC. Those remain unverified until a separately authorized fresh-machine exercise exists. The execution below is the actual installed stack, not a container stand-in.

## Doctor

```powershell
python $ctl doctor --run $run
```

Doctor reads port owners and process creation times using psutil from Hermes's installed environment. It requires known executable/script identities on 18434, 20128 and 8888, exactly one model process, and no 18435 listener before authenticated probes. A present 20140 listener must have the expected dashboard identity. An absent dashboard is recorded as an optional service prerequisite, and the dashboard journey is verified-unreachable with an INCONCLUSIVE verdict. Doctor checks local model properties, authenticated OmniRoute models, Hindsight health and any running dashboard overview. Ownership failures stop authenticated probes. Evidence contains no full command lines or environment dumps.

`tested-builds.json` pins the inspected source files by SHA-256. Before each network/inference journey the controller requires a passing Doctor and compares current process IDs, creation times and executable/script identities with that Doctor. Changed owners require a new Doctor; a prior run's pass is not reused.

If an installation moves or a binary updates, inspect the new identity and update this skill intentionally. Do not weaken the check to process names alone. A healthy sleeping model can still fail its wake; `local-tool` is the real usability check.

## Drive

Run shared services sequentially. The controller locks its runs globally and per run; it cannot lock a human's Desktop or independently launched legacy verifier. Do not run both harnesses concurrently. Local tool journeys refuse when model idle cannot be established. Prefer dashboard, settings or operations while user inference is active.

```powershell
python $ctl settings --run $run
python $ctl operations --run $run
python $ctl autotune --run $run
python $ctl services --run $run
python $ctl dashboard --run $run
python $ctl local-tool --run $run
python $ctl blog-direct --run $run
python $ctl hermes-tool --run $run
python $ctl combos --run $run
python $ctl memory --run $run
python $ctl plugin-memory --run $run
python $ctl report --run $run
```

`python $ctl all --run $run` runs Doctor and the eleven implemented journeys sequentially, stopping further drives if a CLI journey fails or is inconclusive. It consumes model quota and writes only run-owned memory data and CLI state. Use it only when the shared instance is available. It does not run cleanup automatically. Always run Cleanup afterwards, including after a failed attempt. Exit codes are 0 VERIFIED, 1 NOT VERIFIED and 2 INCONCLUSIVE. `report` includes unexecuted/manual entry points and deliberately cannot declare the entire environment verified while those gaps exist.

After a failed or inconclusive drive, run Doctor before continuing. The controller also requires a fresh Doctor after recorded failures. Before retrying a recorded command it copies previous top-level artifacts into `attempts/<command>-<UUID>/`. If a CLI attempt fails, clean its owned resources before further CLI work; use a fresh Launch run after whole-run Cleanup. Preserve all run IDs in the coordinator's scratch report.

The inherited `settings` audit compares effective Hermes profiles, Hindsight process environment, PostgreSQL durability and OmniRoute/tuner boundaries without running tuning. `operations` reads task status, deployed text identity, patch application and recent backup archive readability. Newline/BOM differences are normalized; semantic differences still fail. `autotune` checks the three enabled single scheduler actions, canonical working directory, the compatibility junction and per-role exclusions without running the tuner. `services` checks existing Paperclip and FreeLLMAPI listeners and health metadata. No command here injects failures or proves restore. The two real memory journeys retain separate guarantees of API persistence and actual Hermes explicit memory tools.

## Evidence

Evidence remains in `evidence/ai-stack/runs/<run UUID>/`. `manifest.json` indexes results and owned state; `coverage.json` lists missing journeys and manual gaps. Raw journey artifacts include requests/responses, CLI JSONL, scoped logs, non-secret settings and HTTP contracts. Dashboard response artifacts may include private usage/account labels: keep evidence local and ignored by Git.

Generated-skill proof on 2026-11-02 used the actual installed environment: run `714ef33f-ed28-4e63-b895-393cb60c7499` completed Launch, Doctor, the mapped dashboard HTTP journey and Cleanup. Its manifest and dashboard artifacts survived cleanup, and owned scratch was absent. Earlier run `b2f2f185-b3cf-4a7c-a2f8-a0e5ae483049` also passed settings and operations. The initial process-path diagnosis failure remains in run `bee29df0-ea57-4269-b013-c938080bf470`, whose cleanup passed. These records are historical, not substitutes for a new run; other journeys and manual paths were not rerun as part of generating this skill.

- Require a matching real tool call/result and a final answer containing an unknown file nonce. A printed function name or health endpoint is insufficient.
- Require both provider and model response headers to match the persisted combo primary. A bare model-name match cannot identify a provider. Missing or different identity is INCONCLUSIVE when capability succeeds. This is served-primary identity proof, not a complete fallback-attempt trace.
- Require asynchronous memory completion, a tagged positive-unit document linked to a fact, and recall of its unpredictable value. Query echo is not retrieval. Never use `jun` for test writes.
- Check side effects: isolated files/session rows, retained document and linked fact, and profile release acknowledgement. Preserve failures.
- `blog-direct` performs real inference and writes evidence; it does not run photo analysis, search, article generation or publication. `operations` does not start tasks/backups or test restoration. There is no implicit dry-run mode.
- Keep API, CLI, configuration, archive and UI proof separate. Desktop model switching, dashboard rendering, Codex/Claude hook runtime, automatic memory behavior, Paperclip login/adapter work, backup restore and fresh provisioning remain explicit gaps.
- Never emit API keys, raw `.env`, copied credentials, full process environment or OAuth values. The controller scrubs known secrets and credential patterns; review evidence before sharing it.

## Cleanup

```powershell
python $ctl cleanup --run $run
python $ctl report --run $run
Test-Path "evidence\ai-stack\runs\$run\manifest.json"
```

Cleanup reuses `skills/verify-hermes-stack/scripts/verify_stack.py` ownership validation. It parks/unserves only manifest-owned profiles through the official control socket and requires exact acknowledgement before removing scratch. It validates PID and creation time for any owned standalone process. It never kills by process name and preserves host gateways, shared services, production profiles, user banks and proof artifacts. Generated test banks remain deliberately; bank deletion requires separate authorization.

If release is pending, cleanup fails and leaves scratch intact. Inspect the preserved reply and retry after release; do not force-delete an active profile. Confirm the evidence and manifest still exist after every cleanup.

Windows can briefly retain a file handle after a confirmed profile release. A PermissionError records `cleanup-retry.json` and retries the same ownership-validated cleanup once after one second. Further failure preserves the run for inspection. Cleanup also checks that every pre-existing evidence file, including prior attempts, retains its hash.

## Helpers

- `scripts/control_stack.py`: every command above. It reuses the repository's maintained `skills/verify-hermes-stack/scripts/verify_stack.py` and `audit_settings.py`; there is no copied implementation to drift.
- `scripts/host_probe.py`: read-only identity inventory. The controller invokes it with the interpreter selected by Hermes `installs/*/facts.json`; do not install psutil into another environment to mask a failure.
- Host probe standalone: `& 'C:/Users/jun/AppData/Local/Hermes/installs/883a2c8039b4314b/environments/1c83b35d9ae94ffa85ed0d74cfebc3fe/venv/Scripts/python.exe' '.cursor/skills/verify-ai-stack/scripts/host_probe.py'`. Prefer Doctor after Hermes changes, because it resolves the current environment.
- Controller unit checks: `python -m unittest discover -s tests -p test_ai_stack_control.py -v`.
- Maintained helper unit checks: `python -m unittest discover -s .cursor/skills/verify-ai-stack/scripts -p test_environment.py -v`.

Use `/maintain-verification-skill` when endpoints, hooks, services or UI entry points change. Update this feature map and rerun the affected real journey.

Official contracts: [Hermes local models](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/local-models.md), [Hermes profiles](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md), [Hindsight Hermes integration](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs-integrations/hermes.md), [OmniRoute](https://github.com/cheaper-inference/OmniRoute/blob/main/README.md). Installed source defines the exact supported CLI/API when upstream main differs.
