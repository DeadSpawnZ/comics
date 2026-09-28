// Searchable select: <select data-combobox> becomes a single text field that filters the options
// as you type (like the admin autocomplete). The native select stays in the DOM (hidden) and keeps
// the value: forms submit it and "change" listeners keep working. Options rebuilt later by other
// scripts are picked up automatically. Options with an empty value are placeholders: they are not
// listed, and clearing the field selects them.
window.ComiCombobox = (function () {
  "use strict";

  const normalize = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  let counter = 0;

  function enhance(select) {
    if (select.dataset.comboboxReady) return;
    select.dataset.comboboxReady = "1";
    const id = select.id || `combobox-${++counter}`;
    const listId = `${id}-listbox`;
    const field = select.parentElement;
    const label = field.querySelector(`label[for="${select.id}"]`);

    const input = document.createElement("input");
    input.type = "text";
    input.id = `${id}-combobox`;
    input.className = "form-select md-combobox__input";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = label ? label.textContent.trim() : "";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);

    const list = document.createElement("ul");
    list.id = listId;
    list.className = "md-combobox__list";
    list.setAttribute("role", "listbox");
    list.hidden = true;

    field.classList.add("md-combobox");
    select.hidden = true;
    select.tabIndex = -1;
    select.before(input);
    field.append(list);
    if (label) label.htmlFor = input.id;

    let options = []; // [{value, text, group}]
    let visible = []; // option entries currently listed
    let active = -1;

    function readOptions() {
      options = [];
      for (const child of select.children) {
        if (child.tagName === "OPTGROUP") {
          for (const option of child.children) options.push({ value: option.value, text: option.text, group: child.label });
        } else {
          options.push({ value: child.value, text: child.text, group: null });
        }
      }
    }

    function selectedText() {
      const option = select.selectedOptions[0];
      return option && option.value ? option.text : "";
    }

    function syncFromSelect() {
      readOptions();
      input.disabled = select.disabled;
      if (document.activeElement !== input) input.value = selectedText();
      if (!list.hidden) render(input.value);
    }

    function render(query) {
      const words = normalize(query.trim()).split(/\s+/).filter(Boolean);
      visible = options.filter((option) => option.value && words.every((word) => normalize(option.text).includes(word)));
      if (words.length) {
        // Within each group, options that start with what was typed come first (stable order otherwise).
        const groupOrder = new Map();
        visible.forEach((option) => groupOrder.has(option.group) || groupOrder.set(option.group, groupOrder.size));
        const prefix = normalize(query.trim());
        const rank = (option) => groupOrder.get(option.group) * 2 + (normalize(option.text).startsWith(prefix) ? 0 : 1);
        visible = visible.map((option, index) => ({ option, index })).sort((a, b) => rank(a.option) - rank(b.option) || a.index - b.index).map(({ option }) => option);
      }
      list.replaceChildren();
      let group;
      visible.forEach((option, index) => {
        if (option.group !== group) {
          group = option.group;
          if (group) {
            const header = document.createElement("li");
            header.className = "md-combobox__group";
            header.setAttribute("role", "presentation");
            header.textContent = group;
            list.append(header);
          }
        }
        const item = document.createElement("li");
        item.id = `${listId}-${index}`;
        item.className = "md-combobox__option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.value === select.value));
        item.textContent = option.text;
        item.dataset.index = index;
        list.append(item);
      });
      if (!visible.length) {
        const empty = document.createElement("li");
        empty.className = "md-combobox__empty";
        empty.setAttribute("role", "presentation");
        empty.textContent = gettext("No matches");
        list.append(empty);
      }
      setActive(visible.length ? Math.max(0, visible.findIndex((option) => option.value === select.value)) : -1);
    }

    function setActive(index) {
      list.querySelectorAll(".is-active").forEach((item) => item.classList.remove("is-active"));
      active = index;
      const item = index >= 0 ? document.getElementById(`${listId}-${index}`) : null;
      if (item) {
        item.classList.add("is-active");
        item.scrollIntoView({ block: "nearest" });
        input.setAttribute("aria-activedescendant", item.id);
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function open() {
      if (!list.hidden) return;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      render("");
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function choose(value) {
      const changed = select.value !== value;
      select.value = value;
      input.value = selectedText();
      close();
      if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // Leaving the field: an empty text clears the value; any other unfinished text is discarded.
    function commitOnLeave() {
      if (!input.value.trim() && select.value && options.some((option) => !option.value)) {
        choose("");
      } else {
        input.value = selectedText();
        close();
      }
    }

    input.addEventListener("focus", () => {
      open();
      input.select();
    });
    input.addEventListener("click", open);
    input.addEventListener("input", () => {
      if (list.hidden) open();
      render(input.value);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (list.hidden) return open();
        if (!visible.length) return;
        const step = event.key === "ArrowDown" ? 1 : -1;
        setActive((active + step + visible.length) % visible.length);
      } else if (event.key === "Enter") {
        if (list.hidden) return;
        event.preventDefault(); // do not submit the form
        if (active >= 0) choose(visible[active].value);
      } else if (event.key === "Escape") {
        if (list.hidden) return;
        event.preventDefault();
        input.value = selectedText();
        close();
      } else if (event.key === "Tab") {
        commitOnLeave();
      }
    });
    input.addEventListener("blur", () => {
      if (!list.hidden) commitOnLeave();
    });
    // mousedown (not click) so the choice happens before the input loses focus.
    list.addEventListener("mousedown", (event) => {
      event.preventDefault();
      const item = event.target.closest(".md-combobox__option");
      if (item) choose(visible[Number(item.dataset.index)].value);
    });

    select.addEventListener("change", () => {
      if (document.activeElement !== input) input.value = selectedText();
    });
    new MutationObserver(syncFromSelect).observe(select, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled"],
    });
    syncFromSelect();
  }

  function enhanceAll(root = document) {
    root.querySelectorAll("select[data-combobox]").forEach(enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => enhanceAll());
  } else {
    enhanceAll();
  }

  return { enhance, enhanceAll };
})();
