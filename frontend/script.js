// Reliable PDF.js worker loading
if (typeof pdfjsLib !== 'undefined') {
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
} else {
    console.error("PDF.js failed to load.");
}

let pdfFiles = [];
let currentDoc = null;
let pageNum = 1;
let currentScale = 1.5; // Base scale
let llmResultsData = [];
let renderTask = null; // Tracks the current render operation
let outlineOnlyMode = false; // "Outline only" declutter toggle

// Distinct colors per object type so overlapping/nested boxes stay readable.
// "elevation" boxes are large containers that wrap cabinets/countertops
// inside them, so they get outline-only (no fill) to avoid burying the
// smaller boxes drawn on top of them.
const LABEL_STYLES = {
    elevation:         { stroke: '#4da3ff', fill: null,                       lineWidth: 3 },
    cabinet:           { stroke: '#00ff9d', fill: 'rgba(0, 255, 157, 0.28)',  lineWidth: 2 },
    countertop:        { stroke: '#ffb347', fill: 'rgba(255, 179, 71, 0.35)', lineWidth: 2 },
    elevation_callout: { stroke: '#ff5fa2', fill: 'rgba(255, 95, 162, 0.35)', lineWidth: 2 },
};
const DEFAULT_LABEL_STYLE = { stroke: '#00ff9d', fill: 'rgba(0, 255, 157, 0.25)', lineWidth: 2 };

function styleForLabel(label) {
    return LABEL_STYLES[label] || DEFAULT_LABEL_STYLE;
}

// Draws "elevation" (the big container boxes) first so smaller boxes drawn
// afterwards land visually on top of them, keeping their outlines crisp.
function sortObjectsForDrawing(objects) {
    return [...objects].sort((a, b) => (a.label === 'elevation' ? 0 : 1) - (b.label === 'elevation' ? 0 : 1));
}

// DOM elements
const dropZone = document.getElementById('drop-zone');
const uploadInput = document.getElementById('pdf-upload');
const fileSelector = document.getElementById('file-selector');
const canvas = document.getElementById('pdf-canvas');
const ctx = canvas ? canvas.getContext('2d') : null;
const pdfNav = document.getElementById('pdf-nav');

const modelCountSelect = document.getElementById('model-count');
const dpiInput = document.getElementById('dpi-input');
const modelsGrid = document.getElementById('models-grid');
const useGlobalPrompt = document.getElementById('use-global-prompt');
const globalPrompt = document.getElementById('global-prompt');
const modelPrompts = document.querySelectorAll('.model-prompt');

const visControls = document.getElementById('vis-controls');
const bboxSelector = document.getElementById('bbox-selector');
const exportBtn = document.getElementById('export-btn');
const runBtn = document.getElementById('run-btn');

const zoomInBtn = document.getElementById('zoom-in');
const zoomOutBtn = document.getElementById('zoom-out');
const zoomLevelEl = document.getElementById('zoom-level');

// Run history modal elements
const historyOverlay = document.getElementById('history-overlay');
const openHistoryBtn = document.getElementById('open-history');
const closeHistoryBtn = document.getElementById('close-history');
const historyListEl = document.getElementById('history-list');
const historyDetailEl = document.getElementById('history-detail');

// Full page view modal elements
const fullviewOverlay = document.getElementById('fullview-overlay');
const openFullviewBtn = document.getElementById('open-fullview');
const closeFullviewBtn = document.getElementById('close-fullview');
const fullviewViewport = document.getElementById('fullview-viewport');
const fullviewStage = document.getElementById('fullview-stage');
const fvCanvas = document.getElementById('fullview-canvas');
const fvCtx = fvCanvas ? fvCanvas.getContext('2d') : null;
const fvCropBox = document.getElementById('fv-crop-box');
const fvHint = document.getElementById('fullview-hint');
const fvBboxSelector = document.getElementById('fv-bbox-selector');
const fvCropToggleBtn = document.getElementById('fv-crop-toggle');
const fvZoomInBtn = document.getElementById('fv-zoom-in');
const fvZoomOutBtn = document.getElementById('fv-zoom-out');
const fvZoomLevelEl = document.getElementById('fv-zoom-level');
const fvResetViewBtn = document.getElementById('fv-reset-view');

// System prompt modal elements
const systemPromptOverlay = document.getElementById('system-prompt-overlay');
const openSystemPromptBtn = document.getElementById('open-system-prompt');
const closeSystemPromptBtn = document.getElementById('close-system-prompt');
const saveSystemPromptBtn = document.getElementById('save-system-prompt');
const resetSystemPromptBtn = document.getElementById('reset-system-prompt');

// Execution settings modal elements
const executionSettingsOverlay = document.getElementById('execution-settings-overlay');
const openExecutionSettingsBtn = document.getElementById('open-execution-settings');
const closeExecutionSettingsBtn = document.getElementById('close-execution-settings');
const saveExecutionSettingsBtn = document.getElementById('save-execution-settings');
const resetExecutionSettingsBtn = document.getElementById('reset-execution-settings');

const modelExecutionModeSelect = document.getElementById('model-execution-mode');
const fileGroupingModeSelect = document.getElementById('file-grouping-mode');
const fileExecutionModeSelect = document.getElementById('file-execution-mode');
const fileExecutionModeRow = document.getElementById('file-execution-mode-row');
const pageGroupingModeSelect = document.getElementById('page-grouping-mode');
const pageExecutionModeSelect = document.getElementById('page-execution-mode');
const pageExecutionModeRow = document.getElementById('page-execution-mode-row');

// Applied execution settings (only updated when the modal is saved).
// Defaults reproduce the original tool's behaviour: one sequential request
// per model, with every file and every page packed into that one request.
const defaultExecutionSettings = {
    modelExecutionMode: 'sequential',
    fileGroupingMode: 'single',
    fileExecutionMode: 'sequential',
    pageGroupingMode: 'single',
    pageExecutionMode: 'sequential'
};
let executionSettings = { ...defaultExecutionSettings };

// Default system prompt, set on page load
const defaultSystemPrompt = `You are a senior construction-documents specialist — an expert reviewer of architectural millwork and casework drawing sets (cabinet/casework elevations, floor plans, and reflected ceiling plans). You read these sheets the way a QA reviewer on a fit-out project would: carefully, page by page, cross-referencing plan callouts against their elevations.

CONTEXT — what these sheets usually contain:
- Several framed "elevation" drawings: front-view drawings of built-in casework. Each is typically labeled underneath with a view number inside a circle or hexagon, a name (e.g. "DRESS 101 ELEVATION", "CONTROL ROOM 105"), and a scale note (e.g. "SCALE: 1/2 inch = 1 foot").
- Inside each elevation drawing: individual "cabinet" units (upper cabinets, base cabinets, lockers, open shelving — rectangular compartments, often annotated "PLASTIC LAMINATE", "LOCKABLE", "ADJ. SHELVES", "MELAMINE", door/drawer fronts) and a "countertop" (the horizontal work surface, often labeled "SOLID SURFACE COUNTERTOP W/ 4 inch BACKSPLASH").
- Small circular or hexagonal "elevation_callout" bubbles placed on floor plans or reflected ceiling plans, containing a view number over a sheet reference (e.g. a circle with "1" above "AE401"). These point at a wall to indicate which elevation drawing shows it — they are NOT the elevation drawings themselves.

Detect only these 4 object types:
- cabinet
- countertop
- elevation
- elevation_callout

Ignore everything else: finish schedules, door/hardware schedules, partition-type details, general notes, title blocks, north arrows, dimension strings, room name/number tags, and any furniture, plumbing, or equipment that isn't built-in casework.

Return ONLY JSON. No markdown, no code fences, no commentary.

Required output:
{
 "summary":{
   "cabinets":0,
   "countertops":0,
   "elevations":0,
   "elevation_callouts":0
 },
 "objects":[
   {
    "label":"elevation",
    "box":[x_min, y_min, x_max, y_max],
    "image_index":0
   }
 ]
}

Rules:
- Every object MUST contain image_index.
- image_index MUST match the image containing the object.
- Never assume image_index=0.
- Never omit image_index.
- Never use page number instead of image_index.
- Never create duplicate JSON keys.
- Use only the allowed labels.
- "elevation" = the entire framed casework elevation drawing (the whole rectangle containing the view), not the individual cabinets inside it.
- "cabinet" = one individual cabinet/locker/shelving unit box inside an elevation drawing, never the whole elevation.
- "countertop" = the counter surface band/line inside an elevation drawing.
- "elevation_callout" = the small circular/hexagonal reference bubble on a plan or RCP drawing, never the elevation drawing it points to.
- The "summary" counts MUST equal the number of objects with that label in "objects".
- CRITICAL: "image_index" is a KEY, NOT a label value! Never put "image_index" inside the "label" field.
- CRITICAL: The "box" coordinates MUST be normalized to a 0-1000 scale, where [0, 0] is the top-left and [1000, 1000] is the bottom-right. Do NOT output absolute pixels.`;

