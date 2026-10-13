// Reads and writes the project-level ground-truth file (`<project>-obj-location.json`):
//
//   { "project_id": "...", "objects": [
//       { "id": "cab-001", "category": "cabinet", "page": 1,
//         "bbox": { "x": 10, "y": 20, "width": 30, "height": 40 } } ] }
//
// Coordinates are PDF points with the origin at the top-left of the rendered page. Ids are
// reassigned on every save: objects are sorted by (page, y, x) and numbered per category.
import { DEFAULT_CATEGORY, categoryInfo } from "./categories.js";

export function serializeProject(projectId, boxes) {
  const objects = withIds(boxes).map(({ box, id }) => ({
    id,
    category: box.category,
    page: box.page + 1,
    bbox: { x: box.x0, y: box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0 },
  }));
  return JSON.stringify({ project_id: projectId, objects }, null, 2) + "\n";
}

// The boxes in saved order, each with the id it gets in the file.
export function withIds(boxes) {
  const sorted = [...boxes].sort((a, b) => a.page - b.page || a.y0 - b.y0 || a.x0 - b.x0);
  const counters = {};
  return sorted.map((box) => {
    const { prefix } = categoryInfo(box.category);
    counters[prefix] = (counters[prefix] ?? 0) + 1;
    return { box, id: `${prefix}-${String(counters[prefix]).padStart(3, "0")}` };
  });
}

// Returns the boxes on pages that exist in the open PDF, and how many objects were skipped
// because they reference other pages or have no usable bbox.
export function parseProject(text, pageCount) {
  const data = JSON.parse(text);
  if (!data || !Array.isArray(data.objects)) {
    throw new Error('Not a project file: expected an "objects" list.');
  }
  const boxes = [];
  let skipped = 0;
  for (const obj of data.objects) {
    const page = Number.parseInt(obj?.page ?? 1, 10) - 1;
    const bbox = obj?.bbox ?? {};
    const [x, y, w, h] = [bbox.x, bbox.y, bbox.width, bbox.height].map(Number);
    if (!(page >= 0 && page < pageCount) || ![x, y, w, h].every(Number.isFinite)) {
      skipped += 1;
      continue;
    }
    boxes.push({
      page,
      category: obj.category || DEFAULT_CATEGORY,
      x0: Math.round(x),
      y0: Math.round(y),
      x1: Math.round(x + w),
      y1: Math.round(y + h),
    });
  }
  return { projectId: typeof data.project_id === "string" ? data.project_id : null, boxes, skipped };
}

// `drawing.pdf` → `drawing`, `a.pdf.pdf` → `a`.
export function projectIdFromFilename(filename) {
  let stem = filename;
  while (/\.pdf$/i.test(stem)) stem = stem.slice(0, -4);
  return stem;
}
