const fileInput = document.getElementById("file-input");
const fileName = document.getElementById("file-name");
const uploadNote = document.getElementById("upload-note");
const runButton = document.getElementById("run-button");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const errorEl = document.getElementById("error");
const summaryContainer = document.getElementById("summary-container");
const resultsEl = document.getElementById("results");

const configToggle = document.getElementById("config-toggle");
const configPanel = document.getElementById("config-panel");
const modelSelect = document.getElementById("model-select");
const modelCustom = document.getElementById("model-custom");

const showPromptButton = document.getElementById("show-prompt");
const promptModal = document.getElementById("prompt-modal");
const promptModalBody = document.getElementById("prompt-modal-body");
const promptModalClose = document.getElementById("prompt-modal-close");
const promptModalStatus = document.getElementById("prompt-modal-status");
const promptResetButton = document.getElementById("prompt-reset");

const lightbox = document.getElementById("lightbox");
const lightboxImage = document.getElementById("lightbox-image");
const lightboxOverlay = document.getElementById("lightbox-overlay");
const lightboxCaption = document.getElementById("lightbox-caption");
const lightboxClose = document.getElementById("lightbox-close");

const historyToggle = document.getElementById("history-toggle");
const historyDrawer = document.getElementById("history-drawer");
const historyBackdrop = document.getElementById("history-backdrop");
const historyClose = document.getElementById("history-close");
const historySearch = document.getElementById("history-search");
const historyList = document.getElementById("history-list");
const historyEmpty = document.getElementById("history-empty");

let selectedFile = null;
let labelOrder = [];
let defaultPromptText = "";
let historyRuns = [];

configToggle.addEventListener("click", () => configPanel.classList.toggle("hidden"));
document.addEventListener("click", (event) => {
  const clickedInsidePanel = configPanel.contains(event.target);
  const clickedToggle = configToggle.contains(event.target);
  if (!clickedInsidePanel && !clickedToggle) {
    configPanel.classList.add("hidden");
  }
});

// The textarea itself is the source of truth for the current prompt — edits persist across
// opening/closing the modal, and are only reverted by explicitly clicking "Reset to default".
function isPromptEdited() {
  return promptModalBody.value.trim() !== defaultPromptText.trim();
}

function updatePromptStatus() {
  const edited = isPromptEdited();
  promptModalStatus.textContent = edited ? "Edited — differs from the default" : "Default";
  showPromptButton.textContent = edited ? "Edit prompt (edited)" : "Edit prompt";
}

function openPromptModal() {
  updatePromptStatus();
  promptModal.classList.remove("hidden");
  promptModal.classList.add("flex");
  promptModalBody.focus();
  promptModalBody.setSelectionRange(0, 0);
  promptModalBody.scrollTop = 0;
}

function closePromptModal() {
  promptModal.classList.add("hidden");
  promptModal.classList.remove("flex");
}

showPromptButton.addEventListener("click", () => {
  configPanel.classList.add("hidden");
  openPromptModal();
});
promptModalClose.addEventListener("click", closePromptModal);
promptModal.addEventListener("click", (event) => {
  if (event.target === promptModal) closePromptModal();
});
promptModalBody.addEventListener("input", updatePromptStatus);
promptResetButton.addEventListener("click", () => {
  promptModalBody.value = defaultPromptText;
  updatePromptStatus();
  promptModalBody.focus();
});

function openLightbox(src, caption) {
  lightboxOverlay.classList.add("hidden");
  lightboxOverlay.innerHTML = "";
  lightboxImage.classList.remove("hidden");
  lightboxImage.src = src;
  lightboxCaption.textContent = caption;
  lightbox.classList.remove("hidden");
  lightbox.classList.add("flex");
  resetZoom();
}

