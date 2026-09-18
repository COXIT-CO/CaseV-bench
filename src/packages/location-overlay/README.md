# location-overlay

Draws predicted location boxes onto a rendered drawing page, so the public benchmark demo and
the internal tools show a model's output the same way. It draws; it never scores, loads or
stores anything.

Python 3.11+, Pillow the only dependency, no network, no filesystem, no global state.

## Install

```bash
pip install "location-overlay @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-overlay-v0.1.0#subdirectory=src/packages/location-overlay"
```

**Pin the tag, not a branch.** Releases are tagged `location-overlay-vX.Y.Z`, versioned
independently of the application in the rest of that repository.

## Input

Boxes are the same items [`location-scorer`](../location-scorer/README.md) takes, so one list of
predictions feeds both libraries with no translation step in between:

```python
{"object_type": "cabinet", "bbox": [x_min, y_min, x_max, y_max], "page": 1}
```

- **`object_type`** — drawn as the label, and what the colour is picked from. Any string; the
  library holds no taxonomy.
- **`bbox`** — **normalized to 0–1 with a top-left origin.** This is the one assumption the
  library makes and the one it cannot check: pixel coordinates are numbers too, and would draw
  every box in a huddle in the top-left corner.
- **`page`** — optional here, and **neither drawn nor used to filter**. One image is one page, so
  you pass the boxes for the page you rendered; what the key buys you is the guard that you did.

## Render a page

```python
from PIL import Image

from location_overlay import render

page = Image.open("sheet-1.png")

predictions = [
    {"object_type": "cabinet",    "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},
    {"object_type": "cabinet",    "bbox": [0.50, 0.10, 0.58, 0.30], "page": 1},
    {"object_type": "countertop", "bbox": [0.10, 0.60, 0.90, 0.70], "page": 1},
]

render(page, predictions).save("sheet-1-overlay.png")
```

The result is a **new image in mode `RGB`**. The page you passed in is never modified, whatever
mode it arrived in — a 1-bit or grayscale page render is converted, since a colour overlay
composited onto a grayscale canvas comes out grey.

Predictions usually arrive for a whole document, so filter at the call site:

```python
render(page, [box for box in predictions if box["page"] == page_number])
```

Skip that and the call raises: `boxes span pages [1, 2]`. Page 2's boxes drawn over page 1 make
a plausible-looking picture that is simply wrong, and nothing downstream would catch it.

### Options

```python
render(image, boxes, *, labels=True, line_width=None)
```

**`labels=False`** drops the text chips and leaves bare outlines — for thumbnails, and for dense
pages where the chips cover more than they explain.

**`line_width`** is in pixels. The default, `None`, derives a stroke from the image's shorter
side (`min(width, height) / 400`, never below 1), which is what lets one call site stay legible
across a 400px thumbnail and a 3000px sheet. Label text is sized the same way. Pass a number to
override the stroke; the labels keep scaling with the image, so a 1px stroke on a large sheet
still gets readable text.

## Colours and labels

**No two object types on a page are drawn in the same colour.** Colour is the first thing anyone
reads off an overlay, and two different types sharing one would be read as one type.

Each type prefers the palette entry its name hashes to, which is what keeps a type recognisable
from page to page and from the demo to an internal diff view without anyone passing a palette
around — cabinets are the same red on every sheet. The hash is `crc32` of the name rather than
`hash()`, which is salted per process and would repaint everything on restart. Two names can
prefer the same entry (with sixteen entries the birthday problem says to expect it well before
sixteen types are in play); when that happens on one page, the name that sorts later walks to the
next free entry, which is stored nearly opposite on the colour wheel rather than being the
neighbouring shade — two greens would defeat the point. Sorting is what makes the outcome
independent of the order the boxes arrived in, and only the displaced type moves: the other keeps
its usual colour.

