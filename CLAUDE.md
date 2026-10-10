# Project instructions

- Code comments and docstrings are written in **English**, regardless of what they explain (technical or business context). See the `english-comments` skill (`.claude/skills/english-comments/SKILL.md`).
- Linting and formatting use **Ruff**, configured in `pyproject.toml`. New or changed Python code must pass `ruff check` and `ruff format`. Subagents do **not** run Ruff: the coordinator runs it once at the end of a feature (after all its iterations) and commits the result separately (`style: apply ruff format and fixes`).

## Local environment

- There is no system Python. A `uv`-managed virtualenv lives in `.venv/` (Python 3.11, same as the Dockerfile) with `requirements-dev.txt` installed.
- Tests: if Docker is running (`docker info`), run them in Docker against MySQL **only** — do not also run the SQLite suite. Fall back to `.venv/Scripts/python.exe manage.py test comics --settings=comic.settings_test` (SQLite in memory) only when Docker is not running. The MySQL run stays authoritative for migrations and DB-specific behavior.
- Docker from Git Bash: mounting the working tree needs `MSYS_NO_PATHCONV=1` and a Windows path, e.g. `MSYS_NO_PATHCONV=1 docker compose run --rm -v "$(pwd -W):/code" web sh -c 'MYSQL_USER=root MYSQL_PASSWORD="$MYSQL_ROOT_PASSWORD" python manage.py test comics --noinput'`. Plain `-v "$(pwd):/code"` silently mounts nothing, so the command runs the stale code baked into the image. The same applies to container paths in other commands (e.g. `docker compose exec db rm /tmp/x`).
- New dependencies go pinned into `requirements.txt` (runtime) or `requirements-dev.txt` (dev/test), then `uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt`.

## Backend feature workflow

For non-trivial backend features or fixes, orchestrate the project subagents instead of implementing directly:

1. Delegate the implementation to a **new** `backend-senior` per task with a clear spec. Do not reuse an agent across different tasks: its context keeps growing and every call re-sends it.
2. Send its result to a **new** `code-reviewer` per task for review of the current diff.
3. If the verdict is `CHANGES REQUESTED` (any BLOCKER or MAJOR), pass the findings back to the same `backend-senior` of that task (continue it with SendMessage) and review again with the same reviewer.
4. Stop after `APPROVED` or after **3 review rounds**; if still not approved, summarize the disagreement and ask the user. MINOR findings never open an extra round: carry them into the next task's spec or a backlog.
5. Report to the user (in Spanish): what was built, review rounds, remaining MINOR findings, and test results. Do not commit until the user asks.

Keep verification proportionate:
- Tests: MySQL suite only (see Local environment).
- Migrations: agents only generate them and run the test suite. They do **not** rehearse them on scratch SQLite files or throwaway MySQL databases; the coordinator rehearses on `comic_refactor` before applying to the real DB.
- No mutation checks or performance measurements unless the user asks or a concrete risk calls for it.
- Ask agents for short reports: what changed, decisions, test result, and what needs the user's decision.

## Commits

- Only commit when the user explicitly asks for it.
- Follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): `<type>(<optional scope>): <description>`.
  - Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci`, `chore`, `revert`.
  - Commit messages are always in **English** (subject and body), even though conversation with the user is in Spanish.
  - Never add `Co-Authored-By: Claude` or any other AI attribution trailer/line to commits or PR descriptions.
  - Description imperative mood, lowercase, no trailing period (e.g. `feat(collections): add most recent filter`).
  - Optional body explaining *why*, separated by a blank line.
  - Breaking changes: `!` after the type/scope and/or a `BREAKING CHANGE:` footer.
- One logical change per commit; split unrelated changes into separate commits.
