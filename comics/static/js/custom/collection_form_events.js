document.addEventListener("DOMContentLoaded", function () {
  filterComicsByPublishing();
  filterPreviousTrade();
});

function filterComicsByPublishing(publishingId) {
  const publishingField = document.getElementById("id_publishing");
  const comicField = document.getElementById("id_comic");

  if (!(publishingField && comicField)) {
    return;
  }
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

function filterPreviousTrade() {
  const comicField = document.getElementById("id_comic");
  const previousTradeField = document.getElementById("id_previous_trade");
  const tradeTypeField = document.getElementById("id_trade_type");

  if (!(comicField && previousTradeField && tradeTypeField)) {
    return;
  }
  comicField.addEventListener("change", function () {
    const comicId = this.value;

    if (tradeTypeField.value == "BUYING") {
      console.log("Trade type is BUYING; skipping previous trade filter.");
      return;
    }
    // Limpiar opciones actuales
    previousTradeField.innerHTML = '<option value="">---------</option>';

    if (comicId) {
      fetch(`/ajax/get-previous-trades/${comicId}/`)
        .then((response) => response.json())
        .then((data) => {
          if (data.error) {
            console.error("Error fetching previous trades:", data.error);
            return;
          }
          data.results.forEach(function (trade) {
            const option = document.createElement("option");
            option.value = trade.id;
            option.text = trade.text;
            previousTradeField.appendChild(option);
          });
        });
    }
  });
}
