(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("arc-form-data").textContent);
  const picker = window.ComiIssuePicker;

  picker.setupFilterableSelects();

  // Issues loaded from the server carry ownership and a cover; newly added ones
  // only show their label until the arc is saved.
  function renderLabel(item) {
    const row = document.createElement("span");
    row.className = "arc-entry";

    const thumb = document.createElement("span");
    thumb.className = "arc-entry__thumb" + (item.owned === false ? " is-missing" : "");
    if (item.cover) {
      const img = document.createElement("img");
      img.src = item.cover;
      img.alt = "";
      img.loading = "lazy";
      thumb.append(img);
    } else {
      thumb.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">image</span>';
    }

    const text = document.createElement("span");
    text.textContent = item.label;
    row.append(thumb, text);

    if (item.owned !== undefined) {
      const chip = document.createElement("span");
      chip.className = "md-assist-chip " + (item.owned ? "md-assist-chip--primary" : "md-assist-chip--error");
      chip.textContent = item.owned
        ? item.ownedInCompilation
          ? gettext("In a compilation")
          : gettext("You own it")
        : gettext("Missing");
      row.append(chip);
    }
    return row;
  }

  picker.orderedIssueList({
    issuesUrl: data.issuesUrl,
    publishingSelect: document.getElementById("arc-publishing"),
    issueSelect: document.getElementById("arc-issue"),
    addButton: document.getElementById("arc-add"),
    list: document.getElementById("arc-list"),
    empty: document.getElementById("arc-empty"),
    hidden: document.getElementById("arc-input"),
    initial: data.issues,
    renderLabel,
  });
})();
