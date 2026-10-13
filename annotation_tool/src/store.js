// The annotation state: boxes, selection and undo history. Every change notifies subscribers,
// which re-render whatever depends on it. Boxes live in PDF points with integer coordinates.
import { DEFAULT_CATEGORY } from "./categories.js";

const HISTORY_LIMIT = 200;

let nextUid = 1;

export class Store {
  constructor() {
    this.boxes = []; // { uid, page (0-based), category, x0, y0, x1, y1 }
    this.selectedUid = null;
    this.activeCategory = DEFAULT_CATEGORY;
    this.dirty = false;
    this.undoStack = [];
    this.redoStack = [];
    this.listeners = new Set();
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  emit(change) {
    for (const fn of this.listeners) fn(change);
  }

  get selected() {
    return this.boxes.find((b) => b.uid === this.selectedUid) ?? null;
  }

  boxesOnPage(page) {
    return this.boxes.filter((b) => b.page === page);
  }

  // Replaces everything, e.g. after opening a PDF or importing a file. Not undoable.
  reset(boxes, { dirty = false } = {}) {
    this.boxes = boxes.map((b) => ({ ...normalize(b), uid: nextUid++ }));
    this.selectedUid = null;
    this.undoStack = [];
    this.redoStack = [];
    this.dirty = dirty;
    this.emit({ type: "reset" });
  }

  // Call once before a group of mutations that should undo as a single step (e.g. a drag).
  checkpoint() {
    this.undoStack.push(this.snapshot());
    if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift();
    this.redoStack = [];
  }

  add(box) {
    this.checkpoint();
    const created = { ...normalize(box), uid: nextUid++ };
    this.boxes.push(created);
    this.selectedUid = created.uid;
    this.changed();
    return created;
  }

  // Updates without a checkpoint, so live edits (dragging, typing) can be grouped.
  patch(uid, fields) {
    const box = this.boxes.find((b) => b.uid === uid);
    if (!box) return;
    Object.assign(box, normalize({ ...box, ...fields }));
    this.changed();
  }

  update(uid, fields) {
    this.checkpoint();
    this.patch(uid, fields);
  }

  remove(uid) {
    if (!this.boxes.some((b) => b.uid === uid)) return;
    this.checkpoint();
    this.boxes = this.boxes.filter((b) => b.uid !== uid);
    if (this.selectedUid === uid) this.selectedUid = null;
    this.changed();
  }

  select(uid) {
    if (this.selectedUid === uid) return;
    this.selectedUid = uid;
    this.emit({ type: "select" });
  }

  setActiveCategory(name) {
    this.activeCategory = name;
    this.emit({ type: "category" });
  }

  undo() {
    if (!this.undoStack.length) return false;
    this.redoStack.push(this.snapshot());
    this.restore(this.undoStack.pop());
    return true;
  }

  redo() {
    if (!this.redoStack.length) return false;
    this.undoStack.push(this.snapshot());
    this.restore(this.redoStack.pop());
    return true;
  }

  markSaved() {
    this.dirty = false;
    this.emit({ type: "saved" });
  }

  snapshot() {
    return { boxes: this.boxes.map((b) => ({ ...b })), selectedUid: this.selectedUid };
  }

  restore({ boxes, selectedUid }) {
    this.boxes = boxes;
    this.selectedUid = boxes.some((b) => b.uid === selectedUid) ? selectedUid : null;
    this.changed();
  }

  changed() {
    this.dirty = true;
    this.emit({ type: "boxes" });
  }
}

// Integer coordinates with x0 < x1 and y0 < y1, whichever corners were given.
function normalize(box) {
  const [x0, x1] = [Math.round(box.x0), Math.round(box.x1)].sort((a, b) => a - b);
  const [y0, y1] = [Math.round(box.y0), Math.round(box.y1)].sort((a, b) => a - b);
  return { ...box, category: box.category || DEFAULT_CATEGORY, x0, y0, x1, y1 };
}
