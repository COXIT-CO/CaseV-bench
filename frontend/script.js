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
    // 5-type taxonomy additions (2026-08-17): "callout" replaces
    // "elevation_callout" and "floor plan" is new — note the SPACE, it
    // matches ground truth's category spelling exactly. Both old and new
    // callout names get their own style so history from either era of
    // prompt still renders distinctly rather than falling back to the
    // shared default (which collides with cabinet's color).
    callout:           { stroke: '#c65fff', fill: 'rgba(198, 95, 255, 0.35)', lineWidth: 2 },
    'floor plan':      { stroke: '#ffe14d', fill: null,                       lineWidth: 3 },
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
const maxDimInput = document.getElementById('max-dim-input');
const modelsGrid = document.getElementById('models-grid');
const useGlobalPrompt = document.getElementById('use-global-prompt');
const globalPrompt = document.getElementById('global-prompt');
const modelPrompts = document.querySelectorAll('.model-prompt');

const visControls = document.getElementById('vis-controls');
const bboxSelector = document.getElementById('bbox-selector');
const exportBtn = document.getElementById('export-btn');
const runBtn = document.getElementById('run-btn');
const iouThresholdInput = document.getElementById('iou-threshold');
const groundTruthEl = document.getElementById('ground-truth');

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
const maxParallelPagesInput = document.getElementById('max-parallel-pages-input');
const maxParallelPagesRow = document.getElementById('max-parallel-pages-row');

// Applied execution settings (only updated when the modal is saved).
// Defaults reproduce the original tool's behaviour: one sequential request
// per model, with every file and every page packed into that one request.
const defaultExecutionSettings = {
    modelExecutionMode: 'sequential',
    fileGroupingMode: 'single',
    fileExecutionMode: 'sequential',
    pageGroupingMode: 'single',
    pageExecutionMode: 'sequential',
    maxParallelPages: null
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
    "left":0,
    "top":0,
    "right":0,
    "bottom":0,
    "image_index":0
   }
 ]
}

For each object, report four independently-named fields for its position — "left" (smaller x), "top" (smaller y), "right" (greater than left), "bottom" (greater than top) — not a single combined array. Determine each one directly from the object's own on-screen edge; do not guess one from another.

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
- CRITICAL: left/top/right/bottom MUST be normalized to a 0-1000 scale, where [0, 0] is the top-left and [1000, 1000] is the bottom-right of THIS image. Do NOT output absolute pixels. A value above 1000 or below 0 is never valid — it means you reported a pixel coordinate instead of the 0-1000 fraction.`;

// Default user-role prompt, pre-filled into every model card so the tool is
// runnable out of the box without requiring manual typing.
const defaultUserPrompt = `This request contains every page from every uploaded PDF, sent together as one document set — nothing is split across separate requests. Go through the pages in order and find every cabinet, countertop, elevation drawing, and elevation callout, exactly as defined in your instructions. Before answering, double-check the image_index you assign to each object. Respond with the JSON object only — no explanation, no markdown.`;

const defaultElevationPrompt = `This is one full sheet. Scan it thoroughly and find every "elevation" frame — a flat, straight-on drawing of a wall or built-in casework run, in its own rectangular frame — AND every "elevation_callout" symbol elsewhere on the page (small reference bubbles on floor plans/RCPs, never inside an elevation drawing itself), exactly as defined in your instructions. Report ONLY elevation and elevation_callout objects for this request; ignore cabinets and countertops entirely, they are handled separately afterward. Respond with the JSON object only — no explanation, no markdown.`;

const defaultDetailPrompt = `This image is a cropped close-up of a single elevation drawing, already confirmed by a human reviewer. Find every cabinet and countertop visible within it, exactly as defined in your instructions. Do not report "elevation" or "elevation_callout" — those have already been handled in an earlier pass. Respond with the JSON object only — no explanation, no markdown.`;

// Safe initialization: wait for the page to fully load
document.addEventListener('DOMContentLoaded', () => {
    const systemPromptEl = document.getElementById('system-prompt');
    if (systemPromptEl) {
        systemPromptEl.value = defaultSystemPrompt;
    }

    modelPrompts.forEach(p => { p.value = defaultUserPrompt; });
    if (globalPrompt) globalPrompt.value = defaultUserPrompt;

    const tsElevationPromptEl = document.getElementById('ts-elevation-prompt');
    if (tsElevationPromptEl) tsElevationPromptEl.value = defaultElevationPrompt;
    const tsDetailPromptEl = document.getElementById('ts-detail-prompt');
    if (tsDetailPromptEl) tsDetailPromptEl.value = defaultDetailPrompt;

    const tsModalSharedEl = document.getElementById('ts-modal-system-prompt-shared');
    if (tsModalSharedEl) tsModalSharedEl.value = defaultSystemPrompt;
    const tsModalStage1El = document.getElementById('ts-modal-system-prompt-1');
    if (tsModalStage1El) tsModalStage1El.value = defaultSystemPrompt;
    const tsModalStage2El = document.getElementById('ts-modal-system-prompt-2');
    if (tsModalStage2El) tsModalStage2El.value = defaultSystemPrompt;

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

if (maxDimInput) {
    maxDimInput.addEventListener('change', () => {
        // Optional field — an empty value means "off" (plain DPI flow), so
        // leave it empty rather than snapping it to a default.
        if (maxDimInput.value.trim() === '') return;
        const min = parseInt(maxDimInput.min, 10);
        const max = parseInt(maxDimInput.max, 10);
        let val = parseInt(maxDimInput.value, 10);
        if (!Number.isFinite(val)) { maxDimInput.value = ''; return; }
        val = Math.min(max, Math.max(min, val));
        maxDimInput.value = val;
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
    if (maxParallelPagesInput) maxParallelPagesInput.value = settings.maxParallelPages || '';
    updateExecutionSubRowsVisibility();
}

function updateExecutionSubRowsVisibility() {
    if (fileExecutionModeRow) {
        fileExecutionModeRow.style.display = (fileGroupingModeSelect && fileGroupingModeSelect.value === 'split') ? 'flex' : 'none';
    }
    const pagesSplit = pageGroupingModeSelect && pageGroupingModeSelect.value === 'split';
    if (pageExecutionModeRow) {
        pageExecutionModeRow.style.display = pagesSplit ? 'flex' : 'none';
    }
    if (maxParallelPagesRow) {
        // Only meaningful once pages are both split AND run in parallel —
        // sequential page requests never have more than one in flight anyway.
        const pagesParallel = pageExecutionModeSelect && pageExecutionModeSelect.value === 'parallel';
        maxParallelPagesRow.style.display = (pagesSplit && pagesParallel) ? 'flex' : 'none';
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
if (pageExecutionModeSelect) pageExecutionModeSelect.addEventListener('change', updateExecutionSubRowsVisibility);

if (maxParallelPagesInput) {
    maxParallelPagesInput.addEventListener('change', () => {
        // Optional field — empty means "unlimited", so leave it empty rather
        // than snapping it to a default.
        if (maxParallelPagesInput.value.trim() === '') return;
        const min = parseInt(maxParallelPagesInput.min, 10);
        const max = parseInt(maxParallelPagesInput.max, 10);
        let val = parseInt(maxParallelPagesInput.value, 10);
        if (!Number.isFinite(val)) { maxParallelPagesInput.value = ''; return; }
        val = Math.min(max, Math.max(min, val));
        maxParallelPagesInput.value = val;
    });
}

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
            pageExecutionMode: pageExecutionModeSelect ? pageExecutionModeSelect.value : defaultExecutionSettings.pageExecutionMode,
            maxParallelPages: (maxParallelPagesInput && maxParallelPagesInput.value.trim() !== '') ? parseInt(maxParallelPagesInput.value, 10) : null
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

// ===================================================================
// Score + cost formatting (shared by all three tabs and by History)
// ===================================================================
// All three detection flows now return the same two extra fields next to
// their response: "score" (location-scorer's output, or null when no ground
// truth was entered) and "usage" (latency + token/cost totals). These
// formatters are deliberately shared so a number means exactly the same
// thing whichever tab it's read on — that comparability is the entire point
// of measuring them.

function formatScore(score) {
    if (!score) return '';
    if (score.error) return `Scoring failed: ${score.error}`;
    const m = score.metrics;
    const pct = (v) => `${Math.round(v * 100)}%`;
    const lines = [
        `location-scorer @ IoU ${score.iou_threshold}  —  P ${pct(m.precision)}  R ${pct(m.recall)}  F1 ${pct(m.f1)}  (tp ${score.counts.tp} / fp ${score.counts.fp} / fn ${score.counts.fn})`,
    ];
    Object.entries(score.per_type || {}).forEach(([type, v]) => {
        lines.push(`  ${type}: P ${pct(v.metrics.precision)}  R ${pct(v.metrics.recall)}  F1 ${pct(v.metrics.f1)}  (tp ${v.counts.tp} / fp ${v.counts.fp} / fn ${v.counts.fn})`);
    });
    return lines.join('\n');
}

function formatDuration(ms) {
    if (typeof ms !== 'number') return '—';
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(n) {
    return typeof n === 'number' ? n.toLocaleString('en-US') : '—';
}

function formatCost(usd) {
    if (typeof usd !== 'number') return null;
    // Per-request costs here are routinely well under a cent, so a plain
    // 2-decimal currency format would render almost every real run as
    // "$0.00" and hide exactly the differences worth comparing.
    if (usd === 0) return '$0';
    if (usd < 0.01) return `$${usd.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;
    return `$${usd.toFixed(4)}`;
}

function formatUsage(usage) {
    if (!usage) return '';
    const lat = usage.latency_ms || {};
    const lines = [];

    const cost = formatCost(usage.cost_usd);
    const head = [
        `${usage.requests} request${usage.requests === 1 ? '' : 's'}`,
        `in ${formatTokens(usage.input_tokens)} tok`,
        `out ${formatTokens(usage.output_tokens)} tok`,
    ];
    // OpenRouter reports cost per request; a provider that doesn't leaves it
    // null, in which case the token counts above are the fallback measure.
    head.push(cost ? `cost ${cost}` : 'cost n/a');
    lines.push(head.join('  •  '));

    const timing = [`wall ${formatDuration(usage.wall_ms)}`];
    if (typeof lat.mean === 'number') {
        timing.push(`per-request mean ${formatDuration(lat.mean)}`);
        timing.push(`min ${formatDuration(lat.min)}`);
        timing.push(`max ${formatDuration(lat.max)}`);
        // Only meaningful when requests ran one after another; under parallel
        // execution the sum exceeds the wall clock, which is why both are shown.
        timing.push(`sum ${formatDuration(lat.sum)}`);
    }
    lines.push(timing.join('  •  '));

    const extras = [];
    if (typeof usage.reasoning_tokens === 'number' && usage.reasoning_tokens > 0) {
        extras.push(`reasoning ${formatTokens(usage.reasoning_tokens)} tok (billed as output)`);
    }
    if (typeof usage.cached_tokens === 'number' && usage.cached_tokens > 0) {
        extras.push(`cached input ${formatTokens(usage.cached_tokens)} tok`);
    }
    if (usage.failed_requests) extras.push(`${usage.failed_requests} failed`);
    if (extras.length) lines.push(extras.join('  •  '));

    if (usage.by_stage) {
        const stageLine = ['stage 1', 'stage 2'].map((label, i) => {
            const s = usage.by_stage[i === 0 ? 'stage1' : 'stage2'] || {};
            const c = formatCost(s.cost_usd);
            return `${label}: ${s.requests || 0} req, ${formatTokens(s.input_tokens)}/${formatTokens(s.output_tokens)} tok${c ? `, ${c}` : ''}`;
        });
        lines.push(stageLine.join('  •  '));
    }

    return lines.join('\n');
}

/** Writes text into one of the small score/usage panels under a model card,
 *  hiding the panel entirely when there's nothing to show. */
