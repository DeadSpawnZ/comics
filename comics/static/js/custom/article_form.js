// Gestión article form: the pencil on the image opens the (hidden) file input and the chosen file
// is previewed until the form is saved.
(function () {
  "use strict";

  const input = document.querySelector("input[type=file][name=image]");
  const preview = document.getElementById("image-preview");
  const placeholder = document.getElementById("image-placeholder");
  const fileName = document.getElementById("image-file-name");
  if (!input || !preview) return;

  // The object URL of the file being previewed, released when another file replaces it.
  let previewUrl = null;
  document.getElementById("image-edit").addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const [file] = input.files;
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.hidden = false;
    if (placeholder) placeholder.hidden = true;
    fileName.textContent = interpolate(gettext("New image: %(name)s (applied on save)"), { name: file.name }, true);
    fileName.hidden = false;
  });
})();
