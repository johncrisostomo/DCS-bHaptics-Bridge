"""Measure the SDK1 grid -> TactSuit Pro motor mapping on your own suit.

Why this exists: the bridge submits frames to bHaptics Player over the
SDK1 websocket protocol, whose vest grid has 20 points per side. The
TactSuit Pro has 16 motors per side, and how Player maps the grid onto
the Pro is not published. This tool fires each grid point one at a time
and asks you which Pro motor you felt, then writes the measured table
to pro_mapping.json in the project root. The bridge loads that file
automatically and uses it instead of the built-in seed table.

Motor numbers are the official ones from bHaptics' Motor Index page
(docs.bhaptics.com/sdk/further/motor): front 0-15, back 16-31, as drawn
below. Note the front is drawn as you would see it looking at someone
wearing the vest (mirror view).

Run:  python tools\calibrate_pro.py
Wear the suit, with bHaptics Player running and the suit connected.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bhaptics_bridge.config import load_config
from bhaptics_bridge.layout import FRONT, BACK
from bhaptics_bridge.player import BhapticsPlayer

INTENSITY = 70
DURATION_MS = 700

FRONT_DRAWN = [[3, 2, 1, 0], [7, 6, 5, 4], [11, 10, 9, 8], [15, 14, 13, 12]]
BACK_DRAWN = [[16, 17, 18, 19], [20, 21, 22, 23], [24, 25, 26, 27], [28, 29, 30, 31]]


def draw(title, grid):
    print(title)
    for row in grid:
        print("   " + "  ".join("%2d" % n for n in row))
    print()


def ask_motors(player, position, grid_point, valid_range):
    """Fire one grid point and ask which Pro motor(s) it drove."""
    while True:
        player.submit_dot("cal", position, {grid_point: INTENSITY}, DURATION_MS)
        raw = input(
            "  grid %s #%d fired. Pro motor number(s)? "
            "(comma-separated, 'none', 'r' = fire again, 'q' = quit) "
            % (position, grid_point)).strip().lower()
        if raw == "q":
            return None
        if raw == "r":
            continue
        if raw == "none":
            return []
        try:
            motors = [int(part) for part in raw.split(",") if part.strip()]
        except ValueError:
            print("  not a number list; try again.")
            continue
        if not motors or any(m not in valid_range for m in motors):
            print("  expected numbers in %d-%d; try again."
                  % (valid_range[0], valid_range[-1]))
            continue
        return motors


def main():
    cfg = load_config()
    player = BhapticsPlayer(cfg)
    if not player.ensure_connected():
        print("Could not connect to bHaptics Player. Start Player, "
              "connect the suit, and run this again.")
        return 1

    print("TactSuit Pro mapping calibration")
    print("One grid point fires at a time. Answer with the number of the")
    print("motor you felt, using the official diagram numbers:\n")
    draw("FRONT (as drawn in the official diagram - mirror view):", FRONT_DRAWN)
    draw("BACK (as drawn in the official diagram):", BACK_DRAWN)

    measured = {}  # (position, grid) -> [absolute pro motors]
    sides = [
        (FRONT, "front", list(range(0, 16))),
        (BACK, "back", list(range(16, 32))),
    ]
    quit_early = False
    for position, _name, valid in sides:
        if quit_early:
            break
        print("== %s: 20 grid points ==" % position)
        for grid_point in range(20):
            input("  press Enter to fire grid %s #%d..." % (position, grid_point))
            motors = ask_motors(player, position, grid_point, valid)
            if motors is None:
                quit_early = True
                break
            measured[(position, grid_point)] = motors

    player.turn_off_all([("cal", FRONT), ("cal", BACK)])
    player.close()

    # Invert to Pro motor -> grid points, per side, per-side indices.
    out = {"front": {}, "back": {}}
    for (position, grid_point), motors in measured.items():
        side_key = "front" if position == FRONT else "back"
        offset = 0 if position == FRONT else 16
        for motor in motors:
            out[side_key].setdefault(str(motor - offset), []).append(grid_point)
    for side in out.values():
        for grids in side.values():
            grids.sort()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "pro_mapping.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)

    print("\nWrote %s" % path)
    for side_key, offset in (("front", 0), ("back", 16)):
        claimed = sorted(int(k) + offset for k in out[side_key])
        missing = [m for m in range(offset, offset + 16) if m not in claimed]
        print("%s: %d of 16 motors measured." % (side_key, len(claimed)))
        if missing:
            print("  motors not felt from any grid point: %s "
                  "(these keep the seed mapping)" % missing)
    print("Restart the bridge to use the measured mapping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
