// Signatures of a piece: "Add signature" clones the empty-form template (Django formset prefix
// "signatures"); removing a row checks its DELETE box and hides it, so the formset deletes it on save.
(function () {
  "use strict";

  const root = document.getElementById("signatures");
  if (!root) return;
  const rows = document.getElementById("signature-rows");
  const template = document.getElementById("signature-template");
  const total = root.querySelector("input[name=signatures-TOTAL_FORMS]");
  const empty = document.getElementById("signature-empty");

  const syncEmpty = () => {
    empty.hidden = [...rows.children].some((row) => !row.hidden);
  };

  document.getElementById("signature-add").addEventListener("click", () => {
    const index = Number(total.value);
    const holder = document.createElement("div");
    holder.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index)).trim();
    const row = holder.firstElementChild;
    rows.append(row);
    total.value = String(index + 1);
    // Rows added later are not enhanced automatically: make the artist select searchable.
    row.querySelectorAll("select[data-combobox]").forEach((select) => window.ComiCombobox.enhance(select));
    syncEmpty();
    const first = row.querySelector(".md-combobox__input, select");
    if (first) first.focus();
  });

  rows.addEventListener("click", (event) => {
    const button = event.target.closest("[data-signature-remove]");
    if (!button) return;
    const row = button.closest(".signature-row");
    row.querySelector("input[name$='-DELETE']").checked = true;
    row.hidden = true;
    syncEmpty();
  });

  syncEmpty();
})();
