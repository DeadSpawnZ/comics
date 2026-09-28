// Variants are stored in uppercase (Edition.process_variant); show them that way while typing.
// Used by the admin and the Gestión edition form. The caret position is kept.
document.addEventListener("DOMContentLoaded", function () {
  const variantInput = document.querySelector("#id_variant");
  if (variantInput) {
    variantInput.addEventListener("input", function () {
      const start = this.selectionStart;
      const end = this.selectionEnd;
      this.value = this.value.toUpperCase();
      this.setSelectionRange(start, end);
    });
  }
});
