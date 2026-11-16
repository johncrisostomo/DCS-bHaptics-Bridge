"""TactSuit Pro motor layout.

This project targets the bHaptics TactSuit Pro only. The Pro has 32
motors: 16 on the front, 16 on the back, arranged as a 4x4 grid per side.

Motor numbering follows bHaptics' official Motor Index page
(docs.bhaptics.com/sdk/further/motor):

- Front motors are 0-15, back motors are 16-31 (the "absolute" index).
- Per side, the index is row-major: index = row * 4 + column, with
  row 0 at the top and column 0 at the wearer's left.

This module works in per-side indices (0-15) plus a side ("VestFront" /
"VestBack"), which is how the effect engine addresses motors. Use
absolute() when a single 0-31 number is needed (the calibration tool
and the official diagram use absolute numbers).

Effects are defined in this Pro motor space. bhaptics_bridge.pro_map
translates Pro motors to the SDK1 dot grid that bHaptics Player accepts.
"""

FRONT = "VestFront"
BACK = "VestBack"

COLS = 4
ROWS = 4
POINTS_PER_SIDE = COLS * ROWS  # 16


def absolute(position, index):
    """Per-side index -> official absolute index (front 0-15, back 16-31)."""
    if position == BACK:
        return index + POINTS_PER_SIDE
    return index


class ProLayout:
    """Named regions of the TactSuit Pro, in per-side motor indices."""

    def rows(self, rows):
        """All motor indices in the given rows (0 = top)."""
        out = []
        for row in rows:
            for col in range(COLS):
                out.append(row * COLS + col)
        return out

    def cols(self, cols):
        """All motor indices in the given columns (0 = wearer's left)."""
        out = []
        for row in range(ROWS):
            for col in cols:
                out.append(row * COLS + col)
        return out

    def rows_cols(self, rows, cols):
        out = []
        for row in rows:
            for col in cols:
                out.append(row * COLS + col)
        return out

    def all(self):
        return list(range(POINTS_PER_SIDE))

    # Named regions used by the effect engine.
    def shoulders(self):
        return self.rows([0])

    def chest(self):
        return self.rows([0, 1])

    def mid(self):
        return self.rows([1, 2])

    def lower(self):
        return self.rows([2, 3])

    def bottom(self):
        return self.rows([3])

    def center(self, rows):
        return self.rows_cols(rows, [1, 2])
