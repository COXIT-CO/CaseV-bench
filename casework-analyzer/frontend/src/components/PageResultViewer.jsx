import { useEffect, useState } from "react";
import BBoxCanvas from "./BBoxCanvas.jsx";
import CropsCarousel from "./CropsCarousel.jsx";

export default function PageResultViewer({ page, result, downloadName }) {
  const [viewMode, setViewMode] = useState("annotated");

  const hasObjects = Boolean(result?.parsed?.objects?.length);
  const hasCrops = Boolean(result?.crops?.length);
  const isCleanResult =
    result?.status === "done" &&
    !result.truncated &&
    !result.parse_error &&
    result.parsed;

  useEffect(() => {
    if (isCleanResult) setViewMode(hasCrops ? "crops" : "annotated");
  }, [page?.page_id, isCleanResult, hasCrops]);

  if (!page) {
    return (
      <div className="result-block result-pending">
        Upload a PDF and select a page to see its analysis here.
      </div>
    );
  }

  if (!result) {
    return (
      <div className="result-block result-pending">
        No result yet for Page {page.page_number} — select it on the left and
        click "Send selected".
      </div>
    );
  }

  if (result.status === "pending" || result.status === "running") {
    return <div className="result-block result-pending">Analyzing…</div>;
  }

  if (result.status === "error") {
    return (
      <div className="result-block result-error">
        <p className="result-warning">Error: {result.error_message}</p>
      </div>
    );
  }

  // Multi-Prompting mode: category_errors is set whenever some (but not all)
  // of the 4 per-category requests failed for this page (see
  // mergeCategoryResults.js / backend parser.merge_page_results). Render
  // each as a small inline warning instead of failing the whole page --
  // the categories that did succeed still draw normally below.
  const categoryErrorEntries = result.category_errors
    ? Object.entries(result.category_errors)
    : [];
  const renderCategoryErrors = () =>
    categoryErrorEntries.length > 0 && (
      <div className="multi-prompt-category-errors">
        {categoryErrorEntries.map(([category, message]) => (
          <p className="multi-prompt-category-error" key={category}>
            {category} request failed — retry? ({message})
          </p>
        ))}
      </div>
    );

  // A function, not a precomputed value: `result.parsed` is null in the
  // genuine parse-failure case, and this must never be evaluated then --
  // only called where `hasObjects` (or a clean result) already guarantees
  // `result.parsed` is non-null.
  //
  // AI-crop results (hasCrops) never populate parsed.objects (see
  // ai_crop.py/CropInfo -- detections live per-crop, in that crop's own
  // coordinate space, not remapped to the full page anymore), so for those
  // the view-toggle drops "Annotated image" entirely in favor of "Crops"
  // -- there's nothing meaningful to draw on the full page for this mode.
  const renderViewerBody = () => (
    <>
      <div className="view-toggle">
        {hasCrops ? (
          <button
            type="button"
            className={viewMode === "crops" ? "toggle-active" : ""}
            onClick={() => setViewMode("crops")}
          >
            Crops ({result.crops.length})
          </button>
        ) : (
          <button
            type="button"
            className={viewMode === "annotated" ? "toggle-active" : ""}
            onClick={() => setViewMode("annotated")}
          >
            Annotated image
          </button>
        )}
        <button
          type="button"
          className={viewMode === "json" ? "toggle-active" : ""}
          onClick={() => setViewMode("json")}
        >
          Raw JSON
        </button>
      </div>
      {viewMode === "json" ? (
        <pre className="json-view">{JSON.stringify(result.parsed, null, 2)}</pre>
      ) : hasCrops ? (
        <CropsCarousel crops={result.crops} downloadName={downloadName} />
      ) : (
        <BBoxCanvas
          page={page}
          objects={result.parsed.objects}
          downloadName={downloadName}
        />
      )}
    </>
  );

  // Truncated (hit max_tokens) or a parse failure both still go through the
  // resilient parser (services/parser.py), which can recover a usable prefix
  // of valid detections even from a response that got cut off mid-object or
  // had broken JSON syntax partway through. Rather than hiding those behind
  // the raw text, show them -- alongside the warning, not instead of it.
  if (result.truncated || result.parse_error || !result.parsed) {
    const warning = result.truncated
      ? 'Response was cut off (hit the max tokens limit) before it finished — raise "Temp / Max tokens" above and re-run this page.'
      : "Could not parse JSON from the response";
    // A reasoning/"thinking" model spends its max_tokens budget on invisible
    // reasoning before writing any visible output -- some models can't turn
    // this off at all (see llm_client.py's OpenRouterProvider), so a heavy
    // reasoning spend is the more useful explanation for a cutoff than "not
    // enough tokens for the number of boxes."
    const reasoningShare =
      result.reasoning_tokens != null && result.completion_tokens
        ? result.reasoning_tokens / result.completion_tokens
        : 0;
    const reasoningNote =
      result.truncated && reasoningShare > 0.5
        ? ` This model spent ${result.reasoning_tokens} of its ${result.completion_tokens} completion tokens on internal reasoning before writing any output — raising max_tokens further (or switching to a non-reasoning model) is likely to help more than usual here.`
        : "";
    // hasCrops is included here (not just hasObjects) because an AI-crop
    // result's parsed.objects is always empty by design (detections live
    // per-crop, see renderViewerBody's comment above) -- a truncated/
    // unparseable Pass 2 can still have crops worth viewing in the carousel.
    const showBody = hasObjects || hasCrops;
    return (
      <div className={showBody ? "page-result-viewer" : "result-block result-parse-error"}>
        <p className="result-warning">
          {warning}
          {hasObjects &&
            ` Showing the ${result.parsed.objects.length} detection(s) recovered before the cutoff.`}
          {reasoningNote}
        </p>
        {renderCategoryErrors()}
        {showBody ? renderViewerBody() : result.raw_response && <pre>{result.raw_response}</pre>}
      </div>
    );
  }

  return (
    <div className="page-result-viewer">
      {renderCategoryErrors()}
      {result.ai_crop_message && (
        <p className="result-info">{result.ai_crop_message}</p>
      )}
      {renderViewerBody()}
    </div>
  );
}
