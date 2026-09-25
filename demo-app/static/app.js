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
  lightboxImage.src = src;
  lightboxCaption.textContent = caption;
  lightbox.classList.remove("hidden");
  lightbox.classList.add("flex");
}

function closeLightbox() {
  lightbox.classList.add("hidden");
  lightbox.classList.remove("flex");
  lightboxImage.src = "";
}

lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) closeLightbox();
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
      uploadNote.textContent = `PDF only, up to ${config.max_upload_mb}MB.`;
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
  summaryContainer.innerHTML = "";
  const meta = document.createElement("p");
  meta.className = "mb-2 font-mono text-xs text-muted";
  meta.textContent = data.model + (data.custom_prompt ? " · custom prompt" : "");
  summaryContainer.appendChild(meta);
  summaryContainer.appendChild(summaryTable(data.pages, data.detections));
  summaryContainer.classList.remove("hidden");

  resultsEl.innerHTML = "";
  resultsEl.classList.remove("hidden");

  for (const page of data.pages) {
    const card = document.createElement("div");
    card.className = "border border-line bg-white p-6";

    const title = document.createElement("h3");
    title.className = "mb-4 font-mono text-xs font-medium uppercase tracking-wide text-muted";
    title.textContent = `Page ${page.page}` + (page.status === "failed" ? " — failed" : "");
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
      grid.appendChild(imageBlock("Detected", page.annotated_image, `Page ${page.page} — detected`));
      card.appendChild(grid);
    }
    resultsEl.appendChild(card);
  }

  resultsEl.appendChild(detectionsDetail(data.detections));
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

  try {
    const response = await fetch("/api/detect", { method: "POST", body: form });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Detection failed.");
    }

    const data = await readDetectStream(response, setStatus);
    setStatus("");
    renderResults(data);
    loadHistory();
  } catch (err) {
    setError(err.message || String(err));
  } finally {
    runButton.disabled = !selectedFile;
  }
});

// The backend streams newline-delimited JSON: a "progress" line per step as it happens,
// then one "result" line at the end. Reading it incrementally (rather than one big response
// body) is what lets the status text track what's actually happening page by page.
async function readDetectStream(response, onProgress) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex;
    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) continue;

      const event = JSON.parse(line);
      if (event.type === "progress") {
        onProgress(event.message);
      } else if (event.type === "error") {
        throw new Error(event.message);
      } else if (event.type === "result") {
        result = event.data;
      }
    }
  }

  if (!result) {
    throw new Error("Detection ended without a result.");
  }
  return result;
}

loadConfig();
loadHistory();
