// Copy buttons ([data-copy-value]), e.g. the "Identification numbers" panel: copies the value with
// the Clipboard API, or with a temporary text field where that API is missing (plain HTTP pages),
// and confirms it on the button and to screen readers ([data-copy-status] of the group).
(function () {
  "use strict";

  function fallbackCopy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    field.remove();
    return copied;
  }

  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (error) {
        // Permission denied or not focused: try the fallback.
      }
    }
    return fallbackCopy(text);
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-value]");
    if (!button) return;
    const copied = await copy(button.dataset.copyValue);
    const icon = button.querySelector(".material-symbols-outlined");
    const group = button.closest("[data-copy-group]");
    const statusText = copied ? gettext("Copied") : gettext("Could not copy");
    if (icon) {
      icon.textContent = copied ? "check" : "error";
      setTimeout(() => {
        icon.textContent = "content_copy";
      }, 1500);
    }
    const status = group && group.querySelector("[data-copy-status]");
    if (status) status.textContent = statusText;
  });
})();
