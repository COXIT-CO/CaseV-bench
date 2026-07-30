import { useCallback, useEffect, useMemo, useState } from "react";
import Toolbar from "./components/Toolbar.jsx";
import PageGrid from "./components/PageGrid.jsx";
import PromptEditor from "./components/PromptEditor.jsx";
import ResultsSummary from "./components/ResultsSummary.jsx";
import ScorePanel from "./components/ScorePanel.jsx";
import PageResultViewer from "./components/PageResultViewer.jsx";
import ImagePreviewModal from "./components/ImagePreviewModal.jsx";
import HistoryModal from "./components/HistoryModal.jsx";
import {
  fetchConfig,
  uploadPdf,
  analyzePages,
  savePrompt,
  fetchHistory,
  addModel,
  removeModel,
  resetResults,
} from "./api.js";
import { MULTI_PROMPT_CATEGORIES } from "./multiPromptCategories.js";
import { mergeCategoryResponses } from "./mergeCategoryResults.js";

const DEFAULT_DPI = 300;
const EMPTY_CATEGORY_PROMPTS = Object.fromEntries(
  MULTI_PROMPT_CATEGORIES.map(({ key }) => [key, ""])
);
const DEFAULT_GRID_ROWS = 3;
const DEFAULT_GRID_COLS = 3;
const DEFAULT_OVERLAP_PCT = 0.25;
// Rough per-tile output-token budget, used only for the "max_tokens looks
// low" nudge shown in the Cutting popover -- never enforced/blocking. Kept
// as a duplicated copy of the backend's tiling.TOKENS_PER_TILE_ESTIMATE
// (same reasoning: ~20-35 tokens/object times a handful of objects per
// tile, plus JSON scaffolding) rather than fetched from the backend, since
// this has no correctness requirement to stay byte-for-byte in sync.
const TOKENS_PER_TILE_ESTIMATE = 300;

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState(null);

  const [session, setSession] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  const [selectedPageIds, setSelectedPageIds] = useState(new Set());
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userPrompt, setUserPrompt] = useState("");
  // AI-crop's Pass 1 (region/cell detection) prompt -- editable only when
  // aiCrop is on (see PromptEditor.jsx's "Crop prompt" field). Threaded
  // through analyzePages() exactly like systemPrompt/userPrompt.
  const [cropPrompt, setCropPrompt] = useState("");
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [savePromptError, setSavePromptError] = useState(null);
  const [promptJustSaved, setPromptJustSaved] = useState(false);
  const [multiPromptMode, setMultiPromptMode] = useState(false);
  // Kept in state (not derived) precisely so toggling Multi-Prompting off
  // and back on doesn't lose what was typed into these 4 fields.
  const [categoryPrompts, setCategoryPrompts] = useState(EMPTY_CATEGORY_PROMPTS);
  const [multiPromptProgress, setMultiPromptProgress] = useState(null);
  const [model, setModel] = useState("");
  const [modelActionError, setModelActionError] = useState(null);
  const [temperature, setTemperature] = useState(0);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [dpi, setDpi] = useState(DEFAULT_DPI);
  const [cutting, setCutting] = useState(false);
  const [gridRows, setGridRows] = useState(DEFAULT_GRID_ROWS);
  const [gridCols, setGridCols] = useState(DEFAULT_GRID_COLS);
  const [overlapPct, setOverlapPct] = useState(DEFAULT_OVERLAP_PCT);
  // Mutually exclusive with cutting -- see handleCuttingChange/
  // handleAiCropChange below, which each turn the other off.
  const [aiCrop, setAiCrop] = useState(false);

  const [results, setResults] = useState({});
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  const [previewPage, setPreviewPage] = useState(null);
  const [focusedPageId, setFocusedPageId] = useState(null);
  const [referenceImages, setReferenceImages] = useState([]);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRecords, setHistoryRecords] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(null);

  useEffect(() => {
    fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        setSystemPrompt(cfg.default_system_prompt);
        setUserPrompt(cfg.default_user_prompt);
        setCropPrompt(cfg.default_crop_prompt);
        // There's only one "current default" user prompt in this app (not
        // one per category), and it happens to be cabinet-focused by
        // default (see prompts/cabinet_user.txt) -- pre-fill the cabinets
        // field with it as the closest available match, leave the other 3
        // empty. Runs once on mount, so this can't clobber anything the
        // user has already typed.
        setCategoryPrompts((prev) => ({ ...prev, cabinets: cfg.default_user_prompt }));
        setModel(cfg.default_model);
        setTemperature(cfg.default_temperature);
        setMaxTokens(cfg.default_max_tokens);
      })
      .catch((err) => setConfigError(err.message));
  }, []);

  const handleUpload = useCallback(
    async (file) => {
      setUploading(true);
      setUploadError(null);
      setResults({});
      setReferenceImages([]);
      try {
        const data = await uploadPdf(file, dpi);
        setSession({
          sessionId: data.session_id,
          filename: data.filename,
          pages: data.pages,
        });
        setSelectedPageIds(new Set(data.pages.map((p) => p.page_id)));
        setFocusedPageId(data.pages[0]?.page_id ?? null);
      } catch (err) {
        setUploadError(err.message);
      } finally {
        setUploading(false);
      }
    },
    [dpi]
  );

  const togglePage = useCallback((pageId) => {
    setSelectedPageIds((prev) => {
      const next = new Set(prev);
      if (next.has(pageId)) next.delete(pageId);
      else next.add(pageId);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    if (!session) return;
    setSelectedPageIds(new Set(session.pages.map((p) => p.page_id)));
  }, [session]);

  const deselectAll = useCallback(() => setSelectedPageIds(new Set()), []);

  const handleAddReferenceImages = useCallback(async (files) => {
    const newImages = await Promise.all(
      Array.from(files).map(async (file) => {
        const dataUrl = await readFileAsDataUrl(file);
        const match = /^data:(.+);base64,(.*)$/.exec(dataUrl) || [];
        return {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          name: file.name,
          dataUrl,
          mediaType: match[1] || file.type,
          data: match[2] || "",
        };
      })
    );
    setReferenceImages((prev) => [...prev, ...newImages]);
  }, []);

  const handleRemoveReferenceImage = useCallback((id) => {
    setReferenceImages((prev) => prev.filter((img) => img.id !== id));
  }, []);

  const handleToggleMultiPrompt = useCallback(() => {
    setMultiPromptMode((prev) => !prev);
  }, []);

  const handleCategoryPromptChange = useCallback((key, value) => {
    setCategoryPrompts((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Cutting and AI-crop are mutually exclusive (both are alternate ways of
  // chopping up the same page before detection) -- turning one on turns
  // the other off, enforced here rather than by disabling either button.
  const handleCuttingChange = useCallback((value) => {
    setCutting(value);
    if (value) setAiCrop(false);
  }, []);

  const handleAiCropChange = useCallback((value) => {
    setAiCrop(value);
    if (value) setCutting(false);
  }, []);

  const handleAnalyzeMulti = useCallback(
    async (pageIds) => {
      const activeCategories = MULTI_PROMPT_CATEGORIES.filter(({ key }) =>
        (categoryPrompts[key] || "").trim()
      );
      if (activeCategories.length === 0) {
        setAnalyzeError(
          "Multi-Prompting is on but every category prompt is empty — fill in at least one, or turn it off."
        );
        setResults((prev) => {
          const next = { ...prev };
          pageIds.forEach((id) => delete next[id]);
          return next;
        });
        return;
      }

      setMultiPromptProgress({ done: 0, total: activeCategories.length });
      try {
        // Clear any results already stored server-side for these pages
        // first, so this run's per-category merges (see api.js's
        // analyzePages `category` param) start from a clean slate instead
        // of piling on top of a previous run's detections.
        await resetResults(session.sessionId, pageIds);

        let doneCount = 0;
        const settledRaw = await Promise.allSettled(
          activeCategories.map(({ key }) =>
            analyzePages({
              sessionId: session.sessionId,
              pageIds,
              systemPrompt,
              userPrompt: categoryPrompts[key],
              model,
              temperature: Number(temperature),
              maxTokens: Number(maxTokens),
              referenceImages,
              category: key,
              cutting,
              gridRows: Number(gridRows),
              gridCols: Number(gridCols),
              overlapPct: Number(overlapPct),
              aiCrop,
              cropPrompt,
            }).finally(() => {
              doneCount += 1;
              setMultiPromptProgress({ done: doneCount, total: activeCategories.length });
            })
          )
        );

        const categoryResults = settledRaw.map((settled, i) => ({
          category: activeCategories[i].key,
          status: settled.status,
          response: settled.status === "fulfilled" ? settled.value : null,
          reason: settled.status === "rejected" ? settled.reason : null,
        }));

        const merged = mergeCategoryResponses(pageIds, categoryResults, session.pages);
        setResults((prev) => ({ ...prev, ...merged }));

        if (categoryResults.every((cr) => cr.status === "rejected")) {
          setAnalyzeError("All category requests failed.");
        }
      } catch (err) {
        setAnalyzeError(err.message);
        setResults((prev) => {
          const next = { ...prev };
          pageIds.forEach((id) => {
            next[id] = { status: "error", error_message: err.message };
          });
          return next;
        });
      } finally {
        setMultiPromptProgress(null);
      }
    },
    [
      session,
      categoryPrompts,
      systemPrompt,
      model,
      temperature,
      maxTokens,
      referenceImages,
      cutting,
      gridRows,
      gridCols,
      overlapPct,
      aiCrop,
      cropPrompt,
    ]
  );

  const handleAnalyze = useCallback(async () => {
    if (!session || selectedPageIds.size === 0) return;
    setAnalyzing(true);
    setAnalyzeError(null);

    const pageIds = Array.from(selectedPageIds);
    setResults((prev) => {
      const next = { ...prev };
      pageIds.forEach((id) => {
        next[id] = { status: "running" };
      });
      return next;
    });
    setFocusedPageId((prev) => prev ?? pageIds[0]);

    if (multiPromptMode) {
      await handleAnalyzeMulti(pageIds);
      setAnalyzing(false);
      return;
    }

    try {
      const response = await analyzePages({
        sessionId: session.sessionId,
        pageIds,
        systemPrompt,
        userPrompt,
        model,
        temperature: Number(temperature),
        maxTokens: Number(maxTokens),
        referenceImages,
        cutting,
        gridRows: Number(gridRows),
        gridCols: Number(gridCols),
        overlapPct: Number(overlapPct),
        aiCrop,
        cropPrompt,
      });
      setResults((prev) => {
        const next = { ...prev };
        response.results.forEach((r) => {
          next[r.page_id] = r;
        });
        return next;
      });
    } catch (err) {
      setAnalyzeError(err.message);
      setResults((prev) => {
        const next = { ...prev };
        pageIds.forEach((id) => {
          next[id] = { status: "error", error_message: err.message };
        });
        return next;
      });
    } finally {
      setAnalyzing(false);
    }
  }, [
    session,
    selectedPageIds,
    systemPrompt,
    userPrompt,
    model,
    temperature,
    maxTokens,
    referenceImages,
    multiPromptMode,
    handleAnalyzeMulti,
    cutting,
    gridRows,
    gridCols,
    overlapPct,
    aiCrop,
    cropPrompt,
  ]);

  // Informational only -- never blocks sending. See tiling.py's
  // TOKENS_PER_TILE_ESTIMATE on the backend for the same reasoning (kept as
  // a duplicated JS constant, not fetched from the backend).
  const maxTokensWarning = useMemo(() => {
    if (!cutting) return null;
    const totalTiles = Number(gridRows) * Number(gridCols);
    if (!totalTiles) return null;
    const perTile = Number(maxTokens) / totalTiles;
    if (perTile >= TOKENS_PER_TILE_ESTIMATE) return null;
    return (
      `Max tokens (${maxTokens}) is low for ${totalTiles} tiles ` +
      `(~${Math.round(perTile)}/tile) -- responses may truncate before ` +
      "all tiles are covered."
    );
  }, [cutting, gridRows, gridCols, maxTokens]);

  const handleAddModel = useCallback(async (id) => {
    setModelActionError(null);
    try {
      const newModel = await addModel(id);
      setConfig((prev) => {
        if (!prev) return prev;
        if (prev.models.some((m) => m.id === newModel.id)) return prev;
        return { ...prev, models: [...prev.models, newModel] };
      });
      setModel(newModel.id);
    } catch (err) {
      setModelActionError(err.message);
    }
  }, []);

  const handleRemoveModel = useCallback(
    async (id) => {
      setModelActionError(null);
      try {
        await removeModel(id);
        setConfig((prev) =>
          prev
            ? { ...prev, models: prev.models.filter((m) => m.id !== id) }
            : prev
        );
        setModel((prevModel) =>
          prevModel === id ? config?.default_model ?? "" : prevModel
        );
      } catch (err) {
        setModelActionError(err.message);
      }
    },
    [config]
  );

  const handleResetPrompt = useCallback(() => {
    if (!config) return;
    setSystemPrompt(config.default_system_prompt);
    setUserPrompt(config.default_user_prompt);
  }, [config]);

  const handleSavePrompt = useCallback(async () => {
    setSavingPrompt(true);
    setSavePromptError(null);
    try {
      await savePrompt(systemPrompt, userPrompt);
      setConfig((prev) =>
        prev
          ? {
              ...prev,
              default_system_prompt: systemPrompt,
              default_user_prompt: userPrompt,
            }
          : prev
      );
      setPromptJustSaved(true);
    } catch (err) {
      setSavePromptError(err.message);
    } finally {
      setSavingPrompt(false);
    }
  }, [systemPrompt, userPrompt]);

  const isPromptDirty =
    systemPrompt !== (config?.default_system_prompt ?? "") ||
    userPrompt !== (config?.default_user_prompt ?? "");

  useEffect(() => {
    if (isPromptDirty) setPromptJustSaved(false);
  }, [isPromptDirty]);

  const handleOpenHistory = useCallback(async () => {
    setHistoryOpen(true);
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const records = await fetchHistory();
      setHistoryRecords(records);
    } catch (err) {
      setHistoryError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleLoadHistoryRecord = useCallback((record) => {
    setSystemPrompt(record.system_prompt);
    setUserPrompt(record.user_prompt);
    setModel(record.model);
    setTemperature(record.temperature);
    setMaxTokens(record.max_tokens);
    setDpi(record.dpi);
    setHistoryOpen(false);
  }, []);

  const focusedPage = useMemo(
    () => session?.pages.find((p) => p.page_id === focusedPageId) ?? null,
    [session, focusedPageId]
  );
  const focusedIndex = useMemo(
    () => session?.pages.findIndex((p) => p.page_id === focusedPageId) ?? -1,
    [session, focusedPageId]
  );

  const focusOffset = useCallback(
    (offset) => {
      if (!session) return;
      const nextIndex = focusedIndex + offset;
      if (nextIndex < 0 || nextIndex >= session.pages.length) return;
      setFocusedPageId(session.pages[nextIndex].page_id);
    },
    [session, focusedIndex]
  );

  return (
    <div className="app">
      <header className="app-header">
        <h1>Casework Drawing Analyzer</h1>
        <p className="subtitle">
          Test how well LLMs detect and count cabinets, elevations,
          countertops, and elevation callouts in casework drawings.
        </p>
      </header>

      {configError && (
        <div className="banner banner-error">
          Failed to load configuration: {configError}
        </div>
      )}

      <Toolbar
        filename={session?.filename}
        onUpload={handleUpload}
        uploading={uploading}
        models={config?.models}
        model={model}
        onModelChange={setModel}
        onAddModel={handleAddModel}
        onRemoveModel={handleRemoveModel}
        modelActionError={modelActionError}
        temperature={temperature}
        onTemperatureChange={setTemperature}
        maxTokens={maxTokens}
        onMaxTokensChange={setMaxTokens}
        dpi={dpi}
        onDpiChange={setDpi}
        cutting={cutting}
        onCuttingChange={handleCuttingChange}
        gridRows={gridRows}
        onGridRowsChange={setGridRows}
        gridCols={gridCols}
        onGridColsChange={setGridCols}
        overlapPct={overlapPct}
        onOverlapPctChange={setOverlapPct}
        maxTokensWarning={maxTokensWarning}
        aiCrop={aiCrop}
        onAiCropChange={handleAiCropChange}
        onAnalyze={handleAnalyze}
        analyzing={analyzing}
        selectedCount={selectedPageIds.size}
        onOpenHistory={handleOpenHistory}
      />
      {uploadError && <div className="banner banner-error">{uploadError}</div>}
      {analyzeError && <div className="banner banner-error">{analyzeError}</div>}

      {session && (
        <>
          <div className="layout-columns">
            <div className="column-left">
              <section className="panel">
                <PromptEditor
                  systemPrompt={systemPrompt}
                  userPrompt={userPrompt}
                  onSystemChange={setSystemPrompt}
                  onUserChange={setUserPrompt}
                  onReset={handleResetPrompt}
                  onSave={handleSavePrompt}
                  isDirty={isPromptDirty}
                  saving={savingPrompt}
                  saveError={savePromptError}
                  justSaved={promptJustSaved}
                  referenceImages={referenceImages}
                  onAddReferenceImages={handleAddReferenceImages}
                  onRemoveReferenceImage={handleRemoveReferenceImage}
                  multiPromptMode={multiPromptMode}
                  onToggleMultiPrompt={handleToggleMultiPrompt}
                  categoryPrompts={categoryPrompts}
                  onCategoryPromptChange={handleCategoryPromptChange}
                  aiCrop={aiCrop}
                  cropPrompt={cropPrompt}
                  onCropPromptChange={setCropPrompt}
                />
                {multiPromptProgress && (
                  <p className="multi-prompt-progress">
                    Analyzing… ({multiPromptProgress.done}/{multiPromptProgress.total} categories done)
                  </p>
                )}
              </section>
            </div>

            <div className="column-right">
              <ResultsSummary
                results={results}
                categories={config?.categories ?? []}
                sessionId={session.sessionId}
                pages={session.pages}
              />

              <ScorePanel
                sessionId={session.sessionId}
                hasAnyResult={Object.values(results).some((r) => r.status === "done")}
              />

              <section className="panel">
                <div className="results-panel-header">
                  <h2>
                    {focusedPage
                      ? `Page ${focusedPage.page_number} result`
                      : "Page result"}
                  </h2>
                  {session.pages.length > 1 && (
                    <div className="page-nav">
                      <button
                        type="button"
                        onClick={() => focusOffset(-1)}
                        disabled={focusedIndex <= 0}
                      >
                        &larr; Prev
                      </button>
                      <button
                        type="button"
                        onClick={() => focusOffset(1)}
                        disabled={
                          focusedIndex < 0 ||
                          focusedIndex >= session.pages.length - 1
                        }
                      >
                        Next &rarr;
                      </button>
                    </div>
                  )}
                </div>
                <PageResultViewer
                  page={focusedPage}
                  result={focusedPage ? results[focusedPage.page_id] : null}
                  downloadName={
                    focusedPage
                      ? `${session.filename}-page-${focusedPage.page_number}`
                      : "page"
                  }
                />
              </section>
            </div>
          </div>

          <PageGrid
            pages={session.pages}
            selectedPageIds={selectedPageIds}
            onToggle={togglePage}
            onSelectAll={selectAll}
            onDeselectAll={deselectAll}
            results={results}
            focusedPageId={focusedPageId}
            onFocus={(page) => setFocusedPageId(page.page_id)}
            onPreview={setPreviewPage}
            onAnalyze={handleAnalyze}
            analyzing={analyzing}
            selectedCount={selectedPageIds.size}
          />
        </>
      )}

      {previewPage && (
        <ImagePreviewModal
          page={previewPage}
          onClose={() => setPreviewPage(null)}
        />
      )}

      {historyOpen && (
        <HistoryModal
          records={historyRecords}
          loading={historyLoading}
          error={historyError}
          onSelect={handleLoadHistoryRecord}
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </div>
  );
}
