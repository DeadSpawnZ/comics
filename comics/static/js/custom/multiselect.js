// Searchable multi-select: <select multiple data-multiselect> becomes a box with the chosen options
// as removable chips plus a text field that filters the remaining options (like combobox.js).
// The native select stays in the DOM (hidden) and keeps the selection, so forms submit it as usual.
// Keys: Up/Down to move, Enter to add, Escape to close, Backspace on an empty field removes the last chip.
window.ComiMultiselect = (function () {
  "use strict";

  const normalize = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  let counter = 0;

  function enhance(select) {
    if (select.dataset.multiselectReady) return;
    select.dataset.multiselectReady = "1";
    const id = select.id || `multiselect-${++counter}`;
    const listId = `${id}-listbox`;

    const box = document.createElement("div");
    box.className = "md-multiselect";
    const chips = document.createElement("ul");
    chips.className = "md-multiselect__chips";
    const input = document.createElement("input");
    input.type = "text";
    input.id = `${id}-search`;
    input.className = "md-multiselect__input";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = select.dataset.placeholder || gettext("Add…");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    if (select.getAttribute("aria-label")) input.setAttribute("aria-label", select.getAttribute("aria-label"));
    const list = document.createElement("ul");
    list.id = listId;
    list.className = "md-combobox__list";
    list.setAttribute("role", "listbox");
    list.hidden = true;

    box.append(chips, input, list);
    select.hidden = true;
    select.tabIndex = -1;
    select.after(box);
    const label = document.querySelector(`label[for="${select.id}"]`);
    if (label) label.htmlFor = input.id;

    let visible = [];
    let active = -1;

    const options = () => [...select.options].filter((option) => option.value);

    function renderChips() {
      chips.replaceChildren(
        ...options()
          .filter((option) => option.selected)
          .map((option) => {
            const chip = document.createElement("li");
            chip.className = "md-multiselect__chip";
            const text = document.createElement("span");
            text.textContent = option.text;
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "md-multiselect__remove";
            remove.dataset.value = option.value;
            remove.setAttribute("aria-label", interpolate(gettext("Remove %(name)s"), { name: option.text }, true));
            remove.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';
            chip.append(text, remove);
            return chip;
          })
      );
    }

    function renderList() {
      const words = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
      const prefix = normalize(input.value.trim());
      visible = options().filter((option) => !option.selected && words.every((word) => normalize(option.text).includes(word)));
      if (prefix) {
        // Options that start with what was typed come first.
        visible.sort((a, b) => Number(!normalize(a.text).startsWith(prefix)) - Number(!normalize(b.text).startsWith(prefix)));
      }
      list.replaceChildren();
      visible.forEach((option, index) => {
        const item = document.createElement("li");
        item.id = `${listId}-${index}`;
        item.className = "md-combobox__option";
        item.setAttribute("role", "option");
        item.dataset.index = index;
        item.textContent = option.text;
        list.append(item);
      });
      if (!visible.length) {
        const empty = document.createElement("li");
        empty.className = "md-combobox__empty";
        empty.textContent = gettext("No matches");
        list.append(empty);
      }
      setActive(visible.length ? 0 : -1);
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
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      renderList();
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function setSelected(value, selected) {
      const option = options().find((item) => item.value === value);
      if (!option) return;
      option.selected = selected;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      renderChips();
      if (!list.hidden) renderList();
    }

    box.addEventListener("click", (event) => {
      const remove = event.target.closest(".md-multiselect__remove");
      if (remove) {
        setSelected(remove.dataset.value, false);
        input.focus();
      } else if (!event.target.closest(".md-combobox__list")) {
        input.focus();
      }
    });
    input.addEventListener("focus", open);
    input.addEventListener("input", () => (list.hidden ? open() : renderList()));
    input.addEventListener("blur", () => {
      input.value = "";
      close();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (list.hidden) return open();
        if (!visible.length) return;
        setActive((active + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length);
      } else if (event.key === "Enter") {
        event.preventDefault(); // do not submit the form
        if (!list.hidden && active >= 0) {
          input.value = "";
          setSelected(visible[active].value, true);
        }
      } else if (event.key === "Escape") {
        if (!list.hidden) {
          event.preventDefault();
          input.value = "";
          close();
        }
      } else if (event.key === "Backspace" && !input.value) {
        const last = options().filter((option) => option.selected).pop();
        if (last) setSelected(last.value, false);
      }
    });
    // mousedown (not click) so the choice happens before the input loses focus.
    list.addEventListener("mousedown", (event) => {
      event.preventDefault();
      const item = event.target.closest(".md-combobox__option");
      if (!item) return;
      input.value = "";
      setSelected(visible[Number(item.dataset.index)].value, true);
    });

    renderChips();
  }

  function enhanceAll(root = document) {
    root.querySelectorAll("select[multiple][data-multiselect]").forEach(enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => enhanceAll());
  } else {
    enhanceAll();
  }

  return { enhance, enhanceAll };
})();