// Default user-role prompt, pre-filled into every model card so the tool is
// runnable out of the box without requiring manual typing.
const defaultUserPrompt = `This request contains every page from every uploaded PDF, sent together as one document set — nothing is split across separate requests. Go through the pages in order and find every cabinet, countertop, elevation drawing, and elevation callout, exactly as defined in your instructions. Before answering, double-check the image_index you assign to each object. Respond with the JSON object only — no explanation, no markdown.`;

// Safe initialization: wait for the page to fully load
document.addEventListener('DOMContentLoaded', () => {
    const systemPromptEl = document.getElementById('system-prompt');
    if (systemPromptEl) {
        systemPromptEl.value = defaultSystemPrompt;
    }

    modelPrompts.forEach(p => { p.value = defaultUserPrompt; });
    if (globalPrompt) globalPrompt.value = defaultUserPrompt;

    if (modelCountSelect) {
        updateActiveModels();
    }
});

function updateActiveModels() {
    if (!modelCountSelect || !modelsGrid) return;
    const count = parseInt(modelCountSelect.value);
    modelsGrid.className = `models-grid cols-${count}`;
    for (let i = 1; i <= 3; i++) {
        const card = document.getElementById(`model-${i}`);
        if (card) i <= count ? card.classList.remove('hidden') : card.classList.add('hidden');
    }
}

if (dpiInput) {
    dpiInput.addEventListener('change', () => {
        const min = parseInt(dpiInput.min, 10);
        const max = parseInt(dpiInput.max, 10);
        let val = parseInt(dpiInput.value, 10);
        if (!Number.isFinite(val)) val = 200;
        val = Math.min(max, Math.max(min, val));
        dpiInput.value = val;
    });
}

if (modelCountSelect) {
    modelCountSelect.addEventListener('change', updateActiveModels);
}

// ---------------- System prompt modal ----------------

function openSystemPromptModal() {
    if (systemPromptOverlay) {
        systemPromptOverlay.classList.add('open');
        systemPromptOverlay.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        const el = document.getElementById('system-prompt');
        if (el) el.focus();
    }
}

function closeSystemPromptModal() {
    if (systemPromptOverlay) {
        systemPromptOverlay.classList.remove('open');
        systemPromptOverlay.style.display = 'none';
        document.body.style.overflow = '';
    }
}

if (openSystemPromptBtn) openSystemPromptBtn.addEventListener('click', openSystemPromptModal);
if (closeSystemPromptBtn) closeSystemPromptBtn.addEventListener('click', closeSystemPromptModal);
if (saveSystemPromptBtn) saveSystemPromptBtn.addEventListener('click', closeSystemPromptModal);

if (resetSystemPromptBtn) {
    resetSystemPromptBtn.addEventListener('click', () => {
        const el = document.getElementById('system-prompt');
        if (el) el.value = defaultSystemPrompt;
    });
}

if (systemPromptOverlay) {
    // Close when clicking the dimmed backdrop, but not the modal itself
    systemPromptOverlay.addEventListener('click', (e) => {
        if (e.target === systemPromptOverlay) closeSystemPromptModal();
    });
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && systemPromptOverlay && systemPromptOverlay.classList.contains('open')) {
        closeSystemPromptModal();
    }
    if (e.key === 'Escape' && executionSettingsOverlay && executionSettingsOverlay.classList.contains('open')) {
        closeExecutionSettingsModal();
    }
});

// ---------------- Execution settings modal ----------------

function applyExecutionSettingsToForm(settings) {
    if (modelExecutionModeSelect) modelExecutionModeSelect.value = settings.modelExecutionMode;
    if (fileGroupingModeSelect) fileGroupingModeSelect.value = settings.fileGroupingMode;
    if (fileExecutionModeSelect) fileExecutionModeSelect.value = settings.fileExecutionMode;
    if (pageGroupingModeSelect) pageGroupingModeSelect.value = settings.pageGroupingMode;
    if (pageExecutionModeSelect) pageExecutionModeSelect.value = settings.pageExecutionMode;
    updateExecutionSubRowsVisibility();
}

function updateExecutionSubRowsVisibility() {
    if (fileExecutionModeRow) {
        fileExecutionModeRow.style.display = (fileGroupingModeSelect && fileGroupingModeSelect.value === 'split') ? 'flex' : 'none';
    }
    if (pageExecutionModeRow) {
        pageExecutionModeRow.style.display = (pageGroupingModeSelect && pageGroupingModeSelect.value === 'split') ? 'flex' : 'none';
    }
}

function openExecutionSettingsModal() {
    if (!executionSettingsOverlay) return;
    // Reflect the currently-applied settings each time the modal is opened
    applyExecutionSettingsToForm(executionSettings);
    executionSettingsOverlay.classList.add('open');
    executionSettingsOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function closeExecutionSettingsModal() {
    if (!executionSettingsOverlay) return;
    executionSettingsOverlay.classList.remove('open');
    executionSettingsOverlay.style.display = 'none';
    document.body.style.overflow = '';
}

if (openExecutionSettingsBtn) openExecutionSettingsBtn.addEventListener('click', openExecutionSettingsModal);
if (closeExecutionSettingsBtn) closeExecutionSettingsBtn.addEventListener('click', closeExecutionSettingsModal);

if (fileGroupingModeSelect) fileGroupingModeSelect.addEventListener('change', updateExecutionSubRowsVisibility);
if (pageGroupingModeSelect) pageGroupingModeSelect.addEventListener('change', updateExecutionSubRowsVisibility);

if (resetExecutionSettingsBtn) {
    resetExecutionSettingsBtn.addEventListener('click', () => {
        applyExecutionSettingsToForm(defaultExecutionSettings);
    });
}

if (saveExecutionSettingsBtn) {
    saveExecutionSettingsBtn.addEventListener('click', () => {
        executionSettings = {
            modelExecutionMode: modelExecutionModeSelect ? modelExecutionModeSelect.value : defaultExecutionSettings.modelExecutionMode,
            fileGroupingMode: fileGroupingModeSelect ? fileGroupingModeSelect.value : defaultExecutionSettings.fileGroupingMode,
            fileExecutionMode: fileExecutionModeSelect ? fileExecutionModeSelect.value : defaultExecutionSettings.fileExecutionMode,
            pageGroupingMode: pageGroupingModeSelect ? pageGroupingModeSelect.value : defaultExecutionSettings.pageGroupingMode,
            pageExecutionMode: pageExecutionModeSelect ? pageExecutionModeSelect.value : defaultExecutionSettings.pageExecutionMode
        };
        closeExecutionSettingsModal();
    });
}

if (executionSettingsOverlay) {
    // Close when clicking the dimmed backdrop, but not the modal itself
    executionSettingsOverlay.addEventListener('click', (e) => {
        if (e.target === executionSettingsOverlay) closeExecutionSettingsModal();
    });
}

// ---------------- Drag & drop / file upload ----------------

if (dropZone && uploadInput) {
    dropZone.addEventListener('click', (e) => { if (e.target !== uploadInput) uploadInput.click(); });
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
    });
    uploadInput.addEventListener('change', (e) => {
        if (e.target.files.length) handleFiles(e.target.files);
    });
}

