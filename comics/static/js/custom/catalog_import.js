// Import box of the Gestión edition form: loads an issue from Metron or Comic Vine (through
// comics.views.manage_editions.catalog_lookup) and fills the form with it. Nothing is saved here:
// the user reviews the values and saves the form. The imported cover is only a URL (a hidden
// field); the server downloads it when the form is saved. When the publishing is not in Comi, the
// "Create publishing" dialog (publishing_dialogs.js) is prefilled with the catalogue's series. Every
// text from the catalogue goes into the page with textContent / form values, never as HTML.
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

  let proposal = null;

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

  function renderSummary(cover) {
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
      summaryItem("help", interpolate(gettext("Editorial not found in Comi: %(name)s"), { name: unmatched.editorial }, true), "missing");
    }
    if (unmatched.creators.length) {
      summaryItem(
        "person_off",
        interpolate(gettext("Creators not found in Comi (not added): %(names)s"), { names: unmatched.creators.join(", ") }, true),
        "missing"
      );
    }
    const missingCover = cover ? cover.unmatched_cover_artists : unmatched.cover_artists;
    if (missingCover.length) {
      summaryItem(
        "person_off",
        interpolate(gettext("Cover artists not found in Comi (not added): %(names)s"), { names: missingCover.join(", ") }, true),
        "missing"
      );
    }
    for (const warning of proposal.warnings) summaryItem("warning", warning, "warning");
  }

  // ---------- Filling the form ----------

  // The main cover (null) or one of the variants.
  function applyCover(variant) {
    const edition = proposal.edition;
    setField("id_variant", variant ? variant.letter : edition.variant);
    setField("id_printing", variant ? variant.printing : edition.printing);
    const ratio = document.getElementById("id_ratio");
    ratio.value = variant ? variant.ratio : "";
    ratio.dispatchEvent(new Event("input", { bubbles: true }));
    setField("id_cover_price", variant && variant.cover_price !== null ? variant.cover_price : edition.cover_price);
    const artists = variant ? variant.cover_artists : proposal.cover_artists;
    const unmatchedArtists = variant ? variant.unmatched_cover_artists : proposal.unmatched.cover_artists;
    if (artists.length || unmatchedArtists.length) setArtists("cover_artists", artists);
    setCover(variant ? variant.cover_url : proposal.cover_url, proposal.source_name);
    renderSummary(variant);
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
    const edition = proposal.edition;
    editionForm.useSingleIssue();
    if (proposal.publishing) setField("id_publishing", proposal.publishing.id);
    if (proposal.new_publishing && publishingDialogs) publishingDialogs.prefill(proposal.new_publishing);
    setField("id_number", edition.number);
    setField("id_format", edition.format);
    setField("id_release_date", edition.release_date);
    setField("id_issue_data-synopsis", proposal.synopsis);
    for (const [name, value] of Object.entries(proposal.identifiers)) setField(`id_issue_data-${name}`, value);
    if (proposal.creators.length || proposal.unmatched.creators.length) setArtists("issue_data-creators", proposal.creators);

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
