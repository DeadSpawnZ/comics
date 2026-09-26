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

  const comics = new Map(); // id -> {id, title, detail, thumbnail, owned}
  const pieces = new Map(); // "fila-columna" -> id de comic
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

  // ---------- Operaciones sobre la cuadricula ----------

  function placeComic(comicId, targetKey) {
    const fromKey = keyOfComic(comicId);
    if (fromKey === targetKey) return;
    const occupant = pieces.get(targetKey);
    if (fromKey !== null) {
      // El comic ya estaba colocado: se mueve e intercambia con el ocupante.
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
    el.status.textContent = "Cambios sin guardar";
  }

  // ---------- Render ----------

  function renderCover(comic) {
    const cover = document.createElement("div");
    cover.className = "editor-cover" + (comic.owned ? "" : " is-missing");
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
    const badge = document.createElement("span");
    badge.className = "editor-cover__badge";
    badge.textContent = comic.owned ? "Lo tienes" : "Falta";
    cover.append(badge);
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
    remove.setAttribute("aria-label", `Quitar ${comicLabel(comic)}`);
    remove.title = "Quitar";
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
          cell.setAttribute("aria-label", `Fila ${row}, columna ${column}: ${comicLabel(comics.get(pieces.get(key)))}`);
        } else {
          cell.classList.add("is-empty");
          cell.setAttribute("aria-label", `Fila ${row}, columna ${column}: vacía`);
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
    const owned = placed.filter((key) => comics.get(pieces.get(key)).owned).length;
    el.summary.textContent = `${placed.length}/${rows * columns} celdas · tienes ${owned}`;
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
        placed.title = "Ya está en la cuadrícula";
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

  // ---------- Buscador: publishing -> comics ----------

  function normalize(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function renderPublishingOptions() {
    const query = normalize(el.publishingSearch.value.trim());
    const current = el.publishingSelect.value;
    const options = [new Option("Elige un publishing…", "")];
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
      el.pickerHint.textContent = "Elige un publishing para ver sus comics.";
      return;
    }
    el.pickerHint.textContent = "Cargando…";
    try {
      const response = await fetch(`${data.comicsUrl}?publishing=${encodeURIComponent(publishingId)}`, {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (el.publishingSelect.value !== String(publishingId)) return;
      pickerResults = payload.results;
      for (const comic of pickerResults) comics.set(comic.id, comic);
      el.pickerHint.textContent = pickerResults.length
        ? "Arrastra una portada a la cuadrícula o haz clic en ella y luego en una celda."
        : "Este publishing no tiene comics.";
      renderPicker();
    } catch (e) {
      el.pickerHint.textContent = "No se pudieron cargar los comics. Intenta de nuevo.";
    }
  }

  // ---------- Guardar ----------

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
      showErrors(["Hay piezas fuera de la cuadrícula. Colócalas en una celda o quítalas antes de guardar."]);
      return;
    }

    const body = {
      name: el.name.value.trim(),
      rows,
      columns,
      notes: el.notes.value,
      pieces: [...pieces].map(([key, comic]) => {
        const [row, column] = parseKey(key);
        return { row, column, comic };
      }),
    };

    saving = true;
    el.save.disabled = true;
    el.status.textContent = "Guardando…";
    try {
      const response = await fetch(data.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(body),
      });
      const isJson = (response.headers.get("Content-Type") || "").includes("application/json");
      if (!isJson) {
        showErrors(["Tu sesión expiró o no tienes permisos. Recarga la página e inicia sesión."]);
        el.status.textContent = "Cambios sin guardar";
        return;
      }
      const result = await response.json();
      if (!response.ok) {
        showErrors(result.errors || ["No se pudo guardar."]);
        el.status.textContent = "Cambios sin guardar";
        return;
      }
      showErrors([]);
      dirty = false;
      if (!data.connecting.id) {
        window.location.replace(result.url);
        return;
      }
      el.status.textContent = "";
      showSnackbar("Connecting guardado");
    } catch (e) {
      showErrors(["No se pudo conectar con el servidor."]);
      el.status.textContent = "Cambios sin guardar";
    } finally {
      saving = false;
      el.save.disabled = false;
    }
  }

  // ---------- Inicio ----------

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
      comics.set(piece.comic.id, piece.comic);
      pieces.set(cellKey(piece.row, piece.column), piece.comic.id);
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

    // Soltar una pieza de la cuadricula en el buscador la quita.
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