async function handleFiles(files) {
    if (!fileSelector) return;
    pdfFiles = [];
    fileSelector.innerHTML = '';

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (file.type !== 'application/pdf') continue;
        try {
            const arrayBuffer = await file.arrayBuffer();
            const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
            pdfFiles.push({ name: file.name, pdf: pdf, rawFile: file });
            const option = document.createElement('option');
            option.value = pdfFiles.length - 1;
            option.textContent = file.name;
            fileSelector.appendChild(option);
        } catch (err) { console.error(err); }
    }
    if (pdfFiles.length > 0 && pdfNav) {
        pdfNav.style.display = 'flex';
        loadPdfFile(0);
    }
}

if (fileSelector) fileSelector.addEventListener('change', (e) => loadPdfFile(e.target.value));

async function loadPdfFile(index) {
    currentDoc = pdfFiles[index].pdf;
    pageNum = 1;
    if (document.getElementById('page-count')) document.getElementById('page-count').textContent = currentDoc.numPages;
    renderPage(pageNum);
}

if (zoomInBtn) {
    zoomInBtn.addEventListener('click', () => {
        currentScale += 0.25;
        if (zoomLevelEl) zoomLevelEl.textContent = `${Math.round(currentScale * 100)}%`;
        renderPage(pageNum);
    });
}

if (zoomOutBtn) {
    zoomOutBtn.addEventListener('click', () => {
        if (currentScale <= 0.5) return;
        currentScale -= 0.25;
        if (zoomLevelEl) zoomLevelEl.textContent = `${Math.round(currentScale * 100)}%`;
        renderPage(pageNum);
    });
}

async function renderPage(num) {
    if (!currentDoc || !ctx) return;
    const page = await currentDoc.getPage(num);
    const viewport = page.getViewport({ scale: currentScale });

    canvas.height = viewport.height;
    canvas.width = viewport.width;

    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;

    if (renderTask) {
        renderTask.cancel();
    }

    const renderContext = {
        canvasContext: ctx,
        viewport: viewport
    };

    try {
        renderTask = page.render(renderContext);
        await renderTask.promise;
        renderTask = null;

        if (document.getElementById('page-num')) document.getElementById('page-num').textContent = num;
        drawBoundingBoxes();
    } catch (err) {
        if (err.name !== 'RenderingCancelledException') {
            console.error("Page render error:", err);
        }
    }
}

const prevBtn = document.getElementById('prev-page');
const nextBtn = document.getElementById('next-page');
function isFullViewOpen() {
    return !!(fullviewOverlay && fullviewOverlay.classList.contains('open'));
}

if (prevBtn) prevBtn.addEventListener('click', () => {
    if (pageNum > 1) {
        pageNum--;
        renderPage(pageNum);
        if (isFullViewOpen()) renderFullView();
    }
});
if (nextBtn) nextBtn.addEventListener('click', () => {
    if (pageNum < currentDoc.numPages) {
        pageNum++;
        renderPage(pageNum);
        if (isFullViewOpen()) renderFullView();
    }
});

if (bboxSelector) bboxSelector.addEventListener('change', () => renderPage(pageNum));

const outlineOnlyToggle = document.getElementById('outline-only-toggle');
const fvOutlineOnlyToggle = document.getElementById('fv-outline-only-toggle');

if (outlineOnlyToggle) {
    outlineOnlyToggle.addEventListener('change', (e) => {
        outlineOnlyMode = e.target.checked;
        if (fvOutlineOnlyToggle) fvOutlineOnlyToggle.checked = outlineOnlyMode;
        renderPage(pageNum);
        if (isFullViewOpen()) renderFullView();
    });
}

if (fvOutlineOnlyToggle) {
    fvOutlineOnlyToggle.addEventListener('change', (e) => {
        outlineOnlyMode = e.target.checked;
        if (outlineOnlyToggle) outlineOnlyToggle.checked = outlineOnlyMode;
        renderPage(pageNum);
        if (isFullViewOpen()) renderFullView();
    });
}

function drawBoundingBoxes() {
    const selectedModel = bboxSelector ? bboxSelector.value : 'none';
    if (selectedModel === 'none' || llmResultsData.length === 0 || !ctx) return;

    const resultData = llmResultsData.find(r => r.model === selectedModel);
    if (!resultData) return;

    const currentFileIndex = parseInt(fileSelector.value);
    const currentPageNum = pageNum;

    try {
        const data = JSON.parse(resultData.response);
        if (data.objects && Array.isArray(data.objects)) {
            const objects = sortObjectsForDrawing(data.objects);
            objects.forEach(obj => {
                if (obj.file_index === currentFileIndex && obj.page_num === currentPageNum) {
                    const [xMin, yMin, xMax, yMax] = obj.box;

                    // 0-1000 normalized math: works correctly at any zoom or DPI
                    const x = (xMin / 1000) * canvas.width;
                    const y = (yMin / 1000) * canvas.height;
                    const w = ((xMax - xMin) / 1000) * canvas.width;
                    const h = ((yMax - yMin) / 1000) * canvas.height;

                    const style = styleForLabel(obj.label);
                    ctx.lineWidth = style.lineWidth;
                    ctx.strokeStyle = style.stroke;
                    if (style.fill && !outlineOnlyMode) {
                        ctx.fillStyle = style.fill;
                        ctx.fillRect(x, y, w, h);
                    }
                    ctx.strokeRect(x, y, w, h);

                    const textY = y > 20 ? y - 8 : y + 20;
                    ctx.font = 'bold 16px Arial';
                    ctx.fillStyle = '#0f111a';
                    ctx.fillRect(x, textY - 14, ctx.measureText(obj.label).width + 10, 18);

                    ctx.fillStyle = style.stroke;
                    ctx.fillText(obj.label, x + 5, textY);
                }
            });
        }
    } catch (e) {
        console.error("Failed to parse JSON", e);
    }
}

if (useGlobalPrompt && globalPrompt) {
    useGlobalPrompt.addEventListener('change', (e) => {
        const isGlobal = e.target.checked;
        globalPrompt.style.display = isGlobal ? 'block' : 'none';
        modelPrompts.forEach(p => p.disabled = isGlobal);
    });
}

