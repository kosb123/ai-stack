---
name: verify-ai-stack
description: Verify jun's installed ai-stack through Hermes CLI tool calls, OmniRoute routing, Hindsight memory, dashboard HTTP contracts and operational checks. Use after integration changes or failures across the whole environment.
---

# Verify the installed ai-stack

Read [features/README.md](features/README.md), then the relevant feature recipe. Run from `C:/Users/jun/Documents/GitHub/ai-stack`. This skill controls the installed Windows environment; the repository contains configuration snapshots and adapters, not a fresh-machine installer.

## Launch

Use host Python with the repository's `requirements-dev.txt` dependencies already installed. The active Hermes runtime must already exist at `%LOCALAPPDATA%/Hermes`, the native Hindsight runtime at `E:/hindsight`, OmniRoute at 20128, and the dashboard at 20140. Model files, credentials, databases and service provisioning are deliberately absent from Git. Do not copy example credentials into live configuration.

```powershell
Set-Location C:\Users\jun\Documents\GitHub\ai-stack
$ctl = '.cursor\skills\verify-ai-stack\scripts\control_stack.py'
$run = (python $ctl launch | ConvertFrom-Json).run_id
python $ctl doctor --run $run
```

Launch creates an empty, owned verification workspace at `C:/vh/<run UUID>/` and a new evidence directory. It attaches to the existing services; it never starts a second production stack, wakes the model with `/slots`, restarts a service, edits configuration, runs the tuner, or creates a backup. Doctor establishes whether the identities and authentication are worth driving. The selected feature establishes usability. A launch/doctor-only run is not a feature pass.

For CLI journeys the reused harness creates fresh, owned Hermes profiles and initializes their session databases through the real CLI. Memory journeys create a UUID-scoped bank and seed unpredictable linked facts. They preserve production profiles, conversations and `jun`. Missing credentials, services or seed preconditions fail or mark the journey inconclusive; do not invent substitute credentials or launch an extra model to pass.

This launch does **not** prove migrations, model downloads or seed restoration on a blank PC. Those remain unverified until a separately authorized fresh-machine exercise exists. The execution below is the actual installed stack, not a container stand-in.

## Doctor

```powershell
python $ctl doctor --run $run
```

Doctor reads port owners and process creation times using psutil from Hermes's installed environment. It requires the known executable/script identities on 18434, 20128, 8888 and 20140, exactly one model process, and no 18435 listener before authenticated probes. It checks local model properties, authenticated OmniRoute models, Hindsight health and dashboard overview. Ownership failures stop authenticated probes. Evidence contains no full command lines or environment dumps.

`tested-builds.json` pins the inspected source files by SHA-256. Before each network/inference journey the controller requires a passing Doctor and compares current process IDs, creation times and executable/script identities with that Doctor. Changed owners require a new Doctor; a prior run's pass is not reused.

If an installation moves or a binary updates, inspect the new identity and update this skill intentionally. Do not weaken the check to process names alone. A healthy sleeping model can still fail its wake; `local-tool` is the real usability check.

## Drive

Run shared services sequentially. The controller locks its runs globally and per run; it cannot lock a human's Desktop or independently launched legacy verifier. Do not run both harnesses concurrently. Local tool journeys refuse when model idle cannot be established. Prefer dashboard, settings or operations while user inference is active.

```powershell
python $ctl settings --run $run
python $ctl operations --run $run
python $ctl dashboard --run $run
python $ctl local-tool --run $run
python $ctl blog-direct --run $run
python $ctl hermes-tool --run $run
python $ctl combos --run $run
python $ctl memory --run $run
python $ctl plugin-memory --run $run
python $ctl report --run $run
```

`python $ctl all --run $run` runs Doctor and the nine implemented journeys sequentially and preserves each failure. Use it only when the shared instance is available. It does not run cleanup automatically: always run Cleanup afterwards, including after a failed attempt. Exit codes are 0 VERIFIED, 1 NOT VERIFIED and 2 INCONCLUSIVE. `report` includes unexecuted/manual entry points and deliberately cannot declare the entire environment verified while those gaps exist.

The inherited `settings` audit compares effective Hermes profiles, Hindsight process environment, PostgreSQL durability and OmniRoute/tuner boundaries without running tuning. `operations` reads task status, deployed script identity, patch application and recent backup archive readability. Neither injects failures or proves restore. The two real memory journeys retain different guarantees: API persistence and actual Hermes plugin storage/recall.