// The "Detected" image's lightbox isn't a plain <img> swap like openLightbox — it rebuilds the
// same stacked original+layers view (see buildImageLayers/buildLegend) at lightbox scale, as a
// fresh, independently-toggleable instance, so zooming in doesn't lose the ability to filter
// labels. The legend sits above the image here too (see renderResults for the card layout),
// centered as its own row rather than floating over any part of the picture.
function openDetectionLightbox(page, caption) {
  lightboxImage.classList.add("hidden");
  lightboxImage.src = "";
  lightboxOverlay.innerHTML = "";
  lightboxOverlay.className = "flex flex-col items-center gap-3";

  const { wrap, layerImgs } = buildImageLayers(page, "lightbox");
  const legend = buildLegend(page, layerImgs);
  if (legend) lightboxOverlay.appendChild(legend);
  lightboxOverlay.appendChild(wrap);

  lightboxCaption.textContent = caption;
  lightbox.classList.remove("hidden");
  lightbox.classList.add("flex");
  resetZoom();
}

function closeLightbox() {
  lightbox.classList.add("hidden");
  lightbox.classList.remove("flex");
  resetZoom();
  lightboxImage.src = "";
  lightboxImage.classList.remove("hidden");
  lightboxOverlay.classList.add("hidden");
  lightboxOverlay.innerHTML = "";
}

lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) closeLightbox();
});

// Scroll-to-zoom + drag-to-pan on whichever image is showing in the lightbox, scoped entirely
// to it — the wheel handler calls preventDefault() so scrolling over the lightbox zooms the
// picture instead of the whole page (the page itself has nothing to scroll behind a fixed,
// full-viewport overlay anyway, but pinch-zoom/ctrl+scroll would otherwise zoom the browser).
const ZOOM_MIN = 1;
const ZOOM_MAX = 6;
const ZOOM_WHEEL_SENSITIVITY = 0.0015;

let zoomScale = 1;
let zoomX = 0;
let zoomY = 0;
let isPanning = false;
let panPointerStartX = 0;
let panPointerStartY = 0;
let panOriginX = 0;
let panOriginY = 0;

// The lightbox shows one of two different trees depending on which image was opened (a plain
// <img> for "Original", or buildImageLayers's stacked original+layers for "Detected") — this
// picks whichever is actually visible so the same zoom/pan logic drives both.
function zoomTarget() {
  if (!lightboxImage.classList.contains("hidden")) return lightboxImage;
  return lightboxOverlay.querySelector("[data-zoomable]");
}

function applyZoomTransform() {
  const target = zoomTarget();
  if (!target) return;
  target.style.transform = `translate(${zoomX}px, ${zoomY}px) scale(${zoomScale})`;
  target.style.cursor = zoomScale > ZOOM_MIN ? (isPanning ? "grabbing" : "grab") : "zoom-in";
}

function resetZoom() {
  zoomScale = 1;
  zoomX = 0;
  zoomY = 0;
  isPanning = false;
  applyZoomTransform();
}

lightbox.addEventListener(
  "wheel",
  (event) => {
    if (lightbox.classList.contains("hidden")) return;
    event.preventDefault();
    const next = Math.min(
      ZOOM_MAX,
      Math.max(ZOOM_MIN, zoomScale * (1 - event.deltaY * ZOOM_WHEEL_SENSITIVITY))
    );
    if (next === zoomScale) return;
    zoomScale = next;
    if (zoomScale === ZOOM_MIN) {
      zoomX = 0;
      zoomY = 0;
    }
    applyZoomTransform();
  },
  { passive: false }
);

// A trackpad's two-finger scroll fires the same wheel events a mouse's scroll wheel does, so
// both already drive the zoom above — but a plain mouse has no pinch gesture, and relying on
// "scroll to discover zoom" isn't obvious with a mouse anyway. So a click, not just a drag,
// also does something: a plain click (no real movement between mousedown and mouseup) toggles
// between 1x and ZOOM_CLICK_STEP; a click-and-drag pans instead, once already zoomed in. Either
// way it's scoped to the image/overlay itself (data-zoomable) so it never fights with clicking
// the legend, the caption, or the close button.
const ZOOM_CLICK_STEP = 2.5;
const CLICK_VS_DRAG_PX = 5;

let pointerDownTarget = null;
let pointerDownX = 0;
let pointerDownY = 0;
let pointerMoved = false;

function toggleClickZoom() {
  if (zoomScale > ZOOM_MIN) {
    resetZoom();
    return;
  }
  zoomScale = ZOOM_CLICK_STEP;
  zoomX = 0;
  zoomY = 0;
  applyZoomTransform();
}

