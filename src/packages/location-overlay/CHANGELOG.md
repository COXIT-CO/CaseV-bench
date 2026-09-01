# Changelog

Semver, tagged `location-overlay-vX.Y.Z`. The signature and which colour a type is drawn in are
the contract — see [Versioning and releases](README.md#versioning-and-releases). Any visible
change to what a render looks like is listed here even when it is not breaking.

## 0.1.0 — 2026-08-11

First release.

- `render(image, boxes, *, labels=True, line_width=None)` — draws outlined boxes with labels onto
  a copy of the page and returns it as a new `RGB` image. Python 3.11+, Pillow the only
  dependency.
- Boxes are `location-scorer`-shaped items; `bbox` is normalized 0–1 with a top-left origin.
  `page` is not drawn and not filtered on, but boxes spanning more than one page raise
  `ValueError` — one image is one page.
- **No two object types on a page share a colour.** Each type prefers the entry its name hashes
  to — stable across processes and machines (`crc32`, not `hash()`) — and a type whose preferred
  entry is taken walks to the next free one, resolved in sorted name order. Sixteen colours;
  past sixteen distinct types on a page they repeat.
- Default stroke and label size derive from the image's shorter side.
- A malformed *shape* raises `ValueError` naming the index; malformed *geometry* is drawn —
  inverted corners ordered, zero-area boxes as a line, out-of-range coordinates clipped.
- **Known limitations:** overlapping labels are not de-conflicted; there is no ground-truth /
  prediction distinction, so a caller wanting both on one page renders twice or distinguishes
  them through `object_type`.