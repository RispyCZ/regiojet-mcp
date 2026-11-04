from pathlib import Path

import pytest

from regiojet_mcp.seat_layout import single_seats

FIXTURES = Path(__file__).parent / "fixtures"


def _svg(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_setra_upper_deck_left_column_is_single_seats():
    # Real RegioJet layout: single seats in the left column, pairs on the right,
    # and a back bench of four (55-58) that must not count as singles.
    assert single_seats(_svg("bus_SETRA_patro.svg")) == {
        21,
        22,
        25,
        26,
        31,
        32,
        37,
        38,
        43,
        44,
        45,
        46,
        51,
        52,
    }


def test_setra_lower_deck_has_no_single_seats():
    # Every seat on the lower deck sits next to another one, including the
    # front-left pair 1-2 that has no seats across the aisle.
    assert single_seats(_svg("bus_SETRA_prizemi.svg")) == set()


def test_svg_without_numbered_seats_is_rejected():
    with pytest.raises(ValueError):
        single_seats('<svg viewBox="0 0 192 645"><rect/></svg>')
