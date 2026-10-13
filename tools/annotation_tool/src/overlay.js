// The SVG layer over the page: draws the boxes and handles every pointer gesture on the stage —
// drawing, selecting, moving and resizing boxes, panning, wheel zoom and the measuring line.
//
// The SVG's viewBox is the page in PDF points, so boxes are drawn in the same units they are
// stored in; only stroke widths and handle sizes are converted from screen pixels.
import { categoryInfo } from "./categories.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const POINTS_PER_INCH = 72;
const DRAG_THRESHOLD_PX = 3;
const MIN_BOX_SIDE_PX = 4; // smaller drags are treated as clicks, not boxes
const HANDLE_PX = 9;
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

export class Overlay {
  constructor({ stage, pageEl, svg, viewer, store, getTool, isSpaceHeld, onCursor, onMeasure }) {
    Object.assign(this, { stage, pageEl, svg, viewer, store, getTool, isSpaceHeld, onCursor, onMeasure });
    this.gesture = null;
    this.draft = null; // box being drawn, PDF points
    this.measure = null; // { x0, y0, x1, y1 } in PDF points, kept until the next one or Esc
    this.boxesHidden = false;
    this.hoverUid = null;

    stage.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    stage.addEventListener("pointermove", (e) => this.onPointerMove(e));
    stage.addEventListener("pointerup", (e) => this.onPointerUp(e));
    stage.addEventListener("pointercancel", () => this.cancelGesture());
    stage.addEventListener("pointerleave", () => this.onCursor(null));
    stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    stage.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  get page() {
    return this.viewer.pageIndex;
  }

  // ---- rendering -------------------------------------------------------------------------

  render() {
    const { width, height, zoom } = this.viewer;
    const svg = this.svg;
    svg.setAttribute("viewBox", `0 0 ${width || 1} ${height || 1}`);
    svg.classList.toggle("boxes-hidden", this.boxesHidden);
    svg.replaceChildren();
    if (!this.viewer.loaded) return;

    // Biggest first, so smaller boxes nested inside (cabinets in an elevation) stay clickable.
    const boxes = this.store
      .boxesOnPage(this.page)
      .sort((a, b) => (b.x1 - b.x0) * (b.y1 - b.y0) - (a.x1 - a.x0) * (a.y1 - a.y0));
    const selected = this.store.selected?.page === this.page ? this.store.selected : null;

    const layer = el("g", { class: "boxes" });
    for (const box of boxes) {
      const isSelected = box === selected;
      const g = el("g", {
        class: `box${isSelected ? " selected" : ""}`,
        "data-uid": box.uid,
        style: `--c: ${categoryInfo(box.category).color}`,
      });
      const r = rectAttrs(box);
      g.append(el("rect", { class: "box-fill", ...r }));
      g.append(el("rect", { class: "box-line", ...r }));
      // Only the selected box takes pointer events, and only on a band along its outline, so a
      // drag that starts inside any box still draws (cabinets inside an elevation, …).
      if (isSelected) g.append(el("rect", { class: "box-edge", ...r }));
      if (box.uid === this.hoverUid) g.classList.add("hover");
      layer.append(g);
    }
    if (selected) layer.append(this.selectionDecor(selected, zoom));
    svg.append(layer);

    if (this.draft) {
      svg.append(
        el("rect", {
          class: "draft",
          style: `--c: ${categoryInfo(this.store.activeCategory).color}`,
          ...rectAttrs(normalized(this.draft)),
        }),
      );
    }
    if (this.measure) svg.append(this.measureDecor(this.measure, zoom));
  }

  selectionDecor(box, zoom) {
    const g = el("g", { class: "selection", style: `--c: ${categoryInfo(box.category).color}` });
    const s = HANDLE_PX / zoom;
    const mx = (box.x0 + box.x1) / 2;
    const my = (box.y0 + box.y1) / 2;
    const points = {
      nw: [box.x0, box.y0], n: [mx, box.y0], ne: [box.x1, box.y0], e: [box.x1, my],
      se: [box.x1, box.y1], s: [mx, box.y1], sw: [box.x0, box.y1], w: [box.x0, my],
    };
    const label = el("text", { class: "box-label", x: box.x0, y: box.y0 - 5 / zoom, "font-size": 11 / zoom });
    label.textContent = `${box.category} · ${box.x1 - box.x0}×${box.y1 - box.y0}`;
    g.append(label);
    for (const h of HANDLES) {
      const [x, y] = points[h];
      g.append(el("rect", { class: `handle h-${h}`, "data-handle": h, x: x - s / 2, y: y - s / 2, width: s, height: s }));
    }
    return g;
  }

  measureDecor(m, zoom) {
    const g = el("g", { class: "measure" });
    g.append(el("line", { x1: m.x0, y1: m.y0, x2: m.x1, y2: m.y1 }));
    for (const [x, y] of [[m.x0, m.y0], [m.x1, m.y1]]) g.append(el("circle", { cx: x, cy: y, r: 3 / zoom }));
    const text = el("text", {
      x: (m.x0 + m.x1) / 2,
      y: (m.y0 + m.y1) / 2 - 8 / zoom,
      "font-size": 14 / zoom,
      "stroke-width": 4 / zoom,
    });
    text.textContent = formatInches(measureLength(m));
    g.append(text);
    return g;
  }

  // ---- gestures --------------------------------------------------------------------------

  onPointerDown(e) {
    if (!this.viewer.loaded || this.gesture) return;
    if (e.target.closest(".page-empty")) return;
    const p = this.viewer.toPdf(e.clientX, e.clientY);
    const tool = this.getTool();
    const base = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, start: p, moved: false };

    if (e.button === 1 || (e.button === 0 && (tool === "hand" || this.isSpaceHeld()))) {
      this.begin(e, { ...base, kind: "pan", scrollLeft: this.stage.scrollLeft, scrollTop: this.stage.scrollTop });
    } else if (e.button === 2 || (e.button === 0 && tool === "measure")) {
      if (!this.onPage(p)) return;
      this.measure = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      this.begin(e, { ...base, kind: "measure" });
      this.render();
    } else if (e.button === 0) {
      const handle = e.target.closest("[data-handle]")?.dataset.handle;
      const boxEl = e.altKey ? null : e.target.closest(".box");
      const selected = this.store.selected;
      if (handle && selected) {
        this.begin(e, { ...base, kind: "resize", handle, uid: selected.uid, orig: { ...selected } });
      } else if (boxEl) {
        const uid = Number(boxEl.dataset.uid);
        this.store.select(uid);
        const box = this.store.selected;
        this.begin(e, { ...base, kind: "move", uid, orig: { ...box } });
      } else if (this.onPage(p)) {
        this.begin(e, { ...base, kind: "draw" });
      } else {
        this.store.select(null);
      }
    }
  }