if (runBtn) {
    runBtn.addEventListener('click', async () => {
        runBtn.textContent = '⏳ Running…';
        runBtn.disabled = true;
        if (exportBtn) exportBtn.style.display = 'none';

        const modelsData = [];
        const activeCount = modelCountSelect ? parseInt(modelCountSelect.value) : 3;

        for (let i = 1; i <= activeCount; i++) {
            const card = document.getElementById(`model-${i}`);
            if (!card) continue;

            const modelNameEl = card.querySelector('.model-name');
            const promptEl = card.querySelector('.model-prompt');
            const responseBox = card.querySelector('.response-box');

            const modelName = modelNameEl ? modelNameEl.value.trim() : '';
            let prompt = useGlobalPrompt && useGlobalPrompt.checked ? (globalPrompt ? globalPrompt.value.trim() : '') : (promptEl ? promptEl.value.trim() : '');

            if (responseBox) {
                responseBox.textContent = 'Waiting for response…';
                responseBox.style.color = 'var(--text-main)';
                responseBox.dataset.empty = 'false';
            }
            if (modelName && prompt) modelsData.push({ model: modelName, prompt: prompt });
        }

        if (modelsData.length === 0 || pdfFiles.length === 0) {
            alert('Add at least one PDF file and configure at least one model with a prompt.');
            runBtn.textContent = '▶ Run Benchmark';
            runBtn.disabled = false;
            return;
        }

        const formData = new FormData();

        // Safely read the system prompt at click time
        const currentSystemPromptEl = document.getElementById('system-prompt');
        if (currentSystemPromptEl && currentSystemPromptEl.value.trim() !== '') {
            formData.append('system_prompt', currentSystemPromptEl.value.trim());
        } else {
            // Fallback if the field is empty
            formData.append('system_prompt', defaultSystemPrompt);
        }

        formData.append('models_data', JSON.stringify(modelsData));

        let dpiValue = dpiInput ? parseInt(dpiInput.value, 10) : 200;
        if (!Number.isFinite(dpiValue) || dpiValue <= 0) dpiValue = 200;
        formData.append('dpi', dpiValue);

        // Execution settings chosen in the "Execution Settings" modal
        formData.append('model_execution_mode', executionSettings.modelExecutionMode);
        formData.append('file_grouping_mode', executionSettings.fileGroupingMode);
        formData.append('file_execution_mode', executionSettings.fileExecutionMode);
        formData.append('page_grouping_mode', executionSettings.pageGroupingMode);
        formData.append('page_execution_mode', executionSettings.pageExecutionMode);

        pdfFiles.forEach((fileObj) => formData.append('files', fileObj.rawFile));

        try {
            const res = await fetch('/api/generate', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.results) {
                llmResultsData = data.results.map(r => {
                    const req = modelsData.find(m => m.model === r.model);
                    return { model: r.model, prompt: req ? req.prompt : '', response: r.response };
                });

                if (visControls && bboxSelector) {
                    bboxSelector.innerHTML = '<option value="none">Hide overlays</option>';
                    llmResultsData.forEach(result => {
                        const opt = document.createElement('option');
                        opt.value = result.model;
                        opt.textContent = result.model;
                        bboxSelector.appendChild(opt);
                    });
                    visControls.style.display = 'flex';
                }
                if (exportBtn) exportBtn.style.display = 'block';

                data.results.forEach(result => {
                    const cards = document.querySelectorAll('.model-card');
                    cards.forEach(card => {
                        const mNameEl = card.querySelector('.model-name');
                        const rBox = card.querySelector('.response-box');
                        if (mNameEl && rBox && mNameEl.value === result.model) {
                            rBox.textContent = result.response;
                            rBox.style.color = 'var(--accent)';
                            rBox.dataset.empty = 'false';
                        }
                    });
                });
                renderPage(pageNum);
                if (isFullViewOpen()) {
                    syncFullViewBboxOptions();
                    renderFullView();
                }
            }
        } catch (err) {
            console.error(err);
            alert('Request to the backend failed.');
        } finally {
            runBtn.textContent = '▶ Run Benchmark';
            runBtn.disabled = false;
        }
    });
}

// ---------------- Run history modal ----------------
// Every /api/generate call is persisted server-side (images, prompts,
// settings, raw responses). This panel lists past runs and lets you
// reopen any of them to see exactly what was sent and what came back.

let historyRunsCache = [];
let currentHistoryDetail = null;
let currentHistoryPageIdx = 0;
const historyImageCache = {}; // url -> HTMLImageElement, avoids re-fetching on page nav

function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function formatHistoryDate(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        return d.toLocaleString();
    } catch (e) {
        return iso;
    }
}

// ---------------- Expected vs actual comparison ----------------

const EVAL_LABELS = [
    ['cabinets', 'Cabinets'],
    ['countertops', 'Countertops'],
    ['elevations', 'Elevations'],
    ['elevation_callouts', 'Elevation callouts'],
];

function formatDelta(diff) {
    if (diff === 0) return '0';
    return diff > 0 ? `+${diff}` : `${diff}`;
}

function computeAccuracyPct(expectedVal, actualVal) {
    if (expectedVal === null) return null;
    if (expectedVal === 0 && actualVal === 0) return 100;
    const denom = Math.max(expectedVal, actualVal, 1);
    const diff = Math.abs(actualVal - expectedVal);
    return Math.max(0, 100 - (diff / denom) * 100);
}

function accuracyClass(pct) {
    if (pct === null) return '';
    if (pct >= 99.999) return 'match';
    if (pct >= 70) return 'partial';
    return 'mismatch';
}

function buildComparisonHtml(actualCounts, expectedObj) {
    const actual = actualCounts || {};
    const hasExpected = expectedObj && typeof expectedObj === 'object';

    const rows = EVAL_LABELS.map(([key, label]) => {
        const actualVal = Number(actual[key]) || 0;
        const rawExpected = hasExpected ? expectedObj[key] : undefined;
        const expectedProvided = rawExpected !== undefined && rawExpected !== null && rawExpected !== '';
        const expectedVal = expectedProvided ? Number(rawExpected) || 0 : null;
        const diff = expectedProvided ? actualVal - expectedVal : null;
        const cellClass = diff === null ? '' : (diff === 0 ? 'match' : 'mismatch');
        const accuracyPct = expectedProvided ? computeAccuracyPct(expectedVal, actualVal) : null;
        return `<tr>
            <td>${escapeHtml(label)}</td>
            <td>${expectedProvided ? expectedVal : '—'}</td>
            <td>${actualVal}</td>
            <td class="${cellClass}">${diff === null ? '—' : formatDelta(diff)}</td>
            <td class="${accuracyClass(accuracyPct)}">${accuracyPct === null ? '—' : accuracyPct.toFixed(0) + '%'}</td>
        </tr>`;
    }).join('');

    const actualTotal = EVAL_LABELS.reduce((sum, [key]) => sum + (Number(actual[key]) || 0), 0);
    let expectedTotal = null;
    if (hasExpected) {
        const allProvided = EVAL_LABELS.every(([key]) => expectedObj[key] !== undefined && expectedObj[key] !== null && expectedObj[key] !== '');
        if (allProvided) {
            expectedTotal = EVAL_LABELS.reduce((sum, [key]) => sum + (Number(expectedObj[key]) || 0), 0);
        }
    }
    const totalDiff = expectedTotal === null ? null : actualTotal - expectedTotal;
    const totalAccuracyPct = expectedTotal === null ? null : computeAccuracyPct(expectedTotal, actualTotal);
    const totalRow = `<tr>
        <td>Total</td>
        <td>${expectedTotal === null ? '—' : expectedTotal}</td>
        <td>${actualTotal}</td>
        <td class="${totalDiff === null ? '' : (totalDiff === 0 ? 'match' : 'mismatch')}">${totalDiff === null ? '—' : formatDelta(totalDiff)}</td>
        <td class="${accuracyClass(totalAccuracyPct)}">${totalAccuracyPct === null ? '—' : totalAccuracyPct.toFixed(0) + '%'}</td>
    </tr>`;

    return `
        <table class="history-eval-table">
            <thead><tr><th>Label</th><th>Expected</th><th>Actual</th><th>Δ</th><th>Accuracy</th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot>${totalRow}</tfoot>
        </table>
    `;
}