lightbox.addEventListener("mousedown", (event) => {
  const target = event.target.closest("[data-zoomable], #lightbox-image");
  if (!target) return;
  pointerDownTarget = target;
  pointerDownX = event.clientX;
  pointerDownY = event.clientY;
  pointerMoved = false;
  if (zoomScale > ZOOM_MIN) {
    isPanning = true;
    panPointerStartX = event.clientX;
    panPointerStartY = event.clientY;
    panOriginX = zoomX;
    panOriginY = zoomY;
  }
  applyZoomTransform();
  event.preventDefault();
});

window.addEventListener("mousemove", (event) => {
  if (!pointerDownTarget) return;
  if (
    Math.abs(event.clientX - pointerDownX) > CLICK_VS_DRAG_PX ||
    Math.abs(event.clientY - pointerDownY) > CLICK_VS_DRAG_PX
  ) {
    pointerMoved = true;
  }
  if (!isPanning) return;
  zoomX = panOriginX + (event.clientX - panPointerStartX);
  zoomY = panOriginY + (event.clientY - panPointerStartY);
  applyZoomTransform();
});

window.addEventListener("mouseup", () => {
  if (pointerDownTarget && !pointerMoved) toggleClickZoom();
  pointerDownTarget = null;
  isPanning = false;
  applyZoomTransform();
});

function openHistoryDrawer() {
  historyDrawer.classList.remove("hidden");
  historyDrawer.classList.add("flex");
  historyBackdrop.classList.remove("hidden");
}

function closeHistoryDrawer() {
  historyDrawer.classList.add("hidden");
  historyDrawer.classList.remove("flex");
  historyBackdrop.classList.add("hidden");
}

historyToggle.addEventListener("click", () => {
  if (historyDrawer.classList.contains("hidden")) {
    loadHistory();
    openHistoryDrawer();
  } else {
    closeHistoryDrawer();
  }
});
historyClose.addEventListener("click", closeHistoryDrawer);
historyBackdrop.addEventListener("click", closeHistoryDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeLightbox();
  closePromptModal();
  closeHistoryDrawer();
});

function historyRow(run) {
  const row = document.createElement("div");
  row.className = "group flex cursor-pointer items-start justify-between gap-2 border-b border-line px-4 py-3 hover:bg-soft";

  const info = document.createElement("div");
  info.className = "min-w-0";

  const filenameEl = document.createElement("p");
  filenameEl.className = "truncate text-sm font-medium text-ink";
  filenameEl.textContent = run.filename;
  info.appendChild(filenameEl);

  const metaEl = document.createElement("p");
  metaEl.className = "mt-0.5 font-mono text-xs text-muted";
  const when = new Date(run.created_at).toLocaleString();
  metaEl.textContent =
    `${run.model} · ${when} · ${run.detection_count} found` + (run.custom_prompt ? " · custom prompt" : "");
  info.appendChild(metaEl);

  row.appendChild(info);

  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.title = "Delete this run";
  deleteButton.className = "flex-shrink-0 p-1 text-muted hover:text-brand";
  deleteButton.innerHTML =
    '<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">' +
    '<path fill-rule="evenodd" clip-rule="evenodd" d="M8 2a1 1 0 00-1 1v1H4a1 1 0 000 2h12a1 1 0 100-2h-3V3a1 1 0 00-1-1H8zM5 7a1 1 0 011 1v8a2 2 0 002 2h4a2 2 0 002-2V8a1 1 0 112 0v8a4 4 0 01-4 4H8a4 4 0 01-4-4V8a1 1 0 011-1z"/>' +
    "</svg>";
  deleteButton.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!confirm(`Delete "${run.filename}" from history?`)) return;
    try {
      await fetch(`/api/runs/${run.id}`, { method: "DELETE" });
      await loadHistory();
    } catch (err) {
      alert("Could not delete this run.");
    }
  });
  row.appendChild(deleteButton);

  row.addEventListener("click", async () => {
    closeHistoryDrawer();
    setError("");
    summaryContainer.classList.add("hidden");
    resultsEl.classList.add("hidden");
    setStatus(`Loading "${run.filename}"…`);

    try {
      const response = await fetch(`/api/runs/${run.id}`);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Could not load this run.");
      }
      const data = await response.json();
      setStatus("");
      renderResults(data);
    } catch (err) {
      setStatus("");
      setError(err.message || String(err));
    }
  });

  return row;
}

