# location-parser

Pure, dependency-free extraction of bounding boxes from raw MLLM text replies, so a
model reply that's truncated, fenced, wrapped in prose, or slightly malformed doesn't
throw away an entire batch. It parses; it never scores.

Python 3.11+, no runtime dependencies, no network, no filesystem, no global state.

## Install

```bash
pip install "location-parser @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-parser-v0.1.0#subdirectory=src/packages/location-parser"
```

**Pin the tag, not a branch.** Releases are tagged `location-parser-vX.Y.Z`, versioned
independently of the application in the rest of that repository.

## Why this exists

Ask a model to find objects on a drawing and reply in JSON, and you get JSON back —
except when the reply is cut off mid-array because it hit a token limit, wrapped in a
fenced code block or a sentence of commentary, sprinkled with a stray semicolon or
trailing comma a strict parser rejects outright, or labeled with a class name nobody
agreed to. None of that is a reason to lose the whole reply. This package draws the
line between what can be salvaged and what has to be thrown away, and reports both
counts honestly rather than silently doing one or the other.

It hands off directly to
[`location-scorer`](../location-scorer): every `Box` this package returns is exactly
the shape `location_scorer.score()` takes as `predictions`, so the two compose with no
translation step in between.

## Worked example

```python
from location_parser import parse

reply = '''
Here's what I found:
```json
{
  "objects": [
    {"label": "cabinet", "box": [100, 200, 300, 400]},
    {"label": "cabinet", "box": [500, 200, 700, 400]},
    {"label": "spaceship", "box": [0, 0, 50, 50]},
'''  # cut off mid-array — the model hit its token limit here

parse(reply, page=0, allowed_labels={"cabinet", "countertop"})
```

```python
{
  "boxes": [
    {"object_type": "cabinet", "bbox": [0.1, 0.2, 0.3, 0.4], "page": 0},
    {"object_type": "cabinet", "bbox": [0.5, 0.2, 0.7, 0.4], "page": 0},
  ],
  "dropped": 1,        # "spaceship" isn't in allowed_labels
  "complete": False,   # the reply was cut off; the array had to be closed by hand
  "error": "response was truncated; recovered by closing unterminated brackets",
}
```

Two boxes survive out of three seen: the surrounding markdown fence and commentary
were stripped (that alone would not have marked the reply incomplete), the truncated
array was closed by discarding its last, unfinished entry, the two `cabinet` boxes'
0-1000-scale coordinates were rescaled to 0-1, and `spaceship` was dropped for not
being an allowed label — all counted, none guessed.

## Input and output

```python
def parse(
    text: str,
    *,
    page: int = 0,
    allowed_labels: set[str] | None = None,
) -> ParseResult: ...
```

- **`text`** — one full model reply, in whatever shape it came in.
- **`page`** — stamped onto every box this call returns. One call parses one reply for
  one page; pass whatever page number that reply was generated for. Any page number
  the model itself might mention in the text is not read.
- **`allowed_labels`** — when given, drops any entry whose label isn't in the set.
  `None` accepts any label as-is; the library holds no taxonomy of its own.

```python
class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]  # [x_min, y_min, x_max, y_max], normalized 0-1
    page: int

class ParseResult(TypedDict):
    boxes: list[Box]     # ready to hand to location_scorer as `predictions`
    dropped: int          # candidate entries found, but structurally unusable
    complete: bool         # False if the JSON had to be repaired or was truncated
    error: str | None     # what needed repairing, or None if nothing did
```

`parse()` never raises. Malformed input from an unreliable model is exactly the case
it exists to handle, not something that should stop a batch — the worst outcome is an
empty `boxes` list with `error` explaining why.

## What gets repaired vs. what gets dropped vs. what passes through

Three different things happen to three different kinds of problem, and conflating them
would hide information a caller needs:

| Problem | What happens | Effect on the result |
|---|---|---|
| Fenced in `` ```json ``` `` or wrapped in prose | Extracted, discarded around | No effect — this is normal wrapping, not damage |
| Trailing comma, stray `;` for `,`, `//`/`/* */` comment | Repaired in place, string-aware | `complete=False`, `error` names it |
| Reply cut off mid-array | Recovered by trimming the unfinished tail and closing open brackets | `complete=False`, `error` names it |
| Coordinates out of order (`x_min > x_max` and/or `y_min > y_max`) | Swapped back into order | No effect — the box is kept, just reordered |
| Exact duplicate of an already-kept box (same label + bbox) | Second occurrence removed | Counted in `dropped` |
| Missing/unrecognized label key, label not in `allowed_labels` | Entry removed | Counted in `dropped` |
| Missing/malformed bbox in every accepted shape | Entry removed | Counted in `dropped` |
| Zero-area or out-of-range-after-scaling bbox (even after swapping) | **Left alone, returned as-is** | Not dropped — see below |

**Coordinates out of order are swapped, not dropped or left inverted.** Some models
(observed from Gemini in particular) occasionally emit an otherwise-valid detection
with `left`/`right` or `top`/`bottom` swapped. `x_min`/`x_max` and `y_min`/`y_max` are
each reordered independently if needed, recovering the detection instead of either
silently discarding it or handing a downstream consumer geometry it has to defend
against itself.

**Exact duplicates are dropped.** Some models repeat an entry verbatim rather than
emit it once. An entry whose `object_type` and `bbox` — after scale detection and
coordinate-order swapping — are byte-identical to one already kept from the same call
is removed and counted in `dropped`, on the reasoning that a duplicate carries no
information a scorer needs and would otherwise double-count a single real detection.

**Geometrically nonsense boxes are not dropped.** A box with zero area, or
coordinates still outside 0-1 after scaling and swapping, parses cleanly and is handed
back unchanged. `location_scorer` scores a box like that as a false positive via IoU 0
on its own — dropping it here would erase evidence that the model hallucinated a box,
which is more useful to see in the score than to hide before it gets there. Only
entries this package cannot structurally interpret at all — no usable label, no
recognizable bbox in any accepted shape — count against `dropped`.

**`complete` means the JSON needed surgery, not that the text was messy.** Stripping a
markdown fence or a sentence of commentary around the JSON does not set
`complete=False`; extracting the payload from around it is treated as ordinary
wrapping, not corruption. `complete` goes to `False` only when the JSON itself had to
be repaired to parse at all — a truncated array closed back up, or syntax a strict
parser rejects outright. When that happens, `error` says which kind of repair was
needed. When nothing could be recovered, `boxes` is empty, `dropped` is `0` (there was
nothing to individually drop), `complete` is `False`, and `error` explains why.

## Recognized shapes

Different prompts and different model families name the same two fields differently.
Both label and bbox are read from whichever of their known aliases is present on an
entry, tried in this order:

- **Label:** `label`, `object_type`, `class`, `type`.
- **Bounding box:** `box`, `bbox`, `bounding_box` holding exactly four numbers (or
  numeric strings; `true`/`false` in a bbox slot is rejected, not read as `1`/`0`);
  or, if none of those is present, four separate named fields read directly off the
  entry or off a nested `identifying_properties` object — `left`/`top`/`right`/
  `bottom` first, then `x0`/`y0`/`x1`/`y1`. A `box`/`bbox`/`bounding_box` list, when
  present, always wins over named fields on the same entry.

The list of boxes itself can be a bare JSON array, an object with one of
`objects` / `boxes` / `detections` / `predictions` / `results` holding the array
(`objects` is checked first, since it's what this project's own prompts ask for), or a
single object that itself looks like one box — wrapped into a one-item list.

## Coordinate scale

`location_scorer` takes bboxes in a single, caller-chosen coordinate space; this
project standardizes on 0-1 floats. Several of this project's own prompts, however,
ask the model for 0-1000 integer coordinates instead (easier for a model to reason
about on a raster image). `parse()` auto-detects which scale each box is in,
**per box**: if every coordinate has `abs(value) <= 2.0`, it's assumed already
normalized (or a legitimate near-edge overflow past `1.0` from floating-point/model
imprecision, e.g. `1.05`) and is passed through unrescaled and unclamped; if any
coordinate exceeds `2.0`, the *whole box* is assumed to be 0-1000 scale and every
coordinate in it is divided by `1000`. The threshold sits well above `1.0` on
purpose: a real 0-1000-scale box will almost always land far past `2.0`, since a
bounding box only a couple of units wide on that scale is vanishingly rare for an
actual object — so a coordinate just past `1.0` is read as an imperfect 0-1 box
rather than misinterpreted as a barely-perceptible 1000-scale one and shrunk to a
corner.

## Truncation repair, precisely

A reply cut off by a token limit has no way to close its own brackets. Recovery works
by *removing* characters, never inventing any:

1. Find every `{`/`[` still open at the end of the (possibly comment/semicolon/
   trailing-comma-sanitized) candidate text.
2. Trim characters off the end — starting from none — closing whatever remains open at
   each length and attempting to parse. The first attempt that parses wins.
3. If the text ends inside an unterminated string, the string is closed with a `"`
   before the brackets are closed.

Because trimming only removes trailing characters the model actually wrote, the
recovered JSON never contains anything invented — at worst it discards the last,
incompletely-written entry in an array, leaving every earlier, complete entry intact.
The trim window is capped at 2000 characters, generous enough to discard one
partially-written box entry in this schema without risking a slow scan on a very large
reply; an unfinished array whose last entry alone exceeds that will not recover (see
[CHANGELOG.md](CHANGELOG.md)).

## Versioning and releases

Semantic versioning under tags `location-parser-vX.Y.Z` — namespaced so they never
collide with the application's own tags or with `location-scorer`'s in the same
repository. **Any change to which boxes a given input produces, or to how
`complete`/`dropped` are decided, is breaking**, whatever the diff looks like; a pinned
consumer took this dependency for consistent parsing behavior, not just an API. While
the library is `0.x` that means a minor bump, and everything else is a patch. Versions
are listed in [CHANGELOG.md](CHANGELOG.md).

**To cut a release:** bump `version` in `pyproject.toml` and add the dated changelog
entry, in a pull request of its own. Once it is on `main` with CI green, tag the merge
commit — never a branch head — and push:

```bash
git tag -a location-parser-v0.1.0 -m "location-parser 0.1.0" && git push origin location-parser-v0.1.0
```

Then prove the tag installs from a directory that is *not* a checkout of this
repository, so nothing resolves against local application code:

```bash
cd "$(mktemp -d)" && python3.11 -m venv .venv && . .venv/bin/activate
pip install "location-parser @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-parser-v0.1.0#subdirectory=src/packages/location-parser"
python -c "import location_parser as p; print(p.parse('not json')['error'])"
```

If that fails, delete and re-cut the tag rather than leave a broken one published —
but once anyone has consumed a tag it is immutable, so fix forward with a patch.

## Develop

```bash
cd src/packages/location-parser
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && black --check . && isort --check-only .
```

Use 3.11 — the declared floor, which CI enforces on its own job. `parse()`'s docstring
is the contract in code, and the tests go through the public function only.