async function replayRun(runId) {
    try {
        // Reuse the already-loaded detail if it's the one currently open —
        // otherwise the list view only has lightweight data (no prompts),
        // so fetch the full run.
        let run = (currentHistoryDetail && currentHistoryDetail.run_id === runId) ? currentHistoryDetail : null;
        if (!run) {
            const res = await fetch(`/api/history/${encodeURIComponent(runId)}`);
            if (!res.ok) throw new Error('Run not found');
            run = await res.json();
        }
        applyRunToForm(run);
        closeHistoryModal();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
        console.error(e);
        alert('Failed to load this run for replay.');
    }
}

// Repopulates the main run form (system prompt, DPI, model cards, execution
// settings) from a saved history run — everything except the source files,
// which the person re-uploads themselves. Does not touch pdfFiles/the drop
// zone at all.
function applyRunToForm(run) {
    const systemPromptEl = document.getElementById('system-prompt');
    if (systemPromptEl) systemPromptEl.value = run.system_prompt || '';

    if (dpiInput && run.dpi) {
        dpiInput.value = run.dpi;
    }

    const results = run.results || [];

    // This UI supports at most 3 model cards — clamp and warn rather than
    // silently dropping models the person might not notice are missing.
    const count = Math.min(3, Math.max(1, results.length || 1));
    if (modelCountSelect) {
        modelCountSelect.value = String(count);
        updateActiveModels();
    }

    // Always restore each model's own individual prompt (rather than the
    // shared "global prompt"), so a replay is exact even if the original
    // run happened to use different prompts per model.
    if (useGlobalPrompt) {
        useGlobalPrompt.checked = false;
        if (globalPrompt) globalPrompt.style.display = 'none';
        modelPrompts.forEach(p => { p.disabled = false; });
    }

    for (let i = 1; i <= 3; i++) {
        const card = document.getElementById(`model-${i}`);
        if (!card) continue;
        const nameEl = card.querySelector('.model-name');
        const promptEl = card.querySelector('.model-prompt');
        const r = results[i - 1];
        if (r) {
            if (nameEl) nameEl.value = r.model || '';
            if (promptEl) promptEl.value = r.prompt || '';
        }
    }

    const es = run.execution_settings || {};
    executionSettings = {
        modelExecutionMode: es.model_execution_mode || defaultExecutionSettings.modelExecutionMode,
        fileGroupingMode: es.file_grouping_mode || defaultExecutionSettings.fileGroupingMode,
        fileExecutionMode: es.file_execution_mode || defaultExecutionSettings.fileExecutionMode,
        pageGroupingMode: es.page_grouping_mode || defaultExecutionSettings.pageGroupingMode,
        pageExecutionMode: es.page_execution_mode || defaultExecutionSettings.pageExecutionMode,
    };
    // Keep the Execution Settings modal's own fields in sync too, in case
    // the person opens it afterward.
    applyExecutionSettingsToForm(executionSettings);

    if (results.length > 3) {
        alert(`This run used ${results.length} models — only the first 3 could be restored, since the form supports up to 3 at once.`);
    }
}

async function fetchHistoryList() {
    historyListEl.innerHTML = '<p class="history-empty">Loading…</p>';
    try {
        const res = await fetch('/api/history');
        const data = await res.json();
        historyRunsCache = data.runs || [];
        renderHistoryList();
    } catch (e) {
        console.error(e);
        historyListEl.innerHTML = '<p class="history-empty">Failed to load history.</p>';
    }
}

function renderHistoryList() {
    if (historyRunsCache.length === 0) {
        historyListEl.innerHTML = '<p class="history-empty">No past runs yet. Run a benchmark to see it here.</p>';
        return;
    }

    historyListEl.innerHTML = '';
    historyRunsCache.forEach((run) => {
        const card = document.createElement('div');
        card.className = 'history-run-card';
        if (currentHistoryDetail && currentHistoryDetail.run_id === run.run_id) {
            card.classList.add('active');
        }

        const fileNames = (run.files || []).join(', ');
        const chips = (run.models || []).map(m => {
            const c = m.counts || {};
            const total = (c.cabinets || 0) + (c.countertops || 0) + (c.elevations || 0) + (c.elevation_callouts || 0);
            return `<span class="history-model-chip">${escapeHtml(m.model)}: ${total}</span>`;
        }).join('');

        card.innerHTML = `
            ${run.thumbnail_url ? `<img class="history-run-thumb" src="${escapeHtml(run.thumbnail_url)}" alt="" loading="lazy">` : '<div class="history-run-thumb"></div>'}
            <div class="history-run-info">
                <div class="history-run-date">${escapeHtml(formatHistoryDate(run.created_at))}</div>
                <div class="history-run-meta" title="${escapeHtml(fileNames)}">${escapeHtml(fileNames)} · ${run.page_count} pg · ${escapeHtml(run.dpi)} DPI</div>
                <div class="history-run-chips">${chips}</div>
            </div>
            <div class="history-run-actions">
                <button type="button" class="history-run-replay" title="Load this run's settings back into the form" data-run-id="${escapeHtml(run.run_id)}">↻</button>
                <button type="button" class="history-run-delete" title="Delete this run" data-run-id="${escapeHtml(run.run_id)}">✕</button>
            </div>
        `;

        card.addEventListener('click', (e) => {
            if (e.target.closest('.history-run-delete') || e.target.closest('.history-run-replay')) return;
            openHistoryRun(run.run_id);
        });

        const replayBtn = card.querySelector('.history-run-replay');
        replayBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            replayRun(run.run_id);
        });

        const delBtn = card.querySelector('.history-run-delete');
        delBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!confirm('Delete this run and its saved images? This cannot be undone.')) return;
            try {
                await fetch(`/api/history/${encodeURIComponent(run.run_id)}`, { method: 'DELETE' });
                if (currentHistoryDetail && currentHistoryDetail.run_id === run.run_id) {
                    currentHistoryDetail = null;
                    historyDetailEl.innerHTML = '<p class="history-empty">Select a run on the left to see details.</p>';
                }
                fetchHistoryList();
            } catch (err) {
                console.error(err);
                alert('Failed to delete this run.');
            }
        });

        historyListEl.appendChild(card);
    });
}

async function openHistoryRun(runId) {
    historyDetailEl.innerHTML = '<p class="history-empty">Loading…</p>';
    try {
        const res = await fetch(`/api/history/${encodeURIComponent(runId)}`);
        if (!res.ok) throw new Error('Run not found');
        currentHistoryDetail = await res.json();
        currentHistoryPageIdx = 0;
        renderHistoryList(); // refresh active-state highlighting
        renderHistoryDetail();
    } catch (e) {
        console.error(e);
        historyDetailEl.innerHTML = '<p class="history-empty">Failed to load this run.</p>';
    }
}