So a type's colour is stable *unless* it is displaced by a collision on that particular page.
`callout` and `floor plan` in this project's own taxonomy are exactly such a pair. Past sixteen
distinct types on one page there is nothing left to walk to and colours repeat; the drawn label
is what separates boxes for certain at any count.

Boxes are **outlined, never filled** — the linework underneath is the reason anyone is looking at
the page. Labels sit on a filled chip above the box, dropping inside it only when the box is too
close to the top edge for the chip to fit; bare text over dense drawing linework is unreadable
exactly where the interesting objects are. Every palette entry is dark enough to carry white text
at a WCAG contrast ratio of 5.5:1, which a test asserts against a 4.5:1 floor so a future palette
edit cannot quietly make labels unreadable.

Outlines are drawn in the order given, so a later box overlaps an earlier one, and **all labels
are drawn after all outlines** — a chip is never sliced through by a box that came later.
Overlapping chips are **not** de-conflicted: many boxes of the same type in one corner give you a
pile of chips.

## Validation is asymmetric

Same split as the scorer's, drawn along a different line: what the *caller* got wrong raises,
what the *model* got wrong is drawn.

| Condition | Result |
|---|---|
| missing `object_type`, `bbox` that is not four finite numbers | `ValueError` naming the index |
| boxes carrying more than one distinct `page` | `ValueError` naming the pages |
| `line_width` below 1 | `ValueError` |
| inverted corners (`x_min > x_max`) | corners ordered, box drawn |
| zero width or height | drawn as a line |
| coordinates outside 0–1 | drawn, clipped by the canvas edge |

The first three are bugs at the call site, and a picture is the wrong place to discover them. The
last three are the model's output, and **looking at exactly those is the point** — a box quietly
dropped for being malformed hides the model's worst behaviour from the person reviewing it. Note
that out-of-range coordinates are *clipped*, not clamped: a hallucination reaching off the sheet
keeps looking like one instead of hugging the border like a legitimate edge detection.

## Versioning and releases

Semantic versioning under tags `location-overlay-vX.Y.Z` — namespaced so they never collide with
the application's own tags in the same repository. The contract is the **signature and which
colour a type is drawn in**: a palette edit repaints every screenshot and every overlay anyone
has saved, so it is breaking. Incidental pixel differences — label metrics, a default stroke, a
Pillow upgrade's anti-aliasing — are not breaking, but any visible change is called out in
[CHANGELOG.md](CHANGELOG.md). (The scorer's stricter rule, where *any* change to its output is
breaking, exists because its numbers get published and compared across teams. Nobody compares
two overlays pixel by pixel.)

**To cut a release:** bump `version` in `pyproject.toml` and add the dated changelog entry, in a
pull request of its own. Once it is on `main` with CI green, tag the merge commit — never a
branch head — and push:

```bash
git tag -a location-overlay-v0.1.0 -m "location-overlay 0.1.0" && git push origin location-overlay-v0.1.0
```

Then prove the tag installs from a directory that is *not* a checkout of this repository, so
nothing resolves against local application code:

```bash
cd "$(mktemp -d)" && python3.11 -m venv .venv && . .venv/bin/activate
pip install "location-overlay @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-overlay-v0.1.0#subdirectory=src/packages/location-overlay"
python -c "from PIL import Image; from location_overlay import render; \
  print(render(Image.new('RGB', (100, 100), 'white'), [{'object_type': 'cabinet', 'bbox': [0.1, 0.1, 0.3, 0.3]}]).mode)"
```

If that fails, delete and re-cut the tag rather than leave a broken one published — but once
anyone has consumed a tag it is immutable, so fix forward with a patch.

## Develop

```bash
cd src/packages/location-overlay
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && black --check . && isort --check-only .
```

Use 3.11 — the declared floor, which CI enforces on its own job. `render()`'s docstring is the
contract in code, and the tests go through the public functions only, asserting rendered pixels
rather than internal calls, so they describe what the image looks like and stay valid through any
refactor of the internals.