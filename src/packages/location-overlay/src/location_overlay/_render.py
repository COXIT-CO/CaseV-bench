from typing import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from ._geometry import font_size_for, line_width_for, to_pixels
from ._labels import draw_label
from ._palette import assign
from ._types import Box, Color
from ._validation import validate


def render(
    image: Image.Image,
    boxes: Sequence[Box],
    *,
    labels: bool = True,
    line_width: int | None = None,
    background: bool = True,
    colors: Mapping[str, Color] | None = None,
) -> Image.Image:
    """Draw predicted location boxes onto a rendered drawing page.

    `boxes` are ``{"object_type", "bbox", "page"}`` items — the exact shape `location-scorer`
    takes, so predictions go to both libraries without a translation step. **`bbox` is
    ``[x_min, y_min, x_max, y_max]`` normalized to 0–1 with a top-left origin**, which is the
    one thing this library assumes and cannot check: pixel coordinates would silently draw
    every box in the top-left corner.

    Returns a **new image in mode ``RGB``** (or ``RGBA`` — see `background` below); the caller's
    image is never touched, whatever mode it arrived in (a 1-bit or grayscale page render is
    converted, because a colour overlay on a grayscale canvas would come out grey). Boxes are
    outlined, never filled, so the linework under them stays readable.

    **No two object types on a page are drawn in the same colour.** Each type prefers the
    palette entry its name hashes to — which keeps a type the same colour from page to page and
    from the demo to an internal tool — and a type whose preferred entry is already taken by
    another type on this page moves to the next free one, resolved in sorted name order so the
    picture does not depend on the order the boxes arrived in. The palette holds sixteen
    colours; past sixteen distinct types on one page, colours repeat.

    `page` is **not drawn and not used to filter**: one image is one page, so the caller passes
    the boxes for the page it rendered. What the key does is guard that — boxes carrying more
    than one distinct `page` raise `ValueError`, because the alternative is a plausible-looking
    picture with another sheet's boxes scattered over it. Boxes with no `page` are fine.

    `labels=False` drops the text chips and leaves bare outlines, for thumbnails and for dense
    pages where the chips would cover more than they explain. `line_width` is in pixels and
    defaults to a stroke derived from the image's shorter side, which is what keeps one call
    site legible across a thumbnail and a 3000px sheet.

    `background=False` draws onto a transparent canvas the same size as `image` instead of onto
    `image` itself — `image`'s pixels are never read, only its size — and returns an **`RGBA`**
    image instead of `RGB`, opaque only where a box or label chip was drawn. This is for a caller
    compositing several separately-toggleable layers over one base image (e.g. one layer per
    object type, so a viewer can show or hide a type without a fresh render): draw each layer
    with the *same* `colors` (see below) and stack them in any order, since nothing drawn by one
    call can cover another once they're separate images.

    `colors` fixes the colour for each object type instead of deriving it from `boxes` with
    `assign()`. Without it, each call picks colours from only the types *it* was given — fine
    for one call drawing everything, but wrong across several calls each drawing a subset, since
    a type alone in its own call never collides with anything and always gets its first-preference
    colour, even where `assign()` would have moved it to avoid a collision on the full set. Pass
    the one `assign(all_types_on_the_page)` result to every call that page makes so a type is the
    same colour in every layer, matching what a single combined render would have produced.
    Raises `ValueError` if `colors` is missing an entry for a type `boxes` actually uses.

    **Validation is asymmetric, like the scorer's.** A box whose *shape* is wrong — no
    `object_type`, a `bbox` that is not four finite numbers — raises `ValueError` naming its
    index, because it is a bug in the caller, not an opinion from a model. A box whose
    *geometry* is wrong is drawn: inverted corners are ordered, zero-area boxes come out as a
    line, and coordinates outside 0–1 are clipped by the canvas rather than clamped to it. That
    is the point of looking at the picture, and silently dropping such a box would hide the
    model's worst output from the person reviewing it.

    Outlines are drawn in the order given, so a later box overlaps an earlier one, and **all
    labels are drawn after all outlines** — a chip is never sliced by a box that came later.
    Overlapping chips are not de-conflicted: on a page with many boxes of the same type in one
    corner, the chips will pile up.
    """
    validate(boxes)
    if line_width is not None and line_width < 1:
        raise ValueError(f"line_width must be at least 1 pixel, got {line_width}")

    canvas = (
        image.convert("RGB")
        if background
        else Image.new("RGBA", image.size, (0, 0, 0, 0))
    )
    draw = ImageDraw.Draw(canvas)
    stroke = line_width if line_width is not None else line_width_for(canvas.size)

    if colors is None:
        resolved_colors = assign(box["object_type"] for box in boxes)
    else:
        resolved_colors = dict(colors)
        missing = {box["object_type"] for box in boxes} - resolved_colors.keys()
        if missing:
            raise ValueError(f"colors has no entry for: {sorted(missing)}")

    drawn = [(box, to_pixels(box["bbox"], canvas.size)) for box in boxes]

    for box, rect in drawn:
        draw.rectangle(rect, outline=resolved_colors[box["object_type"]], width=stroke)

    if labels:
        font = ImageFont.load_default(size=font_size_for(canvas.size))
        for box, rect in drawn:
            object_type = box["object_type"]
            draw_label(
                draw, object_type, rect, resolved_colors[object_type], font, canvas.size
            )

    return canvas
