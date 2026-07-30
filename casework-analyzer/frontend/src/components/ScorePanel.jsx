import { useState } from "react";
import { scoreSession } from "../api.js";

// Scores every page analyzed in this session (not just the focused one --
// unlike ResultsSummary, which is deliberately scoped to one page) against
// an uploaded ground-truth file, via location_scorer (packages/location-scorer)
// on the backend (services/scoring.py). The ground-truth file is read
// entirely client-side (FileReader -> JSON.parse) and sent as plain JSON in
// the request body -- no multipart upload, since it's small and this app
// already exports files in exactly this shape (GET .../export/locations),
// so a downloaded export can be fed straight back in to sanity-check a run.
export default function ScorePanel({ sessionId, hasAnyResult }) {
  const [groundTruth, setGroundTruth] = useState(null);
  const [groundTruthName, setGroundTruthName] = useState("");
  const [groundTruthSpace, setGroundTruthSpace] = useState("pdf_points");
  const [iouThreshold, setIouThreshold] = useState(0.5);
  const [scoring, setScoring] = useState(false);
  const [scoreError, setScoreError] = useState(null);
  const [result, setResult] = useState(null);

  const handleFile = async (file) => {
    setScoreError(null);
    setResult(null);
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      setGroundTruth(parsed);
      setGroundTruthName(file.name);
    } catch (err) {
      setGroundTruth(null);
      setGroundTruthName("");
      setScoreError(`Could not read "${file.name}" as JSON: ${err.message}`);
    }
  };

  const handleScore = async () => {
    setScoring(true);
    setScoreError(null);
    try {
      const scored = await scoreSession(sessionId, {
        groundTruth,
        groundTruthSpace,
        iouThreshold: Number(iouThreshold),
      });
      setResult(scored);
    } catch (err) {
      setScoreError(err.message);
      setResult(null);
    } finally {
      setScoring(false);
    }
  };

  const canScore = hasAnyResult && groundTruth != null && !scoring;

  return (
    <section className="panel">
      <h2>Score against ground truth</h2>
      <p className="summary-meta">
        Choose the coordinate space your ground-truth file is in below. A
        benchmark project's own prj*-obj-location.json uses PDF point space
        (72 DPI-equivalent) -- the backend just scales it up to this
        session's render DPI before scoring. One of this app's own exports
        (GET .../export/locations) is already in this session's render
        pixel space, so pick "Render pixels" for those instead -- picking
        the wrong one silently over- or under-scales every box.
      </p>
      <div className="controls-row">
        <label className="control">
          Ground truth JSON
          <input
            type="file"
            accept="application/json,.json"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) handleFile(file);
              event.target.value = "";
            }}
          />
        </label>
        <label className="control">
          Ground truth space
          <select
            value={groundTruthSpace}
            onChange={(event) => setGroundTruthSpace(event.target.value)}
          >
            <option value="pdf_points">Benchmark (raw PDF points)</option>
            <option value="render_pixels">This app's export (render pixels)</option>
          </select>
        </label>
        <label className="control">
          IoU threshold
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={iouThreshold}
            onChange={(event) => setIouThreshold(event.target.value)}
          />
        </label>
        <button type="button" disabled={!canScore} onClick={handleScore}>
          {scoring ? "Scoring…" : "Score"}
        </button>
      </div>

      {!hasAnyResult && (
        <p className="summary-meta">Analyze at least one page first.</p>
      )}
      {groundTruthName && !scoreError && (
        <p className="summary-meta">
          Loaded {groundTruthName} ({groundTruth?.objects?.length ?? 0} ground-truth
          objects).
        </p>
      )}
      {scoreError && <p className="result-warning">{scoreError}</p>}

      {result && (
        <div className="score-result">
          <p className="summary-meta">
            IoU threshold {result.iou_threshold} — TP {result.counts.tp}, FP{" "}
            {result.counts.fp}, FN {result.counts.fn}
          </p>
          <div className="summary-counts">
            <div className="summary-count">
              <span className="summary-count-value">
                {result.metrics.precision.toFixed(3)}
              </span>
              <span className="summary-count-label">precision</span>
            </div>
            <div className="summary-count">
              <span className="summary-count-value">
                {result.metrics.recall.toFixed(3)}
              </span>
              <span className="summary-count-label">recall</span>
            </div>
            <div className="summary-count">
              <span className="summary-count-value">
                {result.metrics.f1.toFixed(3)}
              </span>
              <span className="summary-count-label">f1</span>
            </div>
          </div>
          {Object.keys(result.per_type).length > 0 && (
            <table className="summary-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>TP</th>
                  <th>FP</th>
                  <th>FN</th>
                  <th>Precision</th>
                  <th>Recall</th>
                  <th>F1</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.per_type).map(([type, entry]) => (
                  <tr key={type}>
                    <td>{type}</td>
                    <td>{entry.counts.tp}</td>
                    <td>{entry.counts.fp}</td>
                    <td>{entry.counts.fn}</td>
                    <td>{entry.metrics.precision.toFixed(3)}</td>
                    <td>{entry.metrics.recall.toFixed(3)}</td>
                    <td>{entry.metrics.f1.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