function setInfoBox(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('visible', Boolean(text));
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
            setInfoBox(`score-${i}`, '');
            setInfoBox(`usage-${i}`, '');
            if (modelName && prompt) modelsData.push({ model: modelName, prompt: prompt });
        }

        if (modelsData.length === 0 || pdfFiles.length === 0) {
            alert('Add at least one PDF file and configure at least one model with a prompt.');
            runBtn.textContent = '▶ Run Benchmark';
            runBtn.disabled = false;
            return;
        }

        // Optional — an empty box simply means "run without scoring".
        let groundTruth = [];
        const gtRaw = groundTruthEl ? groundTruthEl.value.trim() : '';
        if (gtRaw) {
            try {
                // Either {"project_id":..., "objects":[...]} (the project's own
                // export shape) or a bare list of the same object shape — the
                // backend accepts both, so just check it parses.
                groundTruth = JSON.parse(gtRaw);
            } catch (e) {
                alert('Ground truth must be valid JSON — see the description above the box for the expected shape.');
                runBtn.textContent = '▶ Run Benchmark';
                runBtn.disabled = false;
                return;
            }
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

        // Optional — only sent when the field actually has a value; left
        // empty, the backend skips the resize and uses the DPI render as-is.
        if (maxDimInput && maxDimInput.value.trim() !== '') {
            const maxDimValue = parseInt(maxDimInput.value, 10);
            if (Number.isFinite(maxDimValue) && maxDimValue > 0) {
                formData.append('max_dim', maxDimValue);
            }
        }

        // Execution settings chosen in the "Execution Settings" modal
        formData.append('model_execution_mode', executionSettings.modelExecutionMode);
        formData.append('file_grouping_mode', executionSettings.fileGroupingMode);
        formData.append('file_execution_mode', executionSettings.fileExecutionMode);
        formData.append('page_grouping_mode', executionSettings.pageGroupingMode);
        formData.append('page_execution_mode', executionSettings.pageExecutionMode);
        if (executionSettings.maxParallelPages !== null && executionSettings.maxParallelPages !== undefined) {
            formData.append('max_parallel_pages', executionSettings.maxParallelPages);
        }

        let iouValue = iouThresholdInput ? parseFloat(iouThresholdInput.value) : 0.5;
        if (!Number.isFinite(iouValue)) iouValue = 0.5;
        formData.append('iou_threshold', iouValue);
        formData.append('ground_truth', JSON.stringify(groundTruth));

        pdfFiles.forEach((fileObj) => formData.append('files', fileObj.rawFile));

        try {
            const res = await fetch('/api/generate', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.results) {
                llmResultsData = data.results.map(r => {
                    const req = modelsData.find(m => m.model === r.model);
                    return {
                        model: r.model, prompt: req ? req.prompt : '', response: r.response,
                        score: r.score, usage: r.usage,
                    };
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
                    for (let i = 1; i <= activeCount; i++) {
                        const card = document.getElementById(`model-${i}`);
                        if (!card) continue;
                        const mNameEl = card.querySelector('.model-name');
                        const rBox = card.querySelector('.response-box');
                        if (!mNameEl || rBox === null || mNameEl.value !== result.model) continue;
                        rBox.textContent = result.response;
                        rBox.style.color = 'var(--accent)';
                        rBox.dataset.empty = 'false';
                        setInfoBox(`usage-${i}`, formatUsage(result.usage));
                        setInfoBox(`score-${i}`, result.score
                            ? formatScore(result.score)
                            : (gtRaw ? 'Ground truth matched no page in these files — nothing to score.' : ''));
                    }
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
    ['callouts', 'Callouts'],
    ['floor_plans', 'Floor plans'],
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
        if (run.run_type === 'two_stage') {
            applyTwoStageRunToForm(run);
            tabButtons.forEach((b) => b.classList.toggle('active', b.dataset.tab === 'twostage'));
            tabPanels.forEach((p) => p.classList.toggle('active', p.id === 'tab-twostage'));
        } else if (run.run_type === 'grid') {
            applyGridRunToForm(run);
            tabButtons.forEach((b) => b.classList.toggle('active', b.dataset.tab === 'grid'));
            tabPanels.forEach((p) => p.classList.toggle('active', p.id === 'tab-grid'));
        } else {
            applyRunToForm(run);
            tabButtons.forEach((b) => b.classList.toggle('active', b.dataset.tab === 'benchmark'));
            tabPanels.forEach((p) => p.classList.toggle('active', p.id === 'tab-benchmark'));
        }
        closeHistoryModal();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
        console.error(e);
        alert('Failed to load this run for replay.');
    }
}

// Repopulates the Two-Stage tab's config (model, both DPIs, concurrency,
// system prompt mode, and both stage prompts) from a saved two-stage run.
// The source PDF is re-uploaded by the person, same as the benchmark tab.
function applyTwoStageRunToForm(run) {
    const r = (run.results || [])[0];
    if (!r) return;

    const modelEl = document.getElementById('ts-model-name');
    if (modelEl) modelEl.value = r.model || '';

    const dpi1El = document.getElementById('ts-dpi-stage1');
    if (dpi1El && run.stage1_dpi) dpi1El.value = run.stage1_dpi;
    const dpi2El = document.getElementById('ts-dpi-stage2');
    if (dpi2El && run.stage2_dpi) dpi2El.value = run.stage2_dpi;
    const concurrencyEl = document.getElementById('ts-stage2-concurrency');
    if (concurrencyEl && run.stage2_concurrency) concurrencyEl.value = run.stage2_concurrency;

    // Split the combined "[Stage 1 — elevations]\n...\n\n[Stage 2 — details...]\n..."
    // prompt string back into its two parts.
    const combined = r.prompt || '';
    const stage1Match = combined.match(/\[Stage 1[^\]]*\]\n([\s\S]*?)\n\n\[Stage 2/);
    const stage2Match = combined.match(/\[Stage 2[^\]]*\]\n([\s\S]*)$/);
    const elevationPromptEl = document.getElementById('ts-elevation-prompt');
    if (elevationPromptEl) elevationPromptEl.value = stage1Match ? stage1Match[1] : combined;
    const detailPromptEl = document.getElementById('ts-detail-prompt');
    if (detailPromptEl) detailPromptEl.value = stage2Match ? stage2Match[1] : '';

    const sys1 = run.system_prompt_stage1 || run.system_prompt || '';
    const sys2 = run.system_prompt_stage2 || run.system_prompt || '';
    const sameSystemPrompt = sys1.trim() === sys2.trim();

    if (tsModalUseSharedSystemPrompt) {
        tsModalUseSharedSystemPrompt.checked = sameSystemPrompt;
        if (tsModalSharedBlock) tsModalSharedBlock.style.display = sameSystemPrompt ? 'block' : 'none';
        if (tsModalSeparateBlock) tsModalSeparateBlock.style.display = sameSystemPrompt ? 'none' : 'block';
    }
    if (tsModalSystemPromptShared) tsModalSystemPromptShared.value = sameSystemPrompt ? sys1 : (tsModalSystemPromptShared.value || defaultSystemPrompt);
    if (tsModalSystemPrompt1) tsModalSystemPrompt1.value = sys1;
    if (tsModalSystemPrompt2) tsModalSystemPrompt2.value = sys2;

    restoreScoringInputs(run, 'ts-iou-threshold', 'ts-ground-truth');

    tsResetStageUI();
}

/** Puts a saved run's IoU threshold and ground truth back into whichever
 *  tab is being replayed. Ground truth is stored already normalized (0-1
 *  boxes), which round-trips fine — the backend auto-detects that form. */
function restoreScoringInputs(run, iouElId, gtElId) {
    const iouEl = document.getElementById(iouElId);
    if (iouEl && run.iou_threshold !== undefined && run.iou_threshold !== null) {
        iouEl.value = run.iou_threshold;
    }
    const gtEl = document.getElementById(gtElId);
    if (gtEl) {
        gtEl.value = (Array.isArray(run.ground_truth) && run.ground_truth.length)
            ? JSON.stringify(run.ground_truth, null, 2)
            : '';
    }
}

// Repopulates the Grid Detection tab's config (system prompt, DPI, max
// image dimension, grid rows/cols, IoU threshold, object types, ground
// truth, model cards, execution settings) from a saved grid run — the
// source PDF is re-uploaded by the person, same as the other tabs.
function applyGridRunToForm(run) {
    const gdSystemPromptEl = document.getElementById('gd-system-prompt');
    if (gdSystemPromptEl) gdSystemPromptEl.value = run.system_prompt || '';

    const dpiEl = document.getElementById('gd-dpi-input');
    if (dpiEl && run.dpi) dpiEl.value = run.dpi;
    const maxDimEl = document.getElementById('gd-max-dim-input');
    if (maxDimEl && run.max_dim) maxDimEl.value = run.max_dim;
    const iouEl = document.getElementById('gd-iou-threshold');
    if (iouEl && run.iou_threshold !== undefined && run.iou_threshold !== null) iouEl.value = run.iou_threshold;
    const typesEl = document.getElementById('gd-object-types');
    if (typesEl && Array.isArray(run.object_types)) typesEl.value = run.object_types.join(',');
    const gtEl = document.getElementById('gd-ground-truth');
    if (gtEl) {
        gtEl.value = (Array.isArray(run.ground_truth) && run.ground_truth.length)
            ? JSON.stringify(run.ground_truth, null, 2)
            : '';
    }

    const results = run.results || [];
    const count = Math.min(3, Math.max(1, results.length || 1));
    const modelCountEl = document.getElementById('gd-model-count');
    if (modelCountEl) { modelCountEl.value = String(count); updateGdActiveModels(); }

    if (gdUseGlobalPrompt) {
        gdUseGlobalPrompt.checked = false;
        if (gdGlobalPrompt) gdGlobalPrompt.style.display = 'none';
        gdModelPrompts.forEach((p) => { p.disabled = false; });
    }

    for (let i = 1; i <= 3; i++) {
        const card = document.getElementById(`gd-model-${i}`);
        if (!card) continue;
        const nameEl = card.querySelector('.model-name');
        const promptEl = card.querySelector('.model-prompt');
        const r = results[i - 1];
        if (r) {
            if (nameEl) nameEl.value = r.model || '';
            if (promptEl) promptEl.value = r.prompt || '';
        }
        setInfoBox(`gd-score-${i}`, '');
        setInfoBox(`gd-usage-${i}`, '');
    }

    const es = run.execution_settings || {};
    gdExecutionSettings = {
        modelExecutionMode: es.model_execution_mode || gdDefaultExecutionSettings.modelExecutionMode,
        pageExecutionMode: es.page_execution_mode || gdDefaultExecutionSettings.pageExecutionMode,
    };
    gdApplyExecutionSettingsToForm(gdExecutionSettings);

    gdGridSettings = {
        rows: run.grid_rows || gdDefaultGridSettings.rows,
        cols: run.grid_cols || gdDefaultGridSettings.cols,
        color: run.grid_color || gdDefaultGridSettings.color,
        opacity: (run.grid_opacity !== undefined && run.grid_opacity !== null) ? run.grid_opacity : gdDefaultGridSettings.opacity,
        thickness: run.grid_thickness || gdDefaultGridSettings.thickness,
        labelScheme: run.label_scheme || gdDefaultGridSettings.labelScheme,
        boxPrecision: run.box_precision || gdDefaultGridSettings.boxPrecision,
    };
    gdApplyGridSettingsToForm(gdGridSettings);

    if (results.length > 3) {
        alert(`This run used ${results.length} models — only the first 3 could be restored, since the form supports up to 3 at once.`);
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
    if (maxDimInput) {
        maxDimInput.value = run.max_dim || '';
    }

    restoreScoringInputs(run, 'iou-threshold', 'ground-truth');

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
        // A replay restores the SETUP, not the results — leaving the previous
        // run's score/cost panels on screen would attribute them to a run
        // that hasn't happened yet.
        setInfoBox(`score-${i}`, '');
        setInfoBox(`usage-${i}`, '');
    }

    const es = run.execution_settings || {};
    executionSettings = {
        modelExecutionMode: es.model_execution_mode || defaultExecutionSettings.modelExecutionMode,
        fileGroupingMode: es.file_grouping_mode || defaultExecutionSettings.fileGroupingMode,
        fileExecutionMode: es.file_execution_mode || defaultExecutionSettings.fileExecutionMode,
        pageGroupingMode: es.page_grouping_mode || defaultExecutionSettings.pageGroupingMode,
        pageExecutionMode: es.page_execution_mode || defaultExecutionSettings.pageExecutionMode,
        maxParallelPages: es.max_parallel_pages ?? null,
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
        const res = await fetch(`/api/history?run_type=${encodeURIComponent(historyFilterType)}`);
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
            const total = (c.cabinets || 0) + (c.countertops || 0) + (c.elevations || 0) + (c.elevation_callouts || 0)
                + (c.callouts || 0) + (c.floor_plans || 0);
            return `<span class="history-model-chip">${escapeHtml(m.model)}: ${total}</span>`;
        }).join('');

        card.innerHTML = `
            ${run.thumbnail_url ? `<img class="history-run-thumb" src="${escapeHtml(run.thumbnail_url)}" alt="" loading="lazy">` : '<div class="history-run-thumb"></div>'}
            <div class="history-run-info">
                <div class="history-run-date">${escapeHtml(formatHistoryDate(run.created_at))}${run.run_type === 'two_stage' ? ' <span class="history-run-badge">Two-Stage</span>' : ''}${run.run_type === 'grid' ? ' <span class="history-run-badge">Grid</span>' : ''}</div>
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
    const settingsHtml = run.run_type === 'two_stage' ? `
        <div class="history-settings-grid">
            <div><b>Model:</b> ${escapeHtml((run.results || []).map(r => r.model).join(', '))}</div>
            <div><b>Stage 1 DPI:</b> ${escapeHtml(run.stage1_dpi)}</div>
            <div><b>Stage 2 DPI:</b> ${escapeHtml(run.stage2_dpi)}</div>
            <div><b>Max concurrent requests:</b> ${escapeHtml(run.stage2_concurrency)}</div>
            <div><b>System prompt:</b> ${run.system_prompt_stage1 && run.system_prompt_stage2 && run.system_prompt_stage1.trim() !== run.system_prompt_stage2.trim() ? 'separate per stage' : 'shared'}</div>
        </div>
        <button type="button" class="run-button history-replay-btn" id="history-detail-replay">↻ Replay this run's setup</button>
    ` : run.run_type === 'grid' ? `
        <div class="history-settings-grid">
            <div><b>DPI:</b> ${escapeHtml(run.dpi)}</div>
            <div><b>Max image dimension:</b> ${escapeHtml(run.max_dim)}px</div>
            <div><b>Grid:</b> ${escapeHtml(run.grid_rows)} rows x ${escapeHtml(run.grid_cols)} cols</div>
            ${run.grid_color ? `<div><b>Grid style:</b> ${escapeHtml(run.grid_color)}, ${escapeHtml(Math.round((run.grid_opacity ?? 0.6) * 100))}% opacity, ${escapeHtml(run.grid_thickness ?? 1)}px</div>` : ''}
            ${run.label_scheme ? `<div><b>Label scheme:</b> ${escapeHtml(run.label_scheme)}</div>` : ''}
            ${run.box_precision ? `<div><b>Box precision:</b> ${escapeHtml(run.box_precision)}</div>` : ''}
            <div><b>IoU threshold:</b> ${escapeHtml(run.iou_threshold)}</div>
            <div><b>Object types:</b> ${escapeHtml((run.object_types || []).join(', '))}</div>
            <div><b>Models:</b> ${escapeHtml((run.results || []).map(r => r.model).join(', '))}</div>
            <div><b>Model order:</b> ${escapeHtml(settings.model_execution_mode)}</div>
            <div><b>Page order:</b> ${escapeHtml(settings.page_execution_mode)}</div>
        </div>
        <button type="button" class="run-button history-replay-btn" id="history-detail-replay">↻ Replay this run's setup</button>
    ` : `
        <div class="history-settings-grid">
            <div><b>DPI:</b> ${escapeHtml(run.dpi)}</div>
            ${run.max_dim ? `<div><b>Max image dimension:</b> ${escapeHtml(run.max_dim)}px</div>` : ''}
            <div><b>Models:</b> ${escapeHtml((run.results || []).map(r => r.model).join(', '))}</div>
            <div><b>Model order:</b> ${escapeHtml(settings.model_execution_mode)}</div>
            <div><b>File batching:</b> ${escapeHtml(settings.file_grouping_mode)} / ${escapeHtml(settings.file_execution_mode)}</div>
            <div><b>Page batching:</b> ${escapeHtml(settings.page_grouping_mode)} / ${escapeHtml(settings.page_execution_mode)}</div>
            ${settings.max_parallel_pages != null ? `<div><b>Max pages in parallel:</b> ${escapeHtml(settings.max_parallel_pages)}</div>` : ''}
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
            <summary>Location score — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">${r.score ? escapeHtml(formatScore(r.score)) : 'No ground truth was entered for this run.'}</div>
            ${r.score && !r.score.error ? `
            <div class="history-eval-actions">
                <input type="text" class="inline-select" id="db-author-${idx}" placeholder="Author" style="width: 8rem;">
                <input type="text" class="inline-select" id="db-config-label-${idx}" placeholder="Config label (author/method)" style="width: 16rem;">
                <button type="button" class="ghost-button" data-save-to-db="${idx}">Save to DB</button>
                <span class="history-eval-status" id="db-save-status-${idx}"></span>
            </div>
            ` : ''}
        </details>
        <details class="history-section" open>
            <summary>Cost &amp; latency — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">${r.usage ? escapeHtml(formatUsage(r.usage)) : 'This run predates cost tracking.'}</div>
        </details>
        ${run.run_type === 'grid' ? '' : `
        <details class="history-section" open>
            <summary>Expected vs actual — ${escapeHtml(r.model)}</summary>
            <div class="history-section-body">
                <div class="history-eval-block">
                    <textarea class="history-eval-textarea" id="expected-input-${idx}" placeholder='{
  "cabinets": 0,
  "countertops": 0,
  "elevations": 0,
  "elevation_callouts": 0,
  "callouts": 0,
  "floor_plans": 0
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
        `}
    `).join('');

    const modelOptions = (run.results || [])
        .map(r => `<option value="${escapeHtml(r.model)}">${escapeHtml(r.model)}</option>`)
        .join('');

    historyDetailEl.innerHTML = `
        <details class="history-section" open>
            <summary>Settings</summary>
            <div class="history-section-body" style="font-family: var(--font-body);">${settingsHtml}</div>
        </details>
        ${run.run_type === 'two_stage' ? `
        <details class="history-section">
            <summary>System prompt — Stage 1</summary>
            <div class="history-section-body">${escapeHtml(run.system_prompt_stage1 || run.system_prompt)}</div>
        </details>
        <details class="history-section">
            <summary>System prompt — Stage 2</summary>
            <div class="history-section-body">${escapeHtml(run.system_prompt_stage2 || run.system_prompt)}</div>
        </details>
        ` : `
        <details class="history-section">
            <summary>System prompt</summary>
            <div class="history-section-body">${escapeHtml(run.system_prompt)}</div>
        </details>
        `}
        ${(run.ground_truth || []).length ? `
        <details class="history-section">
            <summary>Ground truth (${(run.ground_truth || []).length} object(s))</summary>
            <div class="history-section-body">${escapeHtml(JSON.stringify(run.ground_truth, null, 2))}</div>
        </details>
        ` : ''}
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

    // Save to DB: writes one model's scored result into the shared research table.
    (run.results || []).forEach((r, idx) => {
        const dbSaveBtn = historyDetailEl.querySelector(`[data-save-to-db="${idx}"]`);
        if (!dbSaveBtn) return;
        const authorInput = document.getElementById(`db-author-${idx}`);
        const configLabelInput = document.getElementById(`db-config-label-${idx}`);
        const statusEl = document.getElementById(`db-save-status-${idx}`);

        dbSaveBtn.addEventListener('click', async () => {
            const author = (authorInput.value || '').trim();
            const configLabel = (configLabelInput.value || '').trim();
            if (!author || !configLabel) {
                if (statusEl) { statusEl.textContent = 'Author and config label are required'; statusEl.className = 'history-eval-status error'; }
                return;
            }
            dbSaveBtn.disabled = true;
            if (statusEl) { statusEl.textContent = 'Saving…'; statusEl.className = 'history-eval-status'; }
            try {
                const res = await fetch(`/api/history/${encodeURIComponent(run.run_id)}/save-to-db`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: r.model, author, config_label: configLabel })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Save failed');
                if (statusEl) { statusEl.textContent = 'Saved ✓'; statusEl.className = 'history-eval-status saved'; }
            } catch (err) {
                console.error(err);
                if (statusEl) { statusEl.textContent = err.message || 'Failed to save'; statusEl.className = 'history-eval-status error'; }
            } finally {
                dbSaveBtn.disabled = false;
            }
        });
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

        // Every OTHER run type stores boxes 0-1000; the grid-detection flow
        // stores true 0-1 fractions (the location-scorer shared contract —
        // see save_grid_history() in the backend). Rather than rescale
        // every box back to 0-1000 at write time just to match everyone
        // else, a run tags its own scale once via "coord_scale" and this
        // shared drawing path divides by the right denominator for it.
        const coordDenom = run.coord_scale === 'unit' ? 1 : 1000;

        const objects = sortObjectsForDrawing(data.objects);
        objects.forEach(obj => {
            if (obj.file_index === pageMeta.file_index && obj.page_num === pageMeta.page_num) {
                const [xMin, yMin, xMax, yMax] = obj.box;
                const x = (xMin / coordDenom) * canvas.width;
                const y = (yMin / coordDenom) * canvas.height;
                const w = ((xMax - xMin) / coordDenom) * canvas.width;
                const h = ((yMax - yMin) / coordDenom) * canvas.height;

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

let historyFilterType = 'single';

function openHistoryModal(filterType) {
    if (!historyOverlay) return;
    historyFilterType = filterType || 'single';
    const titleEl = document.getElementById('history-modal-title');
    if (titleEl) {
        titleEl.textContent = historyFilterType === 'two_stage' ? 'Two-Stage Run History'
            : historyFilterType === 'grid' ? 'Grid Detection Run History'
            : 'Run History';
    }
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

if (openHistoryBtn) openHistoryBtn.addEventListener('click', () => openHistoryModal('single'));
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

function isTwoStageTabActive() {
    const panel = document.getElementById('tab-twostage');
    return !!(panel && panel.classList.contains('active'));
}

function isGridTabActive() {
    const panel = document.getElementById('tab-grid');
    return !!(panel && panel.classList.contains('active'));
}

// Full Page View is shared between every tab — this is the one place that
// decides which document/page/overlay boxes it should be looking at,
// depending on which tab was active when it was opened.
function getActiveViewerContext() {
    if (isTwoStageTabActive()) {
        return {
            doc: tsFiles[tsFileIdx] && tsFiles[tsFileIdx].pdf,
            page: tsPageNum,
            hasModelSelector: false,
            getOverlayBoxes: () => {
                if (tsFinalObjects) {
                    return tsFinalObjects.filter((o) => o.file_index === tsFileIdx && o.page_num === tsPageNum);
                }
                const entry = tsCurrentPageEntry();
                const elevations = entry ? entry.elevations : [];
                const callouts = entry ? (entry.callouts || []) : [];
                return [
                    ...elevations.filter((e) => e.included).map((e) => ({ label: 'elevation', box: e.box })),
                    ...callouts.map((c) => ({ label: 'elevation_callout', box: c.box })),
                ];
            },
        };
    }
    if (isGridTabActive()) {
        return {
            doc: gdFile && gdFile.pdf,
            page: gdPageNum,
            hasModelSelector: true,
            selectorEl: gdBboxSelector,
            getOverlayBoxes: () => {
                const selectedModel = fvBboxSelector ? fvBboxSelector.value : 'none';
                if (selectedModel === 'none' || gdResultsData.length === 0) return [];
                const resultData = gdResultsData.find((r) => r.model === selectedModel);
                if (!resultData) return [];
                try {
                    const data = JSON.parse(resultData.response);
                    if (!data.objects || !Array.isArray(data.objects)) return [];
                    // gdResultsData boxes are true 0-1 fractions (see the
                    // "Grid Detection" section) — drawFullViewBoundingBoxes()
                    // below divides by 1000 like every other tab, so scale up
                    // here rather than special-casing that shared function.
                    return data.objects
                        .filter((o) => o.page_num === gdPageNum)
                        .map((o) => ({ ...o, box: o.box.map((v) => v * 1000) }));
                } catch (e) {
                    return [];
                }
            },
        };
    }
    return {
        doc: currentDoc,
        page: pageNum,
        hasModelSelector: true,
        selectorEl: bboxSelector,
        getOverlayBoxes: () => {
            const selectedModel = fvBboxSelector ? fvBboxSelector.value : 'none';
            if (selectedModel === 'none' || llmResultsData.length === 0) return [];
            const resultData = llmResultsData.find((r) => r.model === selectedModel);
            if (!resultData) return [];
            const currentFileIndex = fileSelector ? parseInt(fileSelector.value) : 0;
            try {
                const data = JSON.parse(resultData.response);
                if (!data.objects || !Array.isArray(data.objects)) return [];
                return data.objects.filter((o) => o.file_index === currentFileIndex && o.page_num === pageNum);
            } catch (e) {
                return [];
            }
        },
    };
}

async function renderFullView() {
    const vc = getActiveViewerContext();
    if (!vc.doc || !fvCtx) return;
    const page = await vc.doc.getPage(vc.page);
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
    const objects = sortObjectsForDrawing(getActiveViewerContext().getOverlayBoxes());
    objects.forEach(obj => {
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
    });
}

function syncFullViewBboxOptions() {
    const sourceSelector = getActiveViewerContext().selectorEl;
    if (!fvBboxSelector || !sourceSelector) return;
    const previousValue = fvBboxSelector.value || sourceSelector.value || 'none';
    fvBboxSelector.innerHTML = sourceSelector.innerHTML;
    const hasOption = Array.from(fvBboxSelector.options).some(o => o.value === previousValue);
    fvBboxSelector.value = hasOption ? previousValue : (sourceSelector.value || 'none');
}

function openFullViewModal() {
    const vc = getActiveViewerContext();
    if (!fullviewOverlay || !vc.doc) return;

    const fvSelectorGroup = fvBboxSelector ? fvBboxSelector.closest('.control-group') : null;
    if (vc.hasModelSelector) {
        if (fvSelectorGroup) fvSelectorGroup.style.display = '';
        syncFullViewBboxOptions();
    } else {
        // Two-stage has exactly one result state to show (whatever stage is
        // current) — no "which model" to pick, so hide that control rather
        // than show a selector with nothing meaningful to choose.
        if (fvSelectorGroup) fvSelectorGroup.style.display = 'none';
    }

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

const tsOpenFullviewBtn = document.getElementById('ts-open-fullview');
if (tsOpenFullviewBtn) tsOpenFullviewBtn.addEventListener('click', openFullViewModal);

const gdOpenFullviewBtn = document.getElementById('gd-open-fullview');
if (gdOpenFullviewBtn) gdOpenFullviewBtn.addEventListener('click', openFullViewModal);

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
            iou_threshold: iouThresholdInput ? parseFloat(iouThresholdInput.value) : null,
            results: llmResultsData.map(data => {
                let parsedResponse = data.response;
                try { parsedResponse = JSON.parse(data.response); } catch (e) {}
                return {
                    model: data.model, prompt: data.prompt, response: parsedResponse,
                    score: data.score ?? null, usage: data.usage ?? null,
                };
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

// ============================================================
// Tab switcher
// ============================================================

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
tabButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
        const target = btn.dataset.tab;
        tabButtons.forEach((b) => b.classList.toggle('active', b === btn));
        tabPanels.forEach((p) => p.classList.toggle('active', p.id === `tab-${target}`));
        // Canvas sizing can end up wrong if a page was ever rendered while
        // its tab was hidden (display:none) — force a re-render on switch
        // so the preview is never stuck blank/mis-sized after a tab change.
        if (target === 'twostage' && tsFiles[tsFileIdx]) {
            tsRenderPage(tsPageNum);
        } else if (target === 'benchmark' && currentDoc) {
            renderPage(pageNum);
        }
    });
});

const tsOpenHistoryBtn = document.getElementById('ts-open-history');
if (tsOpenHistoryBtn) tsOpenHistoryBtn.addEventListener('click', () => openHistoryModal('two_stage'));

// (Two-Stage's "⚙ System Prompt" button opens its own dedicated modal —
// bound further down, near tsGetSystemPrompt/tsOpenSystemPromptModal.)

// ============================================================
// Two-Stage Detection (elevation-first, human-reviewed, then details)
// ============================================================
// Own PDF viewer, isolated from the main benchmark tab's state entirely
// (separate pdf.js documents, canvas, page number, etc.) so switching tabs
// never disturbs whatever's loaded on the other one.
//
// Unlike the main tab, this flow never batches multiple pages into one
// model request — Stage 1 always needs one full-page image and Stage 2
// always needs one crop (or one full-page callout scan), so every page of
// every uploaded file is still its own independent request. What IS
// configurable (mirroring the main tab's Execution Settings) is only the
// ORDER those independent requests run in: file-by-file or all files at
// once, page-by-page or all pages of a file at once. See
// tsExecutionSettings / the "Execution Settings" modal below.

let tsFiles = [];        // [{name, pdf, rawFile}] — every uploaded PDF
let tsFileIdx = 0;       // which file is currently shown in the viewer
let tsPageNum = 1;       // which page of that file is currently shown
let tsScale = 1.5;
let tsRenderTask = null;

// One entry per (file_index, page_num) that Stage 1 has run on:
// "f:p" -> { pageWidth, pageHeight, elevations: [{id, box, included}] }
let tsElevationsMap = new Map();
// Flat list of every final object (label, box, file_index, page_num) across
// every processed page, set once Stage 2 completes; null before that.
let tsFinalObjects = null;
// Stage 1 runs as its own request, so what it cost is carried here and
// handed back to Stage 2, which reports the two stages' totals together.
let tsStage1Usage = null;
let tsLastUsage = null;
let tsLastScore = null;

function tsPageKey(fileIdx, pageNum) { return `${fileIdx}:${pageNum}`; }
function tsCurrentPageEntry() { return tsElevationsMap.get(tsPageKey(tsFileIdx, tsPageNum)); }

const tsDropZone = document.getElementById('ts-drop-zone');
const tsUploadInput = document.getElementById('ts-pdf-upload');
const tsCanvas = document.getElementById('ts-pdf-canvas');
const tsCtx = tsCanvas ? tsCanvas.getContext('2d') : null;
const tsPdfNav = document.getElementById('ts-pdf-nav');
const tsFileSelector = document.getElementById('ts-file-selector');
const tsZoomInBtn = document.getElementById('ts-zoom-in');
const tsZoomOutBtn = document.getElementById('ts-zoom-out');
const tsZoomLevelEl = document.getElementById('ts-zoom-level');
const tsPrevBtn = document.getElementById('ts-prev-page');
const tsNextBtn = document.getElementById('ts-next-page');
const tsRunStage1Btn = document.getElementById('ts-run-stage1');
const tsRunStage2Btn = document.getElementById('ts-run-stage2');
const tsResetBtn = document.getElementById('ts-reset');
const tsElevationReview = document.getElementById('ts-elevation-review');
const tsElevationList = document.getElementById('ts-elevation-list');
const tsElevationSummary = document.getElementById('ts-elevation-summary');
const tsResultBlock = document.getElementById('ts-result-block');
const tsResponseBox = document.getElementById('ts-response-box');
const tsUsageBox = document.getElementById('ts-usage-box');
const tsScoreBox = document.getElementById('ts-score-box');
const tsIouThresholdInput = document.getElementById('ts-iou-threshold');
const tsGroundTruthEl = document.getElementById('ts-ground-truth');

if (tsDropZone && tsUploadInput) {
    tsDropZone.addEventListener('click', (e) => { if (e.target !== tsUploadInput) tsUploadInput.click(); });
    tsDropZone.addEventListener('dragover', (e) => { e.preventDefault(); tsDropZone.classList.add('dragover'); });
    tsDropZone.addEventListener('dragleave', () => tsDropZone.classList.remove('dragover'));
    tsDropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        tsDropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) tsHandleFiles(e.dataTransfer.files);
    });
    tsUploadInput.addEventListener('change', (e) => {
        if (e.target.files.length) tsHandleFiles(e.target.files);
    });
}

async function tsHandleFiles(fileList) {
    if (!tsFileSelector) return;
    const loaded = [];
    for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        if (file.type !== 'application/pdf') continue;
        try {
            const arrayBuffer = await file.arrayBuffer();
            const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
            loaded.push({ name: file.name, pdf, rawFile: file });
        } catch (err) { console.error(err); }
    }
    if (loaded.length === 0) { alert('Please upload at least one PDF file.'); return; }

    tsFiles = loaded;
    tsFileSelector.innerHTML = '';
    tsFiles.forEach((f, i) => {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = f.name;
        tsFileSelector.appendChild(option);
    });

    if (tsPdfNav) tsPdfNav.style.display = 'flex';
    tsResetStageUI();
    await tsLoadFile(0);
}

async function tsLoadFile(idx) {
    tsFileIdx = idx;
    tsPageNum = 1;
    if (tsFileSelector) tsFileSelector.value = String(idx);
    const pageCountEl = document.getElementById('ts-page-count');
    if (pageCountEl) pageCountEl.textContent = tsFiles[idx].pdf.numPages;
    await tsRenderPage(tsPageNum);
    tsRenderElevationList();
}

if (tsFileSelector) tsFileSelector.addEventListener('change', (e) => tsLoadFile(parseInt(e.target.value, 10)));

async function tsRenderPage(num) {
    const doc = tsFiles[tsFileIdx] && tsFiles[tsFileIdx].pdf;
    if (!doc || !tsCtx) return;
    const page = await doc.getPage(num);
    const viewport = page.getViewport({ scale: tsScale });

    tsCanvas.height = viewport.height;
    tsCanvas.width = viewport.width;
    tsCanvas.style.width = `${viewport.width}px`;
    tsCanvas.style.height = `${viewport.height}px`;

    if (tsRenderTask) tsRenderTask.cancel();

    try {
        tsRenderTask = page.render({ canvasContext: tsCtx, viewport });
        await tsRenderTask.promise;
        tsRenderTask = null;
        const pageNumEl = document.getElementById('ts-page-num');
        if (pageNumEl) pageNumEl.textContent = num;
        tsDrawOverlay();
    } catch (err) {
        if (err.name !== 'RenderingCancelledException') console.error('ts render error:', err);
    }
}

if (tsZoomInBtn) tsZoomInBtn.addEventListener('click', () => {
    tsScale += 0.25;
    if (tsZoomLevelEl) tsZoomLevelEl.textContent = `${Math.round(tsScale * 100)}%`;
    tsRenderPage(tsPageNum);
});
if (tsZoomOutBtn) tsZoomOutBtn.addEventListener('click', () => {
    if (tsScale <= 0.5) return;
    tsScale -= 0.25;
    if (tsZoomLevelEl) tsZoomLevelEl.textContent = `${Math.round(tsScale * 100)}%`;
    tsRenderPage(tsPageNum);
});
// Navigating pages/files only changes what's SHOWN — it never clears
// tsElevationsMap/tsFinalObjects, so review checkboxes and final results
// already computed for other pages survive moving around while reviewing.
if (tsPrevBtn) tsPrevBtn.addEventListener('click', () => {
    if (tsPageNum > 1) { tsPageNum--; tsRenderPage(tsPageNum); tsRenderElevationList(); }
});
if (tsNextBtn) tsNextBtn.addEventListener('click', () => {
    const doc = tsFiles[tsFileIdx] && tsFiles[tsFileIdx].pdf;
    if (doc && tsPageNum < doc.numPages) { tsPageNum++; tsRenderPage(tsPageNum); tsRenderElevationList(); }
});

function tsDrawBox(box, labelText, style) {
    if (!tsCtx) return;
    const [xMin, yMin, xMax, yMax] = box;
    const x = (xMin / 1000) * tsCanvas.width;
    const y = (yMin / 1000) * tsCanvas.height;
    const w = ((xMax - xMin) / 1000) * tsCanvas.width;
    const h = ((yMax - yMin) / 1000) * tsCanvas.height;

    tsCtx.lineWidth = style.lineWidth;
    tsCtx.strokeStyle = style.stroke;
    if (style.fill) {
        tsCtx.fillStyle = style.fill;
        tsCtx.fillRect(x, y, w, h);
    }
    tsCtx.strokeRect(x, y, w, h);

    const textY = y > 20 ? y - 8 : y + 20;
    tsCtx.font = 'bold 16px Arial';
    tsCtx.fillStyle = '#0f111a';
    tsCtx.fillRect(x, textY - 14, tsCtx.measureText(labelText).width + 10, 18);
    tsCtx.fillStyle = style.stroke;
    tsCtx.fillText(labelText, x + 5, textY);
}

// Draws whichever stage's results are current, filtered to the CURRENTLY
// VIEWED (file, page): the final merged objects (elevation + cabinet +
// countertop + elevation_callout) once Stage 2 has run, otherwise the
// Stage 1 elevation boxes for this page being reviewed (dimmed if
// unchecked), otherwise nothing.
function tsDrawOverlay() {
    if (!tsCtx) return;
    if (tsFinalObjects) {
        const pageObjects = tsFinalObjects.filter(
            (o) => o.file_index === tsFileIdx && o.page_num === tsPageNum
        );
        sortObjectsForDrawing(pageObjects).forEach((obj) => {
            tsDrawBox(obj.box, obj.label, styleForLabel(obj.label));
        });
        return;
    }
    const entry = tsCurrentPageEntry();
    const elevations = entry ? entry.elevations : [];
    const callouts = entry ? (entry.callouts || []) : [];
    elevations.forEach((elev, i) => {
        const style = elev.included
            ? styleForLabel('elevation')
            : { stroke: '#666a7d', fill: null, lineWidth: 2 };
        tsDrawBox(elev.box, `${i + 1}`, style);
    });
    // Callouts found alongside the elevations in this same Stage 1 pass —
    // shown for visibility only, they aren't reviewable/checkable here.
    callouts.forEach((c) => {
        tsDrawBox(c.box, 'elevation_callout', styleForLabel('elevation_callout'));
    });
}

function tsResetStageUI() {
    tsElevationsMap = new Map();
    tsFinalObjects = null;
    tsStage1Usage = null;
    tsLastUsage = null;
    tsLastScore = null;
    if (tsElevationReview) tsElevationReview.style.display = 'none';
    if (tsElevationList) tsElevationList.innerHTML = '';
    if (tsElevationSummary) tsElevationSummary.textContent = '';
    if (tsResultBlock) tsResultBlock.style.display = 'none';
    if (tsResponseBox) tsResponseBox.textContent = '';
    setInfoBox('ts-usage-box', '');
    setInfoBox('ts-score-box', '');
    if (tsRunStage2Btn) tsRunStage2Btn.disabled = true;
    tsDrawOverlay();
}

if (tsResetBtn) {
    tsResetBtn.addEventListener('click', () => { tsResetStageUI(); if (tsFiles[tsFileIdx]) tsRenderPage(tsPageNum); });
}

function tsGetSystemPrompt(stage) {
    const useShared = document.getElementById('ts-modal-use-shared-system-prompt');
    if (!useShared || useShared.checked) {
        const el = document.getElementById('ts-modal-system-prompt-shared');
        const v = el ? el.value.trim() : '';
        return v !== '' ? v : defaultSystemPrompt;
    }
    const el = document.getElementById(stage === 1 ? 'ts-modal-system-prompt-1' : 'ts-modal-system-prompt-2');
    const v = el ? el.value.trim() : '';
    return v !== '' ? v : defaultSystemPrompt;
}

const tsSystemPromptOverlay = document.getElementById('ts-system-prompt-overlay');
const tsOpenSystemPromptBtnRef = document.getElementById('ts-open-system-prompt');
const tsCloseSystemPromptBtn = document.getElementById('ts-close-system-prompt');
const tsSaveSystemPromptBtn = document.getElementById('ts-save-system-prompt');
const tsResetSystemPromptBtn = document.getElementById('ts-reset-system-prompt');
const tsModalUseSharedSystemPrompt = document.getElementById('ts-modal-use-shared-system-prompt');
const tsModalSharedBlock = document.getElementById('ts-modal-shared-block');
const tsModalSeparateBlock = document.getElementById('ts-modal-separate-block');
const tsModalSystemPromptShared = document.getElementById('ts-modal-system-prompt-shared');
const tsModalSystemPrompt1 = document.getElementById('ts-modal-system-prompt-1');
const tsModalSystemPrompt2 = document.getElementById('ts-modal-system-prompt-2');

function tsOpenSystemPromptModal() {
    if (!tsSystemPromptOverlay) return;
    tsSystemPromptOverlay.classList.add('open');
    tsSystemPromptOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    (tsModalUseSharedSystemPrompt && tsModalUseSharedSystemPrompt.checked
        ? tsModalSystemPromptShared : tsModalSystemPrompt1)?.focus();
}

function tsCloseSystemPromptModal() {
    if (!tsSystemPromptOverlay) return;
    tsSystemPromptOverlay.classList.remove('open');
    tsSystemPromptOverlay.style.display = 'none';
    document.body.style.overflow = '';
}

if (tsModalUseSharedSystemPrompt) {
    tsModalUseSharedSystemPrompt.addEventListener('change', (e) => {
        const separate = !e.target.checked;
        if (tsModalSharedBlock) tsModalSharedBlock.style.display = separate ? 'none' : 'block';
        if (tsModalSeparateBlock) tsModalSeparateBlock.style.display = separate ? 'block' : 'none';
        // Prefill both stage fields with the shared value the first time,
        // so switching to "separate" doesn't start from blank fields.
        if (separate) {
            const sharedValue = tsModalSystemPromptShared ? tsModalSystemPromptShared.value.trim() : '';
            if (tsModalSystemPrompt1 && !tsModalSystemPrompt1.value.trim()) tsModalSystemPrompt1.value = sharedValue || defaultSystemPrompt;
            if (tsModalSystemPrompt2 && !tsModalSystemPrompt2.value.trim()) tsModalSystemPrompt2.value = sharedValue || defaultSystemPrompt;
        }
    });
}

if (tsOpenSystemPromptBtnRef) tsOpenSystemPromptBtnRef.addEventListener('click', tsOpenSystemPromptModal);
if (tsCloseSystemPromptBtn) tsCloseSystemPromptBtn.addEventListener('click', tsCloseSystemPromptModal);
if (tsSaveSystemPromptBtn) tsSaveSystemPromptBtn.addEventListener('click', tsCloseSystemPromptModal);
if (tsSystemPromptOverlay) {
    tsSystemPromptOverlay.addEventListener('click', (e) => {
        if (e.target === tsSystemPromptOverlay) tsCloseSystemPromptModal();
    });
}

if (tsResetSystemPromptBtn) {
    tsResetSystemPromptBtn.addEventListener('click', () => {
        if (tsModalUseSharedSystemPrompt && tsModalUseSharedSystemPrompt.checked) {
            if (tsModalSystemPromptShared) tsModalSystemPromptShared.value = defaultSystemPrompt;
        } else {
            if (tsModalSystemPrompt1) tsModalSystemPrompt1.value = defaultSystemPrompt;
            if (tsModalSystemPrompt2) tsModalSystemPrompt2.value = defaultSystemPrompt;
        }
    });
}

// ---------------- Two-Stage execution settings modal ----------------
// Same "sequential vs parallel" idea as the main tab's Execution Settings,
// but only a File axis and a Page axis — there's no grouping axis here
// since every page is always its own request (see the note above tsFiles).

const tsExecutionSettingsOverlay = document.getElementById('ts-execution-settings-overlay');
const tsOpenExecutionSettingsBtn = document.getElementById('ts-open-execution-settings');
const tsCloseExecutionSettingsBtn = document.getElementById('ts-close-execution-settings');
const tsSaveExecutionSettingsBtn = document.getElementById('ts-save-execution-settings');
const tsResetExecutionSettingsBtn = document.getElementById('ts-reset-execution-settings');
const tsFileExecutionModeSelect = document.getElementById('ts-file-execution-mode');
const tsPageExecutionModeSelect = document.getElementById('ts-page-execution-mode');

const tsDefaultExecutionSettings = { fileExecutionMode: 'sequential', pageExecutionMode: 'sequential' };
let tsExecutionSettings = { ...tsDefaultExecutionSettings };

function tsApplyExecutionSettingsToForm(settings) {
    if (tsFileExecutionModeSelect) tsFileExecutionModeSelect.value = settings.fileExecutionMode;
    if (tsPageExecutionModeSelect) tsPageExecutionModeSelect.value = settings.pageExecutionMode;
}

function tsOpenExecutionSettingsModal() {
    if (!tsExecutionSettingsOverlay) return;
    tsApplyExecutionSettingsToForm(tsExecutionSettings);
    tsExecutionSettingsOverlay.classList.add('open');
    tsExecutionSettingsOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function tsCloseExecutionSettingsModal() {
    if (!tsExecutionSettingsOverlay) return;
    tsExecutionSettingsOverlay.classList.remove('open');
    tsExecutionSettingsOverlay.style.display = 'none';
    document.body.style.overflow = '';
}

if (tsOpenExecutionSettingsBtn) tsOpenExecutionSettingsBtn.addEventListener('click', tsOpenExecutionSettingsModal);
if (tsCloseExecutionSettingsBtn) tsCloseExecutionSettingsBtn.addEventListener('click', tsCloseExecutionSettingsModal);

if (tsResetExecutionSettingsBtn) {
    tsResetExecutionSettingsBtn.addEventListener('click', () => {
        tsApplyExecutionSettingsToForm(tsDefaultExecutionSettings);
    });
}

if (tsSaveExecutionSettingsBtn) {
    tsSaveExecutionSettingsBtn.addEventListener('click', () => {
        tsExecutionSettings = {
            fileExecutionMode: tsFileExecutionModeSelect ? tsFileExecutionModeSelect.value : tsDefaultExecutionSettings.fileExecutionMode,
            pageExecutionMode: tsPageExecutionModeSelect ? tsPageExecutionModeSelect.value : tsDefaultExecutionSettings.pageExecutionMode,
        };
        tsCloseExecutionSettingsModal();
    });
}

if (tsExecutionSettingsOverlay) {
    tsExecutionSettingsOverlay.addEventListener('click', (e) => {
        if (e.target === tsExecutionSettingsOverlay) tsCloseExecutionSettingsModal();
    });
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && tsExecutionSettingsOverlay && tsExecutionSettingsOverlay.classList.contains('open')) {
        tsCloseExecutionSettingsModal();
    }
});

// Shows the checklist for whichever (file, page) is CURRENTLY VIEWED, plus
// a running total across every page Stage 1 has touched so far — since
// review now spans potentially many pages/files, not just one.
function tsRenderElevationList() {
    if (!tsElevationList) return;

    let totalFound = 0;
    let totalApproved = 0;
    let totalCallouts = 0;
    tsElevationsMap.forEach((v) => {
        totalFound += v.elevations.length;
        totalApproved += v.elevations.filter((e) => e.included).length;
        totalCallouts += (v.callouts || []).length;
    });
    if (tsElevationSummary) {
        const currentFile = tsFiles[tsFileIdx] ? tsFiles[tsFileIdx].name : '';
        tsElevationSummary.textContent = tsElevationsMap.size > 0
            ? `${totalApproved} of ${totalFound} elevation(s) approved, ${totalCallouts} elevation_callout(s) found, across ${tsElevationsMap.size} page(s). Viewing: ${currentFile} — page ${tsPageNum}.`
            : '';
    }

    const entry = tsCurrentPageEntry();
    const elevations = entry ? entry.elevations : [];

    if (elevations.length === 0) {
        tsElevationList.innerHTML = tsElevationsMap.size > 0
            ? '<p class="ts-hint">No elevations detected on this page. Use Prev/Next or the file selector above to review other pages.</p>'
            : '';
        return;
    }

    tsElevationList.innerHTML = elevations.map((elev, i) => `
        <div class="ts-elevation-row">
            <input type="checkbox" data-ts-elev-idx="${i}" ${elev.included ? 'checked' : ''}>
            <span class="ts-elevation-swatch"></span>
            <span>Elevation ${i + 1}</span>
            <span class="ts-elevation-coords">[${elev.box.map((v) => Math.round(v)).join(', ')}]</span>
        </div>
    `).join('');
    tsElevationList.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.tsElevIdx, 10);
            elevations[idx].included = e.target.checked;
            tsRenderElevationList();
            tsRenderPage(tsPageNum);
        });
    });
}