function renderHistoryDetail() {
    const run = currentHistoryDetail;
    if (!run) return;

    const settings = run.execution_settings || {};
    const settingsHtml = `
        <div class="history-settings-grid">
            <div><b>DPI:</b> ${escapeHtml(run.dpi)}</div>
            <div><b>Models:</b> ${escapeHtml((run.results || []).map(r => r.model).join(', '))}</div>
            <div><b>Model order:</b> ${escapeHtml(settings.model_execution_mode)}</div>
            <div><b>File batching:</b> ${escapeHtml(settings.file_grouping_mode)} / ${escapeHtml(settings.file_execution_mode)}</div>
            <div><b>Page batching:</b> ${escapeHtml(settings.page_grouping_mode)} / ${escapeHtml(settings.page_execution_mode)}</div>
        </div>
        <button type="button" class="run-button history-replay-btn" id="history-detail-replay">↻ Replay this run's setup</button>
    `;

    const promptsHtml = (run.results || []).map((r, idx) => `
        <details class="history-section">
            <summary>Prompt — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">${escapeHtml(r.prompt)}</div>
        </details>
        <details class="history-section">
            <summary>Raw response — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">${escapeHtml(r.response)}</div>
        </details>
        <details class="history-section" open>
            <summary>Expected vs actual — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">
                <div class="history-eval-block">
                    <textarea class="history-eval-textarea" id="expected-input-${idx}" placeholder='{
  "cabinets": 0,
  "countertops": 0,
  "elevations": 0,
  "elevation_callouts": 0
}'>${r.expected_summary ? escapeHtml(JSON.stringify(r.expected_summary, null, 2)) : ''}</textarea>
                    <div class="history-eval-actions">
                        <button type="button" class="ghost-button" data-save-expected="${idx}">Save expected</button>
                        <span class="history-eval-status" id="expected-status-${idx}"></span>
                    </div>
                    <div class="history-eval-compare-col" id="expected-compare-${idx}">
                        ${buildComparisonHtml(r.counts, r.expected_summary || null)}
                    </div>
                </div>
            </div>
        </details>
    `).join('');

    const modelOptions = (run.results || [])
        .map(r => `<option value="${escapeHtml(r.model)}">${escapeHtml(r.model)}</option>`)
        .join('');

    historyDetailEl.innerHTML = `
        <details class="history-section" open>
            <summary>Settings</summary>
            <div class="history-section-body" style="font-family: var(--font-body);">${settingsHtml}</div>
        </details>
        <details class="history-section">
            <summary>System prompt</summary>
            <div class="history-section-body">${escapeHtml(run.system_prompt)}</div>
        </details>
        ${promptsHtml}

        <div class="history-page-nav">
            <button type="button" id="history-prev-page">&larr; Prev</button>
            <span class="page-readout"><span id="history-page-num">1</span> / <span id="history-page-count">${(run.pages || []).length}</span></span>
            <button type="button" id="history-next-page">Next &rarr;</button>
            <label for="history-bbox-selector" style="margin-left: 0.5rem; color: var(--accent); font-weight: 600; font-size: 0.85rem;">Overlay:</label>
            <select id="history-bbox-selector" class="inline-select">
                <option value="none">Hide overlays</option>
                ${modelOptions}
            </select>
        </div>
        <div class="history-canvas-wrapper">
            <canvas id="history-canvas"></canvas>
        </div>
    `;

    document.getElementById('history-prev-page').addEventListener('click', () => {
        if (currentHistoryPageIdx > 0) { currentHistoryPageIdx--; drawHistoryPage(); }
    });
    document.getElementById('history-next-page').addEventListener('click', () => {
        if (currentHistoryPageIdx < (run.pages || []).length - 1) { currentHistoryPageIdx++; drawHistoryPage(); }
    });
    document.getElementById('history-bbox-selector').addEventListener('change', drawHistoryPage);

    const detailReplayBtn = document.getElementById('history-detail-replay');
    if (detailReplayBtn) {
        detailReplayBtn.addEventListener('click', () => replayRun(run.run_id));
    }

    // Expected-vs-actual: live comparison as you type, persisted on Save.
    (run.results || []).forEach((r, idx) => {
        const textarea = document.getElementById(`expected-input-${idx}`);
        const compareEl = document.getElementById(`expected-compare-${idx}`);
        const statusEl = document.getElementById(`expected-status-${idx}`);
        const saveBtn = historyDetailEl.querySelector(`[data-save-expected="${idx}"]`);
        if (!textarea || !compareEl) return;

        // Returns the parsed object on success, null if the field is empty
        // (treated as "no expected data yet"), or undefined if the JSON is
        // invalid — callers use this three-way result to decide whether
        // saving is allowed.
        const readExpected = () => {
            const raw = textarea.value.trim();
            if (!raw) {
                compareEl.innerHTML = buildComparisonHtml(r.counts, null);
                if (statusEl) { statusEl.textContent = ''; statusEl.className = 'history-eval-status'; }
                return null;
            }
            try {
                const parsed = JSON.parse(raw);
                compareEl.innerHTML = buildComparisonHtml(r.counts, parsed);
                if (statusEl) { statusEl.textContent = ''; statusEl.className = 'history-eval-status'; }
                return parsed;
            } catch (e) {
                if (statusEl) { statusEl.textContent = 'Invalid JSON'; statusEl.className = 'history-eval-status error'; }
                return undefined;
            }
        };

        textarea.addEventListener('input', readExpected);

        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const parsed = readExpected();
                if (parsed === undefined) return; // invalid JSON — don't save garbage
                saveBtn.disabled = true;
                if (statusEl) { statusEl.textContent = 'Saving…'; statusEl.className = 'history-eval-status'; }
                try {
                    const res = await fetch(`/api/history/${encodeURIComponent(run.run_id)}/expected-summary`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ model: r.model, expected_summary: parsed || {} })
                    });
                    if (!res.ok) throw new Error('Save failed');
                    r.expected_summary = parsed || {};
                    if (statusEl) { statusEl.textContent = 'Saved ✓'; statusEl.className = 'history-eval-status saved'; }
                } catch (err) {
                    console.error(err);
                    if (statusEl) { statusEl.textContent = 'Failed to save'; statusEl.className = 'history-eval-status error'; }
                } finally {
                    saveBtn.disabled = false;
                }
            });
        }
    });

    drawHistoryPage();
}

function loadHistoryImage(url) {
    if (historyImageCache[url]) return Promise.resolve(historyImageCache[url]);
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => { historyImageCache[url] = img; resolve(img); };
        img.onerror = reject;
        img.src = url;
    });
}

