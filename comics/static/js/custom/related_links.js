/*
 * Pencil links next to a select (like the Django admin's "change" link): each
 * `a[data-related-edit]` points to the edit page of the object chosen in the select named by
 * `data-for`. `data-url-template` is that page's URL for object 0, so 0 is replaced by the chosen
 * id. With nothing chosen the link is disabled.
 */
(function () {
  "use strict";

  function bind(link) {
    const select = document.getElementById(link.dataset.for);
    if (!select) {
      return;
    }
    const template = link.dataset.urlTemplate;

    function refresh() {
      const id = select.value;
      if (/^\d+$/.test(id)) {
        link.href = template.replace(/\/0\/(?=[^/]*$)/, "/" + id + "/");
        link.removeAttribute("aria-disabled");
        link.removeAttribute("tabindex");
      } else {
        link.removeAttribute("href");
        link.setAttribute("aria-disabled", "true");
        link.setAttribute("tabindex", "-1");
      }
    }

    select.addEventListener("change", refresh);
    refresh();
  }

  document.querySelectorAll("a[data-related-edit]").forEach(bind);
})();
