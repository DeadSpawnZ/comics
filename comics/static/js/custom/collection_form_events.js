document.addEventListener("DOMContentLoaded", function () {
  filterComicsByPublishing();
  filterPreviousTrade();
});

function filterComicsByPublishing(publishingId) {
  const publishingField = document.getElementById("id_publishing");
  const comicField = document.getElementById("id_edition");

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
  const comicField = document.getElementById("id_edition");
  const previousTradeField = document.getElementById("id_previous_trade");
  const tradeTypeField = document.getElementById("id_trade_type");
  const tradeDateField = document.getElementById("id_trade_date");

  if (!(comicField && previousTradeField && tradeTypeField && tradeDateField)) {
    return;
  }

  function loadPreviousTrades() {
    const comicId = comicField.value;

    // previous_trade solo aplica a ventas (no se puede vender algo que aun no se poseia).
    if (tradeTypeField.value.toUpperCase() !== "SELLING") {
      return;
    }

    // Limpiar opciones actuales
    previousTradeField.innerHTML = '<option value="">---------</option>';

    if (!comicId) {
      return;
    }

    const params = new URLSearchParams();
    if (tradeDateField.value) {
      // Solo se ofrecen compras ocurridas en la fecha de venta o antes.
      params.set("before", tradeDateField.value);
    }

    fetch(`/ajax/get-previous-trades/${comicId}/?${params.toString()}`)
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

  comicField.addEventListener("change", loadPreviousTrades);
  tradeDateField.addEventListener("change", loadPreviousTrades);
  tradeTypeField.addEventListener("change", loadPreviousTrades);
}
