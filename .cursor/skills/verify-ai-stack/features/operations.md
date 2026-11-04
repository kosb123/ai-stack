# Operational state and configuration

The user needs to know which installed services are running, whether their connections agree, whether watchdog/tuner tasks are scheduled, and whether a recent backup archive is readable.

## Sub-features

- `identities`: exact installed core process owners and authentication; a present manual dashboard also requires its known owner.
- `effective-config`: Hermes profiles, native Hindsight environment, PostgreSQL durability and routing policy agree.
- `guards`: deployed startup/watchdog/patch files match Git and the reviewed route patch remains applied.
- `scheduled-tasks`: expected tasks exist, are enabled and their last result is successful or running.
- `autotune-home`: Check, Tune and Verify use one enabled action each, the canonical ai-stack script and working directory, and shared state through the legacy junction. Current combo membership obeys per-role exclusions.
- `optional-services`: existing FreeLLMAPI `/livez` and Paperclip `/api/health` respond with healthy JSON and ports 3001, 3100 and 5433 bind only to loopback.
- `backup-archive`: a nonempty recent archive can be listed by pg_restore.
- `backup-restore`: actual restore and row/memory invariants remain a separate unimplemented journey.

## How to get to it (user POV)

- Run `hermes gateway status`, inspect the service dashboards and use the configured local server.
- Open Windows Task Scheduler for `Local LlamaCpp Watchdog (Qwen3.8 27B)`, OmniAutotune tasks and `Hindsight Daily Backup`.
- Inspect Hindsight's existing backup directory at `C:/Users/jun/.hindsight/backups`.

## Driving it with control_stack.py

Preconditions:

- The installed service files and active Hermes interpreter already exist. Do not install a new stack as scaffolding.

- **Instances.** Run `python $ctl doctor --run $run`. Require known core script/executable identities, one Qwen process, no 18435 listener and authenticated service metadata. If the manual dashboard exists, require its known identity and readable overview. An absent dashboard does not establish a core failure. Read `host-identity.json`.
- **Configuration.** Run `python $ctl settings --run $run`. Require the real effective settings and database durability checks to pass; read `settings-audit.json`. Never copy example YAML over live settings to pass.
- **Automation/backup.** Run `python $ctl operations --run $run`. Require expected task states/results, deployed text equality after newline/BOM normalization, a reverse-checkable applied patch, a backup newer than 48 hours and a successful `pg_restore --list` containing table data. Semantic differences still fail. Read task and backup metadata artifacts.
- **Canonical tuner.** Run `python $ctl autotune --run $run`. Read `autotune-home.json`. This inspects scheduler actions and role exclusions without running Check, Tune, Verify or any model probe. It does not compare runtime state against an old frozen archive, since scheduled execution legitimately updates that state.
- **Optional services.** Run `python $ctl services --run $run`. Read `optional-services.json`. Missing listeners are verified-unreachable prerequisites, while present unhealthy or exposed listeners fail. FreeLLMAPI `/health` can return frontend HTML with 200, so the real health route is `/livez`. Paperclip health is not proof of user login, provider authentication, agent execution or recoverable data backups.
- **Restore.** No restore is performed. Listing an archive does not prove it restores. Keep the restore entry unverified until a separately owned database is restored and actual row/document/fact invariants are compared.

## Gotchas

- These checks do not trigger scheduled tasks, inject crashes, reapply patches, restart services or create/delete backups.
- A successful previous task result is historical status, not a new execution test.
- If a binary/path changes, investigate and update the identity contract instead of changing it to a process-name check.
- Autotune is a live source under this repository. Other service files are management snapshots. Empty-PC service creation, downloads and DB migrations remain outside the proof.

Official contracts include [Paperclip Docker](https://github.com/paperclipai/paperclip/blob/master/doc/DOCKER.md) and [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi/blob/main/README.md). Installed FreeLLMAPI `server/src/routes/status.ts` and `cli/src/doctor.ts` define the `/livez` response used by this pass.
