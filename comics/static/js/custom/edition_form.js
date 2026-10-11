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

  // The server already rendered the list without the own issue; load it fully so the own issue
  // can come back if the publishing or number changes.
  (async () => {
    if (issuePublishing.value) listedIssues = await issuesOf(issuePublishing.value);
    await refreshOwnIssue();
  })();

  // ---------- Compilation: ordered list of issues ----------
  picker.orderedIssueList({
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
  // refreshes its list too.
  function artistPicker(picker) {
    const list = picker.querySelector("[data-artist-list]");
    if (!list) return; // there are no artists yet
    const add = picker.querySelector("[data-artist-add]");
    const empty = picker.querySelector("[data-artist-empty]");
    const rows = [...list.querySelectorAll(".artist-list__item")];
    const checkboxOf = (row) => row.querySelector("input[type=checkbox]");

    const render = () => {
      const placeholder = add.options[0];
      const available = rows.filter((row) => !checkboxOf(row).checked);
      for (const row of rows) row.hidden = !checkboxOf(row).checked;
      add.replaceChildren(placeholder, ...available.map((row) => new Option(row.dataset.artistName, row.dataset.artistId)));
      add.value = "";
      add.disabled = available.length === 0;
      empty.hidden = available.length !== rows.length;
    };

    add.addEventListener("change", () => {
      const row = rows.find((item) => item.dataset.artistId === add.value);
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
  }
  document.querySelectorAll("[data-artist-picker]").forEach(artistPicker);

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

  // What other scripts (catalog_import.js) use to drive the content section.
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
  };
})();
