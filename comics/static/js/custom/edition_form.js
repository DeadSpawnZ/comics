(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("edition-form-data").textContent);

  const normalize = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  // ---------- Selects filtrables (publishings) ----------
  // Conserva la opcion elegida aunque no coincida con el filtro, para no perder el valor.
  document.querySelectorAll("[data-filter-select]").forEach((input) => {
    const select = document.getElementById(input.dataset.filterSelect);
    if (!select) return;
    const allOptions = [...select.options].map((option) => ({ value: option.value, text: option.text }));
    input.addEventListener("input", () => {
      const query = normalize(input.value.trim());
      const current = select.value;
      const visible = allOptions.filter(
        (option) => !option.value || option.value === current || !query || normalize(option.text).includes(query)
      );
      select.replaceChildren(...visible.map((o) => new Option(o.text, o.value, false, o.value === current)));
    });
  });

  // ---------- Issue individual o compilacion ----------
  const singlePanel = document.getElementById("content-single");
  const compilationPanel = document.getElementById("content-compilation");
  const syncContent = () => {
    const value = document.querySelector("input[name=content]:checked").value;
    singlePanel.hidden = value !== "single";
    compilationPanel.hidden = value !== "compilation";
  };
  document.querySelectorAll("input[name=content]").forEach((radio) => radio.addEventListener("change", syncContent));

  async function fetchIssues(publishingId) {
    const response = await fetch(`${data.issuesUrl}?publishing=${encodeURIComponent(publishingId)}`, {
      headers: { Accept: "application/json" },
    });
    return (await response.json()).results;
  }

  // Issue individual: al cambiar el publishing se recargan sus issues ("Automatico" siempre queda primero).
  const issuePublishing = document.getElementById("issue-publishing");
  const issueSelect = document.getElementById("issue-select");
  issuePublishing.addEventListener("change", async () => {
    const autoOption = issueSelect.options[0];
    issueSelect.replaceChildren(autoOption);
    if (!issuePublishing.value) return;
    const issues = await fetchIssues(issuePublishing.value);
    for (const issue of issues) issueSelect.append(new Option(issue.label, issue.id));
  });

  // ---------- Compilacion: lista ordenada de issues ----------
  const collectedPublishing = document.getElementById("collected-publishing");
  const collectedIssue = document.getElementById("collected-issue");
  const addButton = document.getElementById("collected-add");
  const list = document.getElementById("collected-list");
  const empty = document.getElementById("collected-empty");
  const hidden = document.getElementById("collected-input");
  let collected = data.collected.slice();

  function iconButton(icon, label, onClick, disabled) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "md-icon-btn";
    button.disabled = disabled;
    if (disabled) button.classList.add("is-disabled");
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = `<span class="material-symbols-outlined" aria-hidden="true">${icon}</span>`;
    button.addEventListener("click", onClick);
    return button;
  }

  function move(index, delta) {
    const [item] = collected.splice(index, 1);
    collected.splice(index + delta, 0, item);
    renderCollected();
  }

  function renderCollected() {
    list.replaceChildren();
    collected.forEach((issue, index) => {
      const li = document.createElement("li");
      li.className = "collected-list__item";
      const label = document.createElement("span");
      label.className = "collected-list__label";
      label.textContent = issue.label;
      const actions = document.createElement("span");
      actions.className = "collected-list__actions";
      actions.append(
        iconButton("arrow_upward", `Subir ${issue.label}`, () => move(index, -1), index === 0),
        iconButton("arrow_downward", `Bajar ${issue.label}`, () => move(index, 1), index === collected.length - 1),
        iconButton("close", `Quitar ${issue.label}`, () => {
          collected.splice(index, 1);
          renderCollected();
        }, false)
      );
      li.append(label, actions);
      list.append(li);
    });
    empty.hidden = collected.length > 0;
    hidden.value = JSON.stringify(collected.map((issue) => issue.id));
    renderAddState();
  }

  function renderAddState() {
    const id = Number(collectedIssue.value);
    addButton.disabled = !id || collected.some((issue) => issue.id === id);
  }

  collectedPublishing.addEventListener("change", async () => {
    collectedIssue.replaceChildren(new Option(collectedPublishing.value ? "Cargando…" : "Elige primero un publishing", ""));
    collectedIssue.disabled = true;
    renderAddState();
    if (!collectedPublishing.value) return;
    const issues = await fetchIssues(collectedPublishing.value);
    collectedIssue.replaceChildren(
      new Option(issues.length ? "Elige un issue…" : "Este publishing no tiene issues", ""),
      ...issues.map((issue) => new Option(issue.label, issue.id))
    );
    collectedIssue.disabled = issues.length === 0;
    renderAddState();
  });
  collectedIssue.addEventListener("change", renderAddState);
  addButton.addEventListener("click", () => {
    const option = collectedIssue.selectedOptions[0];
    if (!option || !option.value) return;
    collected.push({ id: Number(option.value), label: option.text });
    renderCollected();
  });

  // ---------- Vista previa de la portada ----------
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
  renderCollected();
})();