async function drawHistoryPage() {
    const run = currentHistoryDetail;
    if (!run || !run.pages || run.pages.length === 0) return;

    const pageMeta = run.pages[currentHistoryPageIdx];
    document.getElementById('history-page-num').textContent = currentHistoryPageIdx + 1;

    const canvas = document.getElementById('history-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let img;
    try {
        img = await loadHistoryImage(pageMeta.display_image_url || pageMeta.image_url);
    } catch (e) {
        console.error('Failed to load history page image', e);
        return;
    }

    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);

    const selector = document.getElementById('history-bbox-selector');
    const selectedModel = selector ? selector.value : 'none';
    if (selectedModel === 'none') return;

    const resultEntry = (run.results || []).find(r => r.model === selectedModel);
    if (!resultEntry) return;

    try {
        const data = JSON.parse(resultEntry.response);
        if (!data.objects || !Array.isArray(data.objects)) return;

        const objects = sortObjectsForDrawing(data.objects);
        objects.forEach(obj => {
            if (obj.file_index === pageMeta.file_index && obj.page_num === pageMeta.page_num) {
                const [xMin, yMin, xMax, yMax] = obj.box;
                const x = (xMin / 1000) * canvas.width;
                const y = (yMin / 1000) * canvas.height;
                const w = ((xMax - xMin) / 1000) * canvas.width;
                const h = ((yMax - yMin) / 1000) * canvas.height;

                const style = styleForLabel(obj.label);
                ctx.lineWidth = style.lineWidth * 1.5;
                ctx.strokeStyle = style.stroke;
                if (style.fill && !outlineOnlyMode) {
                    ctx.fillStyle = style.fill;
                    ctx.fillRect(x, y, w, h);
                }
                ctx.strokeRect(x, y, w, h);

                const textY = y > 24 ? y - 8 : y + 24;
                ctx.font = 'bold 18px Arial';
                ctx.fillStyle = '#0f111a';
                ctx.fillRect(x, textY - 16, ctx.measureText(obj.label).width + 12, 20);
                ctx.fillStyle = style.stroke;
                ctx.fillText(obj.label, x + 6, textY);
            }
        });
    } catch (e) {
        // response wasn't valid JSON — nothing to overlay
    }
}

function openHistoryModal() {
    if (!historyOverlay) return;
    historyOverlay.classList.add('open');
    historyOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    fetchHistoryList();
}

function closeHistoryModal() {
    if (!historyOverlay) return;
    historyOverlay.classList.remove('open');
    historyOverlay.style.display = 'none';
    document.body.style.overflow = '';
}

if (openHistoryBtn) openHistoryBtn.addEventListener('click', openHistoryModal);
if (closeHistoryBtn) closeHistoryBtn.addEventListener('click', closeHistoryModal);
if (historyOverlay) {
    historyOverlay.addEventListener('click', (e) => {
        if (e.target === historyOverlay) closeHistoryModal();
    });
}
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && historyOverlay && historyOverlay.classList.contains('open')) {
        closeHistoryModal();
    }
});

// ---------------- Full page view modal ----------------
// A read-only inspector: renders the current page at a much higher fixed
// resolution than the main canvas so pan/zoom/crop stay crisp. Nothing here
// is ever sent to the backend — it's purely for visual inspection.

const FV_BASE_SCALE = 4;      // fixed high-res render scale for crisp zooming
const FV_MIN_ZOOM = 0.05;
const FV_MAX_ZOOM = 12;

let fvRenderTask = null;
let fvZoom = 1;
let fvPanX = 0;
let fvPanY = 0;

let fvIsPanning = false;
let fvPanStartX = 0, fvPanStartY = 0, fvPanOrigX = 0, fvPanOrigY = 0;

let fvCropMode = false;
let fvIsCropping = false;
let fvCropStartX = 0, fvCropStartY = 0;

function applyFullViewTransform() {
    if (!fullviewStage) return;
    fullviewStage.style.transform = `translate(${fvPanX}px, ${fvPanY}px) scale(${fvZoom})`;
    if (fvZoomLevelEl) fvZoomLevelEl.textContent = `${Math.round(fvZoom * 100)}%`;
}

function fitFullViewToContainer() {
    if (!fullviewViewport || !fvCanvas || !fvCanvas.width) return;
    const vw = fullviewViewport.clientWidth;
    const vh = fullviewViewport.clientHeight;
    const scale = Math.min(vw / fvCanvas.width, vh / fvCanvas.height) * 0.98;
    fvZoom = Math.max(FV_MIN_ZOOM, scale);
    fvPanX = (vw - fvCanvas.width * fvZoom) / 2;
    fvPanY = (vh - fvCanvas.height * fvZoom) / 2;
    applyFullViewTransform();
}

async function renderFullView() {
    if (!currentDoc || !fvCtx) return;
    const page = await currentDoc.getPage(pageNum);
    const viewport = page.getViewport({ scale: FV_BASE_SCALE });

    fvCanvas.width = viewport.width;
    fvCanvas.height = viewport.height;

    if (fvRenderTask) fvRenderTask.cancel();

    try {
        fvRenderTask = page.render({ canvasContext: fvCtx, viewport });
        await fvRenderTask.promise;
        fvRenderTask = null;
        drawFullViewBoundingBoxes();
        fitFullViewToContainer();
    } catch (err) {
        if (err.name !== 'RenderingCancelledException') {
            console.error('Full view render error:', err);
        }
    }
}

function drawFullViewBoundingBoxes() {
    if (!fvCtx || !fvCanvas) return;
    const selectedModel = fvBboxSelector ? fvBboxSelector.value : 'none';
    if (selectedModel === 'none' || llmResultsData.length === 0) return;

    const resultData = llmResultsData.find(r => r.model === selectedModel);
    if (!resultData) return;

    const currentFileIndex = fileSelector ? parseInt(fileSelector.value) : 0;

    try {
        const data = JSON.parse(resultData.response);
        if (data.objects && Array.isArray(data.objects)) {
            const objects = sortObjectsForDrawing(data.objects);
            objects.forEach(obj => {
                if (obj.file_index === currentFileIndex && obj.page_num === pageNum) {
                    const [xMin, yMin, xMax, yMax] = obj.box;
                    const x = (xMin / 1000) * fvCanvas.width;
                    const y = (yMin / 1000) * fvCanvas.height;
                    const w = ((xMax - xMin) / 1000) * fvCanvas.width;
                    const h = ((yMax - yMin) / 1000) * fvCanvas.height;

                    const style = styleForLabel(obj.label);
                    fvCtx.lineWidth = style.lineWidth * 1.5;
                    fvCtx.strokeStyle = style.stroke;
                    if (style.fill && !outlineOnlyMode) {
                        fvCtx.fillStyle = style.fill;
                        fvCtx.fillRect(x, y, w, h);
                    }
                    fvCtx.strokeRect(x, y, w, h);

                    const textY = y > 28 ? y - 10 : y + 28;
                    fvCtx.font = 'bold 22px Arial';
                    fvCtx.fillStyle = '#0f111a';
                    fvCtx.fillRect(x, textY - 20, fvCtx.measureText(obj.label).width + 16, 26);

                    fvCtx.fillStyle = style.stroke;
                    fvCtx.fillText(obj.label, x + 8, textY);
                }
            });
        }
    } catch (e) {
        // Response isn't valid JSON yet (still streaming/edited) — skip overlay silently.
    }
}

function syncFullViewBboxOptions() {
    if (!fvBboxSelector || !bboxSelector) return;
    const previousValue = fvBboxSelector.value || bboxSelector.value || 'none';
    fvBboxSelector.innerHTML = bboxSelector.innerHTML;
    const hasOption = Array.from(fvBboxSelector.options).some(o => o.value === previousValue);
    fvBboxSelector.value = hasOption ? previousValue : (bboxSelector.value || 'none');
}

function openFullViewModal() {
    if (!fullviewOverlay || !currentDoc) return;
    syncFullViewBboxOptions();
    fullviewOverlay.classList.add('open');
    fullviewOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    setFvCropMode(false);
    renderFullView();
    if (fvHint) {
        fvHint.style.opacity = '1';
        clearTimeout(openFullViewModal._hintTimer);
        openFullViewModal._hintTimer = setTimeout(() => { fvHint.style.opacity = '0'; }, 3000);
    }
}

