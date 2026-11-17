/*
 * Profile picture dialog: shows a preview of the chosen file before uploading it, and reopens
 * the dialog on load when the server rejected the last upload so its error is visible.
 */
(function () {
  "use strict";

  const dialog = document.getElementById("avatarDialog");
  if (!dialog) {
    return;
  }
  const input = dialog.querySelector('input[type="file"]');
  const current = dialog.querySelector("[data-avatar-current]");
  const preview = dialog.querySelector("[data-avatar-preview]");
  let objectUrl = null;

  function resetPreview() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    preview.hidden = true;
    preview.removeAttribute("src");
    current.hidden = false;
  }

  if (input) {
    input.addEventListener("change", function () {
      resetPreview();
      const file = input.files && input.files[0];
      if (file && file.type.startsWith("image/")) {
        objectUrl = URL.createObjectURL(file);
        preview.src = objectUrl;
        preview.hidden = false;
        current.hidden = true;
      }
    });
  }

  // Closing without uploading discards the choice.
  dialog.addEventListener("hidden.bs.modal", function () {
    if (input) {
      input.value = "";
    }
    resetPreview();
  });

  if (dialog.hasAttribute("data-open-on-load") && window.bootstrap) {
    window.bootstrap.Modal.getOrCreateInstance(dialog).show();
  }
})();
