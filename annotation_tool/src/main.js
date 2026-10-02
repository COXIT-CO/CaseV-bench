// Wires the viewer, the overlay and the store to the page: file handling, the sidebar, the
// toolbar and keyboard shortcuts. Everything that depends on state re-renders through
// `schedule`, at most once per animation frame.
import { CATEGORIES, categoryInfo } from "./categories.js";
import { hydrateIcons, icon } from "./icons.js";
import { Overlay, formatInches } from "./overlay.js";
import { parseProject, projectIdFromFilename, serializeProject, withIds } from "./projectFile.js";
import { Store } from "./store.js";
import { Viewer } from "./viewer.js";

const ZOOM_STEP = 1.25;
const DRAFT_PREFIX = "annotator:draft:";
const DRAFT_DELAY_MS = 500;
const JSON_TYPES = [{ description: "Project JSON", accept: { "application/json": [".json"] } }];

const $ = (sel) => document.querySelector(sel);
const stage = $("#stage");
const pageEl = $("#page");
const projectIdInput = $("#project-id");
const inspector = $("#inspector");
const objectList = $("#object-list");

const store = new Store();
const state = {
  tool: "box",
  space: false,
  scope: "all",
  pdfName: null,
  jsonName: null,
  fileHandle: null, // where Save writes without asking (File System Access API, Chromium only)
  draftKey: null,
};

const viewer = new Viewer({
  stage,
  pageEl,
  baseCanvas: $("#pdf-canvas"),
  detailCanvas: $("#pdf-detail"),
  onZoom: () => schedule("overlay", "chrome"),
  onBusy: (busy) => {
    $("#loading").hidden = !busy;
  },
});

const overlay = new Overlay({
  stage,
  pageEl,
  svg: $("#overlay"),
  viewer,
  store,
  getTool: () => state.tool,
  isSpaceHeld: () => state.space,
  onCursor: (p) => {
    $("#status-cursor").textContent = p ? `x ${p.x.toFixed(1)}   y ${p.y.toFixed(1)} pt` : "—";
  },
  onMeasure: (points) => {
    const readout = $("#status-measure");
    readout.hidden = points === null;
    if (points !== null) readout.textContent = `${formatInches(points)}  (${points.toFixed(1)} pt)`;
  },
});

// ---- render scheduling --------------------------------------------------------------------

const RENDERERS = {
  overlay: () => overlay.render(),
  categories: renderCategories,
  inspector: renderInspector,
  list: renderList,
  chrome: renderChrome,
};
const ALL = Object.keys(RENDERERS);
const pending = new Set();
let frame = 0;

function schedule(...parts) {
  for (const part of parts) pending.add(part);
  frame ||= requestAnimationFrame(() => {
    frame = 0;
    const parts = [...pending];
    pending.clear();
    for (const part of parts) RENDERERS[part]();
  });
}

let draftTimer = 0;
store.subscribe(({ type }) => {
  if (type === "select") schedule("overlay", "inspector", "list");
  else if (type === "category") schedule("categories", "overlay");
  else if (type === "saved") schedule("chrome");
  else schedule(...ALL);
  if (type === "boxes") {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, DRAFT_DELAY_MS);
  }
});

// ---- files --------------------------------------------------------------------------------

async function openPdf(file) {
  if (hasUnsavedWork() && !confirm(`Discard unsaved changes to ${state.pdfName}?`)) return false;
  $("#loading").hidden = false;
  try {
    await viewer.open(file);
  } catch (err) {
    $("#loading").hidden = true;
    toast(`Could not open ${file.name}: ${err.message}`, { error: true });
    return false;
  }
  Object.assign(state, {
    pdfName: file.name,
    jsonName: null,
    fileHandle: null,
    draftKey: `${DRAFT_PREFIX}${file.name}:${file.size}`,
  });
  projectIdInput.value = projectIdFromFilename(file.name);
  pageEl.hidden = false;
  $("#empty").hidden = true;
  overlay.clearMeasure();
  store.reset([]);
  offerDraft();
  return true;
}

