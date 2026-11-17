// Selection of an import batch's variants (manage/import_batch_detail.html). Every other form of
// the page answers with the page again, rendered from the database, so a checkbox the user changed
// but did not save would be lost: here each change of a row's checkbox saves the whole selection at
// once (manage_import_batch_select, asking for JSON), and the "Save selection" button and the
// "Save the selection before creating" hint, which are the page without this script, are hidden.
//
// Saves run one after the other, never at the same time, and each one sends the checkboxes as they
// are when it starts, so the last change always wins. A submission of any other batch form of the
// page waits for the pending save first and is not sent when it fails (the checkboxes stay as the
// user set them and the status says so); while one waits, further submissions are dropped (the first
// click wins, as a double-submit guard). "Select all"/"Select none" only wait for the running save, as
// they replace the selection anyway. The status line, the count of "Create selected" and whether it
// can be pressed follow each answer.
(function () {
  "use strict";

  const form = document.getElementById("import-selection");
  if (!form) return; // no variant of the batch can change

  const status = document.getElementById("import-selection-status");
  const createButton = document.getElementById("import-create");
  const createCount = document.getElementById("import-create-count");
  const saveButton = document.getElementById("import-selection-save");
  const hint = document.getElementById("import-selection-hint");
  const intro = document.getElementById("import-selection-intro");

  if (saveButton) saveButton.hidden = true;
  if (hint) hint.hidden = true;
  if (intro) intro.textContent = gettext("Check the variants to create: each change is saved at once.");

  let changed = false; // a checkbox changed since the last save started
  let saving = null; // the running saves: a Promise of whether the selection ended up saved
  let saved = true; // whether the last save succeeded
  let waiting = false; // a form submission waits for the saves
  const released = new WeakSet(); // forms submitted again once the saves ended

  function showStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("catalog-import__status--error", isError);
  }

  // Only the batch's own actions wait for the selection (all of them post under the batch page's
  // URL): the site's other forms (sign out, language) have nothing to do with it.
  const batchPath = window.location.pathname;
  const isBatchAction = (target) => new URL(target.action, window.location.href).pathname.startsWith(batchPath);

  const isRowBox = (element) =>
    element instanceof HTMLInputElement && element.type === "checkbox" && element.name === "rows" && element.form === form;

  // One save of the checkboxes as they are now. Resolves to whether it was saved.
  async function saveOnce() {
    showStatus(gettext("Saving…"));
    const body = new FormData(form); // the CSRF token and the checked rows (their form attribute)
    let response;
    try {
      response = await fetch(form.action, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        keepalive: true, // a save in flight still arrives if the user leaves the page
      });
    } catch {
      showStatus(gettext("Selection not saved. Check your connection and try again."), true);
      return false;
    }
    const answer = await response.json().catch(() => null);
    if (!response.ok || !answer || typeof answer.to_create !== "number") {
      // A JSON error from the view; anything else (e.g. the login page) means the session ended.
      const error = answer && answer.error;
      showStatus(
        error
          ? interpolate(gettext("Selection not saved: %(error)s"), { error }, true)
          : gettext("Selection not saved. Check your session and try again."),
        true
      );
      return false;
    }
    if (createCount) createCount.textContent = String(answer.to_create);
    if (createButton) createButton.disabled = !answer.can_create;
    return true;
  }

  // Save until no change is left. The loop's last check and `saving = null` run in the same task,
  // so a change can never fall between them unsaved.
  async function drain() {
    let ok = false;
    try {
      while (changed) {
        changed = false;
        ok = await saveOnce();
      }
    } finally {
      saving = null;
      saved = ok;
    }
    if (ok) showStatus(gettext("Selection saved."));
    return ok;
  }

  function save() {
    changed = true;
    if (!saving) saving = drain();
    return saving;
  }

  // The selection saved as the user sees it: waits for the running saves, or tries again a failed one.
  function flush() {
    if (saving) return saving;
    return saved ? Promise.resolve(true) : save();
  }

  document.addEventListener("change", function (event) {
    if (isRowBox(event.target)) save();
  });

  // Capture phase: runs before any other submit handler of the page.
  document.addEventListener(
    "submit",
    async function (event) {
      const target = event.target;
      if (!(target instanceof HTMLFormElement) || !isBatchAction(target)) return;
      if (released.has(target)) {
        released.delete(target);
        return;
      }
      if (!saving && saved) return; // nothing unsaved: the submission goes as it is
      event.preventDefault();
      if (waiting) return; // another submission is already waiting
      const submitter = event.submitter;
      waiting = true;
      let ok = true;
      try {
        // The selection form's own buttons replace the selection: they only must not race a save.
        if (target === form) await saving;
        else ok = await flush();
      } finally {
        waiting = false;
      }
      if (!ok) {
        // The status (maybe far from the button pressed) says why; the checkboxes keep the user's choice.
        // A form in a dialog ("Close batch") closes it first, or the status would stay behind it.
        const dialog = target.closest(".modal");
        if (dialog && window.bootstrap) window.bootstrap.Modal.getInstance(dialog)?.hide();
        status.scrollIntoView({ block: "nearest" });
        return;
      }
      if (submitter && submitter.disabled) return; // e.g. "Create selected" with nothing left to create
      released.add(target);
      if (submitter) target.requestSubmit(submitter);
      else target.requestSubmit();
      released.delete(target); // in case the browser's validation stopped it before its submit event
    },
    true
  );
})();
