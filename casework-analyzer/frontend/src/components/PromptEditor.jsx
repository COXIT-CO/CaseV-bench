import { useRef, useState } from "react";
import { MULTI_PROMPT_CATEGORIES } from "../multiPromptCategories.js";

function CollapsibleField({
  label,
  collapsed,
  onToggleCollapsed,
  className,
  children,
}) {
  return (
    <div className={`prompt-field ${className || ""}`}>
      <div className="prompt-field-header">
        <button
          type="button"
          className="prompt-collapse-toggle"
          onClick={onToggleCollapsed}
          title={collapsed ? "Expand" : "Collapse"}
          aria-expanded={!collapsed}
        >
          {collapsed ? "▸" : "▾"}
        </button>
        <span className="prompt-field-label">{label}</span>
      </div>
      {!collapsed && children}
    </div>
  );
}

export default function PromptEditor({
  systemPrompt,
  userPrompt,
  onSystemChange,
  onUserChange,
  onReset,
  onSave,
  isDirty,
  saving,
  saveError,
  justSaved,
  referenceImages,
  onAddReferenceImages,
  onRemoveReferenceImage,
  multiPromptMode,
  onToggleMultiPrompt,
  categoryPrompts,
  onCategoryPromptChange,
  aiCrop,
  cropPrompt,
  onCropPromptChange,
}) {
  const fileInputRef = useRef(null);
  const [systemCollapsed, setSystemCollapsed] = useState(false);
  const [userCollapsed, setUserCollapsed] = useState(false);
  const [cropCollapsed, setCropCollapsed] = useState(false);

  return (
    <div className="prompt-editor">
      <div className="prompt-editor-header">
        <h2>Prompt</h2>
        <div className="prompt-editor-actions">
          <span className="prompt-state-indicator">
            Using: {isDirty ? "Custom prompt" : "Default prompt"}
          </span>
          {justSaved && !isDirty && (
            <span className="prompt-saved-note">Saved as default</span>
          )}
          <button
            type="button"
            className="primary-button"
            onClick={onSave}
            disabled={!isDirty || saving || multiPromptMode}
            title={
              multiPromptMode
                ? "Not available in Multi-Prompting mode"
                : undefined
            }
          >
            {saving ? "Saving…" : "Save as default"}
          </button>
          <button
            type="button"
            onClick={onReset}
            disabled={multiPromptMode}
            title={
              multiPromptMode
                ? "Not available in Multi-Prompting mode"
                : undefined
            }
          >
            Reset to default
          </button>
        </div>
      </div>

      <CollapsibleField
        label="System prompt"
        collapsed={systemCollapsed}
        onToggleCollapsed={() => setSystemCollapsed((v) => !v)}
        className="prompt-field-system"
      >
        <textarea
          value={systemPrompt}
          onChange={(event) => onSystemChange(event.target.value)}
          rows={14}
          spellCheck={false}
        />
      </CollapsibleField>

      <div className="prompt-field-header multi-prompt-toggle-row">
        <span className="prompt-field-label">User prompt</span>
        <button
          type="button"
          className={`multi-prompt-toggle ${multiPromptMode ? "multi-prompt-toggle-on" : ""}`}
          onClick={onToggleMultiPrompt}
          aria-pressed={multiPromptMode}
          title="Use a separate prompt per detection category, sent as independent parallel requests"
        >
          Multi-Prompting: {multiPromptMode ? "On" : "Off"}
        </button>
      </div>

      {multiPromptMode ? (
        <div className="multi-prompt-fields">
          {MULTI_PROMPT_CATEGORIES.map(({ key, label }) => (
            <div className="prompt-field prompt-field-user" key={key}>
              <div className="prompt-field-header">
                <span className="prompt-field-label">{label}</span>
              </div>
              <textarea
                value={categoryPrompts[key] || ""}
                onChange={(event) => onCategoryPromptChange(key, event.target.value)}
                rows={4}
                spellCheck={false}
                placeholder={`Prompt for detecting ${label.toLowerCase()} (leave blank to skip this category)`}
              />
            </div>
          ))}
        </div>
      ) : (
        <CollapsibleField
          label="User prompt"
          collapsed={userCollapsed}
          onToggleCollapsed={() => setUserCollapsed((v) => !v)}
          className="prompt-field-user"
        >
          <textarea
            value={userPrompt}
            onChange={(event) => onUserChange(event.target.value)}
            rows={4}
            spellCheck={false}
          />
        </CollapsibleField>
      )}

      {aiCrop && (
        <CollapsibleField
          label="Crop prompt"
          collapsed={cropCollapsed}
          onToggleCollapsed={() => setCropCollapsed((v) => !v)}
          className="prompt-field-user"
        >
          <textarea
            value={cropPrompt}
            onChange={(event) => onCropPromptChange(event.target.value)}
            rows={8}
            spellCheck={false}
          />
        </CollapsibleField>
      )}

      {saveError && <p className="result-warning">{saveError}</p>}

      <div className="reference-images">
        <div className="reference-images-header">
          <span className="reference-images-label">
            Reference images (optional)
          </span>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(event) => {
              if (event.target.files?.length) {
                onAddReferenceImages(event.target.files);
              }
              event.target.value = "";
            }}
          />
          <button type="button" onClick={() => fileInputRef.current?.click()}>
            Add images
          </button>
        </div>
        {referenceImages?.length > 0 && (
          <div className="reference-images-list">
            {referenceImages.map((img) => (
              <div className="reference-image-thumb" key={img.id}>
                <img src={img.dataUrl} alt={img.name} />
                <button
                  type="button"
                  className="reference-image-remove"
                  title="Remove"
                  onClick={() => onRemoveReferenceImage(img.id)}
                >
                  &#10005;
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
