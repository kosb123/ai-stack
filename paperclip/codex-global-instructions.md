# Shared workspace rules (Paperclip container)

/workspace is a bind mount of the Windows host directory E:\projects and is
shared concurrently by all agents. Coordinate through git commits, and
re-check the working tree before committing.

## Running tests

- Run tests directly. Include the exact command and its real output in your
  report. Never report "should pass" without having run it.
- Python projects use the Linux virtual environment at
  `/workspace/.venvs/<project-name>/`.
  The `.venv/` directory inside a repository is a Windows venv (Scripts/)
  and cannot execute on Linux — do not use it inside the container.
  - Example: `cd /workspace/policy-radar && /workspace/.venvs/policy-radar/bin/python -m unittest tests.test_youth_seoul -v`
- If the venv is missing or dependencies changed, provision it:
  `bash /workspace/.infra/provision-workspace-venv.sh <project-name> [project-dir]`
  If the container image lacks python3-venv (freshly recreated container),
  ask for or run once as root:
  `docker exec -u root docker-server-1 sh -c 'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv'`
  then retry the script.
- Node projects use the system node. Run `npm install` in the project
  directory if node_modules is missing, then run the project's test script.

## Git

- Work on the assigned branch. Do not commit to main, merge, push, or deploy
  unless the issue explicitly says so.
- Back up a config file as `<file>.bak-before-<change>-<YYYYMMDD>` before
  changing one that git does not track.
