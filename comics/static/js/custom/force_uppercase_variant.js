document.addEventListener("DOMContentLoaded", function () {
  const variantInput = document.querySelector("#id_variant");
  if (variantInput) {
    variantInput.addEventListener("input", function () {
      this.value = this.value.toUpperCase();
    });
  }
});
