"""Seat facts read from RegioJet vehicle layout SVGs (the `layoutURL` of a deck)."""

import re

# Each seat label looks like one of:
#   <text id="n21" transform="matrix(1 0 0 1 30.1 38.7)" ...>21</text>
#   <text id="n1" transform="translate(97.674 534.028)">1</text>
_SEAT_LABEL = re.compile(
    r'<text\s+id="n(\d+)"\s+transform="'
    r'(?:matrix\(1 0 0 1|translate\()\s*([\d.]+)[\s,]+([\d.]+)\)"'
)
_VIEWBOX = re.compile(r'viewBox="([\d.-]+) [\d.-]+ ([\d.]+) [\d.]+"')

# Labels of seats in one row differ by well under this (seat rows are ~40 units apart).
_ROW_TOLERANCE = 8.0


def single_seats(svg: str) -> set[int]:
    """Seat numbers that have no neighbouring seat on their side of the aisle.

    Seats are grouped into rows by the y position of their number label and split at
    the aisle (the middle of the layout); a row side holding exactly one seat is a
    single seat. Raises ValueError when the SVG has no recognisable seat labels.
    """
    seats = [(int(n), float(x), float(y)) for n, x, y in _SEAT_LABEL.findall(svg)]
    viewbox = _VIEWBOX.search(svg)
    if not seats or not viewbox:
        raise ValueError("no seat labels found in vehicle layout")
    aisle = float(viewbox.group(1)) + float(viewbox.group(2)) / 2

    singles: set[int] = set()
    for side in (
        [s for s in seats if s[1] < aisle],
        [s for s in seats if s[1] >= aisle],
    ):
        rows: list[list[tuple[int, float, float]]] = []
        for seat in sorted(side, key=lambda s: s[2]):
            if rows and seat[2] - rows[-1][-1][2] <= _ROW_TOLERANCE:
                rows[-1].append(seat)
            else:
                rows.append([seat])
        singles.update(row[0][0] for row in rows if len(row) == 1)
    return singles
