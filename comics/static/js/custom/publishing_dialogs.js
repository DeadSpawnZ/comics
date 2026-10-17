// "Create publishing" dialog of the Gestión edition form, and the "Create title" and "Create
// editorial" dialogs it opens on top of it (like the "+" next to a related field in Django's admin),
// plus the "Create artist" dialog of the edition form's artist pickers. Each dialog is a Bootstrap
// modal with its own form ([data-create-dialog]), posted with fetch to a Gestión JSON API
// (comics.views.manage_publishings), which validates it and answers {id, label} (the artist API:
// {artists}) or the errors. The new row is added to the select that asked for it and selected
// there: the title or editorials select of the publishing dialog ([data-target-select]), the
// edition's publishing (window.ComiEditionForm.addPublishing) or the artist picker that opened the
// dialog (window.ComiEditionForm.addArtists). Closing or cancelling a dialog keeps what was typed;
// only a successful creation clears its form. Required fields stay highlighted while they are empty
// ([data-required-field]). When an import's publisher matched no editorial, an existing one can be
// picked for it and its name remembered as an alias (aliasChooser). Texts from the server or the
// catalogues go into the page with textContent / form values, never as HTML.
window.ComiPublishingDialogs = (function () {
  "use strict";

  const publishingForm = document.getElementById("publishing-dialog-form");
  if (!publishingForm) return null;
  const artistForm = document.getElementById("artist-dialog-form");

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
    // Opening a dialog (prefilled or not) shows which required fields still need a value.
    element.addEventListener("show.bs.modal", () => syncAllRequired(element));
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
    // The artist picker that opened it is where the new artist goes.
    if (button.dataset.openDialog === "artist-dialog" && artistForm) {
      artistForm.dataset.targetPicker = button.dataset.artistTarget || "";
    }
    open(button.dataset.openDialog, button);
  });

  // ---------- Fields, errors and warnings ----------

  const field = (form, name) => document.getElementById(`id_${form.dataset.prefix}-${name}`);

  // The control people see: the combobox / multiselect input that replaces a select, if any.
  function visibleControl(control) {
    return document.getElementById(`${control.id}-combobox`) || document.getElementById(`${control.id}-search`) || control;
  }

  // Add or remove one id in an element's aria-describedby, keeping the others.
  function describedBy(element, id, on) {
    const ids = (element.getAttribute("aria-describedby") || "").split(/\s+/).filter((item) => item && item !== id);
    if (on) ids.push(id);
    if (ids.length) element.setAttribute("aria-describedby", ids.join(" "));
    else element.removeAttribute("aria-describedby");
  }

  function clearErrors(form) {
    const box = form.querySelector("[data-dialog-errors]");
    box.hidden = true;
    box.querySelector("ul").replaceChildren();
    const errorIds = [...form.querySelectorAll("[data-dialog-field-error]")].map((error) => error.id);
    form.querySelectorAll("[data-dialog-field-error]").forEach((error) => error.remove());
    form.querySelectorAll(".is-invalid").forEach((control) => {
      control.classList.remove("is-invalid");
      control.removeAttribute("aria-invalid");
      for (const id of errorIds) describedBy(control, id, false);
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
        describedBy(element, error.id, true);
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

  // ---------- Required fields: highlighted while empty ----------
  // A [data-required-field] wraps a required control (aria-required, from forms.RequiredHintsMixin)
  // and its hint ([data-required-hint]). While the control is empty the wrapper is in the "needs a
  // value" state (.needs-value: outline + the hint), and the hint describes the control and the
  // control people see in its place (combobox / multiselect input). The server renders the initial
  // state; this keeps it in sync as values are typed, chosen, prefilled or reset.

  function isEmpty(control) {
    if (control.tagName === "SELECT") return ![...control.selectedOptions].some((option) => option.value);
    return !control.value.trim();
  }

  function syncRequired(wrapper) {
    const hint = wrapper.querySelector("[data-required-hint]");
    const control = wrapper.querySelector("input[aria-required], select[aria-required], textarea[aria-required]");
    if (!hint || !control) return;
    const empty = isEmpty(control);
    wrapper.classList.toggle("needs-value", empty);
    hint.hidden = !empty;
    for (const element of new Set([control, visibleControl(control)])) {
      element.setAttribute("aria-required", "true");
      describedBy(element, hint.id, empty);
    }
  }

  function syncAllRequired(root) {
    root.querySelectorAll("[data-required-field]").forEach(syncRequired);
  }

  for (const type of ["input", "change"]) {
    document.addEventListener(type, (event) => {
      const wrapper = event.target.closest && event.target.closest("[data-required-field]");
      if (wrapper) syncRequired(wrapper);
    });
  }
  syncAllRequired(document);

  // Back to the page's initial values (what the server rendered), refreshing the searchable selects.
  function resetForm(form) {
    form.reset();
    clearErrors(form);
    form.querySelectorAll("select").forEach((select) => select.dispatchEvent(new Event("change", { bubbles: true })));
    syncAllRequired(form);
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
      if (response.ok && body && (body.id || body.artists)) return body;
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
      } else if (form === artistForm) {
        window.ComiEditionForm.addArtists(created.artists, form.dataset.targetPicker);
      } else {
        addOption(document.getElementById(form.dataset.targetSelect), created);
      }
      resetForm(form);
      bootstrap.Modal.getOrCreateInstance(form.closest(".modal")).hide();
    });
  });

  // ---------- An import's publisher that matched no editorial: pick an existing one ----------

  const aliasUrl = publishingForm.dataset.aliasUrl;
  const aliasBox = document.getElementById("publisher-alias");
  let chooserCount = 0;

  function selectEditorial(id) {
    const editorials = field(publishingForm, "editorials");
    const option = editorials && [...editorials.options].find((item) => item.value === String(id));
    if (!option) return;
    option.selected = true;
    editorials.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Remember `name` as an alias of the editorial `editorialId`: {alias} or {error}.
  async function rememberAlias(editorialId, name) {
    const body = new FormData();
    body.append("csrfmiddlewaretoken", publishingForm.querySelector("[name=csrfmiddlewaretoken]").value);
    body.append("editorial", editorialId);
    body.append("name", name);
    try {
      const response = await fetch(aliasUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const answer = await response.json().catch(() => null);
      if (response.ok && answer && answer.id) return { alias: answer };
      const messages = answer ? [...Object.values(answer.errors || {}).flat(), ...(answer.non_field_errors || [])] : [];
      return { error: messages.join(" ") || gettext("It could not be saved. Check your session and try again.") };
    } catch (error) {
      return { error: gettext("It could not be saved. Check your connection and try again.") };
    }
  }

  // A small form to pick the existing editorial the publisher `sourceName` is, and (checked by
  // default) remember the name as its alias, so the next import matches it. Choosing selects it in
  // the publishing dialog and announces it with a "comi:editorial-chosen" event on the document
  // ({id, name, sourceName, remembered}), so every chooser and the import summary can follow.
  function aliasChooser(sourceName) {
    const id = `alias-chooser-${++chooserCount}`;
    const box = document.createElement("div");
    box.className = "alias-chooser";

    const selectField = document.createElement("div");
    selectField.className = "form-floating md-field alias-chooser__select";
    const select = document.createElement("select");
    select.className = "form-select";
    select.id = `${id}-editorial`;
    const label = document.createElement("label");
    label.htmlFor = select.id;
    label.textContent = gettext("Existing editorial");
    selectField.append(select, label);
    // The editorials of the publishing dialog, read again on focus (one may have been created since).
    const fill = () => {
      const value = select.value;
      const options = [...field(publishingForm, "editorials").options].filter((option) => option.value);
      select.replaceChildren(
        new Option(gettext("Choose an editorial…"), ""),
        ...options.map((option) => new Option(option.text, option.value))
      );
      select.value = value;
    };
    fill();
    select.addEventListener("focus", fill);

    const check = document.createElement("div");
    check.className = "form-check alias-chooser__remember";
    const remember = document.createElement("input");
    remember.type = "checkbox";
    remember.className = "form-check-input";
    remember.id = `${id}-remember`;
    remember.checked = true;
    const rememberLabel = document.createElement("label");
    rememberLabel.className = "form-check-label";
    rememberLabel.htmlFor = remember.id;
    check.append(remember, rememberLabel);
    const updateLabel = () => {
      const chosen = select.selectedOptions[0];
      rememberLabel.textContent =
        chosen && chosen.value
          ? interpolate(gettext("Remember “%(source)s” as an alias of %(editorial)s"), { source: sourceName, editorial: chosen.text }, true)
          : interpolate(gettext("Remember “%(source)s” as an alias of the chosen editorial"), { source: sourceName }, true);
    };
    select.addEventListener("change", updateLabel);
    updateLabel();

    const use = document.createElement("button");
    use.type = "button";
    use.className = "md-btn md-btn--tonal";
    use.textContent = gettext("Use this editorial");

    const message = document.createElement("div");
    message.className = "alias-chooser__message";
    message.id = `${id}-message`;
    message.setAttribute("role", "alert");
    select.setAttribute("aria-describedby", message.id);

    use.addEventListener("click", async () => {
      message.textContent = "";
      if (!select.value) {
        message.textContent = gettext("Choose an editorial first.");
        select.focus();
        return;
      }
      const editorial = { id: select.value, name: select.selectedOptions[0].text };
      let remembered = false;
      if (remember.checked) {
        use.disabled = true;
        const result = await rememberAlias(editorial.id, sourceName);
        use.disabled = false;
        if (result.error) {
          message.textContent = result.error;
          return;
        }
        remembered = true;
      }
      selectEditorial(editorial.id);
      document.dispatchEvent(
        new CustomEvent("comi:editorial-chosen", { detail: { ...editorial, sourceName, remembered } })
      );
    });

    box.append(selectField, check, use, message);
    return box;
  }

  function showPublisherAlias(sourceName) {
    if (!aliasBox) return;
    aliasBox.replaceChildren();
    aliasBox.hidden = !sourceName || !aliasUrl;
    if (aliasBox.hidden) return;
    const text = document.createElement("p");
    text.className = "md-body-medium mb-2";
    text.textContent = interpolate(
      gettext("The publisher “%(name)s” is not in Comi. Choose the editorial it is, or create it with +."),
      { name: sourceName },
      true
    );
    aliasBox.append(text, aliasChooser(sourceName));
  }

  // Once an editorial is chosen for the publisher (here or in the import summary) nothing is left to ask.
  document.addEventListener("comi:editorial-chosen", () => showPublisherAlias(""));

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
    if (name && !name.value.trim() && publishingTitle) {
      name.value = publishingTitle.value.trim();
      syncAllRequired(titleForm);
    }
  }

  // `proposed`: the proposal's new_publishing (comics.catalog_sources.mapper.ProposedPublishing).
  // The title is never chosen here: the user picks it. An unknown language or start date empties the
  // field (the language shows its "Choose a language" placeholder): both are required, so it cannot
  // be saved with a value nobody chose. Every required field left empty stays highlighted.
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
    if (editorials && proposed.editorial) selectEditorial(proposed.editorial.id);
    const editorialForm = document.getElementById("editorial-dialog-form");
    if (proposed.new_editorial) {
      setValue(editorialForm, "name", proposed.new_editorial.name, false);
      setValue(editorialForm, "country", proposed.new_editorial.country, false);
    }
    showPublisherAlias(proposed.new_editorial ? proposed.new_editorial.name : "");
    showWarnings(proposed.warnings || []);
    syncAllRequired(publishingForm);
    syncAllRequired(editorialForm);
  }

  return {
    open: (trigger) => open("publishing-dialog", trigger),
    prefill,
    aliasChooser,
  };
})();
