// Safety net for long Gestión forms: what is typed is kept in sessionStorage (this browser tab only)
// while working, and after an accidental reload the page offers to restore it (#form-draft).
// Forms opt in with data-draft="<name>"; drafts are keyed by the page path and that name.
//
// Stored: named fields (checkboxes and radios as checked or not) and fields with data-draft-key;
// never file inputs, the CSRF token or fields marked data-draft-skip. A form may add state of its
// own with form.comiDraftExtras = () => ({...}); after a restore it receives a "comi:draft-restored"
// event whose detail is the draft ({values, extras}), for what needs more than setting values.
//
// Submitting a regular form drops every draft of the page (a form sent back with errors shows the
// submitted values anyway). Dialog forms (data-draft-dialog) are submitted with fetch, so they are
// dropped with ComiFormDraft.clear(form) once what they create is saved. Any storage error
// (private mode, quota, storage disabled) only turns the feature off.
window.ComiFormDraft = (function () {
  "use strict";

  const PREFIX = "comi-draft:";
  const SAVE_DELAY_MS = 400;
  const timers = new WeakMap();

  function storage() {
    try {
      return window.sessionStorage || null;
    } catch (error) {
      return null;
    }
  }

  const keyOf = (form) => `${PREFIX}${window.location.pathname}:${form.dataset.draft}`;

  function read(form) {
    try {
      const raw = storage().getItem(keyOf(form));
      const draft = raw ? JSON.parse(raw) : null;
      return draft && typeof draft.values === "object" ? draft : null;
    } catch (error) {
      return null;
    }
  }

  function write(form, draft) {
    try {
      storage().setItem(keyOf(form), JSON.stringify(draft));
    } catch (error) {
      // Quota or disabled storage: nothing is kept.
    }
  }

  function clear(form) {
    if (!form || !form.dataset.draft) return;
    clearTimeout(timers.get(form));
    try {
      storage().removeItem(keyOf(form));
    } catch (error) {
      // Nothing to drop.
    }
  }

  function fieldKey(field) {
    if (field.dataset.draftSkip !== undefined || field.name === "csrfmiddlewaretoken") return null;
    if (["file", "submit", "button", "reset", "password"].includes(field.type)) return null;
    if (field.dataset.draftKey) return field.dataset.draftKey;
    if (!field.name) return null;
    return field.type === "checkbox" || field.type === "radio" ? `${field.name}=${field.value}` : field.name;
  }

  const fields = (form) => [...form.elements].filter((field) => fieldKey(field) !== null);

  function collect(form) {
    const values = {};
    for (const field of fields(form)) {
      const key = fieldKey(field);
      if (field.type === "checkbox" || field.type === "radio") values[key] = field.checked;
      else if (field.tagName === "SELECT" && field.multiple) values[key] = [...field.selectedOptions].map((option) => option.value);
      else values[key] = field.value;
    }
    const extras = typeof form.comiDraftExtras === "function" ? form.comiDraftExtras() : {};
    return { savedAt: Date.now(), values, extras };
  }

  // Set the stored values, then let every listener of the page react as if they were typed.
  function apply(form, draft) {
    const changed = [];
    for (const field of fields(form)) {
      const key = fieldKey(field);
      if (!Object.prototype.hasOwnProperty.call(draft.values, key)) continue;
      const value = draft.values[key];
      if (field.type === "checkbox" || field.type === "radio") {
        field.checked = Boolean(value);
      } else if (field.tagName === "SELECT" && field.multiple) {
        const wanted = new Set(Array.isArray(value) ? value.map(String) : []);
        for (const option of field.options) option.selected = wanted.has(option.value);
      } else if (field.tagName === "SELECT") {
        if (![...field.options].some((option) => option.value === String(value))) continue; // no longer exists
        field.value = String(value);
      } else {
        field.value = String(value ?? "");
      }
      changed.push(field);
    }
    for (const field of changed) {
      const typed = field.tagName === "TEXTAREA" || (field.tagName === "INPUT" && !["checkbox", "radio", "hidden"].includes(field.type));
      if (typed) field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
    form.dispatchEvent(new CustomEvent("comi:draft-restored", { detail: draft }));
  }

  function watch(form, allForms) {
    const save = () => {
      clearTimeout(timers.get(form));
      timers.set(form, setTimeout(() => write(form, collect(form)), SAVE_DELAY_MS));
    };
    form.addEventListener("input", save);
    form.addEventListener("change", save);
    if (form.dataset.draftDialog === undefined) {
      form.addEventListener("submit", () => allForms.forEach(clear));
    }
  }

  function init() {
    if (!storage()) return;
    const forms = [...document.querySelectorAll("form[data-draft]")];
    // Read before watching: the drafts of the previous visit stay in memory even if the user edits
    // (and so overwrites them) before deciding.
    const pending = forms.map((form) => [form, read(form)]).filter(([, draft]) => draft);
    forms.forEach((form) => watch(form, forms));

    const banner = document.getElementById("form-draft");
    if (!banner || !pending.length) return;
    const text = document.getElementById("form-draft-text");
    const actions = document.getElementById("form-draft-actions");
    banner.hidden = false;
    document.getElementById("form-draft-restore").addEventListener("click", () => {
      pending.forEach(([form, draft]) => apply(form, draft));
      actions.hidden = true;
      text.textContent = gettext("Your changes were restored. Check them, then save.");
      text.focus();
    });
    document.getElementById("form-draft-discard").addEventListener("click", () => {
      pending.forEach(([form]) => clear(form));
      banner.hidden = true;
      const first = [...forms[0].querySelectorAll("input, select, textarea")].find(
        (field) => field.type !== "hidden" && !field.hidden && !field.disabled && field.offsetParent !== null
      );
      if (first) first.focus();
    });
  }

  init();

  return { clear };
})();