if (tsRunStage1Btn) {
    tsRunStage1Btn.addEventListener('click', async () => {
        if (tsFiles.length === 0) { alert('Upload at least one PDF first.'); return; }
        const modelName = document.getElementById('ts-model-name').value.trim();
        const elevationPrompt = document.getElementById('ts-elevation-prompt').value.trim();
        if (!modelName || !elevationPrompt) { alert('Fill in the model and the Stage 1 prompt.'); return; }

        tsRunStage1Btn.disabled = true;
        tsRunStage1Btn.textContent = '⏳ Detecting…';
        tsResetStageUI();

        const stage1Dpi = parseInt(document.getElementById('ts-dpi-stage1').value, 10) || 200;

        try {
            const formData = new FormData();
            tsFiles.forEach((f) => formData.append('files', f.rawFile));
            formData.append('stage1_dpi', stage1Dpi);
            formData.append('model', modelName);
            formData.append('system_prompt_stage1', tsGetSystemPrompt(1));
            formData.append('elevation_prompt', elevationPrompt);
            formData.append('file_execution_mode', tsExecutionSettings.fileExecutionMode);
            formData.append('page_execution_mode', tsExecutionSettings.pageExecutionMode);

            const res = await fetch('/api/two-stage/elevations', { method: 'POST', body: formData });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Request failed (${res.status})`);
            }
            const data = await res.json();

            let totalElevations = 0;
            let totalCallouts = 0;
            (data.results || []).forEach((r) => {
                const elevations = (r.elevations || []).map((e) => ({ ...e, included: true }));
                const callouts = r.callouts || [];
                totalElevations += elevations.length;
                totalCallouts += callouts.length;
                tsElevationsMap.set(tsPageKey(r.file_index, r.page_num), {
                    pageWidth: r.page_width,
                    pageHeight: r.page_height,
                    elevations,
                    callouts,
                });
            });

            // Kept so the Stage 2 request can hand it back to the backend —
            // that's what lets the final totals cover both stages instead of
            // only the crops (see /api/two-stage/details).
            tsStage1Usage = data.usage || null;
            if (tsUsageBox && tsStage1Usage) {
                setInfoBox('ts-usage-box', `Stage 1 only —\n${formatUsage(tsStage1Usage)}`);
                if (tsResultBlock) tsResultBlock.style.display = 'block';
            }

            if (totalElevations === 0 && totalCallouts === 0) {
                alert('Nothing was detected on any page.');
            } else {
                if (tsElevationReview) tsElevationReview.style.display = 'block';
                if (tsRunStage2Btn) tsRunStage2Btn.disabled = false;
            }
            tsRenderElevationList();
            await tsRenderPage(tsPageNum);
        } catch (err) {
            console.error(err);
            alert(`Stage 1 failed: ${err.message}`);
        } finally {
            tsRunStage1Btn.disabled = false;
            tsRunStage1Btn.textContent = '① Detect Elevations';
        }
    });
}

if (tsRunStage2Btn) {
    tsRunStage2Btn.addEventListener('click', async () => {
        if (tsElevationsMap.size === 0) { alert('Run Stage 1 first.'); return; }
        const modelName = document.getElementById('ts-model-name').value.trim();
        const detailPrompt = document.getElementById('ts-detail-prompt').value.trim();
        if (!modelName || !detailPrompt) { alert('Fill in the model and the Stage 2 prompt.'); return; }

        tsRunStage2Btn.disabled = true;
        tsRunStage2Btn.textContent = '⏳ Detecting…';

        const stage1Dpi = parseInt(document.getElementById('ts-dpi-stage1').value, 10) || 200;
        const stage2Dpi = parseInt(document.getElementById('ts-dpi-stage2').value, 10) || 300;
        const concurrency = parseInt(document.getElementById('ts-stage2-concurrency').value, 10) || 3;

        // Optional — an empty box simply means "run without scoring".
        let groundTruth = [];
        const gtRaw = tsGroundTruthEl ? tsGroundTruthEl.value.trim() : '';
        if (gtRaw) {
            try {
                groundTruth = JSON.parse(gtRaw);
            } catch (e) {
                alert('Ground truth must be valid JSON — see the description above the box for the expected shape.');
                tsRunStage2Btn.disabled = false;
                tsRunStage2Btn.textContent = '② Looks good — Detect Details';
                return;
            }
        }

        // Every page Stage 1 touched gets its own target: whatever
        // elevations were approved (get cropped and re-detected), plus
        // whatever elevation_callouts Stage 1 already found there — those
        // just get carried straight into the final result, no re-detection.
        const targets = [];
        tsElevationsMap.forEach((entry, key) => {
            const [fileIdxStr, pageNumStr] = key.split(':');
            targets.push({
                file_index: parseInt(fileIdxStr, 10),
                page_num: parseInt(pageNumStr, 10),
                elevations: entry.elevations.filter((e) => e.included).map((e) => ({ id: e.id, box: e.box })),
                callouts: entry.callouts || [],
            });
        });

        try {
            const formData = new FormData();
            tsFiles.forEach((f) => formData.append('files', f.rawFile));
            formData.append('stage1_dpi', stage1Dpi);
            formData.append('stage2_dpi', stage2Dpi);
            formData.append('stage2_concurrency', concurrency);
            formData.append('model', modelName);
            formData.append('system_prompt_stage1', tsGetSystemPrompt(1));
            formData.append('system_prompt_stage2', tsGetSystemPrompt(2));
            formData.append('elevation_prompt', document.getElementById('ts-elevation-prompt').value.trim());
            formData.append('detail_prompt', detailPrompt);
            formData.append('targets', JSON.stringify(targets));
            formData.append('file_execution_mode', tsExecutionSettings.fileExecutionMode);
            formData.append('page_execution_mode', tsExecutionSettings.pageExecutionMode);

            let iouValue = tsIouThresholdInput ? parseFloat(tsIouThresholdInput.value) : 0.5;
            if (!Number.isFinite(iouValue)) iouValue = 0.5;
            formData.append('iou_threshold', iouValue);
            formData.append('ground_truth', JSON.stringify(groundTruth));
            if (tsStage1Usage) formData.append('stage1_usage', JSON.stringify(tsStage1Usage));

            const res = await fetch('/api/two-stage/details', { method: 'POST', body: formData });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Request failed (${res.status})`);
            }
            const data = await res.json();
            const parsed = JSON.parse(data.response);
            tsFinalObjects = parsed.objects || [];

            if (tsResponseBox) tsResponseBox.textContent = JSON.stringify(parsed, null, 2);
            if (tsResultBlock) tsResultBlock.style.display = 'block';
            if (tsElevationReview) tsElevationReview.style.display = 'none';

            tsLastScore = data.score || null;
            tsLastUsage = data.usage || null;
            setInfoBox('ts-usage-box', formatUsage(tsLastUsage));
            setInfoBox('ts-score-box', tsLastScore
                ? formatScore(tsLastScore)
                : (gtRaw ? 'Ground truth matched no page in these files — nothing to score.' : ''));

            await tsRenderPage(tsPageNum);
        } catch (err) {
            console.error(err);
            alert(`Stage 2 failed: ${err.message}`);
        } finally {
            tsRunStage2Btn.disabled = false;
            tsRunStage2Btn.textContent = '② Looks good — Detect Details';
        }
    });
}

