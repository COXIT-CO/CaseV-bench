import { useEffect, useRef, useState } from "react";
import { pageImageUrl } from "../api.js";

// Keyed on the singular `label` values the model is prompted to return
// (see prompts/detector_system.txt), not the plural category names from config.
// Exported so CropsCarousel.jsx (AI-crop mode's per-crop detection view)
// draws boxes with the exact same category->color mapping instead of
// duplicating it.
export const CATEGORY_COLORS = {
  cabinet: "#e53935",
  countertop: "#1e88e5",
  elevation: "#8e24aa",
  elevation_callout: "#43a047",
};
const DEFAULT_COLOR = "#f9a825";

export function colorFor(category) {
  return CATEGORY_COLORS[category] || DEFAULT_COLOR;
}

export default function BBoxCanvas({ page, objects, downloadName }) {
  const canvasRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    setError(null);

    async function draw() {
      try {
        // Fetch + blob + object URL (rather than setting img.src directly to
        // the cross-origin API URL) guarantees a non-tainted canvas, so
        // toBlob() for the PNG download always works regardless of CORS
        // header quirks on the image response.
        const response = await fetch(pageImageUrl(page.image_url));
        if (!response.ok) throw new Error("Failed to load page image");
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);

        const img = new Image();
        img.src = objectUrl;
        await img.decode();
        if (cancelled) return;

        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

        const lineWidth = Math.max(2, canvas.width / 400);
        const fontSize = Math.max(12, canvas.width / 100);
        ctx.lineWidth = lineWidth;
        ctx.font = `${fontSize}px sans-serif`;
        ctx.textBaseline = "top";

        // Runs of small adjacent objects (e.g. a row of narrow cabinets)
        // produce labels wider than their own box, which would otherwise
        // overlap into an unreadable smear. Skip a label when it would
        // collide with the previously drawn one at roughly the same height;
        // the box outline itself is still always drawn.
        const labelHeight = fontSize + 4;
        let lastLabelRight = -Infinity;
        let lastLabelY = -Infinity;

        for (const obj of objects || []) {
          // x_min/y_min/x_max/y_max sit directly on obj (no nested box
          // object), normalized 0-1000 relative to the image's own
          // width/height (see prompts/detector_system.txt) -- NOT 0-1, NOT
          // raw pixels. Canvas pixel size matches the source image 1:1, so
          // canvas.width/height are exactly the W/H to rescale into.
          const { x_min, y_min, x_max, y_max } = obj;
          const px = (x_min / 1000) * canvas.width;
          const py = (y_min / 1000) * canvas.height;
          const pw = ((x_max - x_min) / 1000) * canvas.width;
          const ph = ((y_max - y_min) / 1000) * canvas.height;
          const color = colorFor(obj.label);

          ctx.strokeStyle = color;
          ctx.strokeRect(px, py, pw, ph);

          const label =
            obj.confidence != null
              ? `${obj.label} ${obj.confidence.toFixed(2)}`
              : obj.label;
          const labelWidth = ctx.measureText(label).width + 8;
          const labelY = Math.max(0, py - labelHeight);
          const overlapsPrevious =
            px < lastLabelRight && Math.abs(labelY - lastLabelY) < labelHeight;

          if (!overlapsPrevious) {
            ctx.fillStyle = color;
            ctx.fillRect(px, labelY, labelWidth, labelHeight);
            ctx.fillStyle = "#ffffff";
            ctx.fillText(label, px + 4, labelY + 2);
            lastLabelRight = px + labelWidth;
            lastLabelY = labelY;
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    draw();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [page.image_url, objects]);

  const handleDownload = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${downloadName || "page"}-annotated.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }, "image/png");
  };

  return (
    <div className="bbox-canvas-wrap">
      {error && <p className="result-warning">{error}</p>}
      <canvas ref={canvasRef} className="bbox-canvas" />
      <div className="bbox-canvas-footer">
        <div className="bbox-legend">
          {Object.entries(CATEGORY_COLORS).map(([category, color]) => (
            <span className="bbox-legend-item" key={category}>
              <span
                className="bbox-legend-swatch"
                style={{ background: color }}
              />
              {category.replace(/_/g, " ")}
            </span>
          ))}
        </div>
        <button type="button" onClick={handleDownload}>
          Download annotated image (PNG)
        </button>
      </div>
    </div>
  );
}