async function pickJson() {
  if (!viewer.loaded) {
    toast("Open the PDF first, then import its JSON.", { error: true });
    return;
  }
  if (!window.showOpenFilePicker) {
    $("#json-input").click();
    return;
  }
  try {
    const [handle] = await window.showOpenFilePicker({ types: JSON_TYPES });
    await importJson(await handle.getFile(), handle);
  } catch (err) {
    if (err.name !== "AbortError") toast(`Could not import: ${err.message}`, { error: true });
  }
}

async function importJson(file, handle = null) {
  let parsed;
  try {
    parsed = parseProject(await file.text(), viewer.pageCount);
  } catch (err) {
    toast(`Could not import ${file.name}: ${err.message}`, { error: true });
    return;
  }
  const replacing = store.boxes.length;
  if (store.dirty && replacing && !confirm(`Replace the ${replacing} unsaved boxes with the ${parsed.boxes.length} in ${file.name}?`)) {
    return;
  }
  store.reset(parsed.boxes);
  Object.assign(state, { jsonName: file.name, fileHandle: handle });
  if (parsed.projectId) projectIdInput.value = parsed.projectId;
  let message = `Imported ${parsed.boxes.length} objects from ${file.name}.`;
  if (parsed.skipped) message += ` Skipped ${parsed.skipped} that reference pages outside this PDF.`;
  toast(message);
}

async function save({ saveAs = false } = {}) {
  if (!viewer.loaded) return;
  const projectId = projectIdInput.value.trim();
  if (!projectId) {
    toast("Enter a project ID before saving.", { error: true });
    projectIdInput.focus();
    return;
  }
  const text = serializeProject(projectId, store.boxes);
  const suggestedName = state.jsonName ?? `${projectId}-obj-location.json`;
  let savedAs = suggestedName;
  try {
    if (window.showSaveFilePicker) {
      let handle = saveAs ? null : state.fileHandle;
      handle ??= await window.showSaveFilePicker({ suggestedName, types: JSON_TYPES });
      if ((await handle.queryPermission?.({ mode: "readwrite" })) === "prompt") {
        await handle.requestPermission({ mode: "readwrite" });
      }
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      Object.assign(state, { fileHandle: handle, jsonName: handle.name });
      savedAs = handle.name;
    } else {
      download(text, suggestedName);
    }
  } catch (err) {
    if (err.name !== "AbortError") toast(`Save failed: ${err.message}`, { error: true });
    return;
  }
  store.markSaved();
  saveDraft();
  toast(`Saved ${store.boxes.length} objects to ${savedAs}.`);
}

// A copy of the PDF with every box drawn in its category colour, labelled with its saved id.
async function exportPdf() {
  if (!viewer.loaded) return;
  if (!store.boxes.length) {
    toast("There are no boxes to export yet.", { error: true });
    return;
  }
  const projectId = projectIdInput.value.trim() || projectIdFromFilename(state.pdfName);
  const { objects } = JSON.parse(serializeProject(projectId, store.boxes));
  const colors = Object.fromEntries(CATEGORIES.map((c) => [c.name, c.color]));
  $("#loading").hidden = false;
  try {
    const blob = await viewer.exportPdf({ objects, colors });
    download(blob, `${projectIdFromFilename(state.pdfName)}-annotated.pdf`);
    toast(`Exported ${objects.length} boxes to ${projectIdFromFilename(state.pdfName)}-annotated.pdf.`);
  } catch (err) {
    toast(`Export failed: ${err.message}`, { error: true });
  } finally {
    $("#loading").hidden = true;
  }
}

function download(content, filename) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function hasUnsavedWork() {
  return store.dirty && store.boxes.length > 0;
}

// Unsaved boxes are mirrored to localStorage per PDF, so a closed tab or crash loses nothing.
function saveDraft() {
  if (!state.draftKey) return;
  try {
    if (!store.dirty) {
      localStorage.removeItem(state.draftKey);
      return;
    }
    const boxes = store.boxes.map(({ uid, ...box }) => box);
    const draft = { projectId: projectIdInput.value.trim(), boxes, savedAt: Date.now() };
    localStorage.setItem(state.draftKey, JSON.stringify(draft));
  } catch {
    // Storage can be full or blocked; drafts are a convenience only.
  }
}

