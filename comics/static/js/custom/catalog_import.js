// Import box of the Gestión edition form: loads an issue from Metron, Comic Vine or MyComicShop
// (through comics.views.manage_editions.catalog_lookup) and fills the form with it. MyComicShop is
// loaded by series (its TID, or a pasted URL holding it): its variants are shown in a dialog
// (catalog_mycomicshop_series, includes/catalog_series_dialog.html), the user picks one and that
// variant is looked up and filled in like a Metron or Comic Vine issue. A variant that is probably
// an edition Comi already has (same number and letter, no MyComicShop ID yet) can be linked to it
// from its card (catalog_mycomicshop_link), which saves that ID on it. The import itself saves
// nothing: the user reviews the values and saves the form. The imported cover is only a URL (a hidden
// field); the server downloads it when the form is saved. When the publishing is not in Comi, the
// "Create publishing" dialog (publishing_dialogs.js) is prefilled with the catalogue's series. When
// the publisher matched no editorial, the summary offers to pick an existing one (and remember the
// name as its alias). Each artist the catalogue credits and Comi does not have can be created from
// the summary (only on the user's click): it is then checked where the catalogue credits it, the
// cover artists or the issue's creators. Every text from the catalogue goes into the page with
// textContent / form values, never as HTML.
(function () {
  "use strict";

  const loadButton = document.getElementById("catalog-load");
  if (!loadButton) return; // not the edition form

  const data = JSON.parse(document.getElementById("edition-form-data").textContent);
  const editionForm = window.ComiEditionForm;
  const publishingDialogs = window.ComiPublishingDialogs;
  const sourceSelect = document.getElementById("catalog-source");
  const idInput = document.getElementById("catalog-id");
  const status = document.getElementById("catalog-status");
  const result = document.getElementById("catalog-result");
  const variantRow = document.getElementById("catalog-variant-row");
  const variantSelect = document.getElementById("catalog-variant");
  const summary = document.getElementById("catalog-summary");
  const sourceLink = document.getElementById("catalog-source-link");
  const sourceLinkText = document.getElementById("catalog-source-link-text");
  const idLabel = document.querySelector('label[for="catalog-id"]');

  const seriesDialog = document.getElementById("catalog-series-dialog");
  const seriesTitle = document.getElementById("catalog-series-title");
  const seriesLink = document.getElementById("catalog-series-link");
  const seriesLinkText = document.getElementById("catalog-series-link-text");
  const seriesPublishing = document.getElementById("catalog-series-publishing");
  const seriesStatus = document.getElementById("catalog-series-status");
  const seriesIssues = document.getElementById("catalog-series-issues");
  const seriesImport = document.getElementById("catalog-series-import");
  const seriesDefaultTitle = seriesTitle.textContent;

  const coverUrlInput = document.getElementById("id_catalog_cover_url");
  const coverPreview = document.getElementById("cover-preview");
  const coverPlaceholder = document.getElementById("cover-placeholder");
  const coverFileName = document.getElementById("cover-file-name");
  const coverDiscard = document.getElementById("catalog-cover-discard");
  const imageInput = document.querySelector("input[type=file][name=image]");
  const originalCover = { src: coverPreview.getAttribute("src"), hidden: coverPreview.hidden };

  const csrfToken = document.querySelector("#edition-form [name=csrfmiddlewaretoken]").value;

  let proposal = null;
  let currentVariant = null; // the cover the form was filled with: null (main cover) or a variant
  // Artists created from the summary, by the name the catalogue gives them: from then on they count
  // as matched, also when another cover is chosen.
  const createdArtists = new Map();
  // Cover values only some catalogues give (MyComicShop). The ones the previous import filled in and
  // the current one does not have are emptied, so importing another cover leaves nothing stale.
  const COVER_FIELDS = ["limited_to", "retailer_exclusive", "event_exclusive", "mycomicshop_id"];
  let filledCoverFields = new Set();

  // ---------- Helpers ----------

  function showStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("catalog-import__status--error", isError);
  }

  function setField(id, value) {
    const field = document.getElementById(id);
    if (!field || value === undefined || value === null || value === "") return;
    if (field.tagName === "SELECT" && ![...field.options].some((option) => option.value === String(value))) return;
    field.value = String(value);
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }

  function clearField(id) {
    const field = document.getElementById(id);
    if (!field || field.value === "") return;
    field.value = "";
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // Check exactly `artists` in an artist picker (see edition_form.js) and let it refresh.
  function setArtists(fieldName, artists) {
    const boxes = document.querySelectorAll(`input[type=checkbox][name="${fieldName}"]`);
    if (!boxes.length) return;
    const wanted = new Set(artists.map((artist) => String(artist.id)));
    boxes.forEach((box) => {
      box.checked = wanted.has(box.value);
    });
    boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setCover(url, sourceName) {
    if (!url) {
      discardCover();
      return;
    }
    coverUrlInput.value = url;
    coverPreview.referrerPolicy = "no-referrer";
    coverPreview.src = url;
    coverPreview.hidden = false;
    if (coverPlaceholder) coverPlaceholder.hidden = true;
    coverFileName.textContent = sourceName
      ? interpolate(gettext("Cover from %(source)s (downloaded when you save)"), { source: sourceName }, true)
      : gettext("Imported cover (downloaded when you save)");
    coverFileName.hidden = false;
    coverDiscard.hidden = false;
  }

  function discardCover() {
    if (!coverUrlInput.value) return;
    coverUrlInput.value = "";
    if (originalCover.src) coverPreview.src = originalCover.src;
    else coverPreview.removeAttribute("src");
    coverPreview.hidden = originalCover.hidden;
    if (coverPlaceholder) coverPlaceholder.hidden = !originalCover.hidden;
    coverFileName.hidden = true;
    coverDiscard.hidden = true;
  }

  coverDiscard.addEventListener("click", discardCover);
  // A form sent back with errors still carries the imported cover: show it again.
  if (coverUrlInput.value) setCover(coverUrlInput.value, "");
  // Unsaved changes restored by form_draft.js may carry one too.
  coverUrlInput.addEventListener("change", () => {
    if (coverUrlInput.value) setCover(coverUrlInput.value, "");
  });
  // An uploaded file wins over the imported cover (the server ignores the URL then too).
  if (imageInput) {
    imageInput.addEventListener("change", () => {
      coverUrlInput.value = "";
      coverDiscard.hidden = true;
    });
  }

  // ---------- Summary of what matched and what did not ----------

  function summaryItem(icon, text, tone) {
    const item = document.createElement("li");
    item.className = `catalog-import__item catalog-import__item--${tone}`;
    const symbol = document.createElement("span");
    symbol.className = "material-symbols-outlined";
    symbol.setAttribute("aria-hidden", "true");
    symbol.textContent = icon;
    const label = document.createElement("span");
    label.textContent = text;
    item.append(symbol, label);
    summary.append(item);
    return item;
  }

  // The matched artists plus the ones created since, and the names still missing.
  function resolveArtists(matched, unmatched) {
    const artists = [...matched];
    const missing = [];
    for (const name of unmatched) {
      const created = createdArtists.get(name);
      if (!created) missing.push(name);
      else if (!artists.some((artist) => artist.id === created.id)) artists.push(created);
    }
    return { artists, missing };
  }

  const coverArtistsOf = (variant) =>
    variant
      ? resolveArtists(variant.cover_artists, variant.unmatched_cover_artists)
      : resolveArtists(proposal.cover_artists, proposal.unmatched.cover_artists);
  const creatorsOf = () => resolveArtists(proposal.creators, proposal.unmatched.creators);

  function errorMessages(answer) {
    return answer ? [...Object.values(answer.errors || {}).flat(), ...(answer.non_field_errors || [])] : [];
  }

  // Create the artists `names` (or find them, when Comi has them by now) and check each one where
  // the catalogue credits it: the cover artists of the current cover and/or the issue's creators.
  async function createArtists(names, trigger) {
    if (!names.length) return;
    const coverMissing = new Set(coverArtistsOf(currentVariant).missing);
    const creatorsMissing = new Set(creatorsOf().missing);
    const body = new FormData();
    body.append("csrfmiddlewaretoken", csrfToken);
    names.forEach((name) => body.append("new_artist-name", name));
    trigger.disabled = true;
    try {
      const response = await fetch(data.artistsCreateUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const answer = await response.json().catch(() => null);
      if (!response.ok || !answer || !Array.isArray(answer.artists)) {
        showStatus(
          errorMessages(answer).join(" ") || gettext("The artists could not be created. Check your session and try again."),
          true
        );
        return;
      }
      // The answer lists one artist per name sent, in the same order.
      const artists = answer.artists.map((item) => ({ id: item.id, name: item.name }));
      editionForm.addArtists(artists);
      artists.forEach((artist, index) => {
        const name = names[index];
        createdArtists.set(name, artist);
        if (coverMissing.has(name)) editionForm.checkArtist("cover_artists", artist.id);
        if (creatorsMissing.has(name)) editionForm.checkArtist("issue_data-creators", artist.id);
      });
      showStatus(
        interpolate(gettext("Added to the form: %(names)s."), { names: artists.map((artist) => artist.name).join(", ") }, true)
      );
      renderSummary();
      (summary.querySelector("button") || idInput).focus();
    } catch (error) {
      showStatus(gettext("The artists could not be created. Check your connection and try again."), true);
    } finally {
      trigger.disabled = false;
    }
  }

  // "Not found" names, each with a "+" that creates it.
  function missingArtistsItem(text, names) {
    const item = summaryItem("person_off", text, "missing");
    const list = document.createElement("ul");
    list.className = "catalog-import__choices catalog-import__artists";
    for (const name of names) {
      const entry = document.createElement("li");
      entry.className = "catalog-import__artist";
      const label = document.createElement("span");
      label.textContent = name;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "md-icon-btn catalog-import__artist-add";
      const action = interpolate(gettext("Create artist %(name)s"), { name }, true);
      button.setAttribute("aria-label", action);
      button.title = action;
      const icon = document.createElement("span");
      icon.className = "material-symbols-outlined";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "add";
      button.append(icon);
      button.addEventListener("click", () => createArtists([name], button));
      entry.append(label, button);
      list.append(entry);
    }
    item.append(list);
  }

  // "Variant: RI.A · Ratio: 1:25 · …" for a proposal with cover values (MyComicShop); "" for the
  // catalogues whose issue is the original publication (Metron, Comic Vine).
  function coverValuesText(edition) {
    if (!edition.ratio && !COVER_FIELDS.some((name) => edition[name])) return "";
    const labels = [
      ["variant", gettext("Variant")],
      ["printing", gettext("Printing")],
      ["ratio", gettext("Ratio")],
      ["limited_to", gettext("Limited to")],
      ["retailer_exclusive", gettext("Store (exclusive)")],
      ["event_exclusive", gettext("Event (exclusive)")],
      ["mycomicshop_id", gettext("MyComicShop ID")],
    ];
    return labels
      .filter(([name]) => edition[name])
      .map(([name, label]) => `${label}: ${edition[name]}`)
      .join(" · ");
  }

  function renderSummary() {
    summary.replaceChildren();
    const unmatched = proposal.unmatched;
    if (proposal.publishing) {
      summaryItem("check_circle", interpolate(gettext("Publishing: %(name)s"), { name: proposal.publishing.label }, true), "ok");
    } else {
      const item = summaryItem(
        "help",
        interpolate(gettext("Publishing not found in Comi: %(name)s. Choose it or create it."), { name: unmatched.publishing }, true),
        "missing"
      );
      const choices = document.createElement("div");
      choices.className = "catalog-import__choices";
      if (proposal.new_publishing && publishingDialogs) {
        const create = document.createElement("button");
        create.type = "button";
        create.className = "md-btn md-btn--tonal";
        create.textContent = gettext("Create publishing");
        create.addEventListener("click", () => publishingDialogs.open(create));
        choices.append(create);
      }
      if (proposal.publishing_candidates.length) {
        for (const candidate of proposal.publishing_candidates) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "md-btn md-btn--text";
          button.textContent = interpolate(gettext("Use %(name)s"), { name: candidate.label }, true);
          button.addEventListener("click", () => setField("id_publishing", candidate.id));
          choices.append(button);
        }
      }
      if (choices.childElementCount) item.append(choices);
    }
    if (proposal.issue) {
      summaryItem("check_circle", interpolate(gettext("Issue already in Comi: %(name)s"), { name: proposal.issue.label }, true), "ok");
    } else {
      summaryItem("add_circle", gettext("Issue not in Comi yet: it is created when you save."), "ok");
    }
    const coverValues = coverValuesText(proposal.edition);
    if (coverValues) summaryItem("style", interpolate(gettext("Cover: %(values)s"), { values: coverValues }, true), "ok");
    if (unmatched.editorial) {
      const item = summaryItem(
        "help",
        interpolate(gettext("Editorial not found in Comi: %(name)s"), { name: unmatched.editorial }, true),
        "missing"
      );
      if (publishingDialogs) {
        const choices = document.createElement("div");
        choices.className = "catalog-import__choices";
        choices.append(publishingDialogs.aliasChooser(unmatched.editorial));
        item.append(choices);
      }
    } else if (proposal.editorial) {
      summaryItem("check_circle", interpolate(gettext("Editorial: %(name)s"), { name: proposal.editorial.name }, true), "ok");
    }
    const missingCreators = creatorsOf().missing;
    if (missingCreators.length) {
      missingArtistsItem(gettext("Creators not found in Comi (create them with +):"), missingCreators);
    }
    const missingCover = coverArtistsOf(currentVariant).missing;
    if (missingCover.length) {
      missingArtistsItem(gettext("Cover artists not found in Comi (create them with +):"), missingCover);
    }
    const missing = [...new Set([...missingCreators, ...missingCover])];
    if (missing.length) {
      const item = document.createElement("li");
      item.className = "catalog-import__item";
      const all = document.createElement("button");
      all.type = "button";
      all.className = "md-btn md-btn--tonal";
      const icon = document.createElement("span");
      icon.className = "material-symbols-outlined";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "group_add";
      all.append(icon, document.createTextNode(` ${gettext("Create all missing artists")}`));
      all.addEventListener("click", () => createArtists(missing, all));
      item.append(all);
      summary.append(item);
    }
    for (const warning of proposal.warnings) summaryItem("warning", warning, "warning");
  }

  // An existing editorial was chosen for the publisher (publishing_dialogs.js aliasChooser).
  document.addEventListener("comi:editorial-chosen", (event) => {
    if (!proposal) return;
    const chosen = event.detail;
    proposal.editorial = { id: Number(chosen.id), name: chosen.name };
    proposal.unmatched.editorial = "";
    renderSummary();
    // The summary's chooser (which had the focus) is gone: keep the focus in the summary, unless the
    // choice was made in the publishing dialog, still open.
    if (!document.querySelector(".modal.show")) (summary.querySelector("button") || idInput).focus();
    showStatus(
      chosen.remembered
        ? interpolate(gettext("“%(source)s” is now an alias of %(editorial)s: next imports find it."), { source: chosen.sourceName, editorial: chosen.name }, true)
        : interpolate(gettext("Editorial chosen: %(editorial)s."), { editorial: chosen.name }, true)
    );
  });

  // ---------- Filling the form ----------

  // The main cover (null) or one of the variants.
  function applyCover(variant) {
    currentVariant = variant;
    const edition = proposal.edition;
    setField("id_variant", variant ? variant.letter : edition.variant);
    setField("id_printing", variant ? variant.printing : edition.printing);
    const ratio = document.getElementById("id_ratio");
    ratio.value = variant ? variant.ratio : edition.ratio || "";
    ratio.dispatchEvent(new Event("input", { bubbles: true }));
    setField("id_cover_price", variant && variant.cover_price !== null ? variant.cover_price : edition.cover_price);
    const cover = coverArtistsOf(variant);
    if (cover.artists.length || cover.missing.length) setArtists("cover_artists", cover.artists);
    setCover(variant ? variant.cover_url : proposal.cover_url, proposal.source_name);
    renderSummary();
  }

  function renderVariants() {
    const options = [new Option(gettext("A · Main cover"), "")];
    proposal.variants.forEach((variant, index) => {
      options.push(new Option(`${variant.letter} · ${variant.name}`, String(index)));
    });
    variantSelect.replaceChildren(...options);
    variantRow.hidden = proposal.variants.length === 0;
  }

  async function apply(loaded) {
    proposal = loaded;
    currentVariant = null;
    const edition = proposal.edition;
    editionForm.useSingleIssue();
    if (proposal.publishing) setField("id_publishing", proposal.publishing.id);
    if (proposal.new_publishing && publishingDialogs) publishingDialogs.prefill(proposal.new_publishing);
    setField("id_number", edition.number);
    setField("id_format", edition.format);
    setField("id_release_date", edition.release_date);
    for (const name of COVER_FIELDS) {
      if (edition[name]) setField(`id_${name}`, edition[name]);
      else if (filledCoverFields.has(name)) clearField(`id_${name}`);
    }
    filledCoverFields = new Set(COVER_FIELDS.filter((name) => edition[name]));
    setField("id_issue_data-synopsis", proposal.synopsis);
    for (const [name, value] of Object.entries(proposal.identifiers)) setField(`id_issue_data-${name}`, value);
    const creators = creatorsOf();
    if (creators.artists.length || creators.missing.length) setArtists("issue_data-creators", creators.artists);

    renderVariants();
    applyCover(null);

    // The issue it already is in Comi: its own issue, or one of another series (then linked to it).
    // When there is none, the issue chosen by hand (if any) is kept.
    const issue = proposal.issue;
    if (issue) {
      const own = proposal.publishing && issue.publishing_id === proposal.publishing.id && issue.number === edition.number;
      await editionForm.selectIssue(issue.publishing_id, own ? null : issue.id);
    }

    sourceLink.hidden = !proposal.source_url;
    if (proposal.source_url) {
      sourceLink.href = proposal.source_url;
      sourceLinkText.textContent = interpolate(gettext("Open in %(source)s"), { source: proposal.source_name }, true);
    }
    result.hidden = false;
    showStatus(interpolate(gettext("Loaded from %(source)s. Check the values, then save."), { source: proposal.source_name }, true));
  }

  variantSelect.addEventListener("change", () => {
    const index = variantSelect.value;
    applyCover(index === "" ? null : proposal.variants[Number(index)]);
  });

  // ---------- Loading ----------

  // {proposal} or {error} (a message for the user) for the catalogue lookup with `params`.
  async function fetchProposal(params) {
    const failed = { error: gettext("The catalogue could not be read. Try again.") };
    try {
      const url = new URL(data.catalogUrl, window.location.origin);
      for (const [name, value] of Object.entries(params)) url.searchParams.set(name, value);
      const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body || !body.proposal) return body && body.error ? { error: body.error } : failed;
      return { proposal: body.proposal };
    } catch (error) {
      return failed;
    }
  }

  // Whether the chosen source is loaded by series (MyComicShop): the id is then the series'.
  function bySeries() {
    const option = sourceSelect.selectedOptions[0];
    return Boolean(option && option.hasAttribute("data-requires-series"));
  }

  // The id input asks for an issue id or, for a source loaded by series, the series id (a pasted
  // URL is accepted too, so it may be longer and not only digits).
  function syncIdInput() {
    const series = bySeries();
    const label = series ? idInput.dataset.seriesLabel : idInput.dataset.issueLabel;
    idLabel.textContent = label;
    idInput.placeholder = label;
    idInput.inputMode = series ? "text" : "numeric";
    idInput.maxLength = series ? 500 : 20;
  }

  // MyComicShop's series id: the digits typed, or the TID of a pasted URL ("…/search?TID=57389539").
  function seriesIdFrom(value) {
    if (/^\d{1,10}$/.test(value)) return value;
    const match = value.match(/[?&]TID=(\d{1,10})(?!\d)/i);
    return match ? match[1] : "";
  }

  async function load() {
    // A lookup is still running (Enter in the id input does not check the button): let it finish
    // first, or its late answer would overwrite whatever the next import fills in.
    if (loadButton.disabled) return;
    const id = idInput.value.trim();
    if (bySeries()) {
      const tid = seriesIdFrom(id);
      if (!tid) {
        showStatus(gettext("Enter the series ID (TID) or paste its MyComicShop URL."), true);
        idInput.focus();
        return;
      }
      loadSeries(tid);
      return;
    }
    if (!id) {
      showStatus(gettext("Enter the issue's ID in the catalogue."), true);
      idInput.focus();
      return;
    }
    loadButton.disabled = true;
    showStatus(gettext("Loading…"));
    try {
      const answer = await fetchProposal({ source: sourceSelect.value, id });
      if (answer.error) showStatus(answer.error, true);
      else await apply(answer.proposal);
    } catch (error) {
      showStatus(gettext("The catalogue could not be read. Try again."), true);
    } finally {
      loadButton.disabled = false;
    }
  }

  // ---------- Series dialog (MyComicShop): pick the variant to import ----------

  let series = null; // {source, name, tid} of the series shown in the dialog
  // Bumped by every request the dialog makes and when it closes: an answer that arrives for an older
  // request (or once the dialog is closed) is dropped.
  let seriesRequest = 0;

  function showSeriesStatus(text, isError = false) {
    seriesStatus.textContent = text;
    seriesStatus.classList.toggle("catalog-import__status--error", isError);
  }

  const seriesRadios = () => [...seriesIssues.querySelectorAll("input[type=radio]")];
  const chosenRadio = () => seriesRadios().find((radio) => radio.checked) || null;

  // The chosen variant, else the first one that can be chosen, else Cancel.
  function focusInDialog() {
    const target =
      chosenRadio() ||
      seriesRadios().find((radio) => !radio.disabled) ||
      seriesDialog.querySelector("[data-bs-dismiss=modal]");
    target.focus();
  }

  function chip(text, extraClass = "") {
    const element = document.createElement("span");
    element.className = `md-assist-chip ${extraClass}`.trim();
    element.textContent = text;
    return element;
  }

  // The variants of the series shown, by IVID: a card is rebuilt from them after a link.
  const shownVariants = new Map();

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

  // Store the variant's IVID on the probable edition (after the user confirms) and show the card as
  // already in Comi. Errors go to the dialog's status line.
  async function linkVariant(variant, edition, button) {
    const question = interpolate(
      gettext("Mark the MyComicShop cover #%(label)s as %(edition)s? Its MyComicShop ID is saved on that edition."),
      { label: variant.label, edition: edition.label },
      true
    );
    if (button.disabled || !window.confirm(question)) return;
    const request = seriesRequest; // not bumped: an import or a reload in progress keeps going
    const body = new FormData();
    body.append("csrfmiddlewaretoken", csrfToken);
    body.append("edition", String(edition.id));
    body.append("ivid", String(variant.ivid));
    button.disabled = true;
    try {
      const response = await fetch(data.catalogLinkUrl, {
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
      // Linked to the edition open in the form: its MyComicShop ID field gets the IVID too, or
      // saving the form would write the old (empty) value back and drop the link.
      if (data.editionId && answer.existing.id === data.editionId) setField("id_mycomicshop_id", variant.ivid);
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

  // After a link: the variant's card turns into "Already in Comi", and the edition is no longer a
  // probable match of any other variant (it has an IVID now).
  function showLinked(variant, existing) {
    shownVariants.forEach((other) => {
      const linked = other.ivid === variant.ivid;
      const probable = linked ? [] : (other.probable || []).filter((item) => item.id !== existing.id);
      if (!linked && probable.length === (other.probable || []).length) return;
      const updated = { ...other, probable, existing: linked ? existing : other.existing };
      const card = seriesIssues.querySelector(`.catalog-series__card[data-ivid="${other.ivid}"]`);
      if (!card) return;
      const wasChosen = card.querySelector("input[type=radio]").checked;
      const replacement = variantCard(updated);
      card.replaceWith(replacement);
      const radio = replacement.querySelector("input[type=radio]");
      radio.checked = wasChosen && !radio.disabled;
    });
    seriesImport.disabled = !chosenRadio() || Boolean(seriesImport.dataset.busy);
  }

  // A variant's card: a radio (its label holds the cover and the details), disabled with a link to
  // the edition when Comi already has it; with the probable editions and their "Link" otherwise.
  function variantCard(variant) {
    shownVariants.set(variant.ivid, variant);
    const probable = variant.existing ? [] : variant.probable || [];
    const card = document.createElement("div");
    card.className = "catalog-series__card";
    card.dataset.ivid = String(variant.ivid);
    const choice = document.createElement("label");
    choice.className = "catalog-series__choice";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "catalog-series-variant";
    radio.value = String(variant.ivid);
    radio.className = "visually-hidden";
    radio.disabled = Boolean(variant.existing);

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

    choice.append(radio, thumb, name);
    if (facts.textContent) choice.append(facts);
    if (chips.childElementCount) choice.append(chips);
    if (variant.note) {
      const note = document.createElement("span");
      note.className = "catalog-series__note";
      note.textContent = variant.note;
      note.title = variant.note;
      choice.append(note);
    }
    // A double click picks the variant and imports it.
    choice.addEventListener("dblclick", () => {
      if (radio.disabled) return;
      radio.checked = true;
      importChosen();
    });
    card.append(choice);

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
    const count = body.issues.reduce((total, issue) => total + issue.variants.length, 0);
    const available = seriesRadios().filter((radio) => !radio.disabled).length;
    if (!count) showSeriesStatus(gettext("The catalogue lists no covers for this series."), true);
    else if (!available) showSeriesStatus(gettext("Every cover of this series is already in Comi."));
    else {
      showSeriesStatus(
        interpolate(ngettext("%(count)s cover. Choose the one to import.", "%(count)s covers. Choose the one to import.", count), { count }, true)
      );
    }
    if (seriesDialog.classList.contains("show")) focusInDialog();
  }

  async function loadSeries(tid) {
    const request = ++seriesRequest;
    const option = sourceSelect.selectedOptions[0];
    series = { source: sourceSelect.value, name: option.textContent.trim(), tid };
    seriesTitle.textContent = seriesDefaultTitle;
    seriesLink.hidden = true;
    seriesPublishing.hidden = true;
    seriesIssues.replaceChildren();
    seriesImport.disabled = true;
    showSeriesStatus(gettext("Loading…"));
    showStatus("");
    bootstrap.Modal.getOrCreateInstance(seriesDialog).show();
    try {
      const url = new URL(data.catalogSeriesUrl, window.location.origin);
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

  // Look the chosen variant up and fill the form with it, as with any other catalogue.
  async function importChosen() {
    const radio = chosenRadio();
    if (!radio || radio.disabled || !series || seriesImport.dataset.busy) return;
    const request = ++seriesRequest;
    seriesImport.dataset.busy = "true";
    seriesImport.disabled = true;
    showSeriesStatus(gettext("Loading…"));
    const answer = await fetchProposal({ source: series.source, id: radio.value, series: series.tid });
    delete seriesImport.dataset.busy;
    seriesImport.disabled = !chosenRadio();
    if (request !== seriesRequest) return; // the dialog was closed meanwhile
    if (answer.error) {
      showSeriesStatus(answer.error, true);
      seriesImport.disabled = false;
      return;
    }
    bootstrap.Modal.getOrCreateInstance(seriesDialog).hide();
    try {
      await apply(answer.proposal);
    } catch (error) {
      showStatus(gettext("The catalogue could not be read. Try again."), true);
    }
  }

  seriesIssues.addEventListener("change", () => {
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
  seriesDialog.addEventListener("shown.bs.modal", focusInDialog);
  seriesDialog.addEventListener("hidden.bs.modal", () => {
    seriesRequest += 1;
    loadButton.focus();
  });

  sourceSelect.addEventListener("change", syncIdInput);
  syncIdInput();
  loadButton.addEventListener("click", load);
  idInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault(); // do not submit the edition form
    load();
  });
})();
