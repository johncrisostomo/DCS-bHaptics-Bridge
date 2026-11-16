"""Translation between TactSuit Pro motors and the SDK1 dot grid.

The bridge talks to bHaptics Player over the SDK1 websocket protocol,
whose vest positions ("VestFront" / "VestBack") are a 20-point grid
(indices 0-19 per side) inherited from the TactSuit X40 generation.
The TactSuit Pro has 16 motors per side. How Player maps the 20-point
grid onto the Pro's motors is Player-internal and is not published by
bHaptics.

Effects are therefore defined in Pro motor space (see layout.py), and
this module is the single place that translates Pro motors to grid
points when a frame is submitted.

The built-in table below is a SEED, not a verified fact. It assumes
both grids are row-major, 4 columns wide, numbered from the top-left,
so grid rows 0-3 correspond to Pro rows 0-3 and grid row 4 (points
16-19) falls on the Pro's bottom row (motors 12-15).

Run tools/calibrate_pro.py once to measure the real mapping on your
own suit. It writes pro_mapping.json into the project root; when that
file exists, it replaces the seed. Its format:

    {"front": {"0": [0], "1": [1], ...},
     "back":  {"0": [0], ...}}

mapping each per-side Pro motor index (0-15) to the grid points that
drive it.
"""

import json
import os

from .layout import FRONT, BACK, POINTS_PER_SIDE

GRID_POINTS_PER_SIDE = 20


def _seed_grid_to_pro():
    """Seed assumption, per side: grid point -> Pro motor index."""
    table = {}
    for grid in range(GRID_POINTS_PER_SIDE):
        row, col = divmod(grid, 4)
        pro_row = min(row, 3)  # grid row 4 lands on the Pro's bottom row
        table[grid] = pro_row * 4 + col
    return table


def _invert(grid_to_pro):
    pro_to_grid = {pro: [] for pro in range(POINTS_PER_SIDE)}
    for grid, pro in grid_to_pro.items():
        pro_to_grid[pro].append(grid)
    return pro_to_grid


_SEED = _invert(_seed_grid_to_pro())


def _load_measured(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    mapping = {}
    for side_key, position in (("front", FRONT), ("back", BACK)):
        table = {}
        for pro_key, grids in raw.get(side_key, {}).items():
            pro = int(pro_key)
            if not 0 <= pro < POINTS_PER_SIDE:
                raise ValueError("pro_mapping.json: bad motor index %r" % pro_key)
            grid_list = [int(g) for g in grids]
            for g in grid_list:
                if not 0 <= g < GRID_POINTS_PER_SIDE:
                    raise ValueError("pro_mapping.json: bad grid point %r" % g)
            table[pro] = grid_list
        mapping[position] = table
    return mapping


def load_mapping(root=None):
    """Return {position: {pro_index: [grid points]}}.

    Uses pro_mapping.json from the project root when present (written by
    tools/calibrate_pro.py), otherwise the built-in seed.
    """
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "pro_mapping.json")
    if os.path.exists(path):
        measured = _load_measured(path)
        merged = {}
        for position in (FRONT, BACK):
            table = dict(_SEED)
            table.update(measured.get(position, {}))
            merged[position] = table
        return merged
    return {FRONT: dict(_SEED), BACK: dict(_SEED)}


def translate(mapping, position, pro_points):
    """Translate {pro_index: intensity} to {grid_index: intensity}.

    A Pro motor driven by several grid points sets all of them; where
    two Pro motors share a grid point, the higher intensity wins.
    Zero intensities are preserved so stop frames still zero the grid.
    """
    table = mapping[position]
    grid_points = {}
    for pro, intensity in pro_points.items():
        for grid in table.get(pro, []):
            if grid not in grid_points or intensity > grid_points[grid]:
                grid_points[grid] = intensity
    return grid_points