// ============================================================
// Grid Detection (labeled reference grid instead of raw coordinates)
// ============================================================
// One PDF at a time — ground truth is entered per run, scoped to that one
// document, so every page of it is sent (per gdExecutionSettings), never
// more than one file. Boxes returned by /api/grid/detect are 0-1 xyxy (the
// location-scorer shared contract), NOT the 0-1000 scale every other tab
// uses — gdDrawBox() below works directly in 0-1 fractions since it's a
// standalone function, not reused from the 0-1000-based tabs. The shared
// History modal (used by every tab) is patched separately, at
// drawHistoryPage(), to upscale by 1000 only when a run's own
// "coord_scale" is "unit" — see that function.

const defaultGridSystemPrompt = `You are a senior construction-documents specialist — an expert reviewer of architectural millwork and casework drawing sets (cabinet/casework elevations, floor plans, and reflected ceiling plans). You are looking at ONE single sheet page per request. A labeled reference grid has been drawn on top of the sheet image — this is a tool for you to report positions with, not part of the architectural drawing itself.

═══════════════════════════════════════
THE GRID
═══════════════════════════════════════

The sheet has been divided into a grid of rows and columns (the exact count for THIS request — e.g. "6 rows x 6 columns" — is stated again in the accompanying message, along with which object type(s) to report). Columns are lettered left-to-right starting at "A"; rows are numbered top-to-bottom starting at "1". A cell's label (e.g. "C4" = column C, row 4) is NEVER printed inside the cell itself — column letters run once each along a header strip added ABOVE the sheet, and row numbers once each along a strip added to its LEFT, like a spreadsheet's own row/column headers. To name a cell, trace the grid line down from its column letter and across from its row number to where they meet.

The grid lines are a translucent reference overlay on top of the sheet image; the header strips are outside the sheet image itself, added above and to its left. Read through the lines to the drawing underneath — never mistake a grid line for a wall, a divider, or a dimension line.

═══════════════════════════════════════
YOUR JOB
═══════════════════════════════════════

For every object of the requested type(s), report which grid cell(s) its own visible extent occupies — nothing else. Do NOT report pixel coordinates, a 0-1000 box, or a 0-1 box for this request; the cell label(s) are the only location format that matters here.

Detect only object types explicitly requested for this call (told to you in the accompanying message), drawn from this set:
- cabinet — one individual cabinet/locker/shelving unit box inside an elevation drawing (upper cabinets, base cabinets, lockers, open shelving), identified by its own door/drawer/shelf marks (dashed door-swing diagonals, drawer-pull ticks) and bounded by its own solid vertical carcass lines. Never the whole elevation.
- countertop — the counter surface band/backsplash line inside an elevation drawing, directly above a base-cabinet run. This band is usually THIN (often just 1 row tall) but can run across MANY columns — trace it end-to-end along the full width of the cabinet run it sits above and list every column cell it visibly passes through. Stopping after the first cell or two, instead of tracing all the way to both ends of its own visible run, is a common mistake.
- elevation — the entire framed casework elevation drawing (the whole rectangle containing the view: floor line, side boundaries, the cabinet run(s) standing on it, usually captioned with a view name + scale below or beside it). Never just the cabinets inside it.
- elevation_callout — a small circular or pentagonal reference symbol on a floor plan or reflected ceiling plan (never inside an elevation frame itself), with one or more solid black filled arrowheads pointing at a wall, referencing an elevation drawn elsewhere in the set. Report the cell(s) the SYMBOL'S OWN SHAPE occupies — never the cell(s) that only contain its reference number/sheet text (e.g. "4", "AE401"), which often sits in the cell right next to it. Find the shape itself first, then read off its cell; don't let the nearby text pull your answer toward itself.

Ignore everything else: schedules, partition types, general notes, title blocks, north arrows, dimension strings, room name tags without cabinetry, finish schedules, site maps, and freestanding furniture (lockers, benches) that isn't built-in casework.

═══════════════════════════════════════
CELL REPORTING RULES
═══════════════════════════════════════

- If an object's visible extent sits entirely within one cell, report "cells":["C4"] — a single-element list.
- If an object's own extent spans more than one cell (it visibly crosses a grid line), report EVERY cell its extent touches — e.g. "cells":["C4","C5","D4","D5"] for an object spanning a 2x2 block. List exactly the cells the object's own drawing actually occupies, not a looser or tighter block.
- One physical object is ONE entry in "objects", with ALL of its occupied cells listed together under "cells" — never split one object into several entries just because it spans multiple cells, and never merge two separate objects into one entry because they happen to share a cell.
- Only count a cell as occupied because the object's own drawn extent (the frame/panel/symbol itself) is visibly there — a dimension line, leader arrow, or text label passing through a cell does NOT make that cell part of the object. This cuts both ways: don't add a cell just because a label sits in it, and don't let a label in an ADJACENT cell pull you away from the cell the object itself is actually drawn in either.
- Every cell label you report MUST be one actually drawn on this grid (within the stated rows x columns) and MUST be copied exactly as printed (a letter run, then a number — e.g. "C4", "AA12") — never invent, round, or guess a cell outside the grid.
- On a fine grid (many rows/columns), don't guess a cell from a quick glance — deliberately COUNT gridlines from the nearest labeled header (top for columns, left for rows) out to the object's own edges before naming a cell. A one-line miscount is the most common mistake on a dense grid.

Once you have listed all objects, do ONE final pass over the whole list checking: did I scan every region of the sheet, not just the most obvious drawing? Does every "cells" list contain only cells the object's own extent actually touches? Fix anything that fails before finalizing.

═══════════════════════════════════════

Return ONLY JSON. No markdown, no code fences, no commentary. Top-level output MUST be a single JSON object with exactly one key: "objects" — a list of every requested object found. Never a bare array at the top level. Every entry in "objects" is itself a JSON object.

{
 "objects":[
   {"object_type":"elevation","cells":["B2","B3"]},
   {"object_type":"cabinet","cells":["B3"]}
 ]
}

Rules:
- This request contains exactly ONE image (the full sheet page with its reference grid).
- Only report the object type(s) requested for this call — never a type not asked for.
- "object_type" is the label key — use exactly the type names given to you, nothing else.
- "cells" is always a non-empty list of cell labels, never a box, never pixel or 0-1/0-1000 coordinates.
- Never create duplicate JSON keys.`;

