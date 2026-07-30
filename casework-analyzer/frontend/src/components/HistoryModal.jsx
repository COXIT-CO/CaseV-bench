function formatTimestamp(isoString) {
  const date = new Date(isoString);
  return Number.isNaN(date.getTime()) ? isoString : date.toLocaleString();
}

function truncate(text, length) {
  if (!text) return "";
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

export default function HistoryModal({
  records,
  loading,
  error,
  onSelect,
  onClose,
}) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content history-modal-content"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3>Run history</h3>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>

        {loading && <p>Loading…</p>}
        {error && <p className="result-warning">{error}</p>}
        {!loading && !error && records.length === 0 && (
          <p className="history-empty">
            No runs yet — analyze a page to create your first history entry.
          </p>
        )}

        {!loading && !error && records.length > 0 && (
          <div className="history-table-wrap">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Model</th>
                  <th>DPI</th>
                  <th>Temp</th>
                  <th>Prompt preview</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr
                    key={record.id}
                    className="history-row"
                    onClick={() => onSelect(record)}
                    title="Click to load this run's settings"
                  >
                    <td>{formatTimestamp(record.timestamp)}</td>
                    <td>{record.model}</td>
                    <td>{record.dpi}</td>
                    <td>{Number(record.temperature).toFixed(2)}</td>
                    <td className="history-preview">
                      {truncate(record.user_prompt, 90)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
