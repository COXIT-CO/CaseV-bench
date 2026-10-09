// Tests for the annotation state (`src/store.js`): boxes, selection and undo history.
// Run with `make -C annotation_tool test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Store } from "../src/store.js";

const coords = ({ x0, y0, x1, y1 }) => [x0, y0, x1, y1];

test("adding a box selects it and marks the store unsaved", () => {
  const store = new Store();
  const created = store.add({ page: 0, x0: 1, y0: 2, x1: 3, y1: 4 });
  assert.equal(store.selected, created);
  assert.equal(store.dirty, true);
  assert.equal(created.category, "cabinet");
});

test("boxes get whole, ordered coordinates whichever corner they were drawn from", () => {
  const store = new Store();
  const created = store.add({ page: 0, x0: 30.6, y0: 40.2, x1: 10.4, y1: 20 });
  assert.deepEqual(coords(created), [10, 20, 31, 40]);
});

test("patch keeps coordinates whole and ordered", () => {
  const store = new Store();
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 10, y1: 10 });
  store.patch(uid, { x1: -5.7 });
  assert.deepEqual(coords(store.selected), [-6, 0, 0, 10]);
});

test("undo and redo step through changes", () => {
  const store = new Store();
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 10, y1: 10 });
  store.update(uid, { x0: 5 });
  assert.equal(store.boxes[0].x0, 5);

  assert.equal(store.undo(), true);
  assert.equal(store.boxes[0].x0, 0);
  assert.equal(store.undo(), true);
  assert.equal(store.boxes.length, 0);
  assert.equal(store.undo(), false);

  assert.equal(store.redo(), true);
  assert.equal(store.redo(), true);
  assert.equal(store.boxes[0].x0, 5);
  assert.equal(store.redo(), false);
});

test("a new change clears redo", () => {
  const store = new Store();
  store.add({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 });
  store.undo();
  store.add({ page: 0, x0: 5, y0: 5, x1: 6, y1: 6 });
  assert.equal(store.redo(), false);
});

test("patches after one checkpoint undo as a single step, like a drag", () => {
  const store = new Store();
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 10, y1: 10 });
  store.checkpoint();
  for (const x of [1, 2, 3, 4]) store.patch(uid, { x0: x });
  store.undo();
  assert.equal(store.boxes[0].x0, 0);
});

test("undo does not change the boxes it restored from", () => {
  const store = new Store();
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 10, y1: 10 });
  store.update(uid, { x0: 5 });
  store.undo();
  store.patch(store.boxes[0].uid, { x0: 7 });
  store.redo();
  assert.equal(store.boxes[0].x0, 5);
});

test("removing the selected box clears the selection, and undo brings it back", () => {
  const store = new Store();
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 });
  store.remove(uid);
  assert.equal(store.selected, null);
  assert.equal(store.boxes.length, 0);
  store.undo();
  assert.equal(store.boxes.length, 1);
});

test("removing a box that does not exist changes nothing", () => {
  const store = new Store();
  store.remove(12345);
  assert.equal(store.undo(), false);
  assert.equal(store.dirty, false);
});

test("reset replaces the boxes, clears history and is not unsaved", () => {
  const store = new Store();
  store.add({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 });
  store.reset([{ page: 1, x0: 0, y0: 0, x1: 2, y1: 2, category: "callout" }]);
  assert.equal(store.boxes.length, 1);
  assert.equal(store.boxes[0].category, "callout");
  assert.equal(store.dirty, false);
  assert.equal(store.selected, null);
  assert.equal(store.undo(), false);
});

test("reset can keep a restored draft unsaved", () => {
  const store = new Store();
  store.reset([], { dirty: true });
  assert.equal(store.dirty, true);
});

test("markSaved clears the unsaved flag", () => {
  const store = new Store();
  store.add({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 });
  store.markSaved();
  assert.equal(store.dirty, false);
});

test("boxesOnPage returns only that page", () => {
  const store = new Store();
  store.reset([
    { page: 0, x0: 0, y0: 0, x1: 1, y1: 1 },
    { page: 1, x0: 0, y0: 0, x1: 1, y1: 1 },
    { page: 1, x0: 2, y0: 2, x1: 3, y1: 3 },
  ]);
  assert.equal(store.boxesOnPage(1).length, 2);
});

test("history keeps the last 200 steps", () => {
  const store = new Store();
  for (let i = 0; i < 205; i += 1) store.add({ page: 0, x0: i, y0: 0, x1: i + 1, y1: 1 });
  let steps = 0;
  while (store.undo()) steps += 1;
  assert.equal(steps, 200);
  assert.equal(store.boxes.length, 5);
});

test("subscribers hear about changes until they unsubscribe", () => {
  const store = new Store();
  const seen = [];
  const unsubscribe = store.subscribe((change) => seen.push(change.type));
  const { uid } = store.add({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 });
  store.select(null);
  store.select(null);
  store.setActiveCategory("callout");
  unsubscribe();
  store.remove(uid);
  assert.deepEqual(seen, ["boxes", "select", "category"]);
});