const defaultGridUserPrompt = `This is one full sheet page with a labeled reference grid drawn on top (columns lettered left-to-right starting at "A", rows numbered top-to-bottom starting at "1" — the exact grid size and requested object type(s) for this call are restated in the system message above). Scan the ENTIRE sheet thoroughly, left to right, top to bottom, before answering — do not stop after finding the first few objects.

For every object of the requested type(s), report every grid cell its own visible extent occupies, exactly as defined in your instructions — never a pixel, 0-1000, or 0-1 box. Watch for two specific mistakes: a countertop band that you stop tracing too early instead of following to both ends of its full run, and an elevation_callout cell picked from its nearby reference text instead of from the symbol's own shape. Before answering, double-check that every "cells" entry lists only cells actually drawn on the grid and only cells the object itself touches. Respond with the JSON object only — no explanation, no markdown.`;

let gdFile = null;       // { name, pdf, rawFile } — exactly one PDF, unlike the other tabs
let gdPageNum = 1;
let gdScale = 1.5;
let gdRenderTask = null;
let gdResultsData = [];  // [{ model, prompt, response, score }] — response objects are 0-1 xyxy

const gdDropZone = document.getElementById('gd-drop-zone');
const gdUploadInput = document.getElementById('gd-pdf-upload');
const gdCanvas = document.getElementById('gd-pdf-canvas');
const gdCtx = gdCanvas ? gdCanvas.getContext('2d') : null;
const gdPdfNav = document.getElementById('gd-pdf-nav');
const gdFileSelector = document.getElementById('gd-file-selector');
const gdZoomInBtn = document.getElementById('gd-zoom-in');
const gdZoomOutBtn = document.getElementById('gd-zoom-out');
const gdZoomLevelEl = document.getElementById('gd-zoom-level');
const gdPrevBtn = document.getElementById('gd-prev-page');
const gdNextBtn = document.getElementById('gd-next-page');