function offerDraft() {
  let draft;
  try {
    draft = JSON.parse(localStorage.getItem(state.draftKey));
  } catch {
    return;
  }
  if (!Array.isArray(draft?.boxes) || !draft.boxes.length) return;
  const when = new Date(draft.savedAt).toLocaleString();
  toast(`Unsaved work on this PDF from ${when}: ${draft.boxes.length} boxes.`, {
    duration: 20000,
    action: {
      label: "Restore",
      run: () => {
        store.reset(draft.boxes.filter((b) => b.page < viewer.pageCount), { dirty: true });
        if (draft.projectId) projectIdInput.value = draft.projectId;
      },
    },
  });
}

// ---- navigation ---------------------------------------------------------------------------

async function goToPage(index) {
  if (index === viewer.pageIndex) return;
  try {
    if (!(await viewer.showPage(index))) return;
  } catch (err) {
    toast(`Could not render page ${index + 1}: ${err.message}`, { error: true });
    return;
  }
  overlay.clearMeasure();
  if (store.selected && store.selected.page !== index) store.select(null);
  schedule(...ALL);
}

async function revealBox(uid) {
  const box = store.boxes.find((b) => b.uid === uid);
  if (!box) return;
  await goToPage(box.page);
  store.select(uid);
  viewer.centerOn(box);
}

function selectAdjacent(step) {
  const onPage = withIds(store.boxesOnPage(viewer.pageIndex)).map(({ box }) => box);
  if (!onPage.length) return;
  const i = onPage.indexOf(store.selected);
  const next = i < 0 ? onPage.at(step > 0 ? 0 : -1) : onPage[(i + step + onPage.length) % onPage.length];
  store.select(next.uid);
  viewer.centerOn(next);
}

function setTool(tool) {
  state.tool = tool;
  stage.classList.remove("tool-box", "tool-hand", "tool-measure");
  stage.classList.add(`tool-${tool}`);
  schedule("chrome");
}

function setCategory(name) {
  store.setActiveCategory(name);
  const box = store.selected;
  if (box && box.category !== name) store.update(box.uid, { category: name });
}

function nudge(dx, dy) {
  const box = store.selected;
  if (!box) return false;
  dx = Math.min(Math.max(dx, -box.x0), viewer.width - box.x1);
  dy = Math.min(Math.max(dy, -box.y0), viewer.height - box.y1);
  store.update(box.uid, { x0: box.x0 + dx, x1: box.x1 + dx, y0: box.y0 + dy, y1: box.y1 + dy });
  return true;
}

function toggleBoxes() {
  overlay.boxesHidden = !overlay.boxesHidden;
  schedule("overlay", "chrome");
}

// ---- sidebar ------------------------------------------------------------------------------

function renderCategories() {
  const counts = {};
  for (const b of store.boxes) counts[b.category] = (counts[b.category] ?? 0) + 1;
  $("#categories").innerHTML = CATEGORIES.map(
    (c, i) => `
      <button class="category${c.name === store.activeCategory ? " active" : ""}" data-category="${c.name}" style="--c: ${c.color}">
        <span class="swatch"></span><span>${c.name}</span>
        <span class="num">${counts[c.name] ?? 0}</span><kbd>${i + 1}</kbd>
      </button>`,
  ).join("");
}

let inspectorUid = null;
let editingCoords = false;

function renderInspector() {
  const box = store.selected;
  inspector.hidden = !box;
  if (!box) {
    inspectorUid = null;
    return;
  }
  if (inspectorUid !== box.uid) {
    inspectorUid = box.uid;
    inspector.innerHTML = `
      <h2 class="panel-title">Selected <span class="count" id="inspector-id"></span></h2>
      <div class="chips">
        ${CATEGORIES.map((c) => `<button class="chip" data-category="${c.name}" style="--c: ${c.color}"><span class="swatch"></span>${c.name}</button>`).join("")}
      </div>
      <div class="coords">
        ${["x0", "y0", "x1", "y1"].map((k) => `<label>${k}<input class="input" type="number" step="1" data-coord="${k}" /></label>`).join("")}
      </div>
      <div class="inspector-footer">
        <span class="mono" id="inspector-size"></span>
        <button class="btn danger" id="delete-box" title="Delete (⌫)">${icon("trash")}<span>Delete</span></button>
      </div>`;
  }
  const id = withIds(store.boxes).find((entry) => entry.box === box)?.id ?? "";
  $("#inspector-id").textContent = `${id} · page ${box.page + 1}`;
  for (const chip of inspector.querySelectorAll(".chip")) {
    chip.classList.toggle("active", chip.dataset.category === box.category);
  }
  for (const input of inspector.querySelectorAll("[data-coord]")) {
    if (document.activeElement !== input) {
      input.value = box[input.dataset.coord];
      input.classList.remove("invalid");
    }
  }
  $("#inspector-size").textContent = `${box.x1 - box.x0} × ${box.y1 - box.y0} pt`;
}

