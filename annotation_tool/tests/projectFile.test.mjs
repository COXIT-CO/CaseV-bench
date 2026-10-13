// Tests for reading and writing the project file (`src/projectFile.js`): the JSON the benchmark
// reads. Run with `make -C annotation_tool test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { CATEGORIES } from "../src/categories.js";
import { parseProject, projectIdFromFilename, serializeProject, withIds } from "../src/projectFile.js";

const box = (page, x0, y0, x1, y1, category = "cabinet") => ({ page, category, x0, y0, x1, y1 });

test("serializes boxes as 1-based pages with x/y/width/height", () => {
  const data = JSON.parse(serializeProject("kitchen", [box(0, 10, 20, 40, 60)]));
  assert.deepEqual(data, {
    project_id: "kitchen",
    objects: [{ id: "cab-001", category: "cabinet", page: 1, bbox: { x: 10, y: 20, width: 30, height: 40 } }],
  });
});

test("the file ends with a newline", () => {
  assert.ok(serializeProject("p", []).endsWith("}\n"));
});

test("ids follow page, then y, then x, numbered per category", () => {
  const boxes = [
    box(1, 0, 0, 10, 10),
    box(0, 50, 10, 60, 20),
    box(0, 10, 10, 20, 20),
    box(0, 0, 5, 10, 15, "countertop"),
    box(0, 0, 50, 10, 60),
  ];
  const ids = withIds(boxes).map(({ box, id }) => [id, box.page, box.x0, box.y0]);
  assert.deepEqual(ids, [
    ["ctp-001", 0, 0, 5],
    ["cab-001", 0, 10, 10],
    ["cab-002", 0, 50, 10],
    ["cab-003", 0, 0, 50],
    ["cab-004", 1, 0, 0],
  ]);
});

test("every category has its own id prefix", () => {
  const boxes = CATEGORIES.map((c, i) => box(0, 0, i, 10, i + 10, c.name));
  assert.deepEqual(
    withIds(boxes).map(({ id }) => id),
    ["cab-001", "ctp-001", "elv-001", "cal-001", "flp-001"],
  );
});

test("unknown categories are kept and get the obj prefix", () => {
  const data = JSON.parse(serializeProject("p", [box(0, 0, 0, 1, 1, "sink")]));
  assert.equal(data.objects[0].category, "sink");
  assert.equal(data.objects[0].id, "obj-001");
});

test("withIds does not reorder the boxes it is given", () => {
  const boxes = [box(1, 0, 0, 1, 1), box(0, 0, 0, 1, 1)];
  withIds(boxes);
  assert.equal(boxes[0].page, 1);
});

test("parses a file back into 0-based boxes with corner coordinates", () => {
  const text = JSON.stringify({
    project_id: "kitchen",
    objects: [{ id: "x", category: "elevation", page: 2, bbox: { x: 10, y: 20, width: 30, height: 40 } }],
  });
  assert.deepEqual(parseProject(text, 3), {
    projectId: "kitchen",
    boxes: [box(1, 10, 20, 40, 60, "elevation")],
    skipped: 0,
  });
});

test("rounds fractional coordinates to whole points", () => {
  const text = JSON.stringify({ objects: [{ page: 1, bbox: { x: 10.4, y: 20.6, width: 5.3, height: 4.2 } }] });
  assert.deepEqual(parseProject(text, 1).boxes, [box(0, 10, 21, 16, 25)]);
});

test("missing page means page 1 and missing category means cabinet", () => {
  const text = JSON.stringify({ objects: [{ bbox: { x: 1, y: 2, width: 3, height: 4 } }] });
  assert.deepEqual(parseProject(text, 1).boxes, [box(0, 1, 2, 4, 6, "cabinet")]);
});

test("skips objects on pages the PDF does not have, or without a usable bbox", () => {
  const ok = { page: 1, bbox: { x: 0, y: 0, width: 1, height: 1 } };
  const text = JSON.stringify({
    objects: [
      ok,
      { ...ok, page: 0 },
      { ...ok, page: 3 },
      { page: 1 },
      { page: 1, bbox: { x: "a", y: 0, width: 1, height: 1 } },
      null,
    ],
  });
  const { boxes, skipped } = parseProject(text, 2);
  assert.equal(boxes.length, 1);
  assert.equal(skipped, 5);
});

test("a non-string project_id is ignored", () => {
  assert.equal(parseProject(JSON.stringify({ project_id: 7, objects: [] }), 1).projectId, null);
});

test("rejects files that are not project files", () => {
  assert.throws(() => parseProject("{}", 1), /expected an "objects" list/);
  assert.throws(() => parseProject("null", 1), /expected an "objects" list/);
  assert.throws(() => parseProject("not json", 1), SyntaxError);
});

test("save then load gives back the same boxes", () => {
  const boxes = [box(0, 10, 20, 40, 60), box(2, 5, 5, 9, 9, "floor plan"), box(1, 0, 0, 100, 100, "callout")];
  const { projectId, boxes: loaded, skipped } = parseProject(serializeProject("p", boxes), 3);
  assert.equal(projectId, "p");
  assert.equal(skipped, 0);
  assert.deepEqual(sortBoxes(loaded), sortBoxes(boxes));
});

test("the public dataset loads completely and saves back to the same boxes", () => {
  const text = readFileSync(new URL("../../dataset/public/obj-location.json", import.meta.url), "utf8");
  const original = JSON.parse(text);
  const { projectId, boxes, skipped } = parseProject(text, Number.MAX_SAFE_INTEGER);
  assert.equal(projectId, original.project_id);
  assert.equal(skipped, 0);
  assert.equal(boxes.length, original.objects.length);

  const resaved = JSON.parse(serializeProject(projectId, boxes));
  const strip = (objects) => objects.map(({ id, ...rest }) => JSON.stringify(rest)).sort();
  assert.deepEqual(strip(resaved.objects), strip(original.objects));
});

test("project id comes from the PDF name", () => {
  assert.equal(projectIdFromFilename("drawing.pdf"), "drawing");
  assert.equal(projectIdFromFilename("A.PDF"), "A");
  assert.equal(projectIdFromFilename("a.pdf.pdf"), "a");
  assert.equal(projectIdFromFilename("notes.txt"), "notes.txt");
});

function sortBoxes(boxes) {
  return [...boxes].sort((a, b) => a.page - b.page || a.y0 - b.y0 || a.x0 - b.x0);
}
