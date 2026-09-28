// Shared widget to pick issues: an ordered issue list (publishing -> issue -> add, reorder,
// remove). Used by the edition and reading arc forms. Publishing selects are searchable via combobox.js.
window.ComiIssuePicker = (function () {
  "use strict";

  async function fetchIssues(issuesUrl, publishingId) {
    const response = await fetch(`${issuesUrl}?publishing=${encodeURIComponent(publishingId)}`, {
      headers: { Accept: "application/json" },
    });
    return (await response.json()).results;
  }

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

  // Ordered list of issues kept in sync with a hidden input (JSON array of ids).
  // `renderLabel(item)` may return a custom node for each row (default: the label text).
  function orderedIssueList({ issuesUrl, publishingSelect, issueSelect, addButton, list, empty, hidden, initial, renderLabel }) {
    let items = initial.slice();

    function renderAddState() {
      const id = Number(issueSelect.value);
      addButton.disabled = !id || items.some((item) => item.id === id);
    }

    function move(index, delta) {
      const [item] = items.splice(index, 1);
      items.splice(index + delta, 0, item);
      render();
    }

    function render() {
      list.replaceChildren();
      items.forEach((item, index) => {
        const li = document.createElement("li");
        li.className = "collected-list__item";
        let label;
        if (renderLabel) {
          label = renderLabel(item);
        } else {
          label = document.createElement("span");
          label.textContent = item.label;
        }
        label.classList.add("collected-list__label");
        const actions = document.createElement("span");
        actions.className = "collected-list__actions";
        actions.append(
          iconButton("arrow_upward", interpolate(gettext("Move %(name)s up"), { name: item.label }, true), () => move(index, -1), index === 0),
          iconButton("arrow_downward", interpolate(gettext("Move %(name)s down"), { name: item.label }, true), () => move(index, 1), index === items.length - 1),
          iconButton("close", interpolate(gettext("Remove %(name)s"), { name: item.label }, true), () => {
            items.splice(index, 1);
            render();
          }, false)
        );
        li.append(label, actions);
        list.append(li);
      });
      empty.hidden = items.length > 0;
      hidden.value = JSON.stringify(items.map((item) => item.id));
      renderAddState();
    }

    publishingSelect.addEventListener("change", async () => {
      issueSelect.replaceChildren(new Option(publishingSelect.value ? gettext("Loading…") : gettext("Choose a publishing first"), ""));
      issueSelect.disabled = true;
      renderAddState();
      if (!publishingSelect.value) return;
      const issues = await fetchIssues(issuesUrl, publishingSelect.value);
      issueSelect.replaceChildren(
        new Option(issues.length ? gettext("Choose an issue…") : gettext("This publishing has no issues"), ""),
        ...issues.map((issue) => new Option(issue.label, issue.id))
      );
      issueSelect.disabled = issues.length === 0;
      renderAddState();
    });
    issueSelect.addEventListener("change", renderAddState);
    addButton.addEventListener("click", () => {
      const option = issueSelect.selectedOptions[0];
      if (!option || !option.value) return;
      items.push({ id: Number(option.value), label: option.text });
      render();
    });

    render();
  }

  return { fetchIssues, orderedIssueList };
})();