function onCoordInput() {
  const box = store.selected;
  if (!box) return;
  const inputs = [...inspector.querySelectorAll("[data-coord]")];
  const v = Object.fromEntries(inputs.map((i) => [i.dataset.coord, Number(i.value)]));
  const valid = Object.values(v).every((n) => Number.isFinite(n) && inputs.every((i) => i.value !== ""));
  const okX = valid && v.x0 < v.x1;
  const okY = valid && v.y0 < v.y1;
  for (const i of inputs) i.classList.toggle("invalid", i.dataset.coord.startsWith("x") ? !okX : !okY);
  if (!okX || !okY) return;
  if (!editingCoords) {
    store.checkpoint();
    editingCoords = true;
  }
  store.patch(box.uid, v);
}

function renderList() {
  const entries = withIds(store.boxes).filter(({ box }) => state.scope === "all" || box.page === viewer.pageIndex);
  $("#object-count").textContent = store.boxes.length ? String(store.boxes.length) : "";
  if (!entries.length) {
    const message = !viewer.loaded
      ? "Open a PDF to start."
      : store.boxes.length
        ? "No boxes on this page."
        : "Drag on the page to draw the first box, or import a JSON file.";
    objectList.innerHTML = `<div class="list-empty">${message}</div>`;
    return;
  }
  const html = [];
  let page = -1;
  for (const { box, id } of entries) {
    if (box.page !== page) {
      page = box.page;
      const n = entries.filter((e) => e.box.page === page).length;
      html.push(`<div class="page-group-title${page === viewer.pageIndex ? " current" : ""}"><span>Page ${page + 1}</span><span>${n}</span></div>`);
    }
    const { color } = categoryInfo(box.category);
    html.push(`
      <button class="object${box === store.selected ? " selected" : ""}" data-uid="${box.uid}" title="${escapeHtml(box.category)}" style="--c: ${color}">
        <span class="swatch"></span><span class="oid">${escapeHtml(id)}</span>
        <span class="geom">${box.x0}, ${box.y0} · ${box.x1 - box.x0}×${box.y1 - box.y0}</span>
      </button>`);
  }
  objectList.innerHTML = html.join("");
  objectList.querySelector(".object.selected")?.scrollIntoView({ block: "nearest" });
}

function renderChrome() {
  const loaded = Boolean(viewer.loaded);
  const name = state.pdfName ? [state.pdfName, state.jsonName].filter(Boolean).join("  ·  ") : "";
  $("#doc-name").innerHTML = name ? `${escapeHtml(name)}${store.dirty ? ' <span class="dirty">— edited</span>' : ""}` : "";
  document.title = name ? `${store.dirty ? "• " : ""}${state.pdfName} — Annotator` : "Annotator";

  for (const id of ["import-json", "save", "export-pdf", "zoom-in", "zoom-out", "zoom-label", "toggle-boxes"]) $(`#${id}`).disabled = !loaded;
  $("#undo").disabled = !store.undoStack.length;
  $("#redo").disabled = !store.redoStack.length;
  $("#prev-page").disabled = !loaded || viewer.pageIndex === 0;
  $("#next-page").disabled = !loaded || viewer.pageIndex >= viewer.pageCount - 1;
  $("#zoom-label").textContent = `${Math.round(viewer.zoom * 100)}%`;
  for (const btn of document.querySelectorAll("[data-tool]")) btn.classList.toggle("active", btn.dataset.tool === state.tool);
  for (const btn of document.querySelectorAll("[data-scope]")) btn.classList.toggle("active", btn.dataset.scope === state.scope);
  const toggle = $("#toggle-boxes");
  toggle.innerHTML = icon(overlay.boxesHidden ? "eyeOff" : "eye");
  toggle.title = overlay.boxesHidden ? "Show boxes (O)" : "Hide boxes (O)";

  const select = $("#page-select");
  select.disabled = !loaded;
  const perPage = new Array(viewer.pageCount).fill(0);
  for (const b of store.boxes) perPage[b.page] += 1;
  const options = perPage.map((n, i) => `<option value="${i}">Page ${i + 1} / ${viewer.pageCount}${n ? `  (${n})` : ""}</option>`).join("");
  if (select.dataset.options !== options) {
    select.innerHTML = options;
    select.dataset.options = options;
  }
  select.value = String(viewer.pageIndex);

  $("#status-page").textContent = loaded ? `${Math.round(viewer.width)} × ${Math.round(viewer.height)} pt` : "";
  $("#status-saved").textContent = !loaded ? "" : store.dirty ? "Unsaved changes" : "All changes saved";
}

