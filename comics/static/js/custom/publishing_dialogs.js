// "Create publishing" dialog of the Gestión edition form, and the "Create title" and "Create
// editorial" dialogs it opens on top of it (like the "+" next to a related field in Django's admin).
// Each dialog is a Bootstrap modal with its own form ([data-create-dialog]), posted with fetch to a
// Gestión JSON API (comics.views.manage_publishings), which validates it and answers {id, label} or
// the errors. The new row is added to the select that asked for it and selected there: the title or
// editorials select of the publishing dialog ([data-target-select]), or the edition's publishing
// (window.ComiEditionForm.addPublishing). Closing or cancelling a dialog keeps what was typed; only
// a successful creation clears its form. Texts from the server or the catalogues go into the page
// with textContent / form values, never as HTML.
window.ComiPublishingDialogs = (function () {
  "use strict";

  const publishingForm = document.getElementById("publishing-dialog-form");
  if (!publishingForm) return null;

  // Bootstrap stacks every modal at the same level (dialog 1055, backdrop 1050): each dialog opened
  // on top of another one goes LEVEL_STEP higher, with its backdrop just below it.
  const MODAL_Z = 1055;
  const LEVEL_STEP = 20;
  const stack = []; // [{element, trigger}] of the open dialogs, the top one last

  // ---------- Opening and closing (stacked modals) ----------

  function open(id, trigger) {
    const element = document.getElementById(id);
    if (!element || stack.some((entry) => entry.element === element)) return;
    const level = stack.length;
    stack.push({ element, trigger: trigger || document.activeElement });
    element.style.zIndex = String(MODAL_Z + level * LEVEL_STEP);
    bootstrap.Modal.getOrCreateInstance(element).show();
    // Bootstrap appends the new backdrop synchronously in show().
    const backdrops = document.querySelectorAll(".modal-backdrop");
    if (backdrops.length) backdrops[backdrops.length - 1].style.zIndex = String(MODAL_Z - 5 + level * LEVEL_STEP);
  }

  function firstField(element) {
    return [...element.querySelectorAll("input, select, textarea")].find(
      (field) => field.type !== "hidden" && !field.hidden && !field.disabled && field.offsetParent !== null
    );
  }

  document.querySelectorAll(".modal").forEach((element) => {
    if (!element.querySelector("form[data-create-dialog]")) return;
    element.addEventListener("shown.bs.modal", () => {
      const field = firstField(element);
      if (field) field.focus();
    });
    element.addEventListener("hidden.bs.modal", () => {
      const index = stack.findIndex((entry) => entry.element === element);
      const [entry] = index >= 0 ? stack.splice(index, 1) : [];
      element.style.zIndex = "";
      const below = stack[stack.length - 1];
      if (below) {
        // Closing any modal makes Bootstrap unlock the page and drop its focus trap; the dialog
        // below is still open, so take both back (the trap is Bootstrap's own, re-armed).
        document.body.classList.add("modal-open");
        const instance = bootstrap.Modal.getInstance(below.element);
        const trap = instance && instance._focustrap;
        if (trap && typeof trap.activate === "function") {
          trap.deactivate();
          trap.activate();
        }
      }
      if (entry && entry.trigger && document.contains(entry.trigger)) entry.trigger.focus();
    });
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-dialog]");
    if (!button) return;
    if (button.dataset.openDialog === "title-dialog") suggestTitleName();
    open(button.dataset.openDialog, button);
  });

  // ---------- Fields, errors and warnings ----------

  const field = (form, name) => document.getElementById(`id_${form.dataset.prefix}-${name}`);

  // The control people see: the combobox / multiselect input that replaces a select, if any.
  function visibleControl(control) {
    return document.getElementById(`${control.id}-combobox`) || document.getElementById(`${control.id}-search`) || control;
  }

  function clearErrors(form) {
    const box = form.querySelector("[data-dialog-errors]");
    box.hidden = true;
    box.querySelector("ul").replaceChildren();
    form.querySelectorAll("[data-dialog-field-error]").forEach((error) => error.remove());
    form.querySelectorAll(".is-invalid").forEach((control) => {
      control.classList.remove("is-invalid");
      control.removeAttribute("aria-invalid");
      control.removeAttribute("aria-describedby");
    });
  }

  function showErrors(form, errors, nonField) {
    clearErrors(form);
    let first = null;
    for (const [name, messages] of Object.entries(errors || {})) {
      const control = field(form, name);
      if (!control) {
        nonField = [...nonField, ...messages];
        continue;
      }
      const shown = visibleControl(control);
      const error = document.createElement("div");
      error.className = "invalid-feedback d-block";
      error.id = `${control.id}-error`;
      error.dataset.dialogFieldError = "";
      error.textContent = messages.join(" ");
      (control.closest(".md-field") || control.parentElement).append(error);
      for (const element of new Set([control, shown])) {
        element.classList.add("is-invalid");
        element.setAttribute("aria-invalid", "true");
        element.setAttribute("aria-describedby", error.id);
      }
      first = first || shown;
    }
    if (nonField.length) {
      const box = form.querySelector("[data-dialog-errors]");
      box.querySelector("ul").replaceChildren(
        ...nonField.map((message) => {
          const item = document.createElement("li");
          item.textContent = message;
          return item;
        })
      );
      box.hidden = false;
    }
    if (first) first.focus();
  }

  function showWarnings(warnings) {
    const list = publishingForm.querySelector("[data-dialog-warnings]");
    list.replaceChildren(
      ...warnings.map((warning) => {
        const item = document.createElement("li");
        item.className = "catalog-import__item catalog-import__item--warning";
        const icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "warning";
        const text = document.createElement("span");
        text.textContent = warning;
        item.append(icon, text);
        return item;
      })
    );
    list.hidden = warnings.length === 0;
  }

  // Back to the page's initial values (what the server rendered), refreshing the searchable selects.
  function resetForm(form) {
    form.reset();
    clearErrors(form);
    form.querySelectorAll("select").forEach((select) => select.dispatchEvent(new Event("change", { bubbles: true })));
    if (window.ComiFormDraft) window.ComiFormDraft.clear(form);
  }

  // ---------- Creating ----------

  async function create(form) {
    const button = form.querySelector("[type=submit]");
    button.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const body = await response.json().catch(() => null);
      if (response.ok && body && body.id) return body;
      if (body && (body.errors || body.non_field_errors)) {
        showErrors(form, body.errors, body.non_field_errors || []);
      } else {
        showErrors(form, {}, [gettext("It could not be created. Check your session and try again.")]);
      }
    } catch (error) {
      showErrors(form, {}, [gettext("It could not be created. Check your connection and try again.")]);
    } finally {
      button.disabled = false;
    }
    return null;
  }

  // Add the new row to `select` (selected, as the user just created it) and let it refresh.
  function addOption(select, created) {
    // defaultSelected stays false, so resetting the publishing dialog does not select it again.
    select.append(new Option(created.label, String(created.id), false, true));
    if (!select.multiple) select.value = String(created.id);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.querySelectorAll("form[data-create-dialog]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const created = await create(form);
      if (!created) return;
      if (form === publishingForm) {
        window.ComiEditionForm.addPublishing(created);
        showWarnings([]);
      } else {
        addOption(document.getElementById(form.dataset.targetSelect), created);
      }
      resetForm(form);
      bootstrap.Modal.getOrCreateInstance(form.closest(".modal")).hide();
    });
  });

  // ---------- Prefilling (from the catalogue import) ----------

  function setValue(form, name, value, keepWhenEmpty) {
    const control = field(form, name);
    if (!control) return;
    const text = value === null || value === undefined ? "" : String(value);
    if (!text && keepWhenEmpty) return;
    if (control.tagName === "SELECT" && ![...control.options].some((option) => option.value === text)) return;
    control.value = text;
    control.dispatchEvent(new Event(control.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }

  // The "Create title" dialog starts from the publishing title, if nothing was typed in it yet.
  function suggestTitleName() {
    const titleForm = document.getElementById("title-dialog-form");
    const name = field(titleForm, "name");
    const publishingTitle = field(publishingForm, "publishing_title");
    if (name && !name.value.trim() && publishingTitle) name.value = publishingTitle.value.trim();
  }

  // `proposed`: the proposal's new_publishing (comics.catalog_sources.mapper.ProposedPublishing).
  // The title is never chosen here: the user picks it. An unknown language or start date empties the
  // field (the language shows its "Choose a language" placeholder): both are required, so it cannot
  // be saved with a value nobody chose.
  function prefill(proposed) {
    setValue(publishingForm, "publishing_title", proposed.publishing_title, false);
    setValue(publishingForm, "year", proposed.year, false);
    setValue(publishingForm, "serie", proposed.serie, true);
    setValue(publishingForm, "language", proposed.language, false);
    setValue(publishingForm, "date", proposed.date, false);
    for (const name of ["metron_series_id", "comicvine_volume_id", "gcd_series_id"]) {
      setValue(publishingForm, name, proposed[name], false);
    }
    const editorials = field(publishingForm, "editorials");
    if (editorials && proposed.editorial) {
      const option = [...editorials.options].find((item) => item.value === String(proposed.editorial.id));
      if (option) {
        option.selected = true;
        editorials.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    if (proposed.new_editorial) {
      const editorialForm = document.getElementById("editorial-dialog-form");
      setValue(editorialForm, "name", proposed.new_editorial.name, false);
      setValue(editorialForm, "country", proposed.new_editorial.country, false);
    }
    showWarnings(proposed.warnings || []);
  }

  return {
    open: (trigger) => open("publishing-dialog", trigger),
    prefill,
  };
})();