const gdVisControls = document.getElementById('gd-vis-controls');
const gdBboxSelector = document.getElementById('gd-bbox-selector');
const gdOutlineOnlyToggle = document.getElementById('gd-outline-only-toggle');
let gdOutlineOnlyMode = false;

const gdModelCountSelect = document.getElementById('gd-model-count');
const gdModelsGrid = document.getElementById('gd-models-grid');
const gdUseGlobalPrompt = document.getElementById('gd-use-global-prompt');
const gdGlobalPrompt = document.getElementById('gd-global-prompt');
const gdModelPrompts = document.querySelectorAll('#gd-models-grid .model-prompt');

const gdDpiInput = document.getElementById('gd-dpi-input');
const gdMaxDimInput = document.getElementById('gd-max-dim-input');
const gdIouThresholdInput = document.getElementById('gd-iou-threshold');
const gdObjectTypesInput = document.getElementById('gd-object-types');
const gdGroundTruthEl = document.getElementById('gd-ground-truth');

const gdRunBtn = document.getElementById('gd-run-btn');
const gdExportBtn = document.getElementById('gd-export-btn');

document.addEventListener('DOMContentLoaded', () => {
    const gdSystemPromptEl = document.getElementById('gd-system-prompt');
    if (gdSystemPromptEl) gdSystemPromptEl.value = defaultGridSystemPrompt;
    gdModelPrompts.forEach((p) => { p.value = defaultGridUserPrompt; });
    if (gdGlobalPrompt) gdGlobalPrompt.value = defaultGridUserPrompt;
    updateGdActiveModels();
});

function updateGdActiveModels() {
    if (!gdModelCountSelect || !gdModelsGrid) return;
    const count = parseInt(gdModelCountSelect.value, 10);
    gdModelsGrid.className = `models-grid cols-${count}`;
    for (let i = 1; i <= 3; i++) {
        const card = document.getElementById(`gd-model-${i}`);
        if (card) i <= count ? card.classList.remove('hidden') : card.classList.add('hidden');
    }
}
if (gdModelCountSelect) gdModelCountSelect.addEventListener('change', updateGdActiveModels);