  begin(e, gesture) {
    e.preventDefault();
    this.gesture = gesture;
    this.stage.setPointerCapture(e.pointerId);
    this.stage.classList.toggle("panning", gesture.kind === "pan");
  }

  onPointerMove(e) {
    const p = this.viewer.toPdf(e.clientX, e.clientY);
    this.onCursor(this.viewer.loaded && this.onPage(p) ? p : null);
    const g = this.gesture;
    if (!g) this.setHover(this.viewer.loaded && !e.altKey ? this.smallestBoxAt(p)?.uid : null);
    if (!g || e.pointerId !== g.pointerId) return;

    if (!g.moved && Math.hypot(e.clientX - g.startX, e.clientY - g.startY) >= DRAG_THRESHOLD_PX) {
      g.moved = true;
      if (g.kind === "move" || g.kind === "resize") this.store.checkpoint();
    }
    if (!g.moved) return;

    const { width, height } = this.viewer;
    switch (g.kind) {
      case "pan":
        this.stage.scrollLeft = g.scrollLeft - (e.clientX - g.startX);
        this.stage.scrollTop = g.scrollTop - (e.clientY - g.startY);
        break;
      case "measure":
        Object.assign(this.measure, { x1: p.x, y1: p.y });
        this.onMeasure(measureLength(this.measure));
        this.render();
        break;
      case "draw":
        this.draft = { x0: g.start.x, y0: g.start.y, x1: clamp(p.x, 0, width), y1: clamp(p.y, 0, height) };
        this.render();
        break;
      case "move": {
        const { orig } = g;
        const dx = clamp(Math.round(p.x - g.start.x), -orig.x0, width - orig.x1);
        const dy = clamp(Math.round(p.y - g.start.y), -orig.y0, height - orig.y1);
        this.store.patch(g.uid, { x0: orig.x0 + dx, y0: orig.y0 + dy, x1: orig.x1 + dx, y1: orig.y1 + dy });
        break;
      }
      case "resize": {
        const next = { ...g.orig };
        const x = clamp(p.x, 0, width);
        const y = clamp(p.y, 0, height);
        if (g.handle.includes("w")) next.x0 = x;
        if (g.handle.includes("e")) next.x1 = x;
        if (g.handle.includes("n")) next.y0 = y;
        if (g.handle.includes("s")) next.y1 = y;
        this.store.patch(g.uid, next);
        break;
      }
    }
  }

