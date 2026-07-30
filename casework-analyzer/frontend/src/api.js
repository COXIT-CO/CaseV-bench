const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

async function handleResponse(response) {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // response wasn't JSON; fall back to statusText
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function fetchConfig() {
  const response = await fetch(`${API_BASE}/config`);
  return handleResponse(response);
}

export async function uploadPdf(file, dpi) {
  const formData = new FormData();
  formData.append("file", file);
  if (dpi) formData.append("dpi", String(dpi));
  const response = await fetch(`${API_BASE}/upload`, {
    method: "POST",
    body: formData,
  });
  return handleResponse(response);
}

export async function savePrompt(systemPrompt, userPrompt) {
  const response = await fetch(`${API_BASE}/prompt`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      system_prompt: systemPrompt,
      user_prompt: userPrompt,
    }),
  });
  return handleResponse(response);
}

export async function fetchHistory() {
  const response = await fetch(`${API_BASE}/history`);
  return handleResponse(response);
}

export async function addModel(id) {
  const response = await fetch(`${API_BASE}/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  return handleResponse(response);
}

export async function removeModel(id) {
  const response = await fetch(
    `${API_BASE}/models?model_id=${encodeURIComponent(id)}`,
    { method: "DELETE" }
  );
  return handleResponse(response);
}

export function pageImageUrl(imageUrl) {
  // imageUrl from the backend is already like "/api/sessions/<id>/pages/<id>/image"
  const base = API_BASE.replace(/\/api\/?$/, "");
  return `${base}${imageUrl}`;
}

export async function analyzePages({
  sessionId,
  pageIds,
  systemPrompt,
  userPrompt,
  model,
  temperature,
  maxTokens,
  referenceImages,
  category, // Multi-Prompting mode only -- see App.jsx's handleAnalyze
  cutting, // "Cutting" (overlapping tile detection) mode only
  gridRows,
  gridCols,
  overlapPct,
  aiCrop, // "AI-crop" (two-pass region-localization) mode only -- mutually exclusive with cutting
  cropPrompt, // AI-crop's Pass 1 prompt, editable only when aiCrop is on
}) {
  const response = await fetch(`${API_BASE}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      page_ids: pageIds,
      system_prompt: systemPrompt,
      user_prompt: userPrompt,
      model,
      temperature,
      max_tokens: maxTokens,
      reference_images: (referenceImages || []).map((img) => ({
        data: img.data,
        media_type: img.mediaType,
        name: img.name,
      })),
      ...(category ? { category } : {}),
      ...(cutting
        ? {
            cutting: true,
            grid_rows: gridRows,
            grid_cols: gridCols,
            overlap_pct: overlapPct,
          }
        : {}),
      ...(aiCrop ? { ai_crop: true, crop_prompt: cropPrompt } : {}),
    }),
  });
  return handleResponse(response);
}

// Multi-Prompting mode calls this once, before firing its parallel
// per-category analyzePages() calls, so a re-run merges cleanly instead of
// piling new detections on top of a previous run's -- see the backend's
// session_store.clear_results.
export async function resetResults(sessionId, pageIds) {
  const response = await fetch(
    `${API_BASE}/sessions/${sessionId}/reset-results`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page_ids: pageIds }),
    }
  );
  return handleResponse(response);
}

export function exportUrl(sessionId, kind) {
  // kind: "count" | "locations"
  return `${API_BASE}/sessions/${sessionId}/export/${kind}`;
}

export async function downloadExport(sessionId, kind, filename) {
  const response = await fetch(exportUrl(sessionId, kind));
  const data = await handleResponse(response);
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// Scores the session's current predictions against an uploaded ground-truth
// file -- see backend/app/services/scoring.py, wrapping
// packages/location-scorer. `groundTruth` is the parsed JSON object of a
// prj*-obj-location.json-shaped file (the same shape downloadExport's own
// "locations" export produces), read client-side via FileReader before this
// call -- no file upload/multipart here, just JSON already in memory.
export async function scoreSession(
  sessionId,
  { groundTruth, groundTruthSpace = "pdf_points", iouThreshold, includeObjects = false }
) {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/score`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ground_truth: groundTruth,
      ground_truth_space: groundTruthSpace,
      iou_threshold: iouThreshold,
      include_objects: includeObjects,
    }),
  });
  return handleResponse(response);
}