if (gdUseGlobalPrompt && gdGlobalPrompt) {
    gdUseGlobalPrompt.addEventListener('change', (e) => {
        const isGlobal = e.target.checked;
        gdGlobalPrompt.style.display = isGlobal ? 'block' : 'none';
        gdModelPrompts.forEach((p) => { p.disabled = isGlobal; });
    });
}

// ---------------- Single-PDF upload ----------------

if (gdDropZone && gdUploadInput) {
    gdDropZone.addEventListener('click', (e) => { if (e.target !== gdUploadInput) gdUploadInput.click(); });
    gdDropZone.addEventListener('dragover', (e) => { e.preventDefault(); gdDropZone.classList.add('dragover'); });
    gdDropZone.addEventListener('dragleave', () => gdDropZone.classList.remove('dragover'));
    gdDropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        gdDropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) gdHandleFiles(e.dataTransfer.files);
    });
    gdUploadInput.addEventListener('change', (e) => {
        if (e.target.files.length) gdHandleFiles(e.target.files);
    });
}

async function gdHandleFiles(fileList) {
    // Exactly one PDF for this tab (ground truth is scoped to a single
    // document) — take the first PDF found and ignore/replace the rest,
    // rather than silently combining them like the other tabs do.
    const pdfFile = Array.from(fileList).find((f) => f.type === 'application/pdf');
    if (!pdfFile) { alert('Please upload a PDF file.'); return; }
    if (fileList.length > 1) {
        alert('Grid Detection scores ground truth against ONE PDF — only the first PDF file found was loaded.');
    }
    try {
        const arrayBuffer = await pdfFile.arrayBuffer();
        const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
        gdFile = { name: pdfFile.name, pdf, rawFile: pdfFile };
    } catch (err) {
        console.error(err);
        alert('Failed to load this PDF.');
        return;
    }
    gdResultsData = [];
    if (gdVisControls) gdVisControls.style.display = 'none';
    if (gdExportBtn) gdExportBtn.style.display = 'none';
    for (let i = 1; i <= 3; i++) {
        const box = document.getElementById(`gd-score-${i}`);
        if (box) { box.textContent = ''; box.classList.remove('visible'); }
        const card = document.getElementById(`gd-model-${i}`);
        const rBox = card ? card.querySelector('.response-box') : null;
        if (rBox) { rBox.textContent = 'Waiting to run…'; rBox.dataset.empty = 'true'; }
    }
    if (gdFileSelector) {
        gdFileSelector.innerHTML = '';
        const opt = document.createElement('option');
        opt.value = '0';
        opt.textContent = gdFile.name;
        gdFileSelector.appendChild(opt);
    }
    if (gdPdfNav) gdPdfNav.style.display = 'flex';
    gdPageNum = 1;
    const pageCountEl = document.getElementById('gd-page-count');
    if (pageCountEl) pageCountEl.textContent = gdFile.pdf.numPages;
    await gdRenderPage(gdPageNum);
}

// ---------------- Viewer ----------------

async function gdRenderPage(num) {
    if (!gdFile || !gdCtx) return;
    const page = await gdFile.pdf.getPage(num);
    const viewport = page.getViewport({ scale: gdScale });

    gdCanvas.height = viewport.height;
    gdCanvas.width = viewport.width;
    gdCanvas.style.width = `${viewport.width}px`;
    gdCanvas.style.height = `${viewport.height}px`;

    if (gdRenderTask) gdRenderTask.cancel();

    try {
        gdRenderTask = page.render({ canvasContext: gdCtx, viewport });
        await gdRenderTask.promise;
        gdRenderTask = null;
        const pageNumEl = document.getElementById('gd-page-num');
        if (pageNumEl) pageNumEl.textContent = num;
        gdDrawBoundingBoxes();
    } catch (err) {
        if (err.name !== 'RenderingCancelledException') console.error('grid render error:', err);
    }
}

if (gdZoomInBtn) gdZoomInBtn.addEventListener('click', () => {
    gdScale += 0.25;
    if (gdZoomLevelEl) gdZoomLevelEl.textContent = `${Math.round(gdScale * 100)}%`;
    gdRenderPage(gdPageNum);
    if (isFullViewOpen()) renderFullView();
});
if (gdZoomOutBtn) gdZoomOutBtn.addEventListener('click', () => {
    if (gdScale <= 0.5) return;
    gdScale -= 0.25;
    if (gdZoomLevelEl) gdZoomLevelEl.textContent = `${Math.round(gdScale * 100)}%`;
    gdRenderPage(gdPageNum);
    if (isFullViewOpen()) renderFullView();
});
if (gdPrevBtn) gdPrevBtn.addEventListener('click', () => {
    if (gdPageNum > 1) {
        gdPageNum--;
        gdRenderPage(gdPageNum);
        if (isFullViewOpen()) renderFullView();
    }
});
if (gdNextBtn) gdNextBtn.addEventListener('click', () => {
    if (gdFile && gdPageNum < gdFile.pdf.numPages) {
        gdPageNum++;
        gdRenderPage(gdPageNum);
        if (isFullViewOpen()) renderFullView();
    }
});
if (gdBboxSelector) gdBboxSelector.addEventListener('change', () => gdRenderPage(gdPageNum));
if (gdOutlineOnlyToggle) {
    gdOutlineOnlyToggle.addEventListener('change', (e) => {
        gdOutlineOnlyMode = e.target.checked;
        gdRenderPage(gdPageNum);
    });
}

// Boxes here are 0-1 xyxy fractions straight from /api/grid/detect's
// response — NOT the 0-1000 scale drawBoundingBoxes()/tsDrawBox() use, so
// this multiplies by canvas width/height directly rather than dividing by
// 1000 first.
function gdDrawBoundingBoxes() {
    const selectedModel = gdBboxSelector ? gdBboxSelector.value : 'none';
    if (selectedModel === 'none' || gdResultsData.length === 0 || !gdCtx) return;

    const resultData = gdResultsData.find((r) => r.model === selectedModel);
    if (!resultData) return;

    try {
        const data = JSON.parse(resultData.response);
        if (!data.objects || !Array.isArray(data.objects)) return;

        const objects = sortObjectsForDrawing(data.objects);
        objects.forEach((obj) => {
            if (obj.page_num !== gdPageNum) return;
            const [xMin, yMin, xMax, yMax] = obj.box;

            const x = xMin * gdCanvas.width;
            const y = yMin * gdCanvas.height;
            const w = (xMax - xMin) * gdCanvas.width;
            const h = (yMax - yMin) * gdCanvas.height;

            const style = styleForLabel(obj.label);
            gdCtx.lineWidth = style.lineWidth;
            gdCtx.strokeStyle = style.stroke;
            if (style.fill && !gdOutlineOnlyMode) {
                gdCtx.fillStyle = style.fill;
                gdCtx.fillRect(x, y, w, h);
            }
            gdCtx.strokeRect(x, y, w, h);

            const textY = y > 20 ? y - 8 : y + 20;
            gdCtx.font = 'bold 16px Arial';
            gdCtx.fillStyle = '#0f111a';
            gdCtx.fillRect(x, textY - 14, gdCtx.measureText(obj.label).width + 10, 18);
            gdCtx.fillStyle = style.stroke;
            gdCtx.fillText(obj.label, x + 5, textY);
        });
    } catch (e) {
        console.error('Failed to parse grid response JSON', e);
    }
}

// ---------------- System prompt modal ----------------

const gdSystemPromptOverlay = document.getElementById('gd-system-prompt-overlay');
const gdOpenSystemPromptBtn = document.getElementById('gd-open-system-prompt');
const gdCloseSystemPromptBtn = document.getElementById('gd-close-system-prompt');
const gdSaveSystemPromptBtn = document.getElementById('gd-save-system-prompt');
const gdResetSystemPromptBtn = document.getElementById('gd-reset-system-prompt');

function gdOpenSystemPromptModal() {
    if (!gdSystemPromptOverlay) return;
    gdSystemPromptOverlay.classList.add('open');
    gdSystemPromptOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    const el = document.getElementById('gd-system-prompt');
    if (el) el.focus();
}
function gdCloseSystemPromptModal() {
    if (!gdSystemPromptOverlay) return;
    gdSystemPromptOverlay.classList.remove('open');
    gdSystemPromptOverlay.style.display = 'none';
    document.body.style.overflow = '';
}
if (gdOpenSystemPromptBtn) gdOpenSystemPromptBtn.addEventListener('click', gdOpenSystemPromptModal);
if (gdCloseSystemPromptBtn) gdCloseSystemPromptBtn.addEventListener('click', gdCloseSystemPromptModal);
if (gdSaveSystemPromptBtn) gdSaveSystemPromptBtn.addEventListener('click', gdCloseSystemPromptModal);
if (gdResetSystemPromptBtn) {
    gdResetSystemPromptBtn.addEventListener('click', () => {
        const el = document.getElementById('gd-system-prompt');
        if (el) el.value = defaultGridSystemPrompt;
    });
}
if (gdSystemPromptOverlay) {
    gdSystemPromptOverlay.addEventListener('click', (e) => {
        if (e.target === gdSystemPromptOverlay) gdCloseSystemPromptModal();
    });
}

// ---------------- Execution settings modal ----------------

const gdExecutionSettingsOverlay = document.getElementById('gd-execution-settings-overlay');
const gdOpenExecutionSettingsBtn = document.getElementById('gd-open-execution-settings');
const gdCloseExecutionSettingsBtn = document.getElementById('gd-close-execution-settings');
const gdSaveExecutionSettingsBtn = document.getElementById('gd-save-execution-settings');
const gdResetExecutionSettingsBtn = document.getElementById('gd-reset-execution-settings');
const gdModelExecutionModeSelect = document.getElementById('gd-model-execution-mode');
const gdPageExecutionModeSelect = document.getElementById('gd-page-execution-mode');

const gdDefaultExecutionSettings = { modelExecutionMode: 'sequential', pageExecutionMode: 'sequential' };
let gdExecutionSettings = { ...gdDefaultExecutionSettings };

function gdApplyExecutionSettingsToForm(settings) {
    if (gdModelExecutionModeSelect) gdModelExecutionModeSelect.value = settings.modelExecutionMode;
    if (gdPageExecutionModeSelect) gdPageExecutionModeSelect.value = settings.pageExecutionMode;
}
function gdOpenExecutionSettingsModal() {
    if (!gdExecutionSettingsOverlay) return;
    gdApplyExecutionSettingsToForm(gdExecutionSettings);
    gdExecutionSettingsOverlay.classList.add('open');
    gdExecutionSettingsOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}
function gdCloseExecutionSettingsModal() {
    if (!gdExecutionSettingsOverlay) return;
    gdExecutionSettingsOverlay.classList.remove('open');
    gdExecutionSettingsOverlay.style.display = 'none';
    document.body.style.overflow = '';
}
if (gdOpenExecutionSettingsBtn) gdOpenExecutionSettingsBtn.addEventListener('click', gdOpenExecutionSettingsModal);
if (gdCloseExecutionSettingsBtn) gdCloseExecutionSettingsBtn.addEventListener('click', gdCloseExecutionSettingsModal);
if (gdResetExecutionSettingsBtn) {
    gdResetExecutionSettingsBtn.addEventListener('click', () => gdApplyExecutionSettingsToForm(gdDefaultExecutionSettings));
}
if (gdSaveExecutionSettingsBtn) {
    gdSaveExecutionSettingsBtn.addEventListener('click', () => {
        gdExecutionSettings = {
            modelExecutionMode: gdModelExecutionModeSelect ? gdModelExecutionModeSelect.value : gdDefaultExecutionSettings.modelExecutionMode,
            pageExecutionMode: gdPageExecutionModeSelect ? gdPageExecutionModeSelect.value : gdDefaultExecutionSettings.pageExecutionMode,
        };
        gdCloseExecutionSettingsModal();
    });
}
if (gdExecutionSettingsOverlay) {
    gdExecutionSettingsOverlay.addEventListener('click', (e) => {
        if (e.target === gdExecutionSettingsOverlay) gdCloseExecutionSettingsModal();
    });
}

// ---------------- Grid settings modal ----------------
// Rows/cols plus the overlay's visual styling (color/opacity/thickness) —
// all forwarded to draw_grid_overlay() on the backend, which draws this
// exact grid on the page image before any model ever sees it.