function renderHistoryList() {
  const query = historySearch.value.trim().toLowerCase();
  const filtered = query
    ? historyRuns.filter(
        (run) => run.filename.toLowerCase().includes(query) || run.model.toLowerCase().includes(query)
      )
    : historyRuns;

  historyList.innerHTML = "";
  for (const run of filtered) historyList.appendChild(historyRow(run));
  historyEmpty.classList.toggle("hidden", filtered.length > 0);
  historyEmpty.textContent = historyRuns.length ? "No runs match your search." : "No runs yet.";
}

async function loadHistory() {
  try {
    const response = await fetch("/api/runs");
    historyRuns = await response.json();
  } catch (err) {
    historyRuns = [];
  }
  renderHistoryList();
}

historySearch.addEventListener("input", renderHistoryList);

fileInput.addEventListener("change", () => {
  selectedFile = fileInput.files[0] || null;
  fileName.textContent = selectedFile ? selectedFile.name : "No file selected";
  runButton.disabled = !selectedFile;
});

modelSelect.addEventListener("change", () => {
  const isCustom = modelSelect.value === "__custom__";
  modelCustom.classList.toggle("hidden", !isCustom);
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();

    for (const slug of Object.keys(config.models || {})) {
      const option = document.createElement("option");
      option.value = slug;
      option.textContent = slug;
      modelSelect.appendChild(option);
    }

    labelOrder = config.labels || [];
    defaultPromptText = config.default_prompt || "";
    promptModalBody.value = defaultPromptText;
    if (config.max_upload_mb) {
      uploadNote.textContent = `PDF only, up to ${config.max_upload_mb}MB and ${config.max_pages} pages.`;
    }
  } catch (err) {
    // Config fetch failed; the custom model field below still lets someone type a slug.
  }

  const customOption = document.createElement("option");
  customOption.value = "__custom__";
  customOption.textContent = "Custom slug…";
  modelSelect.appendChild(customOption);
}

function currentModel() {
  if (modelSelect.value === "__custom__") {
    return modelCustom.value.trim();
  }
  return modelSelect.value;
}

function setStatus(text) {
  statusText.textContent = text;
  statusEl.classList.toggle("hidden", !text);
  statusEl.classList.toggle("flex", !!text);
}

function setError(text) {
  errorEl.textContent = text;
  errorEl.classList.toggle("hidden", !text);
}

function imageBlock(label, src, caption) {
  const wrap = document.createElement("div");
  const captionEl = document.createElement("p");
  captionEl.className = "mb-1 font-mono text-xs uppercase tracking-wide text-muted";
  captionEl.textContent = label;
  const img = document.createElement("img");
  img.src = src;
  img.className = "w-full cursor-zoom-in border border-line transition hover:opacity-90";
  img.addEventListener("click", () => openLightbox(src, caption));
  wrap.appendChild(captionEl);
  wrap.appendChild(img);
  return wrap;
}

// Fixed taxonomy order when a label is in it (the normal case); anything outside it (should not
// happen — the backend only ever produces ALLOWED_LABELS) is appended rather than dropped.
function orderLabels(labels) {
  return [...labels].sort((a, b) => labelOrder.indexOf(a) - labelOrder.indexOf(b));
}

// The "Detected" image: the plain page image with one transparent PNG layer stacked on top per
// object type (page.label_layers) — every box in every layer was drawn server-side by
// location_overlay, never by this code; the legend built separately by buildLegend() just shows
// or hides an already-rendered layer, so toggling one is instant with no re-render needed.
// `variant` is "card" (inline result grid) or "lightbox" (the zoomed-in view). Returns the image
// element plus a label -> layer <img> map so a legend can be wired up to it.
function buildImageLayers(page, variant) {
  const wrap = document.createElement("div");
  wrap.className = variant === "lightbox" ? "relative inline-block" : "relative";
  if (variant === "lightbox") wrap.dataset.zoomable = "true";

  const baseImg = document.createElement("img");
  baseImg.src = page.original_image;
  baseImg.className =
    variant === "lightbox"
      ? "block max-h-[85vh] max-w-[90vw] border border-white/10 object-contain"
      : "block w-full cursor-zoom-in border border-line transition hover:opacity-90";
  wrap.appendChild(baseImg);

  if (variant === "card") {
    baseImg.addEventListener("click", () =>
      openDetectionLightbox(page, `Page ${page.page} — detected`)
    );
  }

  const layerImgs = {};
  for (const label of orderLabels(Object.keys(page.label_layers || {}))) {
    const layerImg = document.createElement("img");
    layerImg.src = page.label_layers[label];
    layerImg.className = "pointer-events-none absolute inset-0 h-full w-full";
    wrap.appendChild(layerImg);
    layerImgs[label] = layerImg;
  }

  return { wrap, layerImgs };
}

