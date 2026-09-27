(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("connecting-editor-data").textContent);
  const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const MAX_SIZE = 20;
  const DRAG_TYPE = "application/x-connecting";

  const el = {
    name: document.getElementById("connecting-name"),
    rows: document.getElementById("connecting-rows"),
    columns: document.getElementById("connecting-columns"),
    notes: document.getElementById("connecting-notes"),
    grid: document.getElementById("editor-grid"),
    summary: document.getElementById("grid-summary"),
    overflow: document.getElementById("editor-overflow"),
    overflowItems: document.getElementById("editor-overflow-items"),
    publishingSearch: document.getElementById("publishing-search"),
    publishingSelect: document.getElementById("publishing-select"),
    pickerHint: document.getElementById("picker-hint"),
    pickerResults: document.getElementById("picker-results"),
    save: document.getElementById("editor-save"),
    status: document.getElementById("editor-status"),
    errors: document.getElementById("editor-errors"),
    snackbar: document.getElementById("editor-snackbar"),
  };

  const comics = new Map(); // id -> {id, title, detail, thumbnail}
  const pieces = new Map(); // "row-column" -> comic id
  let rows = data.connecting.rows;
  let columns = data.connecting.columns;
  let selection = null; // {type: "comic", id} | {type: "cell", key}
  let pickerResults = [];
  let dirty = false;
  let saving = false;

  const cellKey = (row, column) => `${row}-${column}`;
  const parseKey = (key) => key.split("-").map(Number);
  const inBounds = (key) => {
    const [row, column] = parseKey(key);
    return row <= rows && column <= columns;
  };
  const keyOfComic = (id) => {
    for (const [key, comicId] of pieces) {
      if (comicId === id) return key;
    }
    return null;
  };

  // ---------- Grid operations ----------

  function placeComic(comicId, targetKey) {
    const fromKey = keyOfComic(comicId);
    if (fromKey === targetKey) return;
    const occupant = pieces.get(targetKey);
    if (fromKey !== null) {
      // The comic was already placed: it is moved and swapped with the occupant.
      if (occupant !== undefined) pieces.set(fromKey, occupant);
      else pieces.delete(fromKey);
    }
    pieces.set(targetKey, comicId);
    markDirty();
  }

  function moveCell(fromKey, toKey) {
    const comicId = pieces.get(fromKey);
    if (comicId !== undefined) placeComic(comicId, toKey);
  }

  function removePiece(key) {
    pieces.delete(key);
    markDirty();
  }

  function markDirty() {
    dirty = true;
    el.status.textContent = gettext("Unsaved changes");
  }

  // ---------- Render ----------

  function renderCover(comic) {
    const cover = document.createElement("div");
    cover.className = "editor-cover";
    if (comic.thumbnail) {
      const img = document.createElement("img");
      img.src = comic.thumbnail;
      img.alt = "";
      img.draggable = false;
      img.loading = "lazy";
      cover.append(img);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "editor-cover__placeholder";
      placeholder.textContent = comic.title;
      cover.append(placeholder);
    }
    return cover;
  }

  function comicLabel(comic) {
    return `${comic.title} ${comic.detail}`;
  }

  function makeDraggable(node, payload) {
    node.draggable = true;
    node.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(payload));
      event.dataTransfer.effectAllowed = "move";
      node.classList.add("is-dragging");
    });
    node.addEventListener("dragend", () => node.classList.remove("is-dragging"));
  }

  function readDrag(event) {
    try {
      return JSON.parse(event.dataTransfer.getData(DRAG_TYPE));
    } catch (e) {
      return null;
    }
  }

  function makeDropTarget(node, onDrop) {
    node.addEventListener("dragover", (event) => {
      if (!event.dataTransfer.types.includes(DRAG_TYPE)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      node.classList.add("is-drop-target");
    });
    node.addEventListener("dragleave", (event) => {
      if (!node.contains(event.relatedTarget)) node.classList.remove("is-drop-target");
    });
    node.addEventListener("drop", (event) => {
      node.classList.remove("is-drop-target");
      const payload = readDrag(event);
      if (!payload) return;
      event.preventDefault();
      onDrop(payload);
      selection = null;
      render();
    });
  }

  function onKeyActivate(node, handler) {
    node.addEventListener("click", handler);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handler(event);
      }
    });
  }

  function renderPiece(key, container, { showPosition }) {
    const comicId = pieces.get(key);
    const comic = comics.get(comicId);
    const [row, column] = parseKey(key);

    container.append(renderCover(comic));
    if (showPosition) {
      const position = document.createElement("span");
      position.className = "editor-cell__position";
      position.textContent = `${row}·${column}`;
      container.append(position);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "editor-cell__remove";
    remove.setAttribute("aria-label", interpolate(gettext("Remove %(name)s"), { name: comicLabel(comic) }, true));
    remove.title = gettext("Remove");
    remove.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      if (selection && selection.type === "cell" && selection.key === key) selection = null;
      removePiece(key);
      render();
    });
    container.append(remove);

    container.title = comicLabel(comic);
    makeDraggable(container, { type: "cell", key });
  }

  function renderGrid() {
    el.grid.style.setProperty("--cols", columns);
    el.grid.replaceChildren();

    for (let row = 1; row <= rows; row++) {
      for (let column = 1; column <= columns; column++) {
        const key = cellKey(row, column);
        const cell = document.createElement("div");
        cell.className = "editor-cell";
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");

        if (pieces.has(key)) {
          renderPiece(key, cell, { showPosition: true });
          cell.setAttribute(
            "aria-label",
            interpolate(
              gettext("Row %(row)s, column %(column)s: %(name)s"),
              { row, column, name: comicLabel(comics.get(pieces.get(key))) },
              true
            )
          );
        } else {
          cell.classList.add("is-empty");
          cell.setAttribute("aria-label", interpolate(gettext("Row %(row)s, column %(column)s: empty"), { row, column }, true));
          const position = document.createElement("span");
          position.className = "editor-cell__empty-label";
          position.textContent = `${row}·${column}`;
          cell.append(position);
        }

        if (selection && selection.type === "cell" && selection.key === key) cell.classList.add("is-selected");
        if (selection) cell.classList.add("is-armed");

        onKeyActivate(cell, () => activateCell(key));
        makeDropTarget(cell, (payload) => {
          if (payload.type === "comic") placeComic(payload.id, key);
          else if (payload.type === "cell") moveCell(payload.key, key);
        });
        el.grid.append(cell);
      }
    }
  }

  function renderOverflow() {
    const outside = [...pieces.keys()].filter((key) => !inBounds(key));
    el.overflow.hidden = outside.length === 0;
    el.overflowItems.replaceChildren();
    for (const key of outside) {
      const item = document.createElement("div");
      item.className = "editor-cell editor-cell--overflow";
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      renderPiece(key, item, { showPosition: false });
      if (selection && selection.type === "cell" && selection.key === key) item.classList.add("is-selected");
      onKeyActivate(item, () => {
        selection = selection && selection.type === "cell" && selection.key === key ? null : { type: "cell", key };
        render();
      });
      el.overflowItems.append(item);
    }
  }

  function renderSummary() {
    const placed = [...pieces.keys()].filter(inBounds);
    el.summary.textContent = interpolate(
      gettext("%(placed)s/%(total)s cells"),
      { placed: placed.length, total: rows * columns },
      true
    );
  }

  function renderPicker() {
    el.pickerResults.replaceChildren();
    const placedIds = new Set(pieces.values());

    for (const comic of pickerResults) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-pressed", String(Boolean(selection && selection.type === "comic" && selection.id === comic.id)));
      item.title = comicLabel(comic);
      if (placedIds.has(comic.id)) item.classList.add("is-placed");
      if (selection && selection.type === "comic" && selection.id === comic.id) item.classList.add("is-selected");

      item.append(renderCover(comic));
      const label = document.createElement("span");
      label.className = "picker-item__label";
      label.textContent = comic.detail;
      item.append(label);
      if (placedIds.has(comic.id)) {
        const placed = document.createElement("span");
        placed.className = "picker-item__placed";
        placed.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">check</span>';
        placed.title = gettext("Already in the grid");
        item.append(placed);
      }

      onKeyActivate(item, () => {
        const isSelected = selection && selection.type === "comic" && selection.id === comic.id;
        selection = isSelected ? null : { type: "comic", id: comic.id };
        render();
      });
      makeDraggable(item, { type: "comic", id: comic.id });
      el.pickerResults.append(item);
    }
  }

  function render() {
    renderGrid();
    renderOverflow();
    renderSummary();
    renderPicker();
  }

  function activateCell(key) {
    if (selection) {
      if (selection.type === "comic") placeComic(selection.id, key);
      else if (selection.key !== key) moveCell(selection.key, key);
      selection = null;
    } else if (pieces.has(key)) {
      selection = { type: "cell", key };
    }
    render();
  }

  // ---------- Picker: publishing -> comics ----------

  function normalize(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function renderPublishingOptions() {
    const query = normalize(el.publishingSearch.value.trim());
    const current = el.publishingSelect.value;
    const options = [new Option(gettext("Choose a publishing…"), "")];
    for (const publishing of data.publishings) {
      if (!query || normalize(publishing.label).includes(query)) {
        options.push(new Option(publishing.label, publishing.id, false, String(publishing.id) === current));
      }
    }
    el.publishingSelect.replaceChildren(...options);
  }

  async function loadComics(publishingId) {
    pickerResults = [];
    renderPicker();
    if (!publishingId) {
      el.pickerHint.textContent = gettext("Choose a publishing to see its comics.");
      return;
    }
    el.pickerHint.textContent = gettext("Loading…");
    try {
      const response = await fetch(`${data.comicsUrl}?publishing=${encodeURIComponent(publishingId)}`, {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (el.publishingSelect.value !== String(publishingId)) return;
      pickerResults = payload.results;
      for (const comic of pickerResults) comics.set(comic.id, comic);
      el.pickerHint.textContent = pickerResults.length
        ? gettext("Drag a cover onto the grid, or click it and then a cell.")
        : gettext("This publishing has no comics.");
      renderPicker();
    } catch (e) {
      el.pickerHint.textContent = gettext("The comics could not be loaded. Try again.");
    }
  }

  // ---------- Save ----------

  function showErrors(messages) {
    el.errors.replaceChildren();
    el.errors.hidden = messages.length === 0;
    if (!messages.length) return;
    const icon = document.createElement("span");
    icon.className = "material-symbols-outlined";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "error";
    const list = document.createElement("ul");
    for (const message of messages) {
      const li = document.createElement("li");
      li.textContent = message;
      list.append(li);
    }
    el.errors.append(icon, list);
  }

  function showSnackbar(message) {
    el.snackbar.textContent = message;
    el.snackbar.hidden = false;
    clearTimeout(showSnackbar.timer);
    showSnackbar.timer = setTimeout(() => {
      el.snackbar.hidden = true;
    }, 3000);
  }

  async function save() {
    if (saving) return;
    const outside = [...pieces.keys()].filter((key) => !inBounds(key));
    if (outside.length) {
      showErrors([gettext("There are pieces outside the grid. Place them in a cell or remove them before saving.")]);
      return;
    }

    const body = {
      name: el.name.value.trim(),
      rows,
      columns,
      notes: el.notes.value,
      pieces: [...pieces].map(([key, edition]) => {
        const [row, column] = parseKey(key);
        return { row, column, edition };
      }),
    };

    saving = true;
    el.save.disabled = true;
    el.status.textContent = gettext("Saving…");
    try {
      const response = await fetch(data.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(body),
      });
      const isJson = (response.headers.get("Content-Type") || "").includes("application/json");
      if (!isJson) {
        showErrors([gettext("Your session expired or you do not have permission. Reload the page and log in.")]);
        el.status.textContent = gettext("Unsaved changes");
        return;
      }
      const result = await response.json();
      if (!response.ok) {
        showErrors(result.errors || [gettext("Could not save.")]);
        el.status.textContent = gettext("Unsaved changes");
        return;
      }
      showErrors([]);
      dirty = false;
      if (!data.connecting.id) {
        window.location.replace(result.url);
        return;
      }
      el.status.textContent = "";
      showSnackbar(gettext("Connecting saved"));
    } catch (e) {
      showErrors([gettext("Could not connect to the server.")]);
      el.status.textContent = gettext("Unsaved changes");
    } finally {
      saving = false;
      el.save.disabled = false;
    }
  }

  // ---------- Init ----------

  function readSize(input, fallback) {
    const value = parseInt(input.value, 10);
    if (Number.isNaN(value)) return fallback;
    return Math.min(MAX_SIZE, Math.max(1, value));
  }

  function init() {
    el.name.value = data.connecting.name;
    el.rows.value = rows;
    el.columns.value = columns;
    el.notes.value = data.connecting.notes;
    for (const piece of data.connecting.pieces) {
      comics.set(piece.edition.id, piece.edition);
      pieces.set(cellKey(piece.row, piece.column), piece.edition.id);
    }

    el.rows.addEventListener("input", () => {
      rows = readSize(el.rows, rows);
      markDirty();
      render();
    });
    el.columns.addEventListener("input", () => {
      columns = readSize(el.columns, columns);
      markDirty();
      render();
    });
    el.rows.addEventListener("change", () => (el.rows.value = rows));
    el.columns.addEventListener("change", () => (el.columns.value = columns));
    el.name.addEventListener("input", markDirty);
    el.notes.addEventListener("input", markDirty);

    el.publishingSearch.addEventListener("input", renderPublishingOptions);
    el.publishingSelect.addEventListener("change", () => loadComics(el.publishingSelect.value));

    // Dropping a grid piece on the picker removes it.
    makeDropTarget(el.pickerResults, (payload) => {
      if (payload.type === "cell") removePiece(payload.key);
    });

    el.save.addEventListener("click", save);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && selection) {
        selection = null;
        render();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        save();
      }
    });
    window.addEventListener("beforeunload", (event) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    renderPublishingOptions();
    render();
  }

  init();
})();