// ---- toasts -------------------------------------------------------------------------------

function toast(message, { error = false, action = null, duration = 4500 } = {}) {
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.append(Object.assign(document.createElement("span"), { textContent: message }));
  const close = () => node.remove();
  if (action) {
    const btn = Object.assign(document.createElement("button"), { className: "btn primary", textContent: action.label });
    btn.addEventListener("click", () => {
      action.run();
      close();
    });
    node.append(btn);
  }
  const dismiss = Object.assign(document.createElement("button"), { className: "btn icon", title: "Dismiss", innerHTML: icon("plus") });
  dismiss.style.transform = "rotate(45deg)";
  dismiss.addEventListener("click", close);
  node.append(dismiss);
  $("#toasts").append(node);
  setTimeout(close, error ? duration * 2 : duration);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// ---- events -------------------------------------------------------------------------------

$("#open-pdf").addEventListener("click", () => $("#pdf-input").click());
$("#empty-open").addEventListener("click", () => $("#pdf-input").click());
$("#import-json").addEventListener("click", pickJson);
$("#save").addEventListener("click", (e) => save({ saveAs: e.shiftKey }));
$("#export-pdf").addEventListener("click", exportPdf);
$("#undo").addEventListener("click", () => store.undo());
$("#redo").addEventListener("click", () => store.redo());
$("#prev-page").addEventListener("click", () => goToPage(viewer.pageIndex - 1));
$("#next-page").addEventListener("click", () => goToPage(viewer.pageIndex + 1));
$("#page-select").addEventListener("change", (e) => goToPage(Number(e.target.value)));
$("#zoom-in").addEventListener("click", () => viewer.setZoom(viewer.zoom * ZOOM_STEP));
$("#zoom-out").addEventListener("click", () => viewer.setZoom(viewer.zoom / ZOOM_STEP));
$("#zoom-label").addEventListener("click", () => viewer.setZoom(viewer.fitZoom("width")));
$("#toggle-boxes").addEventListener("click", toggleBoxes);
$("#help").addEventListener("click", () => $("#help-dialog").showModal());
projectIdInput.addEventListener("input", () => {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, DRAFT_DELAY_MS);
});

$("#pdf-input").addEventListener("change", async (e) => {
  const [file] = e.target.files;
  e.target.value = "";
  if (file) await openPdf(file);
});
$("#json-input").addEventListener("change", async (e) => {
  const [file] = e.target.files;
  e.target.value = "";
  if (file) await importJson(file);
});

for (const btn of document.querySelectorAll("[data-tool]")) btn.addEventListener("click", () => setTool(btn.dataset.tool));
for (const btn of document.querySelectorAll("[data-scope]")) {
  btn.addEventListener("click", () => {
    state.scope = btn.dataset.scope;
    schedule("list", "chrome");
  });
}

$("#categories").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-category]");
  if (btn) store.setActiveCategory(btn.dataset.category);
});

inspector.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip && store.selected) store.update(store.selected.uid, { category: chip.dataset.category });
  if (e.target.closest("#delete-box") && store.selected) store.remove(store.selected.uid);
});
inspector.addEventListener("input", (e) => {
  if (e.target.matches("[data-coord]")) onCoordInput();
});
inspector.addEventListener("focusout", (e) => {
  if (!e.target.matches("[data-coord]")) return;
  editingCoords = false;
  schedule("inspector");
});

