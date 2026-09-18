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

const lightbox = document.getElementById("lightbox");
const lightboxImage = document.getElementById("lightbox-image");
const lightboxCaption = document.getElementById("lightbox-caption");
const lightboxClose = document.getElementById("lightbox-close");

let selectedFile = null;
let labelOrder = [];
let promptText = "";

configToggle.addEventListener("click", () => configPanel.classList.toggle("hidden"));
document.addEventListener("click", (event) => {
  const clickedInsidePanel = configPanel.contains(event.target);
  const clickedToggle = configToggle.contains(event.target);
  if (!clickedInsidePanel && !clickedToggle) {
    configPanel.classList.add("hidden");
  }
});

function openPromptModal() {
  promptModalBody.textContent = promptText;
  promptModal.classList.remove("hidden");
  promptModal.classList.add("flex");
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
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeLightbox();
  closePromptModal();
});

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
    promptText = config.prompt || "";
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
  captionEl.className = "mb-1 text-xs uppercase tracking-wide text-gray-400";
  captionEl.textContent = label;
  const img = document.createElement("img");
  img.src = src;
  img.className =
    "w-full cursor-zoom-in rounded-lg border border-gray-100 transition hover:opacity-90";
  img.addEventListener("click", () => openLightbox(src, caption));
  wrap.appendChild(captionEl);
  wrap.appendChild(img);
  return wrap;
}

// Rows are the fixed object taxonomy (from /api/config), columns are the pages actually
// present in this run, plus a Total column/row. Pages aren't capped, so this wraps in a
// horizontal-scroll container with a sticky label column for sheets with many pages.
function summaryTable(pages, detections) {
  const wrap = document.createElement("div");
  wrap.className = "overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm";

  const heading = document.createElement("div");
  heading.className = "border-b border-gray-100 px-5 py-3";
  heading.innerHTML = '<h3 class="text-sm font-semibold text-gray-700">Summary</h3>';
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
  headRow.className = "bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500";
  headRow.appendChild(th("Label", true));
  for (const page of pageNumbers) headRow.appendChild(th(`Page ${page}`));
  headRow.appendChild(th("Total", false, true));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  const pageTotals = Object.fromEntries(pageNumbers.map((page) => [page, 0]));
  let grandTotal = 0;

  labels.forEach((label, index) => {
    const tr = document.createElement("tr");
    tr.className =
      "border-t border-gray-100" + (index % 2 === 1 ? " bg-gray-50/60" : "") + " hover:bg-blue-50/60";
    tr.appendChild(td(label, true));
    let rowTotal = 0;
    for (const page of pageNumbers) {
      const count = counts[label][page] || 0;
      rowTotal += count;
      pageTotals[page] += count;
      tr.appendChild(td(String(count)));
    }
    grandTotal += rowTotal;
    tr.appendChild(td(String(rowTotal), false, true));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  const tfoot = document.createElement("tfoot");
  const totalRow = document.createElement("tr");
  totalRow.className = "border-t-2 border-gray-200 font-semibold text-gray-900";
  totalRow.appendChild(td("Total", true));
  for (const page of pageNumbers) totalRow.appendChild(td(String(pageTotals[page])));
  totalRow.appendChild(td(String(grandTotal), false, true));
  tfoot.appendChild(totalRow);
  table.appendChild(tfoot);

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
    (sticky ? " sticky left-0 bg-gray-50" : "") +
    (accent ? " text-blue-600" : "");
  el.textContent = text;
  return el;
}

function td(text, sticky, bold) {
  const el = document.createElement("td");
  el.className =
    "px-5 py-2" +
    (sticky ? " sticky left-0 bg-inherit font-medium text-gray-700" : " text-gray-600") +
    (bold ? " font-semibold text-gray-900" : "");
  el.textContent = text;
  return el;
}

function detectionsDetail(detections) {
  const wrap = document.createElement("div");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className =
    "w-full rounded-lg bg-blue-600 px-4 py-2.5 font-medium text-white hover:bg-blue-700";
  toggle.textContent = `Show all detections (${detections.length})`;

  const tableWrap = document.createElement("div");
  tableWrap.className =
    "mt-4 hidden overflow-x-auto rounded-xl border border-gray-200 bg-white p-4";

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
        <tr class="border-b border-gray-200 text-gray-500">
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
      tr.className = "border-b border-gray-100";
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
  summaryContainer.appendChild(summaryTable(data.pages, data.detections));
  summaryContainer.classList.remove("hidden");

  resultsEl.innerHTML = "";
  resultsEl.classList.remove("hidden");

  for (const page of data.pages) {
    const card = document.createElement("div");
    card.className = "rounded-xl border border-gray-200 bg-white p-6";

    const title = document.createElement("h3");
    title.className = "mb-4 text-sm font-semibold text-gray-700";
    title.textContent = `Page ${page.page}` + (page.status === "failed" ? " — failed" : "");
    card.appendChild(title);

    if (page.status === "failed") {
      const err = document.createElement("p");
      err.className = "text-sm text-red-600";
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

  try {
    const response = await fetch("/api/detect", { method: "POST", body: form });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Detection failed.");
    }

    const data = await readDetectStream(response, setStatus);
    setStatus("");
    renderResults(data);
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