// A legend row of checkbox + colour swatch + label name, one per type actually present on the
// page, wired to show/hide the matching layer in `layerImgs`. Given its own background/border
// (rather than bare text) so it stays readable regardless of what it's sitting on — a white
// page card in the result grid, or the dark lightbox backdrop when zoomed in.
// The checkmark glyph that pops into a chip once it's checked — see .legend-check in
// index.html for the scale-in animation. `stroke="currentColor"` is what lets one <svg>
// pick up whichever chip's own colour .legend-chip:has(input:checked) sets as `color`.
function legendCheckIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "3");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.classList.add("legend-check", "h-3.5", "flex-shrink-0", "overflow-hidden");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M4 10l4 4 8-8");
  svg.appendChild(path);
  return svg;
}

function buildLegend(page, layerImgs) {
  const labels = orderLabels(Object.keys(layerImgs));
  if (labels.length === 0) return null;

  // No background/border on the row itself — each chip is already its own opaque pill (see
  // .legend-chip), so it reads fine floating directly on whatever's behind it: the white page
  // card here, or the dark lightbox backdrop in openDetectionLightbox.
  // Centered in the page block, not pinned to the left edge under the grid.
  const legend = document.createElement("div");
  legend.className = "flex flex-wrap items-center justify-center gap-2.5 font-mono text-sm";

  // A master on/off control, not just another chip — plain bordered button matching the app's
  // other secondary buttons (e.g. "Edit prompt"), so it reads as an action rather than one more
  // label to toggle. Label flips between the two states; clicking always drives every chip to
  // the *other* state from whatever they're mostly in right now.
  const toggleAll = document.createElement("button");
  toggleAll.type = "button";
  toggleAll.className =
    "border border-line bg-white px-3 py-1.5 text-muted hover:bg-soft hover:text-ink";
  legend.appendChild(toggleAll);

  const checkboxes = [];

  function updateToggleAllLabel() {
    const allChecked = checkboxes.every((checkbox) => checkbox.checked);
    toggleAll.textContent = allChecked ? "Hide all" : "Show all";
  }

  toggleAll.addEventListener("click", () => {
    const makeChecked = !checkboxes.every((checkbox) => checkbox.checked);
    for (const checkbox of checkboxes) {
      if (checkbox.checked === makeChecked) continue;
      checkbox.checked = makeChecked;
      checkbox.dispatchEvent(new Event("change"));
    }
  });

  for (const label of labels) {
    const color = (page.colors || {})[label] || "#19201d";

    // Sharp corners, not pill-shaped — this app has no rounded corners anywhere else (see the
    // "Run detection" button, the settings/history panels, every input), so a rounded-full chip
    // stood out as the one rounded thing on the page.
    const chip = document.createElement("label");
    chip.className =
      "legend-chip flex cursor-pointer select-none items-center gap-1.5 border border-line bg-white px-3 py-1.5 text-muted";
    chip.style.setProperty("--chip", color);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.className = "sr-only";
    checkbox.addEventListener("change", () => {
      layerImgs[label].style.display = checkbox.checked ? "block" : "none";
      updateToggleAllLabel();
    });
    chip.appendChild(checkbox);
    checkboxes.push(checkbox);

    const swatch = document.createElement("span");
    swatch.className = "h-2.5 w-2.5 flex-shrink-0 border border-ink/20";
    swatch.style.backgroundColor = color;
    chip.appendChild(swatch);

    const text = document.createElement("span");
    text.textContent = label;
    chip.appendChild(text);

    chip.appendChild(legendCheckIcon());

    legend.appendChild(chip);
  }

  updateToggleAllLabel();
  return legend;
}

