# Backlog

Pending work, open decisions and known limits of Comi. It is meant to give a new working session
(human or AI) enough context to pick any item up without the conversation that produced it.

How to work on an item: follow `CLAUDE.md` (backend workflow with `backend-senior` → `code-reviewer`,
MySQL tests only, Ruff once per feature, migrations rehearsed on `comic_refactor` before the real
database). Remove an item from this file in the same commit that resolves it.

Last updated: 2026-10-10, on branch `feat/change-requests`.

## In progress

### Change requests (branch `feat/change-requests`)

Users request corrections or additions to Issues and Editions; Reviewers validate, Administrators
approve. Design agreed with the user:

- **History is agnostic.** Every saved state of an Issue or Edition is a version (who, when, type,
  reason, relations) via `django-simple-history` 3.12.0, whatever its source (Gestión, Django admin,
  scripts or an approved request). Any version can be restored; a restore is itself a version.
  Done in commit `2c68464` (task 1): `comics/history.py`, `comics/versions.py`
  (`apply_changes`, `revert_to_version`), Gestión history pages, migrations `0107`/`0108`.
- **Requests are a separate model** that points to the history version its approval produced.
  Auditing requests means querying the request model; the history never refers to requests.
- **Task 2, done in commit `1e1990d`:** request workflow (pending → validated → approved/rejected,
  withdraw), permissions for Reviewers/Administrators (migration `0110`), review area `/revisiones/`
  for reviewers without Gestión access, conflict detection against the base version, notifications
  to the author, achievement "Reportero" (`0111`). Service: `comics/change_requests.py`.
- **Deployed locally on 2026-10-10:** migrations `0107`–`0111` rehearsed on `comic_refactor` and
  applied to the real DB (backup `backups/comic-20261010-013936.sql`, previous image tagged
  `comi:pre-requests`). Pending: Ruff pass and the PRs `feat/change-requests` → `develop` → `master`.
- **Decisions taken by the implementer, awaiting user confirmation:** nobody reviews their own
  request; reviewers can't reject a request once it is validated; authors don't see reviewer names.
- **v1 scope cuts (widen later if wanted):** no cover image proposals; no editing of a compilation's
  collected issues; issue corrections limited to synopsis and creators (series/number are corrected
  through the edition).

## Next features

### Change requests, later iterations
- Requests for **Connectings**.
- **Strikes** for users whose requests are rejected as unfounded (keep the decider and outcome on
  each request so strikes can be computed from them).
- Image (cover) proposals and compilation-content edits in requests (left out of v1).
- `ReadingArcForm` still duplicates the widget-styling loop now in `MaterialFieldsMixin`.

### ComicVine integration (agreed order)
1. **Prefill in the Gestión edition form:** an optional ComicVine id field with a button that loads
   the data into the form; the issue can be chosen before or after loading. The form is the review
   step, so a bad mapping is caught before saving.
2. **Bulk import** of one issue or a whole volume, mapped into **change requests** so every change
   can be audited and approved before it is applied.
- Both share one mapper (ComicVine → Comi fields). Needs an API key in an environment variable
  (free key, ~200 requests per resource per hour, a User-Agent header). Store `comicvine_id` on the
  imported models to avoid duplicates and allow re-syncing.
- ComicVine has no data for Comi's variants, printings, ratio, store/event exclusives, or the
  edition's country/language: those stay manual.

### Achievements waiting for a feature
- **Popular / Super Popular** (20 / 100 followers): needs a follow system.
- `GeekCollectable` has no owner, so there are no per-user collectable achievements.

### Daily achievement evaluation (deferred by the user)
Date-based achievements (Veterano) are only checked at sign-in or by `award_achievements`. The user
does **not** want host schedulers (Windows Task Scheduler / host cron): any periodic job must live
inside the app or the Docker stack. Options discussed: evaluate date-based rules on each user's first
visit of the day (recommended), or a `scheduler` service in `docker-compose`. The README section
"Logros" still shows cron/`schtasks` examples and should be rewritten when this is done.

## Technical debt

### History (from the task 1 review)
- Reverting an edition can restore a deleted issue; if one of that issue's creators is gone the
  refusal message names "Creators", which is confusing in an edition revert.
- Reverting an Issue's publishing/number while editions point to it can leave them out of step;
  consider refusing it.
- The history page has no pagination.
- Deleting an Artist removes cover-artist/creator links without writing a version of the affected
  editions/issues (cascade skips signals).
- No UI to reach the history of a deleted edition (the service can restore it).
- Dumps taken before the history existed restore without an initial version; an `ensure_history`
  command could create them.

### General
- `Edition.process_image` logs a full traceback ("Error procesando imagen y thumbnail…") on every
  save of an edition without an image (`comics/models.py`). Harmless but noisy.
- Two JS files hardcode `/ajax/...` URLs instead of using `{% url %}`:
  `comics/static/js/custom/collection_form_events.js`, `fill_release_date.js`.
- `edition_form` and `_clean_content` in `comics/views/manage_editions.py` are too complex
  (`# noqa: C901`); split them now that tests exist.
- Achievement re-evaluation after edition content changes exists both in
  `manage_editions._schedule_owners_after_content_change` and `versions.schedule_edition_owners`.
- **Ruff pass pending** for everything merged since the last one (the `feat/user-profile` work went
  into `develop` without it). Run it once at the end of the current feature and commit it as
  `style: apply ruff format and fixes`.

## Known limits and security

- **No SMTP:** no email verification on registration and no password reset by email.
- **No rate limiting** on login or registration (brute force, spam sign-ups).
- **`/media/` is served without authentication:** pictures of private profiles can be fetched by
  anyone who has the (random, unguessable) URL.
- **Upload size** is checked after the file arrives; a hard limit belongs in the web server/proxy.
- **Production settings:** `DJANGO_DEBUG` defaults to `True` and `DJANGO_ALLOWED_HOSTS` to `*` when
  the environment variables are unset (`comic/settings.py`); a real deployment must set both, plus
  `DJANGO_SECRET_KEY`.
- Gestión re-evaluations of achievements run inside the request (`schedule_for_everyone`); fine for
  a few hundred users, beyond that move them to a background job.

## Open product decisions

- Profiles are **public by default**; the user may prefer private.
- Django's own Spanish password messages use "usted" while the app uses "tú".
- New superusers created with `createsuperuser` only join Collectors (they still pass Gestión).
- `speed_force` counts purchases sharing a trade date, so cataloguing an old backlog in one sitting
  can unlock it.