## Evidence

Evidence remains in `evidence/ai-stack/runs/<run UUID>/`. `manifest.json` indexes results and owned state; `coverage.json` lists missing journeys and manual gaps. Raw journey artifacts include requests/responses, CLI JSONL, scoped logs, non-secret settings and HTTP contracts. Dashboard response artifacts may include private usage/account labels: keep evidence local and ignored by Git.

Generated-skill proof on 2026-11-02 used the actual installed environment: run `714ef33f-ed28-4e63-b895-393cb60c7499` completed Launch, Doctor, the mapped dashboard HTTP journey and Cleanup. Its manifest and dashboard artifacts survived cleanup, and owned scratch was absent. Earlier run `b2f2f185-b3cf-4a7c-a2f8-a0e5ae483049` also passed settings and operations. The initial process-path diagnosis failure remains in run `bee29df0-ea57-4269-b013-c938080bf470`, whose cleanup passed. These records are historical, not substitutes for a new run; other journeys and manual paths were not rerun as part of generating this skill.

- Require a matching real tool call/result and a final answer containing an unknown file nonce. A printed function name or health endpoint is insufficient.
- Require served routing headers to match current persisted combo primary with `fallback: false`. Successful fallback is not proof of a healthy primary.
- Require asynchronous memory completion, a tagged positive-unit document linked to a fact, and recall of its unpredictable value. Query echo is not retrieval. Never use `jun` for test writes.
- Check side effects: isolated files/session rows, retained document and linked fact, and profile release acknowledgement. Preserve failures.
- `blog-direct` performs real inference and writes evidence; it does not run photo analysis, search, article generation or publication. `operations` does not start tasks/backups or test restoration. There is no implicit dry-run mode.
- Keep API, CLI, configuration, archive and UI proof separate. Desktop model switching, dashboard rendering, Codex/Claude hook runtime, backup restore and fresh provisioning remain explicit gaps.
- Never emit API keys, raw `.env`, copied credentials, full process environment or OAuth values. The controller scrubs known secrets and credential patterns; review evidence before sharing it.

## Cleanup

```powershell
python $ctl cleanup --run $run
python $ctl report --run $run
Test-Path "evidence\ai-stack\runs\$run\manifest.json"
```

Cleanup reuses `skills/verify-hermes-stack/scripts/verify_stack.py` ownership validation. It parks/unserves only manifest-owned profiles through the official control socket and requires exact acknowledgement before removing scratch. It validates PID and creation time for any owned standalone process. It never kills by process name and preserves host gateways, shared services, production profiles, user banks and proof artifacts. Generated test banks remain deliberately; bank deletion requires separate authorization.

If release is pending, cleanup fails and leaves scratch intact. Inspect the preserved reply and retry after release; do not force-delete an active profile. Confirm the evidence and manifest still exist after every cleanup.

## Helpers

- `scripts/control_stack.py`: every command above. It reuses the repository's maintained `skills/verify-hermes-stack/scripts/verify_stack.py` and `audit_settings.py`; there is no copied implementation to drift.
- `scripts/host_probe.py`: read-only identity inventory. The controller invokes it with the interpreter selected by Hermes `installs/*/facts.json`; do not install psutil into another environment to mask a failure.
- Host probe standalone: `& 'C:/Users/jun/AppData/Local/Hermes/installs/883a2c8039b4314b/environments/1c83b35d9ae94ffa85ed0d74cfebc3fe/venv/Scripts/python.exe' '.cursor/skills/verify-ai-stack/scripts/host_probe.py'`. Prefer Doctor after Hermes changes, because it resolves the current environment.
- Controller unit checks: `python -m unittest discover -s tests -p test_ai_stack_control.py -v`.

Use `/maintain-verification-skill` when endpoints, hooks, services or UI entry points change. Update this feature map and rerun the affected real journey.

Official contracts: [Hermes local models](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/local-models.md), [Hermes profiles](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md), [Hindsight Hermes integration](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs-integrations/hermes.md), [OmniRoute](https://github.com/cheaper-inference/OmniRoute/blob/main/README.md). Installed source defines the exact supported CLI/API when upstream main differs.
