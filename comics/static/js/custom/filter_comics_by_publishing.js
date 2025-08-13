document.addEventListener("DOMContentLoaded", function () {
  const publishingField = document.getElementById("id_publishing");
  const comicField = document.getElementById("id_comic");

  if (publishingField && comicField) {
    publishingField.addEventListener("change", function () {
      const publishingId = this.value;

      // Limpiar opciones actuales
      comicField.innerHTML = '<option value="">---------</option>';

      if (publishingId) {
        fetch(`/ajax/get-comics/${publishingId}/`)
          .then((response) => response.json())
          .then((data) => {
            data.results.forEach(function (comic) {
              const option = document.createElement("option");
              option.value = comic.id;
              option.text = comic.text;
              comicField.appendChild(option);
            });
          });
      }
    });
  }
});
