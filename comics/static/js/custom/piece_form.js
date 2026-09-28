// Piece (collection record) form: publishing -> edition list, cover preview, and for sales the
// purchase being sold (reloaded when the edition, date or collector change).
(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("piece-form-data").textContent);
  const publishing = document.getElementById("id_publishing");
  const edition = document.getElementById("id_edition");
  const tradeDate = document.getElementById("id_trade_date");
  const collector = document.getElementById("id_collector"); // only in Gestión
  const previous = document.getElementById("id_previous_trade");
  const previousPanel = document.getElementById("previous-trade-panel");
  const cover = document.getElementById("piece-cover");
  const coverPlaceholder = document.getElementById("piece-cover-placeholder");
  const coverTitle = document.getElementById("piece-cover-title");

  const editionsById = new Map();
  const isSelling = () => document.querySelector("input[name=trade_type]:checked")?.value === "selling";

  async function getJson(url, params) {
    const response = await fetch(`${url}?${new URLSearchParams(params)}`, { headers: { Accept: "application/json" } });
    return (await response.json()).results;
  }

  function showCover() {
    const item = editionsById.get(edition.value);
    if (!item) return; // keep the server-rendered preview until the list is loaded
    cover.hidden = !item.thumbnail;
    if (item.thumbnail) cover.src = item.thumbnail;
    coverPlaceholder.hidden = Boolean(item.thumbnail);
    coverTitle.textContent = item.title;
  }

  function clearCover() {
    cover.hidden = true;
    coverPlaceholder.hidden = false;
    coverTitle.textContent = gettext("Choose an edition");
  }

  async function loadEditions() {
    const selected = edition.value;
    editionsById.clear();
    if (!publishing.value) {
      edition.replaceChildren(new Option(gettext("Choose a publishing first"), ""));
      clearCover();
      return loadPurchases();
    }
    const publishingId = publishing.value;
    edition.replaceChildren(new Option(gettext("Loading…"), ""));
    const editions = await getJson(data.editionsUrl, { publishing: publishingId });
    if (publishingId !== publishing.value) return;
    for (const item of editions) editionsById.set(String(item.id), item);
    edition.replaceChildren(
      new Option(editions.length ? "---------" : gettext("This publishing has no editions"), ""),
      ...editions.map((item) => new Option(item.label, item.id, false, String(item.id) === selected))
    );
    if (edition.value) showCover();
    else clearCover();
    loadPurchases();
  }

  async function loadPurchases() {
    previousPanel.hidden = !isSelling();
    if (!isSelling()) return;
    const selected = previous.value;
    if (!edition.value) {
      previous.replaceChildren(new Option(gettext("Choose an edition first"), ""));
      return;
    }
    const params = { edition: edition.value, before: tradeDate.value };
    if (collector && collector.value) params.collector = collector.value;
    if (data.current) params.current = data.current;
    const purchases = await getJson(data.purchasesUrl, params);
    previous.replaceChildren(
      new Option(purchases.length ? "---------" : gettext("No purchases of this edition to sell"), ""),
      ...purchases.map((item) => new Option(item.label, item.id, false, String(item.id) === selected))
    );
  }

  publishing.addEventListener("change", loadEditions);
  edition.addEventListener("change", () => {
    if (edition.value) showCover();
    else clearCover();
    loadPurchases();
  });
  tradeDate.addEventListener("change", loadPurchases);
  if (collector) collector.addEventListener("change", loadPurchases);
  document.querySelectorAll("input[name=trade_type]").forEach((radio) => radio.addEventListener("change", loadPurchases));

  // Load the edition data (covers) of the current publishing without touching the selection.
  if (publishing.value) {
    getJson(data.editionsUrl, { publishing: publishing.value }).then((editions) => {
      for (const item of editions) editionsById.set(String(item.id), item);
    });
  }
})();
