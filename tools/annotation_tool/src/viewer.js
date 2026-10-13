// Shows one page of the open PDF. Pages are rendered by the local server (PyMuPDF, see
// serve.py) in two layers: a base image of the whole page, fetched once per page, and — when
// zoomed in past its resolution — a sharp detail image of just the visible area, refetched
// after scrolling or zooming settles.
//
// Sizes and coordinates are PDF points with the page rotation applied and the origin at the
// top-left: the space boxes are stored in, and the one the benchmark runner renders in.
export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 8;

const BASE_LONG_SIDE_PX = 2400;
const DETAIL_MARGIN = 0.25; // fraction of the viewport fetched beyond each edge
const DETAIL_MAX_PIXELS = 30_000_000;
const DETAIL_DELAY_MS = 150;

export class Viewer {
  constructor({ stage, pageEl, baseCanvas, detailCanvas, onZoom, onBusy }) {
    Object.assign(this, { stage, pageEl, baseCanvas, detailCanvas, onZoom, onBusy });
    this.file = null;
    this.docId = null;
    this.pages = [];
    this.pageIndex = 0;
    this.width = 0;
    this.height = 0;
    this.zoom = 1;
    this.baseScale = 0;
    this.baseSeq = 0;
    this.detail = null; // { rect, zoom } of what the detail canvas currently shows
    this.detailAbort = null;
    this.detailTimer = 0;
    stage.addEventListener("scroll", () => this.scheduleDetail(), { passive: true });
    window.addEventListener("resize", () => this.scheduleDetail());
  }

  get loaded() {
    return this.docId !== null;
  }

  get pageCount() {
    return this.pages.length;
  }

  async open(file) {
    const { id, pages } = await this.upload(file);
    if (!pages.length) throw new Error("the PDF has no pages");
    Object.assign(this, { file, docId: id, pages });
    await this.showPage(0, { fit: true });
  }

  async upload(file) {
    let res;
    try {
      res = await fetch("api/documents", { method: "POST", body: file });
    } catch {
      throw new Error("the annotation server is not running (start it with `make start`)");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error ?? `server error ${res.status}`);
    return body;
  }

  async showPage(index, { fit = false } = {}) {
    if (!this.loaded || index < 0 || index >= this.pageCount) return false;
    this.pageIndex = index;
    ({ width: this.width, height: this.height } = this.pages[index]);
    if (fit) this.zoom = this.fitZoom("width");
    this.clearDetail();
    this.applySize();
    this.stage.scrollTo(0, 0);
    await this.renderBase();
    this.scheduleDetail();
    return true;
  }

  fitZoom(mode) {
    const pad = 32;
    const byWidth = (this.stage.clientWidth - pad) / this.width;
    const byHeight = (this.stage.clientHeight - pad) / this.height;
    return clamp(mode === "page" ? Math.min(byWidth, byHeight) : byWidth, MIN_ZOOM, MAX_ZOOM);
  }

