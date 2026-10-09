# Annotation tool

A browser-based tool for drawing ground-truth boxes on PDF drawings. It writes the project file
the benchmark reads (`<project>-obj-location.json`, as in [`dataset/public/`](../dataset/public)).

## Start

```bash
make -C annotation_tool start
```

This serves the tool at <http://localhost:8765> and opens it in your browser. The only dependency
is PyMuPDF, which renders the pages. With [uv](https://docs.astral.sh/uv/) installed, nothing else
is needed. Without uv, the first run creates `annotation_tool/.venv` using any `python3` ≥ 3.9.
Use `PORT=9000 make -C annotation_tool start` to change the port. Stop it with `Ctrl+C`, or with
`make -C annotation_tool stop` (add `PORT=...` if you changed it) when it runs in the background.

Everything runs locally: the PDF is uploaded only to this local server, and nothing goes to the
internet.

## Workflow

1. **Open PDF** (or drop it on the page). To continue earlier work, also **Import** its
   JSON file, or drop the PDF and the JSON together.
2. Pick a category with `1`–`5` (cabinet, countertop, elevation, callout, floor plan), then drag
   on the drawing to draw a box. New boxes use the active category.
3. Click inside a box to select it. When boxes are nested, the click selects the smallest one.
   Drag a selected box by its edge to move it, or by a handle to resize it. You can also nudge it
   with the arrow keys or type exact coordinates in the sidebar. `⌫` deletes it, and `⌘Z` undoes.
4. **Save** (`⌘S`). In Chrome and Edge, Save writes back to the imported file. Other browsers
   download the file.

**Export PDF** (`⌘E`) downloads `<name>-annotated.pdf`, a copy of the drawing with every box
drawn in its category color and labelled with the id it gets in the JSON. Use it to review the
annotations or share them with people who don't run the tool.

To measure a length in inches, right-drag on the drawing (72 pt = 1 in). Press `?` to see all
shortcuts.

Unsaved boxes are kept in the browser's local storage for each PDF. If the tab closes before you
save, reopen the same PDF and choose **Restore**.

## File format

```json
{
  "project_id": "public",
  "objects": [
    { "id": "cab-001", "category": "cabinet", "page": 1,
      "bbox": { "x": 105, "y": 36, "width": 873, "height": 523 } }
  ]
}
```

Coordinates are integer PDF points. The origin is the top-left of the page as rendered, with page
rotation applied (PyMuPDF's `page.rect`, the same space the runner renders in). `page` starts at 1.

Ids are reassigned on every save. Objects are sorted by page, then `y`, then `x`, and numbered per
category (`cab`, `ctp`, `elv`, `cal`, `flp`). Category names are written as shown above, for
example `floor plan`. The runner normalises them.

## Layout

| Path | |
|---|---|
| `serve.py` | Local server: static files plus PyMuPDF page rendering |
| `index.html`, `styles.css` | The page |
| `src/main.js` | UI wiring: files, sidebar, toolbar, shortcuts, drafts |
| `src/viewer.js` | Page display, zoom, and the base and detail render layers |
| `src/overlay.js` | SVG boxes and mouse gestures (draw, select, move, resize, pan, measure) |
| `src/store.js` | Boxes, selection, undo history |
| `src/projectFile.js` | Reading and writing the JSON file |
| `src/categories.js` | Categories, id prefixes, colors |

There is no build step. Edit a file and reload the page.

The previous Tk desktop app was replaced by this tool. It is still in git history; to run it, check
out a commit from before the replacement, for example `git worktree add ../old main`.

Videos about annotation conventions, recorded with the previous desktop version of the tool:
[counting `referenced times`](https://drive.google.com/file/d/1RdTlwjkYxTsxuvyUkwDxqIhxiY73hU17/view?usp=drive_link),
[cabinet depth](https://drive.google.com/file/d/1kVT1GmCV4WYktXS2jCL2m9HXw606nCTj/view?usp=drive_link),
[room numbers](https://drive.google.com/file/d/1uVEj3vEVP0bHA1WaCk-0PEX7T7KlOdaQ/view?usp=drive_link).