const gdGridSettingsOverlay = document.getElementById('gd-grid-settings-overlay');
const gdOpenGridSettingsBtn = document.getElementById('gd-open-grid-settings');
const gdCloseGridSettingsBtn = document.getElementById('gd-close-grid-settings');
const gdSaveGridSettingsBtn = document.getElementById('gd-save-grid-settings');
const gdResetGridSettingsBtn = document.getElementById('gd-reset-grid-settings');
const gdModalGridRowsInput = document.getElementById('gd-modal-grid-rows');
const gdModalGridColsInput = document.getElementById('gd-modal-grid-cols');
const gdModalGridColorInput = document.getElementById('gd-modal-grid-color');
const gdModalGridOpacityInput = document.getElementById('gd-modal-grid-opacity');
const gdModalGridOpacityReadout = document.getElementById('gd-modal-grid-opacity-readout');
const gdModalGridThicknessInput = document.getElementById('gd-modal-grid-thickness');
const gdModalLabelSchemeSelect = document.getElementById('gd-modal-label-scheme');
const gdModalBoxPrecisionSelect = document.getElementById('gd-modal-box-precision');

const gdDefaultGridSettings = {
    rows: 6, cols: 6, color: '#dc0000', opacity: 0.6, thickness: 1,
    labelScheme: 'alpha', boxPrecision: 'cell',
};
let gdGridSettings = { ...gdDefaultGridSettings };

function gdApplyGridSettingsToForm(settings) {
    if (gdModalGridRowsInput) gdModalGridRowsInput.value = settings.rows;
    if (gdModalGridColsInput) gdModalGridColsInput.value = settings.cols;
    if (gdModalGridColorInput) gdModalGridColorInput.value = settings.color;
    if (gdModalGridOpacityInput) gdModalGridOpacityInput.value = settings.opacity;
    if (gdModalGridOpacityReadout) gdModalGridOpacityReadout.textContent = `${Math.round(settings.opacity * 100)}%`;
    if (gdModalGridThicknessInput) gdModalGridThicknessInput.value = settings.thickness;
    if (gdModalLabelSchemeSelect) gdModalLabelSchemeSelect.value = settings.labelScheme;
    if (gdModalBoxPrecisionSelect) gdModalBoxPrecisionSelect.value = settings.boxPrecision;
}
function gdOpenGridSettingsModal() {
    if (!gdGridSettingsOverlay) return;
    gdApplyGridSettingsToForm(gdGridSettings);
    gdGridSettingsOverlay.classList.add('open');
    gdGridSettingsOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}
function gdCloseGridSettingsModal() {
    if (!gdGridSettingsOverlay) return;
    gdGridSettingsOverlay.classList.remove('open');
    gdGridSettingsOverlay.style.display = 'none';
    document.body.style.overflow = '';
}
if (gdOpenGridSettingsBtn) gdOpenGridSettingsBtn.addEventListener('click', gdOpenGridSettingsModal);
if (gdCloseGridSettingsBtn) gdCloseGridSettingsBtn.addEventListener('click', gdCloseGridSettingsModal);
if (gdResetGridSettingsBtn) {
    gdResetGridSettingsBtn.addEventListener('click', () => gdApplyGridSettingsToForm(gdDefaultGridSettings));
}
if (gdModalGridOpacityInput && gdModalGridOpacityReadout) {
    gdModalGridOpacityInput.addEventListener('input', () => {
        gdModalGridOpacityReadout.textContent = `${Math.round(parseFloat(gdModalGridOpacityInput.value) * 100)}%`;
    });
}
if (gdSaveGridSettingsBtn) {
    gdSaveGridSettingsBtn.addEventListener('click', () => {
        let rows = gdModalGridRowsInput ? parseInt(gdModalGridRowsInput.value, 10) : gdDefaultGridSettings.rows;
        if (!Number.isFinite(rows) || rows <= 0) rows = gdDefaultGridSettings.rows;
        let cols = gdModalGridColsInput ? parseInt(gdModalGridColsInput.value, 10) : gdDefaultGridSettings.cols;
        if (!Number.isFinite(cols) || cols <= 0) cols = gdDefaultGridSettings.cols;
        let opacity = gdModalGridOpacityInput ? parseFloat(gdModalGridOpacityInput.value) : gdDefaultGridSettings.opacity;
        if (!Number.isFinite(opacity)) opacity = gdDefaultGridSettings.opacity;
        let thickness = gdModalGridThicknessInput ? parseInt(gdModalGridThicknessInput.value, 10) : gdDefaultGridSettings.thickness;
        if (!Number.isFinite(thickness) || thickness <= 0) thickness = gdDefaultGridSettings.thickness;
        gdGridSettings = {
            rows, cols, opacity, thickness,
            color: gdModalGridColorInput ? gdModalGridColorInput.value : gdDefaultGridSettings.color,
            labelScheme: gdModalLabelSchemeSelect ? gdModalLabelSchemeSelect.value : gdDefaultGridSettings.labelScheme,
            boxPrecision: gdModalBoxPrecisionSelect ? gdModalBoxPrecisionSelect.value : gdDefaultGridSettings.boxPrecision,
        };
        gdCloseGridSettingsModal();
    });
}
if (gdGridSettingsOverlay) {
    gdGridSettingsOverlay.addEventListener('click', (e) => {
        if (e.target === gdGridSettingsOverlay) gdCloseGridSettingsModal();
    });
}

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (gdSystemPromptOverlay && gdSystemPromptOverlay.classList.contains('open')) gdCloseSystemPromptModal();
    if (gdExecutionSettingsOverlay && gdExecutionSettingsOverlay.classList.contains('open')) gdCloseExecutionSettingsModal();
    if (gdGridSettingsOverlay && gdGridSettingsOverlay.classList.contains('open')) gdCloseGridSettingsModal();
});

// ---------------- Run ----------------

if (gdRunBtn) {
    gdRunBtn.addEventListener('click', async () => {
        if (!gdFile) { alert('Upload one PDF file first.'); return; }

        const modelsData = [];
        const activeCount = gdModelCountSelect ? parseInt(gdModelCountSelect.value, 10) : 3;
        for (let i = 1; i <= activeCount; i++) {
            const card = document.getElementById(`gd-model-${i}`);
            if (!card) continue;
            const modelNameEl = card.querySelector('.model-name');
            const promptEl = card.querySelector('.model-prompt');
            const responseBox = card.querySelector('.response-box');
            const modelName = modelNameEl ? modelNameEl.value.trim() : '';
            const prompt = (gdUseGlobalPrompt && gdUseGlobalPrompt.checked)
                ? (gdGlobalPrompt ? gdGlobalPrompt.value.trim() : '')
                : (promptEl ? promptEl.value.trim() : '');
            if (responseBox) {
                responseBox.textContent = 'Waiting for response…';
                responseBox.dataset.empty = 'false';
            }
            setInfoBox(`gd-score-${i}`, '');
            setInfoBox(`gd-usage-${i}`, '');
            if (modelName && prompt) modelsData.push({ model: modelName, prompt });
        }

        if (modelsData.length === 0) {
            alert('Configure at least one model with a prompt.');
            return;
        }

        let objectTypes;
        try {
            const raw = (gdObjectTypesInput ? gdObjectTypesInput.value : '').trim();
            objectTypes = raw.split(',').map((s) => s.trim()).filter(Boolean);
            if (objectTypes.length === 0) throw new Error();
        } catch (e) {
            alert('Enter at least one object type (comma-separated).');
            return;
        }

        let groundTruth = [];
        const gtRaw = gdGroundTruthEl ? gdGroundTruthEl.value.trim() : '';
        if (gtRaw) {
            try {
                // Either {"project_id":..., "objects":[...]} (the project's
                // own export shape) or a bare list of the same object shape
                // — the backend accepts both, so just check it parses.
                groundTruth = JSON.parse(gtRaw);
            } catch (e) {
                alert('Ground truth must be valid JSON — see the placeholder for the expected shape.');
                return;
            }
        }

        gdRunBtn.textContent = '⏳ Running…';
        gdRunBtn.disabled = true;
        if (gdExportBtn) gdExportBtn.style.display = 'none';

        const formData = new FormData();
        formData.append('file', gdFile.rawFile);

        let dpiValue = gdDpiInput ? parseInt(gdDpiInput.value, 10) : 200;
        if (!Number.isFinite(dpiValue) || dpiValue <= 0) dpiValue = 200;
        formData.append('dpi', dpiValue);

        let maxDimValue = gdMaxDimInput ? parseInt(gdMaxDimInput.value, 10) : 1568;
        if (!Number.isFinite(maxDimValue) || maxDimValue <= 0) maxDimValue = 1568;
        formData.append('max_dim', maxDimValue);

        formData.append('grid_rows', gdGridSettings.rows);
        formData.append('grid_cols', gdGridSettings.cols);
        formData.append('grid_color', gdGridSettings.color);
        formData.append('grid_opacity', gdGridSettings.opacity);
        formData.append('grid_thickness', gdGridSettings.thickness);
        formData.append('label_scheme', gdGridSettings.labelScheme);
        formData.append('box_precision', gdGridSettings.boxPrecision);

        let iouValue = gdIouThresholdInput ? parseFloat(gdIouThresholdInput.value) : 0.5;
        if (!Number.isFinite(iouValue)) iouValue = 0.5;
        formData.append('iou_threshold', iouValue);

        formData.append('object_types', JSON.stringify(objectTypes));
        formData.append('models_data', JSON.stringify(modelsData));

        const gdSystemPromptEl = document.getElementById('gd-system-prompt');
        formData.append('system_prompt', (gdSystemPromptEl && gdSystemPromptEl.value.trim() !== '') ? gdSystemPromptEl.value.trim() : defaultGridSystemPrompt);

        formData.append('ground_truth', JSON.stringify(groundTruth));
        formData.append('model_execution_mode', gdExecutionSettings.modelExecutionMode);
        formData.append('page_execution_mode', gdExecutionSettings.pageExecutionMode);

        try {
            const res = await fetch('/api/grid/detect', { method: 'POST', body: formData });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Request failed (${res.status})`);
            }
            const data = await res.json();

            if (data.results) {
                gdResultsData = data.results.map((r) => {
                    const req = modelsData.find((m) => m.model === r.model);
                    return {
                        model: r.model, prompt: req ? req.prompt : '', response: r.response,
                        score: r.score, usage: r.usage,
                    };
                });

                if (gdVisControls && gdBboxSelector) {
                    gdBboxSelector.innerHTML = '<option value="none">Hide overlays</option>';
                    gdResultsData.forEach((result) => {
                        const opt = document.createElement('option');
                        opt.value = result.model;
                        opt.textContent = result.model;
                        gdBboxSelector.appendChild(opt);
                    });
                    gdVisControls.style.display = 'flex';
                }
                if (gdExportBtn) gdExportBtn.style.display = 'block';

                data.results.forEach((result) => {
                    for (let i = 1; i <= 3; i++) {
                        const card = document.getElementById(`gd-model-${i}`);
                        if (!card) continue;
                        const mNameEl = card.querySelector('.model-name');
                        if (!mNameEl || mNameEl.value !== result.model) continue;
                        const rBox = card.querySelector('.response-box');
                        if (rBox) { rBox.textContent = result.response; rBox.dataset.empty = 'false'; }
                        setInfoBox(`gd-usage-${i}`, formatUsage(result.usage));
                        setInfoBox(`gd-score-${i}`, result.score ? formatScore(result.score) : '');
                    }
                });

                await gdRenderPage(gdPageNum);
                if (isFullViewOpen()) {
                    syncFullViewBboxOptions();
                    renderFullView();
                }
            }
        } catch (err) {
            console.error(err);
            alert(`Grid detection failed: ${err.message}`);
        } finally {
            gdRunBtn.textContent = '▶ Run Grid Detection';
            gdRunBtn.disabled = false;
        }
    });
}

// ---------------- Export ----------------

if (gdExportBtn) {
    gdExportBtn.addEventListener('click', () => {
        if (!gdResultsData || gdResultsData.length === 0) return;
        const gdSystemPromptEl = document.getElementById('gd-system-prompt');
        const systemPromptValue = (gdSystemPromptEl && gdSystemPromptEl.value.trim() !== '')
            ? gdSystemPromptEl.value.trim()
            : defaultGridSystemPrompt;

        const exportData = {
            timestamp: new Date().toISOString(),
            file: gdFile ? { name: gdFile.name, pages: gdFile.pdf.numPages } : null,
            system_prompt: systemPromptValue,
            grid_rows: gdGridSettings.rows,
            grid_cols: gdGridSettings.cols,
            grid_color: gdGridSettings.color,
            grid_opacity: gdGridSettings.opacity,
            grid_thickness: gdGridSettings.thickness,
            label_scheme: gdGridSettings.labelScheme,
            box_precision: gdGridSettings.boxPrecision,
            iou_threshold: gdIouThresholdInput ? parseFloat(gdIouThresholdInput.value) : null,
            execution_settings: { ...gdExecutionSettings },
            results: gdResultsData.map((data) => {
                let parsedResponse = data.response;
                try { parsedResponse = JSON.parse(data.response); } catch (e) {}
                return {
                    model: data.model, prompt: data.prompt, response: parsedResponse,
                    score: data.score, usage: data.usage ?? null,
                };
            }),
        };
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `grid_detection_results_${new Date().getTime()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });
}

// ---------------- History ----------------

const gdOpenHistoryBtn = document.getElementById('gd-open-history');
if (gdOpenHistoryBtn) gdOpenHistoryBtn.addEventListener('click', () => openHistoryModal('grid'));