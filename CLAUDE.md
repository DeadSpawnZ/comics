# Project instructions

- Code comments and docstrings are written in **English**, regardless of what they explain (technical or business context). See the `english-comments` skill (`.claude/skills/english-comments/SKILL.md`).
- Linting and formatting use **Ruff**, configured in `pyproject.toml`. New or changed Python code must pass `ruff check` and `ruff format`.

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
