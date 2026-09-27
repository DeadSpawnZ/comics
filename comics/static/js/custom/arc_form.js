(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("arc-form-data").textContent);
  const picker = window.ComiIssuePicker;

  picker.setupFilterableSelects();

  // Issues loaded from the server carry a cover; newly added ones only show
  // their label until the arc is saved.
  function renderLabel(item) {
    const row = document.createElement("span");
    row.className = "arc-entry";

    const thumb = document.createElement("span");
    thumb.className = "arc-entry__thumb";
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