// Rows are the fixed object taxonomy (from /api/config), columns are the pages actually
// present in this run, plus a Total column. Pages aren't capped, so this wraps in a
// horizontal-scroll container with a sticky label column for sheets with many pages — that
// column needs its own opaque background (not just inherited from the row) or the page
// columns scrolling underneath show through it.
function summaryTable(pages, detections) {
  const wrap = document.createElement("div");
  wrap.className = "border border-line bg-white";

  const heading = document.createElement("div");
  heading.className = "border-b border-line bg-soft px-5 py-3";
  heading.innerHTML =
    '<h3 class="font-mono text-xs font-medium uppercase tracking-wide text-muted">Summary</h3>';
  wrap.appendChild(heading);

  const pageNumbers = pages.map((page) => page.page);
  const labels = labelOrder.length
    ? labelOrder
    : [...new Set(detections.map((detection) => detection.label))].sort();

  const counts = {};
  for (const label of labels) {
    counts[label] = {};
    for (const page of pageNumbers) counts[label][page] = 0;
  }
  for (const detection of detections) {
    if (!counts[detection.label]) counts[detection.label] = {};
    counts[detection.label][detection.page] = (counts[detection.label][detection.page] || 0) + 1;
  }

  const table = document.createElement("table");
  table.className = "w-full text-left text-sm";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.className = "bg-soft font-mono text-xs font-medium uppercase tracking-wide text-muted";
  headRow.appendChild(th("Label", true));
  for (const page of pageNumbers) headRow.appendChild(th(`Page ${page}`));
  headRow.appendChild(th("Total", false, true));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");

  labels.forEach((label, index) => {
    const zebra = index % 2 === 1;
    const tr = document.createElement("tr");
    tr.className = "group border-t border-line" + (zebra ? " bg-soft/60" : "") + " hover:bg-soft";
    tr.appendChild(td(label, true, false, zebra));
    let rowTotal = 0;
    for (const page of pageNumbers) {
      rowTotal += counts[label][page] || 0;
      tr.appendChild(td(String(counts[label][page] || 0)));
    }
    tr.appendChild(td(String(rowTotal), false, true));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  const scroller = document.createElement("div");
  scroller.className = "overflow-x-auto";
  scroller.appendChild(table);
  wrap.appendChild(scroller);
  return wrap;
}

function th(text, sticky, accent) {
  const el = document.createElement("th");
  el.className =
    "px-5 py-2.5" +
    (sticky ? " sticky left-0 z-10 bg-soft" : "") +
    (accent ? " text-forest" : "");
  el.textContent = text;
  return el;
}

// `zebra` picks the sticky cell's own opaque background to match its row: `bg-inherit` would
// pick up the row's *semi-transparent* stripe color (or nothing, on a plain row), which is
// exactly what let page columns scrolling underneath show through the label column.
function td(text, sticky, accent, zebra) {
  const el = document.createElement("td");
  const stickyBg = zebra ? "bg-soft" : "bg-white";
  el.className =
    "px-5 py-2" +
    (sticky ? ` sticky left-0 z-10 ${stickyBg} group-hover:bg-soft font-medium text-ink` : " font-mono text-muted") +
    (accent ? " font-semibold text-forest" : "");
  el.textContent = text;
  return el;
}

function detectionsDetail(detections) {
  const wrap = document.createElement("div");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "w-full bg-brand px-4 py-3 font-medium text-white hover:bg-brand-bright";
  toggle.textContent = `Show all detections (${detections.length})`;

  const tableWrap = document.createElement("div");
  tableWrap.className = "mt-4 hidden overflow-x-auto border border-line bg-white p-4";

  toggle.addEventListener("click", () => {
    const isHidden = tableWrap.classList.contains("hidden");
    tableWrap.classList.toggle("hidden", !isHidden);
    toggle.textContent = isHidden
      ? `Hide detections (${detections.length})`
      : `Show all detections (${detections.length})`;
  });

  if (detections.length) {
    const table = document.createElement("table");
    table.className = "w-full text-left text-sm";
    table.innerHTML = `
      <thead>
        <tr class="border-b border-line font-mono text-xs uppercase tracking-wide text-muted">
          <th class="py-1.5 pr-4">Page</th>
          <th class="py-1.5 pr-4">Label</th>
          <th class="py-1.5 pr-4">x_min</th>
          <th class="py-1.5 pr-4">y_min</th>
          <th class="py-1.5 pr-4">x_max</th>
          <th class="py-1.5 pr-4">y_max</th>
        </tr>
      </thead>
    `;
    const tbody = document.createElement("tbody");
    for (const row of detections) {
      const tr = document.createElement("tr");
      tr.className = "border-b border-line font-mono text-ink";
      tr.innerHTML = `
        <td class="py-1.5 pr-4">${row.page}</td>
        <td class="py-1.5 pr-4">${row.label}</td>
        <td class="py-1.5 pr-4">${row.x_min}</td>
        <td class="py-1.5 pr-4">${row.y_min}</td>
        <td class="py-1.5 pr-4">${row.x_max}</td>
        <td class="py-1.5 pr-4">${row.y_max}</td>
      `;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableWrap.appendChild(table);
  }

  wrap.appendChild(toggle);
  wrap.appendChild(tableWrap);
  return wrap;
}

function renderResults(data) {
  // Defensive: a stale tab running old JS against an already-redeployed backend (or any
  // other shape mismatch) should show an empty state, not crash on .map of undefined.
  const pages = data.pages || [];
  const detections = data.detections || [];

  summaryContainer.innerHTML = "";
  const meta = document.createElement("p");
  meta.className = "mb-2 font-mono text-xs text-muted";
  meta.textContent = data.model + (data.custom_prompt ? " · custom prompt" : "");
  summaryContainer.appendChild(meta);
  summaryContainer.appendChild(summaryTable(pages, detections));
  summaryContainer.classList.remove("hidden");

  resultsEl.innerHTML = "";
  resultsEl.classList.remove("hidden");

  for (const page of pages) {
    const card = document.createElement("div");
    card.className = "border border-line bg-white p-6";

    const title = document.createElement("h3");
    title.className = "mb-4 font-mono text-xs font-medium uppercase tracking-wide text-muted";
    let titleText = `Page ${page.page}`;
    if (page.elapsed_seconds != null) {
      titleText += ` (Processing time: ${page.elapsed_seconds.toFixed(1)}s)`;
    }
    if (page.status === "failed") titleText += " — failed";
    title.textContent = titleText;
    card.appendChild(title);

    if (page.status === "failed") {
      const err = document.createElement("p");
      err.className = "text-sm text-brand";
      err.textContent = page.error;
      card.appendChild(err);
    } else {
      const grid = document.createElement("div");
      grid.className = "grid grid-cols-1 gap-6 lg:grid-cols-2";
      grid.appendChild(imageBlock("Original", page.original_image, `Page ${page.page} — original`));

      const detectedWrap = document.createElement("div");
      const detectedCaption = document.createElement("p");
      detectedCaption.className = "mb-1 font-mono text-xs uppercase tracking-wide text-muted";
      detectedCaption.textContent = "Detected";
      detectedWrap.appendChild(detectedCaption);
      const { wrap: detectedImage, layerImgs } = buildImageLayers(page, "card");
      detectedWrap.appendChild(detectedImage);
      grid.appendChild(detectedWrap);

      card.appendChild(grid);

      // One legend shared by both images, below the grid rather than above either one — that
      // keeps Original and Detected starting at the same height (a per-image legend used to
      // push just the Detected image down, leaving the two misaligned).
      const legend = buildLegend(page, layerImgs);
      if (legend) {
        legend.classList.add("mt-4");
        card.appendChild(legend);
      }
    }
    resultsEl.appendChild(card);
  }

  resultsEl.appendChild(detectionsDetail(detections));
}

runButton.addEventListener("click", async () => {
  if (!selectedFile) return;
  const model = currentModel();
  if (!model) {
    setError("Choose or enter a model first.");
    return;
  }

  setError("");
  summaryContainer.classList.add("hidden");
  resultsEl.classList.add("hidden");
  runButton.disabled = true;
  setStatus("Uploading PDF…");

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("model", model);
  form.append("prompt", promptModalBody.value);

  let jobId;
  try {
    const response = await fetch("/api/detect", { method: "POST", body: form });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Detection failed.");
    }
    ({ job_id: jobId } = await response.json());
    if (!jobId) {
      // /api/detect used to stream the whole result over one long connection; it now
      // starts a background job and hands back just an id. Getting here with no job_id
      // means this tab is still running JS from before that change — a hard refresh
      // picks up the current version.
      throw new Error("This page is out of date — please refresh and try again.");
    }
  } catch (err) {
    setStatus("");
    setError(err.message || String(err));
    runButton.disabled = !selectedFile;
    return;
  }

  await watchJob(jobId, selectedFile.name);
});

// Tracks whichever job this tab is currently waiting on, so a page refresh (or closing and
// reopening the tab) doesn't orphan it: the detect job itself runs entirely server-side and
// keeps going regardless, but without this the *browser* would have no way left to find it
// again — the user would just see an empty form with no sign anything was ever started,
// until it quietly showed up in history minutes later.
const ACTIVE_JOB_KEY = "casev-active-job";

function saveActiveJob(jobId, filename) {
  try {
    localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ jobId, filename }));
  } catch (err) {
    // Unavailable (private browsing, storage quota) — resuming after a refresh just
    // won't work in that case; not worth failing the run itself over.
  }
}

