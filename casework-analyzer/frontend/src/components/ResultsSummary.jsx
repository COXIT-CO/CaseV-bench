import { downloadExport } from "../api.js";

// Aggregates across EVERY page analyzed in this session (not just the
// currently focused one) -- so what's shown here always matches what the
// Download buttons produce, since the backend export endpoints
// (services/parser.py build_count_export/build_location_export) already sum
// over the whole session's results, not just one page.
function summarize(results, categories) {
  const counts = Object.fromEntries(categories.map((c) => [c, 0]));
  const objects = [];
  let pagesDone = 0;
  let pagesFailed = 0;

  for (const result of Object.values(results)) {
    if (result?.status === "done" && result.parsed) {
      pagesDone += 1;
      for (const [category, count] of Object.entries(result.parsed.counts || {})) {
        if (category in counts) counts[category] += count;
      }
      for (const obj of result.parsed.objects || []) {
        objects.push({ ...obj, page: result.page_number, index: objects.length + 1 });
      }
    } else if (result?.status === "done" || result?.status === "error") {
      pagesFailed += 1;
    }
  }

  return { counts, objects, pagesDone, pagesFailed };
}

export default function ResultsSummary({ results, categories, sessionId, pages }) {
  const { counts, objects, pagesDone, pagesFailed } = summarize(results, categories);
  const anyDone = pagesDone > 0;
  // Per-page dimensions (not a single shared width/height) since each
  // object must be rescaled using ITS OWN page's size -- pages in a
  // session aren't guaranteed to share dimensions.
  const pageDimensions = Object.fromEntries(
    (pages ?? []).map((p) => [p.page_number, { width: p.width, height: p.height }])
  );

  if (pagesDone === 0 && pagesFailed === 0) return null;

  return (
    <section className="panel">
      <h2>Results</h2>
      <div className="summary-counts">
        {categories.map((category) => (
          <div className="summary-count" key={category}>
            <span className="summary-count-value">{counts[category]}</span>
            <span className="summary-count-label">{category.replace(/_/g, " ")}</span>
          </div>
        ))}
      </div>
      <p className="summary-meta">
        {pagesDone} page{pagesDone === 1 ? "" : "s"} parsed successfully
        {pagesFailed > 0
          ? `, ${pagesFailed} page${pagesFailed === 1 ? "" : "s"} failed or could not be parsed.`
          : "."}
      </p>
      <div className="controls-row summary-downloads">
        <button
          type="button"
          disabled={!anyDone}
          onClick={() => downloadExport(sessionId, "count", "obj-count.json")}
        >
          Download obj-count.json
        </button>
        <button
          type="button"
          disabled={!anyDone}
          onClick={() =>
            downloadExport(sessionId, "locations", "obj-location.json")
          }
        >
          Download obj-location.json
        </button>
      </div>
      {objects.length > 0 && (
        <details className="summary-details">
          <summary>
            {objects.length} detected objects across {pagesDone} page
            {pagesDone === 1 ? "" : "s"} (locations)
          </summary>
          <table className="summary-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Label</th>
                <th>Confidence</th>
                <th>Page</th>
                <th>BBox (x, y, w, h) — pixels</th>
              </tr>
            </thead>
            <tbody>
              {objects.map((obj) => {
                // x_min/y_min/x_max/y_max sit directly on obj (no nested box
                // object), normalized 0-1000 relative to that object's OWN
                // page's width/height (see prompts/detector_system.txt) --
                // rescale using that page's own dimensions, the same way
                // BBoxCanvas.jsx and parser.build_location_export do.
                const { x_min, y_min, x_max, y_max } = obj;
                const dims = pageDimensions[obj.page];
                const hasDims = Boolean(dims?.width && dims?.height);
                const px = hasDims ? (x_min / 1000) * dims.width : null;
                const py = hasDims ? (y_min / 1000) * dims.height : null;
                const pw = hasDims ? ((x_max - x_min) / 1000) * dims.width : null;
                const ph = hasDims ? ((y_max - y_min) / 1000) * dims.height : null;
                return (
                  <tr key={`${obj.page}-${obj.index}`}>
                    <td>{obj.index}</td>
                    <td>{obj.label}</td>
                    <td>{obj.confidence != null ? obj.confidence.toFixed(2) : "—"}</td>
                    <td>{obj.page}</td>
                    <td>
                      {hasDims
                        ? `${px.toFixed(1)}, ${py.toFixed(1)}, ${pw.toFixed(1)}, ${ph.toFixed(1)}`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