objectList.addEventListener("click", (e) => {
  const item = e.target.closest("[data-uid]");
  if (item) revealBox(Number(item.dataset.uid));
});

// Drop a PDF, a JSON, or both at once (the PDF is opened first).
window.addEventListener("dragover", (e) => {
  e.preventDefault();
  stage.classList.add("dragover");
});
window.addEventListener("dragleave", (e) => {
  if (!e.relatedTarget) stage.classList.remove("dragover");
});
window.addEventListener("drop", async (e) => {
  e.preventDefault();
  stage.classList.remove("dragover");
  const files = [...e.dataTransfer.files];
  const pdf = files.find((f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf");
  const json = files.find((f) => /\.json$/i.test(f.name));
  if (pdf && !(await openPdf(pdf))) return;
  if (json) {
    if (viewer.loaded) await importJson(json);
    else toast("Drop the PDF together with (or before) its JSON.", { error: true });
  }
});

window.addEventListener("beforeunload", (e) => {
  if (hasUnsavedWork()) e.preventDefault();
});

window.addEventListener("blur", () => {
  state.space = false;
  stage.classList.remove("space");
});

document.addEventListener("keyup", (e) => {
  if (e.key === " ") {
    state.space = false;
    stage.classList.remove("space");
  }
});

document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  const key = e.key.toLowerCase();
  if (mod && key === "s") {
    e.preventDefault();
    save({ saveAs: e.shiftKey });
    return;
  }
  if (mod && key === "e") {
    e.preventDefault();
    exportPdf();
    return;
  }
  if (mod && key === "o") {
    e.preventDefault();
    $("#pdf-input").click();
    return;
  }
  if (e.target.closest?.("input, select, textarea")) {
    if (e.key === "Escape" || (e.key === "Enter" && e.target.matches("input"))) e.target.blur();
    return;
  }
  if ($("#help-dialog").open) return;

  if (mod) {
    if (key === "z") {
      e.preventDefault();
      if (e.shiftKey) store.redo();
      else store.undo();
    } else if (key === "y") {
      e.preventDefault();
      store.redo();
    } else if (viewer.loaded && (key === "=" || key === "+" || key === "-" || key === "0")) {
      e.preventDefault(); // zoom the drawing, not the whole UI
      zoomKey(key);
    }
    return;
  }

  const index = CATEGORIES.findIndex((_, i) => e.key === String(i + 1));
  if (index >= 0) {
    setCategory(CATEGORIES[index].name);
    return;
  }

  const step = e.shiftKey ? 10 : 1;
  const handled = (() => {
    switch (e.key) {
      case " ":
        state.space = true;
        stage.classList.add("space");
        return true;
      case "Escape":
        return overlay.cancelGesture() || overlay.clearMeasure() || (store.select(null), true);
      case "Delete":
      case "Backspace":
        if (store.selected) store.remove(store.selected.uid);
        return true;
      case "ArrowLeft":
        return nudge(-step, 0);
      case "ArrowRight":
        return nudge(step, 0);
      case "ArrowUp":
        return nudge(0, -step);
      case "ArrowDown":
        return nudge(0, step);
      case "Tab":
        selectAdjacent(e.shiftKey ? -1 : 1);
        return true;
      case "[":
        goToPage(viewer.pageIndex - 1);
        return true;
      case "]":
        goToPage(viewer.pageIndex + 1);
        return true;
      case "?":
        $("#help-dialog").showModal();
        return true;
    }
    switch (key) {
      case "b":
        setTool("box");
        return true;
      case "h":
        setTool("hand");
        return true;
      case "m":
        setTool("measure");
        return true;
      case "o":
        toggleBoxes();
        return true;
      case "=":
      case "+":
      case "-":
      case "0":
        zoomKey(key);
        return true;
    }
    return false;
  })();
  if (handled) e.preventDefault();
});

function zoomKey(key) {
  if (key === "0") viewer.setZoom(viewer.fitZoom("width"));
  else viewer.setZoom(viewer.zoom * (key === "-" ? 1 / ZOOM_STEP : ZOOM_STEP));
}

hydrateIcons();
setTool("box");
schedule(...ALL);

// For quick checks from the console.
Object.assign(window, { annotator: { store, viewer, overlay } });