  // Zooms keeping the PDF point under (clientX, clientY) fixed; defaults to the stage centre.
  setZoom(zoom, clientX, clientY) {
    if (!this.loaded) return;
    zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(zoom - this.zoom) < 1e-4) return;
    const stageRect = this.stage.getBoundingClientRect();
    clientX ??= stageRect.left + stageRect.width / 2;
    clientY ??= stageRect.top + stageRect.height / 2;
    const anchor = this.toPdf(clientX, clientY);
    this.zoom = zoom;
    this.applySize();
    const pageRect = this.pageEl.getBoundingClientRect();
    this.stage.scrollLeft += pageRect.left + anchor.x * zoom - clientX;
    this.stage.scrollTop += pageRect.top + anchor.y * zoom - clientY;
    this.scheduleDetail();
  }

  applySize() {
    this.pageEl.style.width = `${this.width * this.zoom}px`;
    this.pageEl.style.height = `${this.height * this.zoom}px`;
    if (this.detail) this.positionDetail();
    this.onZoom?.(this.zoom);
  }

  async renderBase() {
    const seq = ++this.baseSeq;
    const index = this.pageIndex;
    this.baseScale = Math.min(BASE_LONG_SIDE_PX / Math.max(this.width, this.height), 4);
    this.onBusy?.(true);
    try {
      const image = await this.fetchPage(index, this.baseScale);
      if (seq !== this.baseSeq) return;
      drawInto(this.baseCanvas, image);
    } finally {
      if (seq === this.baseSeq) this.onBusy?.(false);
    }
  }

  scheduleDetail() {
    if (!this.loaded) return;
    clearTimeout(this.detailTimer);
    this.detailTimer = setTimeout(() => this.renderDetail().catch(() => {}), DETAIL_DELAY_MS);
  }

  async renderDetail() {
    let scale = this.zoom * (window.devicePixelRatio || 1);
    if (scale <= this.baseScale * 1.05) {
      this.clearDetail();
      return;
    }
    const visible = this.visibleRect();
    if (!visible) return;
    const d = this.detail;
    if (d && d.zoom === this.zoom && contains(d.rect, visible)) return;

    const mx = (visible.x1 - visible.x0) * DETAIL_MARGIN;
    const my = (visible.y1 - visible.y0) * DETAIL_MARGIN;
    const rect = {
      x0: Math.max(0, visible.x0 - mx),
      y0: Math.max(0, visible.y0 - my),
      x1: Math.min(this.width, visible.x1 + mx),
      y1: Math.min(this.height, visible.y1 + my),
    };
    const pixels = (rect.x1 - rect.x0) * (rect.y1 - rect.y0) * scale * scale;
    if (pixels > DETAIL_MAX_PIXELS) scale *= Math.sqrt(DETAIL_MAX_PIXELS / pixels);

    this.detailAbort?.abort();
    const abort = new AbortController();
    this.detailAbort = abort;
    const { pageIndex, zoom } = this;
    let image;
    try {
      image = await this.fetchPage(pageIndex, scale, rect, abort.signal);
    } catch (err) {
      if (err.name === "AbortError") return;
      throw err;
    }
    if (abort.signal.aborted || pageIndex !== this.pageIndex) return;
    drawInto(this.detailCanvas, image);
    this.detail = { rect, zoom };
    this.positionDetail();
    this.detailCanvas.hidden = false;
  }

  positionDetail() {
    const { rect } = this.detail;
    const s = this.detailCanvas.style;
    s.left = `${rect.x0 * this.zoom}px`;
    s.top = `${rect.y0 * this.zoom}px`;
    s.width = `${(rect.x1 - rect.x0) * this.zoom}px`;
    s.height = `${(rect.y1 - rect.y0) * this.zoom}px`;
  }

  clearDetail() {
    this.detailAbort?.abort();
    this.detail = null;
    this.detailCanvas.hidden = true;
  }

  // The part of the page inside the stage's viewport, in PDF points.
  visibleRect() {
    const page = this.pageEl.getBoundingClientRect();
    const stage = this.stage.getBoundingClientRect();
    const x0 = (Math.max(page.left, stage.left) - page.left) / this.zoom;
    const y0 = (Math.max(page.top, stage.top) - page.top) / this.zoom;
    const x1 = (Math.min(page.right, stage.right) - page.left) / this.zoom;
    const y1 = (Math.min(page.bottom, stage.bottom) - page.top) / this.zoom;
    return x1 > x0 && y1 > y0 ? { x0, y0, x1, y1 } : null;
  }

  async exportPdf(payload) {
    const send = () =>
      fetch(`api/documents/${this.docId}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    let res = await send();
    if (res.status === 404 && this.file) {
      await this.upload(this.file);
      res = await send();
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error ?? `server error ${res.status}`);
    }
    return res.blob();
  }

  async fetchPage(index, scale, clip = null, signal = undefined) {
    let url = `api/documents/${this.docId}/pages/${index}.png?scale=${scale.toFixed(4)}`;
    if (clip) url += `&clip=${[clip.x0, clip.y0, clip.x1, clip.y1].map((v) => v.toFixed(2)).join(",")}`;
    let res = await fetch(url, { signal });
    if (res.status === 404 && this.file) {
      // The server forgot the document (it was restarted): upload it again and retry once.
      await this.upload(this.file);
      res = await fetch(url, { signal });
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error ?? `server error ${res.status}`);
    }
    return createImageBitmap(await res.blob());
  }

  // Client (screen) coordinates → PDF points on the current page.
  toPdf(clientX, clientY) {
    const rect = this.pageEl.getBoundingClientRect();
    return { x: (clientX - rect.left) / this.zoom, y: (clientY - rect.top) / this.zoom };
  }

  // Scrolls so the given PDF-space rectangle is centred in the stage.
  centerOn({ x0, y0, x1, y1 }) {
    const pageRect = this.pageEl.getBoundingClientRect();
    const stageRect = this.stage.getBoundingClientRect();
    this.stage.scrollBy({
      left: pageRect.left + ((x0 + x1) / 2) * this.zoom - (stageRect.left + stageRect.width / 2),
      top: pageRect.top + ((y0 + y1) / 2) * this.zoom - (stageRect.top + stageRect.height / 2),
      behavior: "smooth",
    });
  }
}

function drawInto(canvas, image) {
  canvas.width = image.width;
  canvas.height = image.height;
  canvas.getContext("2d").drawImage(image, 0, 0);
  image.close();
}

function contains(outer, inner) {
  return outer.x0 <= inner.x0 && outer.y0 <= inner.y0 && outer.x1 >= inner.x1 && outer.y1 >= inner.y1;
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
