// MyComicShop series dialog (includes/catalog_series_dialog.html): the variants of a series
// (catalog_mycomicshop_series), one card per variant grouped by issue number. Opened by the import
// box of the edition form (catalog_import.js) and by Gestión → Imports (import_batch_list.html)
// through window.ComiSeriesDialog:
//
//   ComiSeriesDialog.open(tid, {
//     fill: { load(ivid, tid), apply(value) },  // optional: "Import" fills a form with one variant
//     onLinked(ivid, existing),                 // optional: a variant was linked to an edition
//     returnFocus,                              // optional: the element focused once it closes
//   });
//   ComiSeriesDialog.seriesIdFrom(text);        // the TID typed or held by a pasted URL, else ""
//
// With `fill`, each card is a radio: "Import" (or a double click, or Enter) calls fill.load with the
// chosen variant's IVID, which resolves to {error: message} (shown in the dialog) or {value}; the
// dialog then closes and fill.apply(value) runs. Without it, "Import" is hidden and a card only
// checks its variant. Every variant not in Comi yet has a checkbox, unchecked at first (a probable
// match too: the user may know the match is false); "Import selected" makes an import batch of the
// checked ones (manage_import_batch_new, at most `maxBatchRows`) and opens its page. A variant that
// is probably an edition Comi already has (same number and letter, no MyComicShop ID yet) can be
// linked to it from its card (catalog_mycomicshop_link), which saves that ID on it. Every text from
// the catalogue goes into the page with textContent, never as HTML.
(function () {
  "use strict";

  const seriesDialog = document.getElementById("catalog-series-dialog");
  if (!seriesDialog) return; // the page does not include the dialog

  // seriesUrl, linkUrl, batchUrl, maxBatchRows, sourceName (manage_editions.series_dialog_data).
  const config = JSON.parse(document.getElementById("catalog-series-dialog-data").textContent);
  const seriesTitle = document.getElementById("catalog-series-title");
  const seriesLink = document.getElementById("catalog-series-link");
  const seriesLinkText = document.getElementById("catalog-series-link-text");
  const seriesPublishing = document.getElementById("catalog-series-publishing");
  const seriesStatus = document.getElementById("catalog-series-status");
  const seriesIssues = document.getElementById("catalog-series-issues");
  const seriesImport = document.getElementById("catalog-series-import");
  const seriesBatch = document.getElementById("catalog-series-batch");
  const seriesBatchText = document.getElementById("catalog-series-batch-text");
  const seriesBatchHint = document.getElementById("catalog-series-batch-hint");
  const seriesDefaultTitle = seriesTitle.textContent;
  const linkDialog = document.getElementById("catalog-link-dialog");
  const linkConfirm = document.getElementById("catalog-link-confirm");
  const csrfToken = seriesDialog.querySelector("[name=csrfmiddlewaretoken]").value;

  let series = null; // {name, tid} of the series shown in the dialog
  let options = {}; // those of the open() that shows it
  // Bumped by every request the dialog makes and when it closes: an answer that arrives for an older
  // request (or once the dialog is closed) is dropped.
  let seriesRequest = 0;
  // The variants of the series shown, by IVID: a card is rebuilt from them after a link.
  const shownVariants = new Map();
  const picked = new Set(); // the IVIDs checked for an import batch
  let batchBusy = false; // the batch is being created

  // MyComicShop's series id: the digits typed, or the TID of a pasted URL ("…/search?TID=57389539").
  function seriesIdFrom(value) {
    if (/^\d{1,10}$/.test(value)) return value;
    const match = value.match(/[?&]TID=(\d{1,10})(?!\d)/i);
    return match ? match[1] : "";
  }

  function showSeriesStatus(text, isError = false) {
    seriesStatus.textContent = text;
    seriesStatus.classList.toggle("catalog-import__status--error", isError);
  }

  const seriesRadios = () => [...seriesIssues.querySelectorAll("input[type=radio]")];
  const chosenRadio = () => seriesRadios().find((radio) => radio.checked) || null;

  // The chosen variant, else the first one that can be chosen or checked, else Cancel.
  function focusInDialog() {
    const target =
      chosenRadio() ||
      seriesRadios().find((radio) => !radio.disabled) ||
      seriesIssues.querySelector("input[data-pick]") ||
      seriesDialog.querySelector("[data-bs-dismiss=modal]");
    target.focus();
  }

  // "Import selected (N)": disabled with nothing checked, more than a batch takes (with a hint
  // saying so) or while the batch is being created.
  function updateBatch() {
    const count = picked.size;
    const tooMany = count > config.maxBatchRows;
    seriesBatchText.textContent = interpolate(gettext("Import selected (%(count)s)"), { count }, true);
    seriesBatchHint.textContent = tooMany
      ? interpolate(gettext("A batch can have at most %(count)s variants."), { count: config.maxBatchRows }, true)
      : "";
    seriesBatchHint.hidden = !tooMany;
    seriesBatch.disabled = !series || count === 0 || tooMany || batchBusy;
  }

  function chip(text, extraClass = "") {
    const element = document.createElement("span");
    element.className = `md-assist-chip ${extraClass}`.trim();
    element.textContent = text;
    return element;
  }

  // A probable match of a variant (an edition with the same number and letter, no IVID yet): its
  // thumbnail, a link to it (new tab) and "Link", which stores the variant's IVID on it. Outside the
  // card's label, so neither the link nor the button picks the variant.
  function probableBlock(variant, edition) {
    const block = document.createElement("div");
    block.className = "catalog-series__probable";
    if (edition.thumb_url) {
      const image = document.createElement("img");
      image.className = "catalog-series__probable-thumb";
      image.loading = "lazy";
      image.decoding = "async";
      image.alt = "";
      image.src = edition.thumb_url;
      block.append(image);
    }
    const link = document.createElement("a");
    link.className = "catalog-series__probable-link";
    link.href = edition.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = edition.label;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "md-btn md-btn--text catalog-series__probable-button";
    const icon = document.createElement("span");
    icon.className = "material-symbols-outlined";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "link";
    button.append(icon, document.createTextNode(` ${gettext("Link")}`));
    button.title = interpolate(gettext("Mark this MyComicShop cover as %(edition)s"), { edition: edition.label }, true);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      linkVariant(variant, edition, button);
    });
    block.append(link, button);
    return block;
  }

  // ---------- Link confirmation (a dialog on top of the series dialog) ----------

  // Bootstrap gives every modal the same z-index (dialog 1055, backdrop 1050): the confirmation goes
  // one level higher, with its backdrop just below it (as publishing_dialogs.js stacks its dialogs).
  const MODAL_Z = 1055;
  const LEVEL_STEP = 20;
  let linkAnswer = null; // resolves the open confirmation once it is hidden
  let linkConfirmed = false; // "Link" was pressed (Cancel, Escape: false)

  function showLinkImage(id, url) {
    const image = document.getElementById(id);
    image.hidden = !url;
    if (url) image.src = url;
    else image.removeAttribute("src");
  }

  // Ask whether the variant is the edition, showing both covers. Resolves to true or false.
  function confirmLink(variant, edition) {
    if (!linkDialog) return Promise.resolve(false);
    showLinkImage("catalog-link-source-image", variant.thumb_url);
    showLinkImage("catalog-link-target-image", edition.thumb_url);
    document.getElementById("catalog-link-source").textContent = `#${variant.label}`;
    document.getElementById("catalog-link-target").textContent = edition.label;
    document.getElementById("catalog-link-text").textContent = interpolate(
      gettext("Mark the MyComicShop cover #%(label)s as %(edition)s? Its MyComicShop ID is saved on that edition."),
      { label: variant.label, edition: edition.label },
      true
    );
    return new Promise((resolve) => {
      linkAnswer = resolve;
      linkConfirmed = false;
      linkDialog.style.zIndex = String(MODAL_Z + LEVEL_STEP);
      bootstrap.Modal.getOrCreateInstance(linkDialog).show();
      // Bootstrap appends the new backdrop synchronously in show().
      const backdrops = document.querySelectorAll(".modal-backdrop");
      if (backdrops.length > 1) backdrops[backdrops.length - 1].style.zIndex = String(MODAL_Z - 5 + LEVEL_STEP);
    });
  }

  if (linkDialog) {
    linkConfirm.addEventListener("click", () => {
      linkConfirmed = true;
      bootstrap.Modal.getOrCreateInstance(linkDialog).hide();
    });
    linkDialog.addEventListener("shown.bs.modal", () => linkConfirm.focus());
    // The answer is given once the dialog is gone and the series dialog has its focus trap back, so
    // whatever the caller focuses next is not taken away by the trap.
    linkDialog.addEventListener("hidden.bs.modal", () => {
      linkDialog.style.zIndex = "";
      if (seriesDialog.classList.contains("show")) {
        // Closing a modal makes Bootstrap unlock the page and drop its focus trap, but the series
        // dialog is still open: take both back (the trap is Bootstrap's own, re-armed).
        document.body.classList.add("modal-open");
        const instance = bootstrap.Modal.getInstance(seriesDialog);
        const trap = instance && instance._focustrap;
        if (trap && typeof trap.activate === "function") {
          trap.deactivate();
          trap.activate();
        }
      }
      const resolve = linkAnswer;
      linkAnswer = null;
      if (resolve) resolve(linkConfirmed);
    });
  }

  // Store the variant's IVID on the probable edition (after the user confirms) and show the card as
  // already in Comi. Errors go to the dialog's status line.
  async function linkVariant(variant, edition, button) {
    if (button.disabled) return;
    const confirmed = await confirmLink(variant, edition);
    if (!confirmed || !document.contains(button)) {
      if (document.contains(button)) button.focus();
      return;
    }
    const request = seriesRequest; // not bumped: an import or a reload in progress keeps going
    const body = new FormData();
    body.append("csrfmiddlewaretoken", csrfToken);
    body.append("edition", String(edition.id));
    body.append("ivid", String(variant.ivid));
    // The series' TID: stored on the edition's publishing when it has none yet.
    if (series && series.tid) body.append("tid", String(series.tid));
    button.disabled = true;
    try {
      const response = await fetch(config.linkUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const answer = await response.json().catch(() => null);
      if (request !== seriesRequest) return; // the dialog was closed or shows another series
      if (!response.ok || !answer || !answer.existing) {
        showSeriesStatus((answer && answer.error) || gettext("The cover could not be linked. Try again."), true);
        return;
      }
      showLinked(variant, answer.existing);
      if (options.onLinked) options.onLinked(variant.ivid, answer.existing);
      // The button is gone with the old card: focus goes to the link of the edition it now is.
      const linked = seriesIssues.querySelector(`.catalog-series__card[data-ivid="${variant.ivid}"] .catalog-series__existing`);
      if (linked) linked.focus();
      showSeriesStatus(interpolate(gettext("Linked: %(edition)s is this cover now."), { edition: answer.existing.label }, true));
    } catch (error) {
      if (request === seriesRequest) showSeriesStatus(gettext("The cover could not be linked. Check your connection and try again."), true);
    } finally {
      button.disabled = false;
    }
  }

  // After a link: the variant's card turns into "Already in Comi" (and leaves the batch selection),
  // and the edition is no longer a probable match of any other variant (it has an IVID now).
  function showLinked(variant, existing) {
    picked.delete(variant.ivid);
    shownVariants.forEach((other) => {
      const linked = other.ivid === variant.ivid;
      const probable = linked ? [] : (other.probable || []).filter((item) => item.id !== existing.id);
      if (!linked && probable.length === (other.probable || []).length) return;
      const updated = { ...other, probable, existing: linked ? existing : other.existing };
      const card = seriesIssues.querySelector(`.catalog-series__card[data-ivid="${other.ivid}"]`);
      if (!card) return;
      const oldRadio = card.querySelector("input[type=radio]");
      const wasChosen = Boolean(oldRadio && oldRadio.checked);
      const replacement = variantCard(updated);
      card.replaceWith(replacement);
      const radio = replacement.querySelector("input[type=radio]");
      if (radio) radio.checked = wasChosen && !radio.disabled;
    });
    seriesImport.disabled = !chosenRadio() || Boolean(seriesImport.dataset.busy);
    updateBatch();
  }

  // The checkbox that puts a variant (not in Comi yet) in the import batch.
  function pickBox(variant) {
    const wrapper = document.createElement("div");
    wrapper.className = "form-check catalog-series__pick";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "form-check-input";
    box.id = `catalog-series-pick-${variant.ivid}`;
    box.value = String(variant.ivid);
    box.dataset.pick = "";
    box.checked = picked.has(variant.ivid);
    const label = document.createElement("label");
    label.className = "form-check-label";
    label.htmlFor = box.id;
    label.textContent = gettext("Add to the batch");
    const which = document.createElement("span");
    which.className = "visually-hidden";
    which.textContent = ` #${variant.label}`;
    label.append(which);
    wrapper.append(box, label);
    return wrapper;
  }

  // A variant's card. Its label holds the cover and the details: with `fill`, it is a radio
  // (disabled when Comi already has the variant); without it, it checks the variant's checkbox.
  // The variants already in Comi link to their edition and have no checkbox; the others show their
  // probable editions with their "Link".
  function variantCard(variant) {
    shownVariants.set(variant.ivid, variant);
    const fills = Boolean(options.fill);
    const probable = variant.existing ? [] : variant.probable || [];
    const card = document.createElement("div");
    card.className = "catalog-series__card";
    card.dataset.ivid = String(variant.ivid);
    const choice = document.createElement("label");
    choice.className = "catalog-series__choice";
    const pick = variant.existing ? null : pickBox(variant);
    let radio = null;
    if (fills) {
      radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "catalog-series-variant";
      radio.value = String(variant.ivid);
      radio.className = "visually-hidden";
      radio.disabled = Boolean(variant.existing);
    } else if (pick) {
      choice.htmlFor = pick.querySelector("input").id;
    }

    const thumb = document.createElement("span");
    thumb.className = "catalog-series__thumb";
    if (variant.thumb_url) {
      const image = document.createElement("img");
      image.loading = "lazy";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.alt = interpolate(gettext("Cover #%(label)s"), { label: variant.label }, true);
      image.src = variant.thumb_url;
      thumb.append(image);
    } else {
      const icon = document.createElement("span");
      icon.className = "material-symbols-outlined";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "image";
      thumb.append(icon);
    }

    const name = document.createElement("span");
    name.className = "catalog-series__name";
    name.textContent = variant.variant || variant.label;
    name.title = `#${variant.label}`;

    const facts = document.createElement("span");
    facts.className = "catalog-series__facts";
    facts.textContent = [variant.date, variant.price ? `$${variant.price}` : ""].filter(Boolean).join(" · ");

    const chips = document.createElement("span");
    chips.className = "catalog-series__chips";
    if (variant.existing) chips.append(chip(gettext("Already in Comi"), "md-assist-chip--primary"));
    probable.forEach((edition) => {
      chips.append(chip(interpolate(gettext("Possibly in Comi: %(cover)s"), { cover: edition.cover }, true), "catalog-series__probable-chip"));
    });
    if (variant.ratio) chips.append(chip(variant.ratio));
    if (variant.limited_to) chips.append(chip(interpolate(gettext("Limited to %(count)s"), { count: variant.limited_to }, true)));
    if (variant.printing) chips.append(chip(interpolate(gettext("Printing: %(printing)s"), { printing: variant.printing }, true)));
    if (variant.retailer_exclusive) {
      chips.append(chip(interpolate(gettext("Store: %(name)s"), { name: variant.retailer_exclusive }, true)));
    }
    if (variant.event_exclusive) {
      chips.append(chip(interpolate(gettext("Event: %(name)s"), { name: variant.event_exclusive }, true)));
    }

    if (radio) choice.append(radio);
    choice.append(thumb, name);
    if (facts.textContent) choice.append(facts);
    if (chips.childElementCount) choice.append(chips);
    if (variant.note) {
      const note = document.createElement("span");
      note.className = "catalog-series__note";
      note.textContent = variant.note;
      note.title = variant.note;
      choice.append(note);
    }
    if (radio) {
      // A double click picks the variant and imports it.
      choice.addEventListener("dblclick", () => {
        if (radio.disabled) return;
        radio.checked = true;
        importChosen();
      });
    }
    card.append(choice);
    if (pick) {
      card.append(pick);
      card.classList.toggle("is-picked", picked.has(variant.ivid));
    }

    if (variant.existing) {
      card.classList.add("is-existing");
      const link = document.createElement("a");
      link.className = "catalog-series__existing";
      link.href = variant.existing.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = variant.existing.label;
      card.append(link);
    }
    probable.forEach((edition) => card.append(probableBlock(variant, edition)));
    return card;
  }

  function renderSeries(body) {
    const info = body.series;
    const details = [info.year, info.publisher].filter(Boolean).join(", ");
    seriesTitle.textContent = details ? `${info.title} (${details})` : info.title;
    seriesLink.hidden = !(info.url && info.url.startsWith("https://"));
    if (!seriesLink.hidden) {
      seriesLink.href = info.url;
      seriesLinkText.textContent = interpolate(gettext("Open in %(source)s"), { source: series.name }, true);
    }
    seriesPublishing.textContent = info.publishing
      ? interpolate(gettext("Publishing in Comi: %(name)s"), { name: info.publishing.label }, true)
      : gettext("No publishing in Comi matches this series, so the covers it already has without a MyComicShop ID are not detected.");
    seriesPublishing.hidden = false;
    shownVariants.clear();
    const groups = body.issues.map((issue) => {
      const group = document.createElement("fieldset");
      group.className = "catalog-series__issue";
      const legend = document.createElement("legend");
      legend.className = "md-title-small";
      legend.textContent = `#${issue.number}`;
      const grid = document.createElement("div");
      grid.className = "catalog-series__grid";
      grid.append(...issue.variants.map(variantCard));
      group.append(legend, grid);
      return group;
    });
    seriesIssues.replaceChildren(...groups);
    const variants = body.issues.flatMap((issue) => issue.variants);
    const count = variants.length;
    const available = variants.filter((variant) => !variant.existing).length;
    if (!count) showSeriesStatus(gettext("The catalogue lists no covers for this series."), true);
    else if (!available) showSeriesStatus(gettext("Every cover of this series is already in Comi."));
    else if (options.fill) {
      showSeriesStatus(
        interpolate(ngettext("%(count)s cover. Choose the one to import.", "%(count)s covers. Choose the one to import.", count), { count }, true)
      );
    } else {
      showSeriesStatus(
        interpolate(ngettext("%(count)s cover. Select the ones to import.", "%(count)s covers. Select the ones to import.", count), { count }, true)
      );
    }
    updateBatch();
    if (seriesDialog.classList.contains("show")) focusInDialog();
  }

  // Show the variants of the series `tid` (see the header for `opts`).
  async function open(tid, opts = {}) {
    const request = ++seriesRequest;
    options = opts;
    series = { name: config.sourceName, tid };
    picked.clear();
    // batchBusy is not reset here: a batch still being created from a previous opening keeps the
    // button disabled until createBatch ends, so the same selection cannot be posted twice.
    seriesTitle.textContent = seriesDefaultTitle;
    seriesLink.hidden = true;
    seriesPublishing.hidden = true;
    seriesIssues.replaceChildren();
    seriesImport.hidden = !options.fill;
    seriesImport.disabled = true;
    updateBatch();
    showSeriesStatus(gettext("Loading…"));
    bootstrap.Modal.getOrCreateInstance(seriesDialog).show();
    try {
      const url = new URL(config.seriesUrl, window.location.origin);
      url.searchParams.set("id", tid);
      const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const body = await response.json().catch(() => null);
      if (request !== seriesRequest) return;
      if (!response.ok || !body || !body.series) {
        showSeriesStatus((body && body.error) || gettext("The catalogue could not be read. Try again."), true);
        return;
      }
      renderSeries(body);
    } catch (error) {
      if (request === seriesRequest) showSeriesStatus(gettext("The catalogue could not be read. Try again."), true);
    }
  }

  // Load the chosen variant through `fill` and, once loaded, close the dialog and apply it.
  async function importChosen() {
    const fill = options.fill;
    const radio = chosenRadio();
    if (!fill || !radio || radio.disabled || !series || seriesImport.dataset.busy) return;
    const request = ++seriesRequest;
    seriesImport.dataset.busy = "true";
    seriesImport.disabled = true;
    showSeriesStatus(gettext("Loading…"));
    const answer = await fill.load(radio.value, series.tid);
    delete seriesImport.dataset.busy;
    seriesImport.disabled = !chosenRadio();
    if (request !== seriesRequest) return; // the dialog was closed meanwhile
    if (answer.error) {
      showSeriesStatus(answer.error, true);
      seriesImport.disabled = false;
      return;
    }
    bootstrap.Modal.getOrCreateInstance(seriesDialog).hide();
    await fill.apply(answer.value);
  }

  // Make an import batch of the checked variants and open its page. Errors (an invalid selection,
  // a variant the series no longer lists or an edition already has, MyComicShop failing) go to the
  // dialog's status line.
  async function createBatch() {
    if (seriesBatch.disabled) return;
    const request = seriesRequest; // not bumped: a link in progress keeps going
    const body = new FormData();
    body.append("csrfmiddlewaretoken", csrfToken);
    body.append("tid", String(series.tid));
    picked.forEach((ivid) => body.append("ivids", String(ivid)));
    batchBusy = true;
    updateBatch();
    showSeriesStatus(gettext("Creating the import batch…"));
    try {
      const response = await fetch(config.batchUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const answer = await response.json().catch(() => null);
      // Only a page of this site is opened ("//host/..." also starts with "/", so compare origins).
      const target = answer && typeof answer.url === "string" ? new URL(answer.url, window.location.origin) : null;
      if (response.ok && target && target.origin === window.location.origin) {
        // The batch exists now: open it even if the dialog was closed meanwhile (the button stays
        // disabled while the page leaves).
        window.location.assign(target.href);
        return;
      }
      if (request === seriesRequest) {
        showSeriesStatus((answer && answer.error) || gettext("The import batch could not be created. Try again."), true);
      }
    } catch (error) {
      if (request === seriesRequest) {
        showSeriesStatus(gettext("The import batch could not be created. Check your connection and try again."), true);
      }
    }
    batchBusy = false;
    updateBatch();
  }

  seriesIssues.addEventListener("change", (event) => {
    const box = event.target;
    if (box.matches("input[data-pick]")) {
      const ivid = Number(box.value);
      if (box.checked) picked.add(ivid);
      else picked.delete(ivid);
      box.closest(".catalog-series__card").classList.toggle("is-picked", box.checked);
      updateBatch();
      return;
    }
    seriesImport.disabled = !chosenRadio() || Boolean(seriesImport.dataset.busy);
  });
  // Enter on a variant imports it (Space checks it, arrows move between the variants).
  seriesIssues.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !event.target.matches("input[type=radio]")) return;
    event.preventDefault();
    event.target.checked = true;
    importChosen();
  });
  seriesImport.addEventListener("click", importChosen);
  seriesBatch.addEventListener("click", createBatch);
  seriesDialog.addEventListener("shown.bs.modal", focusInDialog);
  seriesDialog.addEventListener("hidden.bs.modal", () => {
    seriesRequest += 1;
    if (options.returnFocus) options.returnFocus.focus();
  });

  window.ComiSeriesDialog = { open, seriesIdFrom };
})();
