---
name: comments-and-business-docs
description: Language and placement rules for comments, docstrings and business documentation in this repository. Use whenever writing or modifying code in this repo (Python, templates, JS, CSS, migrations, scripts) that adds or changes a comment, docstring or log message, or that adds or changes a business rule or domain concept (editions, issues, publishings, compilations, connectings, collections, ownership, trades).
---

# Comments and business documentation

All comments and documentation are written in **English**. What changes is *where* they live: **how the code works** stays in the code; **why the business works that way** lives in `README.md`, never in code comments.

## Rule 1 — Everything inside the code is in English

Applies to every file you create or touch:

- Comments (`#`, `//`, `/* */`, `{# #}`, `<!-- -->`).
- Docstrings (modules, classes, functions, migrations).
- Log messages (`logger.debug/info/warning/exception`) and developer-facing exception messages (`RuntimeError`, assertion text in migrations).
- Commit messages and TODOs.

Does **not** apply to user-facing text, which stays in Spanish: template/UI copy, form labels, `messages.*` notifications, validation errors shown in forms (`ValidationError` raised to users), JSON `errors` returned to the frontend, admin `description=`/`verbose_name`.

When you edit a function or block that still has a Spanish comment or docstring, translate that comment in the same change (only the ones you touch; do not rewrite unrelated files).

## Rule 2 — Business context goes to README.md, not to code comments

A comment is **business context** if it explains the domain rather than the code: what an entity means, why a rule exists, what the owner decided. Examples:

- "An edition is the physical copy; an issue is the story."
- "You cannot sell something you did not own yet, so previous trades must be on or before the sale date."
- "TPB/HC are formats, not necessarily compilations."
- "A compilation has no issue of its own."

Put it in `README.md`, in English, under `## Domain model and business rules` (create the section if it does not exist):

- `### Glossary` — one entry per domain concept.
- `### Business rules` — one bullet per rule, phrased as the rule itself, mentioning where it is enforced (model/method) so it can be found.

In the code, keep at most a short pointer when the rule is not obvious from the code, e.g. `# Business rule: see README "Business rules" (previous trade date).`

**Technical context stays in the code**, in English: workarounds, library/database limitations, invariants, performance reasons. Examples: MySQL cannot defer unique constraints so rows are deleted and reinserted; `prefetch_related` is used through the relation to avoid N+1; `RenameField` does not update constraint fields.

## Rule 3 — Keep README and code in sync

- Adding or changing a business rule in code → add or update its README bullet in the same change.
- Removing a rule → remove its bullet.
- Renaming a domain concept → update the glossary.

## Checklist before finishing a change

1. Every comment, docstring and log message you added or edited is in English.
2. No explanations of business rules remain in the code you touched; they are in README.md, in English.
3. README "Domain model and business rules" reflects the rules the change introduced or modified.
4. User-facing strings are still in Spanish.
