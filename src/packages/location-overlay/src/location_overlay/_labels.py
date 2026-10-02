from PIL import ImageDraw, ImageFont

from ._palette import LABEL_TEXT
from ._types import Color, Rect, Size


def draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect: Rect,
    color: Color,
    font: ImageFont.FreeTypeFont,
    size: Size,
) -> None:
    """Write `text` on a filled chip pinned to the top-left corner of `rect`.

    The chip is filled rather than transparent because a drawing is dense black linework: bare
    text over it is unreadable exactly where the interesting objects are. It goes *above* the
    box so it never hides the pixels the box is pointing at, and drops inside only when the box
    is too close to the top edge for the chip to fit on the page.

    A box entirely off-canvas draws no chip either: the box itself is invisible (clipped by
    Pillow, see `to_pixels`), and pinning a label to where an invisible box would be produces a
    floating chip with nothing for it to label.
    """
    if not text:
        return

    left, top, right, bottom = rect
    if right <= 0 or left >= size[0] or bottom <= 0 or top >= size[1]:
        return

    padding = max(2, round(font.size / 6))
    text_left, text_top, text_right, text_bottom = draw.textbbox(
        (0, 0), text, font=font
    )
    chip_width = (text_right - text_left) + 2 * padding
    chip_height = (text_bottom - text_top) + 2 * padding

    x = min(max(left, 0), max(0, size[0] - chip_width))
    y = top - chip_height
    if y < 0:
        y = max(top, 0)

    draw.rectangle((x, y, x + chip_width, y + chip_height), fill=color)
    draw.text(
        (x + padding - text_left, y + padding - text_top),
        text,
        font=font,
        fill=LABEL_TEXT,
    )