function closeFullViewModal() {
    if (!fullviewOverlay) return;
    fullviewOverlay.classList.remove('open');
    fullviewOverlay.style.display = 'none';
    document.body.style.overflow = '';
    setFvCropMode(false);
}

if (openFullviewBtn) openFullviewBtn.addEventListener('click', openFullViewModal);
if (closeFullviewBtn) closeFullviewBtn.addEventListener('click', closeFullViewModal);

if (fullviewOverlay) {
    fullviewOverlay.addEventListener('click', (e) => {
        if (e.target === fullviewOverlay) closeFullViewModal();
    });
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && fullviewOverlay && fullviewOverlay.classList.contains('open')) {
        closeFullViewModal();
    }
});

if (fvBboxSelector) {
    fvBboxSelector.addEventListener('change', () => {
        // Redraw only the modal's own canvas — the main canvas overlay is
        // controlled independently by its own selector.
        renderFullView();
    });
}

if (fvResetViewBtn) fvResetViewBtn.addEventListener('click', fitFullViewToContainer);

if (fvZoomInBtn) {
    fvZoomInBtn.addEventListener('click', () => {
        zoomFullViewAroundCenter(1.25);
    });
}
if (fvZoomOutBtn) {
    fvZoomOutBtn.addEventListener('click', () => {
        zoomFullViewAroundCenter(1 / 1.25);
    });
}

function zoomFullViewAroundCenter(factor) {
    if (!fullviewViewport) return;
    const vw = fullviewViewport.clientWidth;
    const vh = fullviewViewport.clientHeight;
    zoomFullViewAt(vw / 2, vh / 2, factor);
}

function zoomFullViewAt(screenX, screenY, factor) {
    const newZoom = Math.min(FV_MAX_ZOOM, Math.max(FV_MIN_ZOOM, fvZoom * factor));
    const ratio = newZoom / fvZoom;
    fvPanX = screenX - (screenX - fvPanX) * ratio;
    fvPanY = screenY - (screenY - fvPanY) * ratio;
    fvZoom = newZoom;
    applyFullViewTransform();
}

// ---- Wheel-to-zoom ----
if (fullviewViewport) {
    fullviewViewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = fullviewViewport.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        zoomFullViewAt(mouseX, mouseY, factor);
    }, { passive: false });
}

// ---- Drag-to-pan ----
if (fullviewViewport) {
    fullviewViewport.addEventListener('mousedown', (e) => {
        if (fvCropMode) return;
        fvIsPanning = true;
        fvPanStartX = e.clientX;
        fvPanStartY = e.clientY;
        fvPanOrigX = fvPanX;
        fvPanOrigY = fvPanY;
        fullviewViewport.classList.add('panning');
    });
}

window.addEventListener('mousemove', (e) => {
    if (!fvIsPanning) return;
    fvPanX = fvPanOrigX + (e.clientX - fvPanStartX);
    fvPanY = fvPanOrigY + (e.clientY - fvPanStartY);
    applyFullViewTransform();
});

window.addEventListener('mouseup', () => {
    if (fvIsPanning) {
        fvIsPanning = false;
        if (fullviewViewport) fullviewViewport.classList.remove('panning');
    }
});

// ---- Crop-to-zoom ----
function setFvCropMode(enabled) {
    fvCropMode = enabled;
    if (fvCropToggleBtn) fvCropToggleBtn.classList.toggle('active', enabled);
    if (fullviewViewport) fullviewViewport.classList.toggle('crop-mode', enabled);
    if (fvCropBox) fvCropBox.hidden = true;
}

if (fvCropToggleBtn) {
    fvCropToggleBtn.addEventListener('click', () => setFvCropMode(!fvCropMode));
}

if (fullviewViewport && fvCropBox) {
    fullviewViewport.addEventListener('mousedown', (e) => {
        if (!fvCropMode) return;
        fvIsCropping = true;
        const rect = fullviewViewport.getBoundingClientRect();
        fvCropStartX = e.clientX - rect.left;
        fvCropStartY = e.clientY - rect.top;
        fvCropBox.style.left = `${fvCropStartX}px`;
        fvCropBox.style.top = `${fvCropStartY}px`;
        fvCropBox.style.width = '0px';
        fvCropBox.style.height = '0px';
        fvCropBox.hidden = false;
    });

    window.addEventListener('mousemove', (e) => {
        if (!fvIsCropping) return;
        const rect = fullviewViewport.getBoundingClientRect();
        const curX = e.clientX - rect.left;
        const curY = e.clientY - rect.top;
        const left = Math.min(curX, fvCropStartX);
        const top = Math.min(curY, fvCropStartY);
        const w = Math.abs(curX - fvCropStartX);
        const h = Math.abs(curY - fvCropStartY);
        fvCropBox.style.left = `${left}px`;
        fvCropBox.style.top = `${top}px`;
        fvCropBox.style.width = `${w}px`;
        fvCropBox.style.height = `${h}px`;
    });

    window.addEventListener('mouseup', () => {
        if (!fvIsCropping) return;
        fvIsCropping = false;
        fvCropBox.hidden = true;

        const boxW = parseFloat(fvCropBox.style.width) || 0;
        const boxH = parseFloat(fvCropBox.style.height) || 0;

        // Ignore accidental clicks/tiny drags
        if (boxW < 8 || boxH < 8) {
            setFvCropMode(false);
            return;
        }

        const screenLeft = parseFloat(fvCropBox.style.left);
        const screenTop = parseFloat(fvCropBox.style.top);

        // Convert the selection from viewport/screen space into the stage's
        // own (unscaled canvas-pixel) coordinate space, then fit it to the viewport.
        const stageX = (screenLeft - fvPanX) / fvZoom;
        const stageY = (screenTop - fvPanY) / fvZoom;
        const stageW = boxW / fvZoom;
        const stageH = boxH / fvZoom;

        const vw = fullviewViewport.clientWidth;
        const vh = fullviewViewport.clientHeight;
        const newZoom = Math.min(FV_MAX_ZOOM, Math.min(vw / stageW, vh / stageH) * 0.98);

        const excessW = vw - stageW * newZoom;
        const excessH = vh - stageH * newZoom;

        fvZoom = Math.max(FV_MIN_ZOOM, newZoom);
        fvPanX = -stageX * fvZoom + excessW / 2;
        fvPanY = -stageY * fvZoom + excessH / 2;
        applyFullViewTransform();

        setFvCropMode(false);
    });
}

if (exportBtn) {
    exportBtn.addEventListener('click', () => {
        if (!llmResultsData || llmResultsData.length === 0) return;

        const systemPromptEl = document.getElementById('system-prompt');
        const systemPromptValue = (systemPromptEl && systemPromptEl.value.trim() !== '')
            ? systemPromptEl.value.trim()
            : defaultSystemPrompt;

        let dpiValue = dpiInput ? parseInt(dpiInput.value, 10) : 200;
        if (!Number.isFinite(dpiValue) || dpiValue <= 0) dpiValue = 200;

        const exportData = {
            timestamp: new Date().toISOString(),
            files: pdfFiles.map(f => ({ name: f.name, pages: f.pdf.numPages })),
            system_prompt: systemPromptValue,
            dpi: dpiValue,
            execution_settings: { ...executionSettings },
            results: llmResultsData.map(data => {
                let parsedResponse = data.response;
                try { parsedResponse = JSON.parse(data.response); } catch (e) {}
                return { model: data.model, prompt: data.prompt, response: parsedResponse };
            })
        };
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `llm_benchmark_results_${new Date().getTime()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });
}