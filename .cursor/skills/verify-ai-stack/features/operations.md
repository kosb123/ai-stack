# Operational state and configuration

The user needs to know which installed services are running, whether their connections agree, whether watchdog/tuner tasks are scheduled, and whether a recent backup archive is readable.

## Sub-features

- `identities`: exact installed process owners and authentication on the four service ports.
- `effective-config`: Hermes profiles, native Hindsight environment, PostgreSQL durability and routing policy agree.
- `guards`: deployed startup/watchdog/patch files match Git and the reviewed route patch remains applied.
- `scheduled-tasks`: expected tasks exist, are enabled and their last result is successful or running.
- `backup-archive`: a nonempty recent archive can be listed by pg_restore.
- `backup-restore`: actual restore and row/memory invariants remain a separate unimplemented journey.

## How to get to it (user POV)

- Run `hermes gateway status`, inspect the service dashboards and use the configured local server.
- Open Windows Task Scheduler for `Local LlamaCpp Watchdog (Qwen3.8 27B)`, OmniAutotune tasks and `Hindsight Daily Backup`.
- Inspect Hindsight's existing backup directory at `C:/Users/jun/.hindsight/backups`.

## Driving it with control_stack.py

Preconditions:

- The installed service files and active Hermes interpreter already exist. Do not install a new stack as scaffolding.

- **Instances.** Run `python $ctl doctor --run $run`. Require known script/executable identities, one Qwen process, no 18435 listener, authenticated service metadata and readable dashboard overview. Read `host-identity.json`.
- **Configuration.** Run `python $ctl settings --run $run`. Require the real effective settings and database durability checks to pass; read `settings-audit.json`. Never copy example YAML over live settings to pass.
- **Automation/backup.** Run `python $ctl operations --run $run`. Require expected task states/results, exact deployed file equality, a reverse-checkable applied patch, a backup newer than 48 hours and a successful `pg_restore --list` containing table data. Read task and backup metadata artifacts.
- **Restore.** No restore is performed. Listing an archive does not prove it restores. Keep the restore entry unverified until a separately owned database is restored and actual row/document/fact invariants are compared.

## Gotchas

- These checks do not trigger scheduled tasks, inject crashes, reapply patches, restart services or create/delete backups.
- A successful previous task result is historical status, not a new execution test.
- If a binary/path changes, investigate and update the identity contract instead of changing it to a process-name check.
- This repository is a non-secret snapshot, so empty-PC service creation, downloads and DB migrations remain outside the proof.
