import { useEffect, useRef, useState } from "react";
import { CATEGORY_COLORS, colorFor } from "./BBoxCanvas.jsx";

// Draws one crop (its own image + its own detections) onto a given canvas --
// shared by the visible carousel canvas and the offscreen canvases used by
// "Download all crops", so the two draw paths can't drift apart.
async function renderCropToCanvas(crop, canvas) {
  const img = new Image();
  img.src = `data:${crop.media_type};base64,${crop.image_base64}`;
  await img.decode();

  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

  const lineWidth = Math.max(2, canvas.width / 400);
  const fontSize = Math.max(12, canvas.width / 100);
  ctx.lineWidth = lineWidth;
  ctx.font = `${fontSize}px sans-serif`;
  ctx.textBaseline = "top";

  const labelHeight = fontSize + 4;
  let lastLabelRight = -Infinity;
  let lastLabelY = -Infinity;

  for (const obj of crop.detections || []) {
    const { x_min, y_min, x_max, y_max } = obj;
    const px = (x_min / 1000) * canvas.width;
    const py = (y_min / 1000) * canvas.height;
    const pw = ((x_max - x_min) / 1000) * canvas.width;
    const ph = ((y_max - y_min) / 1000) * canvas.height;
    const color = colorFor(obj.label);

    ctx.strokeStyle = color;
    ctx.strokeRect(px, py, pw, ph);

    const label =
      obj.confidence != null ? `${obj.label} ${obj.confidence.toFixed(2)}` : obj.label;
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
}

function downloadCanvasAsPng(canvas, filename) {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => {
      if (blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      }
      resolve();
    }, "image/png");
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// AI-crop mode's per-crop detection view (services/ai_crop.py's `crops`
// array on PageResult). Unlike BBoxCanvas.jsx, there's no full-page remap
// anymore -- each crop's `detections` are already normalized 0-1000
// relative to THAT crop's own image, so this draws directly on it with the
// same rescale math, just without BBoxCanvas's fetch/blob step (the image
// bytes are already in memory as base64, not fetched from a page URL).
export default function CropsCarousel({ crops, downloadName }) {
  const [index, setIndex] = useState(0);
  const canvasRef = useRef(null);
  const [error, setError] = useState(null);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadAllProgress, setDownloadAllProgress] = useState(0);

  const crop = crops[index];

  useEffect(() => {
    let cancelled = false;
    setError(null);

    async function draw() {
      try {
        const canvas = canvasRef.current;
        if (!canvas) return;
        await renderCropToCanvas(crop, canvas);
        if (cancelled) return;
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    draw();
    return () => {
      cancelled = true;
    };
  }, [crop]);

  const filenameFor = (c) => `${downloadName || "page"}-cell-${c.cell_id}-annotated.png`;

  // Downloads exactly what's currently drawn on the canvas (the crop's own
  // image + its own detections), same as BBoxCanvas.jsx's PNG download --
  // one file per crop, since each crop is its own image, not a page.
  const handleDownload = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    downloadCanvasAsPng(canvas, filenameFor(crop));
  };

  // Every crop, not just the one on screen -- draws each onto its own
  // offscreen canvas (the visible canvas only ever holds the focused crop)
  // and triggers one download per crop, with a short pause between each
  // since browsers can silently drop a burst of programmatic downloads
  // fired back-to-back with no gap.
  const handleDownloadAll = async () => {
    setDownloadingAll(true);
    setError(null);
    try {
      for (let i = 0; i < crops.length; i++) {
        setDownloadAllProgress(i + 1);
        const c = crops[i];
        const offscreen = document.createElement("canvas");
        await renderCropToCanvas(c, offscreen);
        await downloadCanvasAsPng(offscreen, filenameFor(c));
        if (i < crops.length - 1) await sleep(300);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloadingAll(false);
      setDownloadAllProgress(0);
    }
  };

  return (
    <div className="crops-carousel">
      <div className="crops-carousel-header">
        <div className="crops-carousel-caption">
          <strong>
            Cell {crop.cell_id}: {crop.tag_text}
          </strong>
          {!crop.verified && (
            <span className="crops-carousel-unverified-badge">
              ⚠ unverified — spot-check this one
            </span>
          )}
        </div>
        <div className="crops-carousel-nav">
          <button
            type="button"
            onClick={() => setIndex((i) => i - 1)}
            disabled={index <= 0}
          >
            &larr; Prev
          </button>
          <span className="crops-carousel-counter">
            {index + 1} / {crops.length}
          </span>
          <button
            type="button"
            onClick={() => setIndex((i) => i + 1)}
            disabled={index >= crops.length - 1}
          >
            Next &rarr;
          </button>
        </div>
      </div>

      {error && <p className="result-warning">{error}</p>}
      <canvas
        ref={canvasRef}
        className={`bbox-canvas ${!crop.verified ? "crops-carousel-canvas-unverified" : ""}`}
      />

      <div className="bbox-canvas-footer">
        <div className="bbox-legend">
          {Object.entries(CATEGORY_COLORS).map(([category, color]) => (
            <span className="bbox-legend-item" key={category}>
              <span className="bbox-legend-swatch" style={{ background: color }} />
              {category.replace(/_/g, " ")}
            </span>
          ))}
        </div>
        <div className="crops-carousel-downloads">
          <button type="button" onClick={handleDownload} disabled={downloadingAll}>
            Download annotated crop (PNG)
          </button>
          <button type="button" onClick={handleDownloadAll} disabled={downloadingAll}>
            {downloadingAll
              ? `Downloading ${downloadAllProgress}/${crops.length}…`
              : `Download all crops (${crops.length} PNGs)`}
          </button>
        </div>
      </div>
    </div>
  );
}
