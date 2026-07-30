import { pageImageUrl } from "../api.js";

function StatusBadge({ status }) {
  return <span className={`status-badge status-${status}`}>{status}</span>;
}

export default function PageGrid({
  pages,
  selectedPageIds,
  onToggle,
  onSelectAll,
  onDeselectAll,
  results,
  focusedPageId,
  onFocus,
  onPreview,
}) {
  return (
    <section className="panel">
      <div className="page-grid-header">
        <h2>Pages ({pages.length})</h2>
        <div className="page-grid-actions">
          <button type="button" onClick={onSelectAll}>
            Select all
          </button>
          <button type="button" onClick={onDeselectAll}>
            Deselect all
          </button>
        </div>
      </div>
      <div className="page-grid">
        {pages.map((page) => {
          const result = results[page.page_id];
          const isFocused = page.page_id === focusedPageId;
          return (
            <div
              className={`page-card ${isFocused ? "page-card-focused" : ""}`}
              key={page.page_id}
            >
              <div className="page-card-header">
                <label>
                  <input
                    type="checkbox"
                    checked={selectedPageIds.has(page.page_id)}
                    onChange={() => onToggle(page.page_id)}
                  />
                  Page {page.page_number}
                </label>
                {result && <StatusBadge status={result.status} />}
              </div>
              <div className="page-thumb-wrap">
                <img
                  className="page-thumb"
                  src={pageImageUrl(page.image_url)}
                  alt={`Page ${page.page_number}`}
                  onClick={() => onFocus(page)}
                />
                <button
                  type="button"
                  className="page-thumb-expand"
                  title="Open full-size preview"
                  onClick={(event) => {
                    event.stopPropagation();
                    onPreview(page);
                  }}
                >
                  &#128269;
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
