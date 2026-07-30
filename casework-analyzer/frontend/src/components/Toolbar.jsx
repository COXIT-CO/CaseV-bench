import { useEffect, useRef, useState } from "react";

const DPI_MIN = 1;
const DPI_MAX = 2400;
const GRID_MIN = 1;
const GRID_MAX = 6;
const OVERLAP_MIN = 0;
const OVERLAP_MAX = 50;

export default function Toolbar({
  filename,
  onUpload,
  uploading,
  models,
  model,
  onModelChange,
  onAddModel,
  onRemoveModel,
  modelActionError,
  temperature,
  onTemperatureChange,
  maxTokens,
  onMaxTokensChange,
  dpi,
  onDpiChange,
  cutting,
  onCuttingChange,
  gridRows,
  onGridRowsChange,
  gridCols,
  onGridColsChange,
  overlapPct,
  onOverlapPctChange,
  maxTokensWarning,
  aiCrop,
  onAiCropChange,
  onAnalyze,
  analyzing,
  selectedCount,
  onOpenHistory,
}) {
  const fileInputRef = useRef(null);
  const modelWrapRef = useRef(null);
  const tempWrapRef = useRef(null);
  const dpiWrapRef = useRef(null);
  const cuttingWrapRef = useRef(null);
  const maxTokensWrapRef = useRef(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [tempOpen, setTempOpen] = useState(false);
  const [dpiOpen, setDpiOpen] = useState(false);
  const [cuttingOpen, setCuttingOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxTokensOpen, setMaxTokensOpen] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [dpiText, setDpiText] = useState(String(dpi));
  const [dpiError, setDpiError] = useState(null);
  const [rowsText, setRowsText] = useState(String(gridRows));
  const [rowsError, setRowsError] = useState(null);
  const [colsText, setColsText] = useState(String(gridCols));
  const [colsError, setColsError] = useState(null);
  // overlapPct (state, elsewhere in the app) is a 0-0.5 fraction; this
  // field displays/edits it as a 0-50 whole-number percent.
  const [overlapText, setOverlapText] = useState(String(Math.round(overlapPct * 100)));
  const [overlapError, setOverlapError] = useState(null);
  const [newModelId, setNewModelId] = useState("");
  const [addingModel, setAddingModel] = useState(false);

  useEffect(() => {
    // Keep the free-text field in sync when `dpi` changes from outside this
    // component (e.g. loading a history record), but not on every keystroke
    // of our own edits — those flow the other way, through handleDpiInput.
    setDpiText(String(dpi));
    setDpiError(null);
  }, [dpi]);

  useEffect(() => {
    setRowsText(String(gridRows));
    setRowsError(null);
  }, [gridRows]);

  useEffect(() => {
    setColsText(String(gridCols));
    setColsError(null);
  }, [gridCols]);

  useEffect(() => {
    setOverlapText(String(Math.round(overlapPct * 100)));
    setOverlapError(null);
  }, [overlapPct]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (modelWrapRef.current && !modelWrapRef.current.contains(event.target)) {
        setModelOpen(false);
      }
      if (tempWrapRef.current && !tempWrapRef.current.contains(event.target)) {
        setTempOpen(false);
      }
      if (dpiWrapRef.current && !dpiWrapRef.current.contains(event.target)) {
        setDpiOpen(false);
      }
      if (
        cuttingWrapRef.current &&
        !cuttingWrapRef.current.contains(event.target)
      ) {
        setCuttingOpen(false);
      }
      if (
        maxTokensWrapRef.current &&
        !maxTokensWrapRef.current.contains(event.target)
      ) {
        setMaxTokensOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleFiles = (files) => {
    const file = files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      alert("Please select a PDF file.");
      return;
    }
    onUpload(file);
  };

  const currentModelOption = models?.find((m) => m.id === model);
  const currentModelLabel = currentModelOption?.label || "Select model";
  const currentModelUnimplemented = currentModelOption?.implemented === false;

  const handleDpiInput = (value) => {
    setDpiText(value);
    if (value.trim() === "") {
      setDpiError("Enter a DPI value.");
      return;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
      setDpiError("DPI must be a whole number.");
      return;
    }
    if (parsed < DPI_MIN || parsed > DPI_MAX) {
      setDpiError(`DPI must be between ${DPI_MIN} and ${DPI_MAX}.`);
      return;
    }
    setDpiError(null);
    onDpiChange(parsed);
  };

  const handleRowsInput = (value) => {
    setRowsText(value);
    const parsed = Number(value);
    if (value.trim() === "" || !Number.isFinite(parsed) || !Number.isInteger(parsed)) {
      setRowsError("Rows must be a whole number.");
      return;
    }
    if (parsed < GRID_MIN || parsed > GRID_MAX) {
      setRowsError(`Rows must be between ${GRID_MIN} and ${GRID_MAX}.`);
      return;
    }
    setRowsError(null);
    onGridRowsChange(parsed);
  };

  const handleColsInput = (value) => {
    setColsText(value);
    const parsed = Number(value);
    if (value.trim() === "" || !Number.isFinite(parsed) || !Number.isInteger(parsed)) {
      setColsError("Cols must be a whole number.");
      return;
    }
    if (parsed < GRID_MIN || parsed > GRID_MAX) {
      setColsError(`Cols must be between ${GRID_MIN} and ${GRID_MAX}.`);
      return;
    }
    setColsError(null);
    onGridColsChange(parsed);
  };

  const handleOverlapInput = (value) => {
    setOverlapText(value);
    const parsed = Number(value);
    if (value.trim() === "" || !Number.isFinite(parsed)) {
      setOverlapError("Overlap % must be a number.");
      return;
    }
    if (parsed < OVERLAP_MIN || parsed > OVERLAP_MAX) {
      setOverlapError(`Overlap % must be between ${OVERLAP_MIN} and ${OVERLAP_MAX}.`);
      return;
    }
    setOverlapError(null);
    onOverlapPctChange(parsed / 100);
  };

  const handleAddModelSubmit = async (event) => {
    event.preventDefault();
    const id = newModelId.trim();
    if (!id || !onAddModel) return;
    setAddingModel(true);
    try {
      await onAddModel(id);
      setNewModelId("");
    } finally {
      setAddingModel(false);
    }
  };

  return (
    <div
      className={`toolbar ${dragActive ? "toolbar-drag-active" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        handleFiles(event.dataTransfer.files);
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={(event) => handleFiles(event.target.files)}
      />
      <button
        type="button"
        className="toolbar-button"
        onClick={() => fileInputRef.current?.click()}
        disabled={uploading}
        title="Change PDF (or drag & drop one anywhere on this toolbar). Uses the current DPI setting for the next PDF processed."
      >
        <span className="toolbar-icon" aria-hidden="true">
          &#128196;
        </span>
        {uploading ? "Uploading…" : "Change PDF"}
      </button>

      <button
        type="button"
        className="toolbar-button"
        onClick={onOpenHistory}
        title="View past detection runs"
      >
        <span className="toolbar-icon" aria-hidden="true">
          &#128336;
        </span>
        History
      </button>

      <div className="toolbar-popover-wrap" ref={modelWrapRef}>
        <button
          type="button"
          className="toolbar-button"
          onClick={() => {
            setModelOpen((open) => !open);
            setTempOpen(false);
            setDpiOpen(false);
            setCuttingOpen(false);
            setMaxTokensOpen(false);
          }}
          title="Change model"
        >
          <span className="toolbar-icon" aria-hidden="true">
            &#129302;
          </span>
          {currentModelLabel}
          {currentModelUnimplemented && (
            <span aria-hidden="true" title="Not yet implemented">
              {" "}
              ⚠
            </span>
          )}
        </button>
        {modelOpen && (
          <div className="toolbar-popover">
            <label className="control">
              Model
              <select
                value={model}
                onChange={(event) => onModelChange(event.target.value)}
              >
                {models?.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                    {m.implemented === false ? " (not yet implemented)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {currentModelUnimplemented && (
              <span className="control-error">
                ⚠ Not yet implemented — this model has no working backend
                yet; analyzing with it will fail.
              </span>
            )}
            {models?.some((m) => m.custom) && (
              <ul className="custom-model-list">
                {models
                  .filter((m) => m.custom)
                  .map((m) => (
                    <li key={m.id} className="custom-model-item">
                      <span>{m.label}</span>
                      <button
                        type="button"
                        className="custom-model-remove"
                        title={`Remove ${m.label}`}
                        onClick={() => onRemoveModel?.(m.id)}
                      >
                        &#10005;
                      </button>
                    </li>
                  ))}
              </ul>
            )}
            <form className="control" onSubmit={handleAddModelSubmit}>
              Add model (OpenRouter slug)
              <div className="add-model-row">
                <input
                  type="text"
                  placeholder="e.g. moonshotai/kimi-k3"
                  value={newModelId}
                  onChange={(event) => setNewModelId(event.target.value)}
                />
                <button type="submit" disabled={addingModel || !newModelId.trim()}>
                  {addingModel ? "Adding…" : "Add"}
                </button>
              </div>
            </form>
            {modelActionError && (
              <span className="control-error">{modelActionError}</span>
            )}
          </div>
        )}
      </div>

      <div className="toolbar-popover-wrap" ref={tempWrapRef}>
        <button
          type="button"
          className="toolbar-button"
          onClick={() => {
            setTempOpen((open) => !open);
            setModelOpen(false);
            setDpiOpen(false);
            setCuttingOpen(false);
            setMaxTokensOpen(false);
          }}
          title="Change temperature"
        >
          <span className="toolbar-icon" aria-hidden="true">
            &#127777;
          </span>
          Temp {Number(temperature).toFixed(2)}
        </button>
        {tempOpen && (
          <div className="toolbar-popover">
            <label className="control">
              Temperature: {Number(temperature).toFixed(2)}
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={temperature}
                onChange={(event) => onTemperatureChange(event.target.value)}
              />
            </label>
          </div>
        )}
      </div>

      <div className="toolbar-popover-wrap" ref={dpiWrapRef}>
        <button
          type="button"
          className="toolbar-button"
          onClick={() => {
            setDpiOpen((open) => !open);
            setModelOpen(false);
            setTempOpen(false);
            setCuttingOpen(false);
            setMaxTokensOpen(false);
          }}
          title="Change DPI used when rendering PDF pages to images"
        >
          <span className="toolbar-icon" aria-hidden="true">
            &#128208;
          </span>
          DPI {dpi}
        </button>
        {dpiOpen && (
          <div className="toolbar-popover">
            <label className="control">
              DPI (PDF → image, next upload)
              <input
                type="number"
                min={DPI_MIN}
                max={DPI_MAX}
                step="1"
                value={dpiText}
                onChange={(event) => handleDpiInput(event.target.value)}
              />
            </label>
            {dpiError && <span className="control-error">{dpiError}</span>}
          </div>
        )}
      </div>

      <div className="toolbar-popover-wrap" ref={cuttingWrapRef}>
        <button
          type="button"
          className={`toolbar-button ${cutting ? "toolbar-button-active" : ""}`}
          onClick={() => {
            setCuttingOpen((open) => !open);
            setModelOpen(false);
            setTempOpen(false);
            setDpiOpen(false);
            setMaxTokensOpen(false);
          }}
          title="Overlapping tile detection: split the page into a grid of overlapping crops, analyzed together in one request"
        >
          <span className="toolbar-icon" aria-hidden="true">
            &#9974;
          </span>
          Cutting {cutting ? `${gridRows}x${gridCols}` : "Off"}
        </button>
        {cuttingOpen && (
          <div className="toolbar-popover">
            <label className="control control-toggle">
              <input
                type="checkbox"
                checked={cutting}
                onChange={(event) => onCuttingChange(event.target.checked)}
              />
              Cutting (overlapping tiles)
            </label>
            {cutting && (
              <>
                <label className="control">
                  Rows
                  <input
                    type="number"
                    min={GRID_MIN}
                    max={GRID_MAX}
                    step="1"
                    value={rowsText}
                    onChange={(event) => handleRowsInput(event.target.value)}
                  />
                </label>
                {rowsError && <span className="control-error">{rowsError}</span>}
                <label className="control">
                  Cols
                  <input
                    type="number"
                    min={GRID_MIN}
                    max={GRID_MAX}
                    step="1"
                    value={colsText}
                    onChange={(event) => handleColsInput(event.target.value)}
                  />
                </label>
                {colsError && <span className="control-error">{colsError}</span>}
                <button
                  type="button"
                  className="prompt-collapse-toggle"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  aria-expanded={advancedOpen}
                >
                  {advancedOpen ? "▾" : "▸"} Advanced
                </button>
                {advancedOpen && (
                  <>
                    <label className="control">
                      Overlap %
                      <input
                        type="number"
                        min={OVERLAP_MIN}
                        max={OVERLAP_MAX}
                        step="1"
                        value={overlapText}
                        onChange={(event) => handleOverlapInput(event.target.value)}
                      />
                    </label>
                    {overlapError && (
                      <span className="control-error">{overlapError}</span>
                    )}
                  </>
                )}
                {maxTokensWarning && (
                  <span className="control-error">{maxTokensWarning}</span>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <button
        type="button"
        className={`toolbar-button ${aiCrop ? "toolbar-button-active" : ""}`}
        onClick={() => onAiCropChange(!aiCrop)}
        title="AI-crop: locate tagged elevation-detail cells first (Pass 1), filter out floor plans, then run object detection on just those crops (Pass 2). Mutually exclusive with Cutting."
      >
        <span className="toolbar-icon" aria-hidden="true">
          &#127760;
        </span>
        AI-crop {aiCrop ? "On" : "Off"}
      </button>

      <div className="toolbar-popover-wrap" ref={maxTokensWrapRef}>
        <button
          type="button"
          className="toolbar-button"
          onClick={() => {
            setMaxTokensOpen((open) => !open);
            setModelOpen(false);
            setTempOpen(false);
            setDpiOpen(false);
            setCuttingOpen(false);
          }}
          title="Change max tokens for the model response"
        >
          <span className="toolbar-icon" aria-hidden="true">
            &#128221;
          </span>
          Max tokens {maxTokens}
        </button>
        {maxTokensOpen && (
          <div className="toolbar-popover">
            <label className="control">
              Max tokens
              <input
                type="number"
                min="1"
                max="64000"
                value={maxTokens}
                onChange={(event) => onMaxTokensChange(event.target.value)}
              />
            </label>
          </div>
        )}
      </div>

      {filename && (
        <span className="toolbar-filename" title={filename}>
          {filename}
        </span>
      )}

      {filename && onAnalyze && (
        <button
          type="button"
          className="primary-button"
          onClick={onAnalyze}
          disabled={analyzing || selectedCount === 0}
        >
          {analyzing ? "Analyzing…" : `Send selected (${selectedCount})`}
        </button>
      )}
    </div>
  );
}
