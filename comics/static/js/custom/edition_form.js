(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("edition-form-data").textContent);
  const picker = window.ComiIssuePicker;

  picker.setupFilterableSelects();

  // ---------- Single issue or compilation ----------
  const singlePanel = document.getElementById("content-single");
  const compilationPanel = document.getElementById("content-compilation");
  const syncContent = () => {
    const value = document.querySelector("input[name=content]:checked").value;
    singlePanel.hidden = value !== "single";
    compilationPanel.hidden = value !== "compilation";
  };
  document.querySelectorAll("input[name=content]").forEach((radio) => radio.addEventListener("change", syncContent));

  // Single issue: changing the publishing reloads its issues ("Automatico" always stays first).
  const issuePublishing = document.getElementById("issue-publishing");
  const issueSelect = document.getElementById("issue-select");
  issuePublishing.addEventListener("change", async () => {
    const autoOption = issueSelect.options[0];
    issueSelect.replaceChildren(autoOption);
    if (!issuePublishing.value) return;
    const issues = await picker.fetchIssues(data.issuesUrl, issuePublishing.value);
    for (const issue of issues) issueSelect.append(new Option(issue.label, issue.id));
  });

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

  // ---------- Cover preview ----------
  const imageInput = document.querySelector("input[type=file][name=image]");
  const preview = document.getElementById("cover-preview");
  const placeholder = document.getElementById("cover-placeholder");
  if (imageInput && preview) {
    imageInput.addEventListener("change", () => {
      const [file] = imageInput.files;
      if (!file) return;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      if (placeholder) placeholder.hidden = true;
    });
  }

  syncContent();
})();
