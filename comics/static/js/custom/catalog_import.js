// Import box of the Gestión edition form: loads an issue from Metron or Comic Vine (through
// comics.views.manage_editions.catalog_lookup) and fills the form with it. Nothing is saved here:
// the user reviews the values and saves the form. The imported cover is only a URL (a hidden
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
  if (!loadButton) return; // importing is off (no credentials)

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
    ratio.value = variant ? variant.ratio : "";
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

  async function load() {
    const id = idInput.value.trim();
    if (!id) {
      showStatus(gettext("Enter the issue's ID in the catalogue."), true);
      idInput.focus();
      return;
    }
    loadButton.disabled = true;
    showStatus(gettext("Loading…"));
    try {
      const url = new URL(data.catalogUrl, window.location.origin);
      url.searchParams.set("source", sourceSelect.value);
      url.searchParams.set("id", id);
      const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body || !body.proposal) {
        showStatus((body && body.error) || gettext("The catalogue could not be read. Try again."), true);
        return;
      }
      await apply(body.proposal);
    } catch (error) {
      showStatus(gettext("The catalogue could not be read. Try again."), true);
    } finally {
      loadButton.disabled = false;
    }
  }

  loadButton.addEventListener("click", load);
  idInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault(); // do not submit the edition form
    load();
  });
})();