  onPointerUp(e) {
    const g = this.gesture;
    if (!g || e.pointerId !== g.pointerId) return;
    this.gesture = null;
    this.stage.classList.remove("panning");

    if (g.kind === "draw") {
      const draft = this.draft && normalized(this.draft);
      this.draft = null;
      const zoom = this.viewer.zoom;
      if (draft && (draft.x1 - draft.x0) * zoom >= MIN_BOX_SIDE_PX && (draft.y1 - draft.y0) * zoom >= MIN_BOX_SIDE_PX) {
        this.store.add({ ...draft, page: this.page, category: this.store.activeCategory });
      } else {
        // A click rather than a drag: select the smallest box under the cursor, if any.
        this.store.select(this.smallestBoxAt(g.start)?.uid ?? null);
        this.render();
      }
    } else if (g.kind === "measure" && !g.moved) {
      this.clearMeasure();
    }
  }

  cancelGesture() {
    const g = this.gesture;
    if (!g) return false;
    this.gesture = null;
    this.stage.classList.remove("panning");
    if (g.kind === "draw") this.draft = null;
    if ((g.kind === "move" || g.kind === "resize") && g.moved) this.store.undo();
    if (g.kind === "measure") this.measure = null;
    this.render();
    return true;
  }

  clearMeasure() {
    if (!this.measure) return false;
    this.measure = null;
    this.onMeasure(null);
    this.render();
    return true;
  }

  onWheel(e) {
    if (!this.viewer.loaded || !(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    // Trackpad pinches arrive as small ctrl+wheel deltas, mouse wheels as large ones.
    const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    this.viewer.setZoom(this.viewer.zoom * Math.exp(-delta * 0.0025), e.clientX, e.clientY);
  }

  setHover(uid) {
    uid ??= null;
    if (uid === this.hoverUid) return;
    this.svg.querySelector(".box.hover")?.classList.remove("hover");
    this.hoverUid = uid;
    if (uid !== null) this.svg.querySelector(`.box[data-uid="${uid}"]`)?.classList.add("hover");
  }

  smallestBoxAt({ x, y }) {
    let best = null;
    for (const b of this.store.boxesOnPage(this.page)) {
      if (x < b.x0 || x > b.x1 || y < b.y0 || y > b.y1) continue;
      if (!best || (b.x1 - b.x0) * (b.y1 - b.y0) < (best.x1 - best.x0) * (best.y1 - best.y0)) best = b;
    }
    return best;
  }

  onPage(p) {
    return p.x >= 0 && p.y >= 0 && p.x <= this.viewer.width && p.y <= this.viewer.height;
  }
}

export function formatInches(points) {
  return `${(points / POINTS_PER_INCH).toFixed(2)} in`;
}

function measureLength(m) {
  return Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
}

function normalized({ x0, y0, x1, y1 }) {
  return { x0: Math.min(x0, x1), y0: Math.min(y0, y1), x1: Math.max(x0, x1), y1: Math.max(y0, y1) };
}

function rectAttrs({ x0, y0, x1, y1 }) {
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

function el(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
