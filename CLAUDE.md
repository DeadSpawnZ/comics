# Project instructions

- Code comments and docstrings are written in **English**, regardless of what they explain (technical or business context). See the `english-comments` skill (`.claude/skills/english-comments/SKILL.md`).
- Linting and formatting use **Ruff**, configured in `pyproject.toml`. New or changed Python code must pass `ruff check` and `ruff format`.

## Local environment

- There is no system Python. A `uv`-managed virtualenv lives in `.venv/` (Python 3.11, same as the Dockerfile) with `requirements-dev.txt` installed.
- Tests: if Docker is running (`docker info`), run them in Docker against MySQL; otherwise fall back to `.venv/Scripts/python.exe manage.py test comics --settings=comic.settings_test` (SQLite in memory). The MySQL run stays authoritative for migrations and DB-specific behavior.
- New dependencies go pinned into `requirements.txt` (runtime) or `requirements-dev.txt` (dev/test), then `uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt`.

## Backend feature workflow

For non-trivial backend features or fixes, orchestrate the project subagents instead of implementing directly:

1. Delegate the implementation to `backend-senior` with a clear spec of the feature.
2. Send its result to `code-reviewer` for review of the current diff.
3. If the verdict is `CHANGES REQUESTED`, pass the findings back to the same `backend-senior` (continue it with SendMessage so it keeps its context) and review again.
4. Stop after `APPROVED` or after **3 review rounds**; if still not approved, summarize the disagreement and ask the user.
5. Report to the user (in Spanish): what was built, review rounds, remaining MINOR findings, and test results. Do not commit until the user asks.

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
