(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("edition-form-data").textContent);
  const picker = window.ComiIssuePicker;

  // ---------- Single issue or compilation ----------
  const singlePanel = document.getElementById("content-single");
  const compilationPanel = document.getElementById("content-compilation");
  const syncContent = () => {
    const value = document.querySelector("input[name=content]:checked").value;
    singlePanel.hidden = value !== "single";
    compilationPanel.hidden = value !== "compilation";
  };
  document.querySelectorAll("input[name=content]").forEach((radio) => radio.addEventListener("change", syncContent));

  // Single issue: the first option is always the edition's own issue (same publishing and
  // number). Its label follows the publishing/number fields and it is not repeated in the list.
  const issuePublishing = document.getElementById("issue-publishing");
  const issueSelect = document.getElementById("issue-select");
  const ownOption = document.getElementById("own-issue-option");
  const editionPublishing = document.getElementById("id_publishing");
  const editionNumber = document.getElementById("id_number");

  const issuesCache = new Map();
  const issuesOf = (publishingId) => {
    if (!issuesCache.has(publishingId)) issuesCache.set(publishingId, picker.fetchIssues(data.issuesUrl, publishingId));
    return issuesCache.get(publishingId);
  };

  let listedIssues = [];
  let ownIssueId = null;

  function renderIssueOptions() {
    const selected = issueSelect.value;
    const options = listedIssues
      .filter((issue) => String(issue.id) !== ownIssueId)
      .map((issue) => new Option(issue.label, issue.id, false, String(issue.id) === selected));
    issueSelect.replaceChildren(ownOption, ...options);
    // Picking the own issue by hand is the same as the first option.
    if (selected === ownIssueId || !issueSelect.value) ownOption.selected = true;
  }

  async function refreshOwnIssue() {
    const publishingId = editionPublishing.value;
    const number = editionNumber.value.trim();
    const title = data.publishingTitles[publishingId];
    if (!title || !number) {
      ownIssueId = null;
      ownOption.text = gettext("Own issue (by publishing and number)");
      renderIssueOptions();
      return;
    }
    const issues = await issuesOf(publishingId);
    // A newer edit may have happened while fetching.
    if (publishingId !== editionPublishing.value || number !== editionNumber.value.trim()) return;
    const own = issues.find((issue) => issue.number === number);
    ownIssueId = own ? String(own.id) : null;
    const template = own ? gettext("%(issue)s · own issue") : gettext("%(issue)s · own issue (will be created)");
    ownOption.text = interpolate(template, { issue: `${title} #${number}` }, true);
    renderIssueOptions();
  }

  issuePublishing.addEventListener("change", async () => {
    const publishingId = issuePublishing.value;
    listedIssues = publishingId ? await issuesOf(publishingId) : [];
    if (publishingId !== issuePublishing.value) return;
    renderIssueOptions();
  });
  editionPublishing.addEventListener("change", refreshOwnIssue);
  editionNumber.addEventListener("input", refreshOwnIssue);

  // ---------- Release date: the publishing's start date by default ----------
  // On a new edition, picking (or creating) a publishing proposes its start date, as long as the date
  // is still the one the page started with or the last one proposed here. A date typed by hand, by
  // the catalogue import or by a restored draft is different, so it is never replaced.
  const releaseDate = document.getElementById("id_release_date");
  let proposedDate = data.fillReleaseDate && releaseDate ? releaseDate.value : null;
  editionPublishing.addEventListener("change", () => {
    const start = data.publishingDates[editionPublishing.value];
    if (proposedDate === null || !start || releaseDate.value !== proposedDate) return;
    releaseDate.value = start;
    proposedDate = start;
    releaseDate.dispatchEvent(new Event("input", { bubbles: true }));
  });

  // The server already rendered the list without the own issue; load it fully so the own issue
  // can come back if the publishing or number changes.
  (async () => {
    if (issuePublishing.value) listedIssues = await issuesOf(issuePublishing.value);
    await refreshOwnIssue();
  })();

  // ---------- Compilation: ordered list of issues ----------
  const collectedList = picker.orderedIssueList({
    issuesUrl: data.issuesUrl,
    publishingSelect: document.getElementById("collected-publishing"),
    issueSelect: document.getElementById("collected-issue"),
    addButton: document.getElementById("collected-add"),
    list: document.getElementById("collected-list"),
    empty: document.getElementById("collected-empty"),
    hidden: document.getElementById("collected-input"),
    initial: data.collected,
  });

  // ---------- Cover name preview: "event store variant" (see Edition.variant_label) ----------
  const labelPreview = document.getElementById("variant-label-preview");
  const labelParts = ["id_event_exclusive", "id_retailer_exclusive", "id_variant"].map((id) => document.getElementById(id));
  const renderLabelPreview = () => {
    const parts = labelParts.map((input) => input.value.trim()).filter(Boolean);
    const exclusive = labelParts[0].value.trim() || labelParts[1].value.trim();
    labelPreview.hidden = !exclusive; // only useful once the cover is an exclusive
    labelPreview.querySelector("strong").textContent = parts.join(" ");
  };
  labelParts.forEach((input) => input.addEventListener("input", renderLabelPreview));
  renderLabelPreview();

  // ---------- Artists (cover artists, issue creators): visible chips + selector to add more ----------
  // Checking a box from another script (the catalogue import) and dispatching "change" on it
  // refreshes its list too. Artists created on the page (addArtists below) get a row in every picker.
  const artistPickers = new Map(); // field name -> {list, render}
  const checkboxOf = (row) => row.querySelector("input[type=checkbox]");

  function artistPicker(picker) {
    const list = picker.querySelector("[data-artist-list]");
    const add = picker.querySelector("[data-artist-add]");
    const empty = picker.querySelector("[data-artist-empty]");
    const rows = () => [...list.querySelectorAll(".artist-list__item")];

    const render = () => {
      const placeholder = add.options[0];
      const all = rows();
      const available = all.filter((row) => !checkboxOf(row).checked);
      for (const row of all) row.hidden = !checkboxOf(row).checked;
      add.replaceChildren(placeholder, ...available.map((row) => new Option(row.dataset.artistName, row.dataset.artistId)));
      add.value = "";
      add.disabled = available.length === 0;
      empty.hidden = available.length !== all.length;
    };

    add.addEventListener("change", () => {
      const row = rows().find((item) => item.dataset.artistId === add.value);
      if (row) checkboxOf(row).checked = true;
      render();
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-artist-remove]");
      if (!button) return;
      checkboxOf(button.closest(".artist-list__item")).checked = false;
      render();
      add.focus();
    });
    list.addEventListener("change", render);
    render();
    artistPickers.set(picker.dataset.fieldName, { picker, list, render });
  }
  document.querySelectorAll("[data-artist-picker]").forEach(artistPicker);

  // A row like the server renders (manage/includes/artist_picker.html), unchecked, kept in name order.
  function addArtistRow({ picker, list }, artist) {
    const id = String(artist.id);
    if (list.querySelector(`.artist-list__item[data-artist-id="${CSS.escape(id)}"]`)) return;
    const row = document.createElement("li");
    row.className = "artist-list__item";
    row.dataset.artistId = id;
    row.dataset.artistName = artist.name;
    row.hidden = true;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.name = picker.dataset.fieldName;
    box.value = id;
    box.id = `${picker.dataset.fieldId}_new_${id}`;
    box.className = "form-check-input";
    const hidden = document.createElement("span");
    hidden.hidden = true;
    hidden.append(box);
    const photo = document.createElement("span");
    photo.className = "artist-list__photo material-symbols-outlined";
    photo.setAttribute("aria-hidden", "true");
    photo.textContent = "person";
    const name = document.createElement("span");
    name.className = "artist-list__name";
    name.textContent = artist.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "artist-list__remove";
    remove.dataset.artistRemove = "";
    const removeText = interpolate(gettext("Remove %(name)s"), { name: artist.name }, true);
    remove.setAttribute("aria-label", removeText);
    remove.title = removeText;
    const icon = document.createElement("span");
    icon.className = "material-symbols-outlined";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "close";
    remove.append(icon);
    row.append(hidden, photo, name, remove);
    const after = [...list.children].find((item) => item.dataset.artistName.localeCompare(artist.name) > 0);
    list.insertBefore(row, after || null);
  }

  // ---------- Cover preview ----------
  const imageInput = document.querySelector("input[type=file][name=image]");
  const preview = document.getElementById("cover-preview");
  const placeholder = document.getElementById("cover-placeholder");
  const editButton = document.getElementById("cover-edit");
  const fileName = document.getElementById("cover-file-name");
  if (imageInput && preview) {
    // The file input is hidden; the pencil button on the cover opens it.
    editButton.addEventListener("click", () => imageInput.click());
    imageInput.addEventListener("change", () => {
      const [file] = imageInput.files;
      if (!file) return;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      if (placeholder) placeholder.hidden = true;
      fileName.textContent = interpolate(gettext("New cover: %(name)s (applied on save)"), { name: file.name }, true);
      fileName.hidden = false;
    });
  }

  syncContent();

  // ---------- Unsaved changes kept by form_draft.js ----------
  // The issue lists are loaded on demand and the compilation is a widget: they are kept as extras
  // and restored once the plain fields are (the publishing first, so the own issue is right).
  const editionForm = editionPublishing.form;
  editionForm.comiDraftExtras = () => ({
    collected: collectedList.items(),
    issuePublishing: issuePublishing.value,
    issue: issueSelect.value,
  });
  editionForm.addEventListener("comi:draft-restored", (event) => {
    const extras = event.detail.extras || {};
    if (Array.isArray(extras.collected)) collectedList.setItems(extras.collected);
    if (extras.issuePublishing) window.ComiEditionForm.selectIssue(extras.issuePublishing, extras.issue);
  });

  // What other scripts (catalog_import.js, publishing_dialogs.js) use to drive the form.
  window.ComiEditionForm = {
    useSingleIssue() {
      const single = document.querySelector("input[name=content][value=single]");
      single.checked = true;
      syncContent();
    },
    // Link the edition to an issue of any publishing (an empty publishing goes back to the own issue).
    async selectIssue(publishingId, issueId) {
      issuePublishing.value = String(publishingId || "");
      issuePublishing.dispatchEvent(new Event("change", { bubbles: true }));
      listedIssues = publishingId ? await issuesOf(String(publishingId)) : [];
      renderIssueOptions();
      issueSelect.value = issueId ? String(issueId) : "";
      if (!issueSelect.value) ownOption.selected = true;
    },
    // A publishing just created (the "Create publishing" dialog): offered in every publishing select
    // of the page and chosen for the edition, as if picked by hand.
    addPublishing(publishing) {
      const id = String(publishing.id);
      data.publishingTitles[id] = publishing.publishing_title;
      data.publishingDates[id] = publishing.date;
      for (const select of [editionPublishing, issuePublishing, document.getElementById("collected-publishing")]) {
        if (!select || [...select.options].some((option) => option.value === id)) continue;
        const groups = select.querySelectorAll("optgroup");
        (groups.length ? groups[groups.length - 1] : select).append(new Option(publishing.label, id));
      }
      editionPublishing.value = id;
      editionPublishing.dispatchEvent(new Event("change", { bubbles: true }));
    },
    // Artists just created (or found by name) by the "Create artist" API: offered in every artist
    // picker, and checked in the one named `fieldName` ("cover_artists", "issue_data-creators"), if any.
    addArtists(artists, fieldName) {
      for (const entry of artistPickers.values()) {
        for (const artist of artists) addArtistRow(entry, artist);
        entry.render();
      }
      if (fieldName) for (const artist of artists) this.checkArtist(fieldName, artist.id);
    },
    // Check one more artist in a picker (the others stay as they are).
    checkArtist(fieldName, artistId) {
      const entry = artistPickers.get(fieldName);
      if (!entry) return;
      const row = entry.list.querySelector(`.artist-list__item[data-artist-id="${CSS.escape(String(artistId))}"]`);
      if (!row) return;
      checkboxOf(row).checked = true;
      entry.render();
    },
  };
})();
