// Collectable piece form: image preview of the chosen article and, for sales, the purchase being
// sold (reloaded when the article, date or collector change).
(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("collectable-form-data").textContent);
  const article = document.getElementById("id_article");
  const tradeDate = document.getElementById("id_trade_date");
  const collector = document.getElementById("id_collector"); // only in Gestión
  const previous = document.getElementById("id_previous_trade");
  const previousPanel = document.getElementById("previous-trade-panel");
  const image = document.getElementById("article-image");
  const placeholder = document.getElementById("article-image-placeholder");
  const name = document.getElementById("article-name");
  const category = document.getElementById("article-category");

  let purchasesRequest = 0; // increases with each purchases request, to drop stale responses
  const isSelling = () => document.querySelector("input[name=trade_type]:checked")?.value === "selling";

  function showArticle() {
    const option = article.selectedOptions[0];
    const url = article.value ? data.images[article.value] : null;
    image.hidden = !url;
    if (url) image.src = url;
    placeholder.hidden = Boolean(url);
    name.textContent = article.value ? option.text : gettext("Choose an article");
    // The options are grouped by category (optgroup).
    category.textContent = article.value && option.parentElement.tagName === "OPTGROUP" ? option.parentElement.label : "";
  }

  async function loadPurchases() {
    const request = ++purchasesRequest; // any later call makes this one stale
    previousPanel.hidden = !isSelling();
    if (!isSelling()) return;
    const selected = previous.value;
    if (!article.value) {
      previous.replaceChildren(new Option(gettext("Choose an article first"), ""));
      return;
    }
    const params = { article: article.value, before: tradeDate.value };
    if (collector && collector.value) params.collector = collector.value;
    if (data.current) params.current = data.current;
    let purchases;
    try {
      const response = await fetch(`${data.purchasesUrl}?${new URLSearchParams(params)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      purchases = (await response.json()).results;
    } catch (error) {
      purchases = null;
    }
    if (request !== purchasesRequest) return; // a newer request (article, date, type...) is loading
    if (purchases === null) {
      previous.replaceChildren(new Option(gettext("The purchases could not be loaded"), ""));
      return;
    }
    previous.replaceChildren(
      new Option(purchases.length ? "---------" : gettext("No purchases of this article to sell"), ""),
      ...purchases.map((item) => new Option(item.label, item.id, false, String(item.id) === selected))
    );
  }

  article.addEventListener("change", () => {
    showArticle();
    loadPurchases();
  });
  tradeDate.addEventListener("change", loadPurchases);
  if (collector) collector.addEventListener("change", loadPurchases);
  document.querySelectorAll("input[name=trade_type]").forEach((radio) => radio.addEventListener("change", loadPurchases));
})();