function clearActiveJob() {
  try {
    localStorage.removeItem(ACTIVE_JOB_KEY);
  } catch (err) {
    // ignore
  }
}

function loadActiveJob() {
  try {
    const raw = localStorage.getItem(ACTIVE_JOB_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
}

// Shared by both a freshly-started run and a resumed one (see resumeActiveJob below) — the
// only difference is whether a 404 (job already gone) is treated as a real failure. For a
// resume, "gone" most likely just means it finished and was cleaned up while this tab was
// away, not that anything went wrong.
async function watchJob(jobId, filename, { isResume = false } = {}) {
  saveActiveJob(jobId, filename);
  setError("");
  summaryContainer.classList.add("hidden");
  resultsEl.classList.add("hidden");
  runButton.disabled = true;
  setStatus(isResume ? `Resuming "${filename}"…` : "Processing…");

  try {
    const data = await pollJob(jobId, setStatus);
    setStatus("");
    renderResults(data);
    loadHistory();
  } catch (err) {
    setStatus("");
    if (!(isResume && err.status === 404)) {
      setError(err.message || String(err));
    }
  } finally {
    clearActiveJob();
    runButton.disabled = !selectedFile;
  }
}

async function resumeActiveJob() {
  const active = loadActiveJob();
  if (!active) return;
  await watchJob(active.jobId, active.filename, { isResume: true });
}

const JOB_POLL_INTERVAL_MS = 1500;

// A large multi-page run can take minutes end to end — long enough that keeping one HTTP
// connection open for the whole thing risks a reverse-proxy timeout cutting it before the
// backend ever gets to respond (which is still running regardless; it just can't tell
// anyone). Polling instead means no single request is ever more than a couple seconds old,
// so it doesn't matter what that timeout is. `after` is a cursor: each poll asks for events
// since the last one it saw, and the server evicts anything at or before that once it's been
// delivered, so a long run's memory doesn't pile up server-side either.
async function pollJob(jobId, onProgress) {
  let after = 0;
  let summary = null;
  const pages = [];
  const detections = [];

  while (true) {
    const response = await fetch(`/api/jobs/${jobId}?after=${after}`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const err = new Error(data.detail || "Lost track of this run.");
      err.status = response.status;
      throw err;
    }
    const payload = await response.json();
    after = payload.next_after;

    for (const event of payload.events) {
      if (event.type === "progress") {
        onProgress(event.data);
      } else if (event.type === "page") {
        const { detections: pageDetections, ...page } = event.data;
        pages.push(page);
        detections.push(...pageDetections);
      } else if (event.type === "error") {
        throw new Error(event.data.message);
      } else if (event.type === "result") {
        summary = event.data;
      }
    }

    if (payload.status !== "running") break;
    await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS));
  }

  fetch(`/api/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});

  if (!summary) {
    throw new Error("Detection ended without a result.");
  }
  pages.sort((a, b) => a.page - b.page);
  return { ...summary, pages, detections };
}

loadConfig();
loadHistory();
resumeActiveJob();
