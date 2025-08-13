document.addEventListener("DOMContentLoaded", function () {
  const imageInput = document.querySelector("#id_image");
  const previewImg = document.querySelector("#thumb-preview");

  if (imageInput && previewImg) {
    imageInput.addEventListener("change", function (e) {
      const file = e.target.files[0];
      if (file && file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = function (event) {
          previewImg.src = event.target.result;
          previewImg.style.display = "block";
        };
        reader.readAsDataURL(file);
      } else {
        previewImg.style.display = "none";
      }
    });
  }
});
