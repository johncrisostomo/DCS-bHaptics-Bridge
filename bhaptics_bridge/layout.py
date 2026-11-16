"""TactSuit motor layout in the SDK1 dot-mode grid.

bHaptics Player is addressed with two positions, "VestFront" and
"VestBack", each a grid of 20 points (indices 0-19). Player maps that
grid onto the connected device (a TactSuit Pro has 16 motors per side).

The grid is 4 columns x 5 rows, index = row * 4 + column, row 0 at the
top, as used by SDK1-era clients. If the rows feel upside down on your
suit, set flip_vertical in config.ini — it is a known calibration
suspect on a first run.
"""

FRONT = "VestFront"
BACK = "VestBack"

COLS = 4
ROWS = 5
POINTS_PER_SIDE = COLS * ROWS


class VestLayout:
    def __init__(self, flip_vertical=False):
        self.flip_vertical = flip_vertical

    def _row(self, row):
        if self.flip_vertical:
            return ROWS - 1 - row
        return row

    def rows(self, rows):
        """All point indices in the given rows (0 = top)."""
        out = []
        for row in rows:
            r = self._row(row)
            for col in range(COLS):
                out.append(r * COLS + col)
        return out

    def cols(self, cols):
        """All point indices in the given columns (0 = wearer's right side
        as indexed; left/right mirroring is a calibration detail)."""
        out = []
        for row in range(ROWS):
            r = self._row(row)
            for col in cols:
                out.append(r * COLS + col)
        return out

    def rows_cols(self, rows, cols):
        out = []
        for row in rows:
            r = self._row(row)
            for col in cols:
                out.append(r * COLS + col)
        return out

    def all(self):
        return list(range(POINTS_PER_SIDE))

    # Named regions used by the effect engine.
    def shoulders(self):
        return self.rows([0])

    def chest(self):
        return self.rows([0, 1])

    def mid(self):
        return self.rows([2])

    def lower(self):
        return self.rows([3, 4])

    def belly(self):
        return self.rows([4])

    def center_front(self):
        return self.rows_cols([1, 2, 3], [1, 2])
