document.addEventListener("DOMContentLoaded", function () {
  const publishingField = document.getElementById("id_publishing");
  const releaseDateField = document.getElementById("id_release_date");

  if (publishingField) {
    publishingField.addEventListener("change", function () {
      const publishingId = this.value;
      if (publishingId) {
        fetch(`/ajax/get-publishing-date/${publishingId}/`)
          .then((response) => response.json())
          .then((data) => {
            if (data.date) {
              releaseDateField.value = data.date;
            }
          });
      }
    });
  }
});
