from typing import Iterable
from zlib import crc32

from ._types import Color

# Sixteen hues, evenly spaced around the wheel at a high saturation no drawing linework has, and
# each one darkened to the lightest value that still carries white label text at a WCAG contrast
# ratio of 5.5:1. The contrast floor is asserted in the tests: an entry that drifts lighter makes
# its own labels unreadable.
#
# They are stored interleaved rather than in hue order — every seventh hue, which visits all
# sixteen — so that neighbouring entries are nearly opposite on the wheel. `assign()` resolves a
# collision by taking the next entry, and in hue order that would hand the displaced type the
# adjacent shade of the same colour: two greens where two colours were the whole point.
PALETTE: tuple[Color, ...] = (
    (205, 25, 55),  # crimson
    (14, 120, 63),  # emerald
    (186, 22, 172),  # magenta
    (45, 120, 14),  # grass
    (105, 31, 255),  # violet
    (111, 107, 13),  # olive
    (25, 101, 208),  # blue
    (190, 58, 23),  # vermilion
    (14, 117, 100),  # sea
    (198, 24, 118),  # rose
    (15, 122, 24),  # green
    (165, 27, 222),  # purple
    (81, 115, 14),  # moss
    (31, 40, 255),  # ultramarine
    (147, 93, 18),  # amber
    (17, 113, 139),  # teal
)

LABEL_TEXT: Color = (255, 255, 255)


def assign(object_types: Iterable[str]) -> dict[str, Color]:
    """Give every distinct object type on this page a colour, and never the same one twice.

    Each name prefers the palette entry its `crc32` hashes to, which is what keeps a type the
    same colour from page to page, from the demo to an internal tool, and across processes —
    `hash()` is salted per process and would repaint everything on restart. Two names can hash
    to the same entry, so a name that finds its preferred entry taken walks forward to the next
    free one; names are resolved in sorted order, so which of the two moves does not depend on
    the order the boxes arrived in.

    Past sixteen distinct types on one page there is nothing left to walk to and colours repeat.
    """
    assigned: dict[str, Color] = {}
    taken: set[int] = set()

    for object_type in sorted(set(object_types)):
        index = crc32(object_type.encode("utf-8")) % len(PALETTE)
        while index in taken and len(taken) < len(PALETTE):
            index = (index + 1) % len(PALETTE)
        taken.add(index)
        assigned[object_type] = PALETTE[index]

    return assigned
