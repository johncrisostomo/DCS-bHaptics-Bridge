"""Web console for the bridge: device view, motor test, effect lab.

Served on the local network (default http://<this PC>:41232) when the
bridge runs with --web. Standard library only, like the rest of the
project. There is no authentication: anyone on the LAN who can open
the page can drive the suit. Trusted networks only.

Three things live here:

- Status: whether the bridge is connected to bHaptics Player. (The
  SDK1 protocol cannot report whether the vest itself is powered and
  paired; that state is only visible in Player's own window. If Player
  is connected here and the suit check fires, the full chain works.)
- Motor test: the TactSuit Pro drawn front and back with the official
  motor numbers. Pressing and holding a motor vibrates it until
  released, through the same Pro -> SDK1 grid translation the bridge
  uses for effects (seed table, or pro_mapping.json when present -
  the same file format as bhaptics_bridge.pro_map on the Pro branch).
- Effect lab: the bridge's real EffectEngine fed with synthetic
  telemetry, so every effect (G, burner, stall, gear, touchdown,
  release, gun, countermeasures, damage, taxi) can be tested on the
  bench, without DCS.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .effects import EffectEngine
from .layout import FRONT, BACK, VestLayout
from .telemetry import Telemetry

HOLD_KEY = "web-hold"
HOLD_TIMEOUT = 15.0       # seconds; a stuck hold stops itself
LAB_TICK = 0.05           # 20 Hz, same cadence as the DCS loop

# ---------------------------------------------------------------------
# Pro motor -> SDK1 grid translation (seed).
#
# Same seed and same pro_mapping.json format as the Pro-layout work:
# both grids are row-major, 4 columns wide, numbered from the top-left;
# grid row 4 (points 16-19) falls on the Pro's bottom row (12-15).
# ---------------------------------------------------------------------


def _seed_pro_to_grid():
    table = {}
    for pro in range(16):
        row, _col = divmod(pro, 4)
        if row < 3:
            table[pro] = [pro]
        else:
            table[pro] = [pro, pro + 4]
    return table


def load_pro_mapping(root=None):
    """{position: {pro_index: [grid points]}}; measured file wins."""
    seed = _seed_pro_to_grid()
    mapping = {FRONT: dict(seed), BACK: dict(seed)}
    source = "seed"
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "pro_mapping.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for side_key, position in (("front", FRONT), ("back", BACK)):
                for pro_key, grids in raw.get(side_key, {}).items():
                    pro = int(pro_key)
                    grid_list = [int(g) for g in grids]
                    if 0 <= pro < 16 and all(0 <= g < 20 for g in grid_list):
                        mapping[position][pro] = grid_list
            source = "pro_mapping.json"
        except (ValueError, TypeError, AttributeError):
            source = "seed (pro_mapping.json unreadable)"
    return mapping, source


class _Hold:
    """One press-and-hold motor activation, re-submitted on a cadence."""

    def __init__(self, player, mapping, submit_lock):
        self.player = player
        self.mapping = mapping
        self.lock = submit_lock
        self.state = None  # (position, pro_index, grid_points, intensity, deadline)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def start(self, position, pro_index, intensity):
        grids = self.mapping[position][pro_index]
        self.state = (position, pro_index, grids, intensity,
                      time.monotonic() + HOLD_TIMEOUT)

    def stop(self):
        if self.state is not None:
            position, _pro, grids, _i, _d = self.state
            self.state = None
            self._zero(position, grids)

    def describe(self):
        if self.state is None:
            return None
        position, pro_index, _g, intensity, _d = self.state
        return {"side": "front" if position == FRONT else "back",
                "motor": pro_index if position == FRONT else pro_index + 16,
                "intensity": intensity}

    def _zero(self, position, grids):
        with self.lock:
            self.player.submit_dot(HOLD_KEY, position,
                                   {g: 0 for g in grids}, 30)

    def _run(self):
        while not self._stop.is_set():
            state = self.state
            if state is not None:
                position, _pro, grids, intensity, deadline = state
                if time.monotonic() > deadline:
                    self.stop()
                else:
                    with self.lock:
                        self.player.submit_dot(
                            HOLD_KEY, position,
                            {g: intensity for g in grids}, 150)
            time.sleep(0.1)

    def close(self):
        self.stop()
        self._stop.set()


class EffectLab:
    """The real EffectEngine, fed synthetic telemetry from the page."""

    def __init__(self, cfg, player, submit_lock):
        self.player = player
        self.lock = submit_lock
        self.engine = EffectEngine(cfg, VestLayout(cfg.flip_vertical))
        self.tel = Telemetry()
        self.tel.ac = True
        self.tel.name = "WEB_LAB"
        self.tel.gy = 1.0
        self.tel.gear = 1.0
        self.tel.wow1 = self.tel.wow4 = self.tel.wow6 = 1.0
        self.tel.shells = 578
        self.tel.stn = 4
        self.tel.chaff = 60
        self.tel.flare = 60
        # Page-controlled state.
        self.gy = 1.0
        self.burner = False
        self.airborne = False
        self.stall = False
        self.taxi = False
        self.taxi_speed = 15.0
        self.firing = False
        self.gear_target = 1.0
        self._touchdown = None  # (sink, phase_deadline)
        self._cm_flare_next = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # -- page API ------------------------------------------------------
    def set_state(self, data):
        if "gy" in data:
            self.gy = max(-1.0, min(9.0, float(data["gy"])))
        if "burner" in data:
            self.burner = bool(data["burner"])
        if "airborne" in data:
            self.airborne = bool(data["airborne"])
        if "stall" in data:
            self.stall = bool(data["stall"])
            if self.stall:
                self.airborne = True
        if "taxi" in data:
            self.taxi = bool(data["taxi"])
        if "taxi_speed" in data:
            self.taxi_speed = max(0.0, min(40.0, float(data["taxi_speed"])))

    def action(self, name, value=None):
        now = time.monotonic()
        if name == "check":
            self.engine.start_session(now)
        elif name == "release":
            self.tel.stn = max(0, self.tel.stn - 1)
        elif name == "cm":
            if self._cm_flare_next:
                self.tel.flare = max(0, self.tel.flare - 2)
            else:
                self.tel.chaff = max(0, self.tel.chaff - 2)
            self._cm_flare_next = not self._cm_flare_next
        elif name == "damage":
            self.tel.warn = frozenset({"LeftEngineFailure"})
        elif name == "repair":
            self.tel.warn = frozenset()
        elif name == "rearm":
            self.tel.stn = 4
            self.tel.shells = 578
            self.tel.chaff = 60
            self.tel.flare = 60
            self.tel.warn = frozenset()
        elif name == "gear":
            self.gear_target = 0.0 if self.gear_target > 0.5 else 1.0
        elif name == "gun_start":
            self.firing = True
        elif name == "gun_stop":
            self.firing = False
        elif name == "touchdown":
            sink = 3.0 if value is None else max(1.0, min(6.0, float(value)))
            self._touchdown = (sink, now + 0.9)

    def describe(self):
        return {
            "gy": self.gy, "burner": self.burner,
            "airborne": self.airborne, "stall": self.stall,
            "taxi": self.taxi, "taxi_speed": self.taxi_speed,
            "firing": self.firing, "gear": round(self.tel.gear, 2),
            "shells": self.tel.shells, "stn": self.tel.stn,
            "chaff": self.tel.chaff, "flare": self.tel.flare,
            "damaged": bool(self.tel.warn),
        }

    # -- tick ------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            now = time.monotonic()
            tel = self.tel

            if self._touchdown is not None:
                sink, deadline = self._touchdown
                if now < deadline:
                    tel.wow1 = tel.wow4 = tel.wow6 = 0.0
                    tel.agl = 20.0
                    tel.vv = -sink
                else:
                    tel.wow1 = tel.wow4 = tel.wow6 = 1.0
                    tel.agl = 0.0
                    tel.vv = 0.0
                    self._touchdown = None
                    self.airborne = False
            elif self.airborne:
                tel.wow1 = tel.wow4 = tel.wow6 = 0.0
                tel.agl = 500.0
                tel.vv = 0.0
            else:
                tel.wow1 = tel.wow4 = tel.wow6 = 1.0
                tel.agl = 0.0
                tel.vv = 0.0

            tel.gy = self.gy
            if self.taxi and not self.airborne:
                # Rolling jolts: brief load deviations over 1 G.
                tel.tas = self.taxi_speed
                phase = int(now * 7) % 5
                tel.gy = 1.0 + (0.16 if phase == 0 else 0.02)
            else:
                tel.tas = 0.0

            tel.ab1 = tel.ab2 = 1.0 if self.burner else 0.0
            tel.aoa = 0.35 if self.stall else 0.05

            # Gear transit at 0.5 / s (2 s end to end).
            if tel.gear < self.gear_target:
                tel.gear = min(self.gear_target, tel.gear + 0.5 * LAB_TICK)
            elif tel.gear > self.gear_target:
                tel.gear = max(self.gear_target, tel.gear - 0.5 * LAB_TICK)

            if self.firing and tel.shells > 0:
                tel.shells = max(0, tel.shells - 5)

            # The engine detects events by comparing successive telemetry
            # objects, so hand it a fresh snapshot each tick (exactly what
            # the UDP receiver does with each parsed packet). Mutating one
            # shared object would make every change invisible to it.
            snap = Telemetry()
            snap.ac = True
            snap.name = tel.name
            for key in ("gy", "gear", "wow1", "wow4", "wow6", "agl", "vv",
                        "aoa", "tas", "ab1", "ab2", "shells", "stn",
                        "chaff", "flare"):
                setattr(snap, key, getattr(tel, key))
            snap.warn = tel.warn

            frames = self.engine.update(snap, now)
            if frames:
                with self.lock:
                    for frame in frames:
                        self.player.submit(frame)
            time.sleep(LAB_TICK)

    def close(self):
        self._stop.set()


def _page_bytes():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "webui", "index.html")
    with open(path, "rb") as fh:
        return fh.read()


class WebConsole:
    """HTTP server + hold + lab, sharing the bridge's Player client."""

    def __init__(self, cfg, player, log=print, submit_lock=None):
        self.cfg = cfg
        self.player = player
        self.log = log
        self.lock = submit_lock or threading.Lock()
        self.mapping, self.mapping_source = load_pro_mapping()
        self.hold = _Hold(player, self.mapping, self.lock)
        self.lab = EffectLab(cfg, player, self.lock)
        self.httpd = None

    def status(self):
        # The Player client connects lazily, on the first frame sent.
        # With nothing submitted yet it would report "not connected"
        # even when Player is running - the pill then stayed red until
        # the first motor click forced a connection. The page polls
        # this endpoint, so use the poll to establish (or re-establish)
        # the connection and report the result.
        with self.lock:
            self.player.ensure_connected()
        return {
            "player_connected": bool(self.player.connected),
            "device": "TactSuit Pro",
            "mapping_source": self.mapping_source,
            "hold": self.hold.describe(),
            "lab": self.lab.describe(),
        }

    def start(self):
        console = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > 65536:
                    return {}
                try:
                    return json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return {}

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = _page_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/api/status":
                    self._json(console.status())
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                data = self._body()
                try:
                    if self.path == "/api/hold":
                        side = data.get("side")
                        motor = int(data.get("motor"))
                        intensity = int(data.get("intensity", 80))
                        position = FRONT if side == "front" else BACK
                        pro = motor if position == FRONT else motor - 16
                        if side not in ("front", "back") or not 0 <= pro < 16:
                            return self._json({"error": "bad motor"}, 400)
                        intensity = max(1, min(100, intensity))
                        console.hold.start(position, pro, intensity)
                        return self._json({"ok": True})
                    if self.path == "/api/hold/stop":
                        console.hold.stop()
                        return self._json({"ok": True})
                    if self.path == "/api/lab":
                        console.lab.set_state(data)
                        return self._json({"ok": True})
                    if self.path == "/api/lab/action":
                        console.lab.action(str(data.get("action")),
                                           data.get("value"))
                        return self._json({"ok": True})
                    return self._json({"error": "not found"}, 404)
                except (TypeError, ValueError) as exc:
                    return self._json({"error": str(exc)}, 400)

        self.httpd = ThreadingHTTPServer(
            (self.cfg.web_host, self.cfg.web_port), Handler)
        thread = threading.Thread(target=self.httpd.serve_forever,
                                  daemon=True)
        thread.start()
        # 0.0.0.0 is a bind address, not an address a browser can open;
        # name the reachable ones instead.
        if self.cfg.web_host in ("0.0.0.0", ""):
            where = ("http://localhost:%d on this PC; on the LAN use this "
                     "PC's IP address" % self.cfg.web_port)
        else:
            where = "http://%s:%d" % (self.cfg.web_host, self.cfg.web_port)
        self.log("[web] console at %s (no authentication)" % where)
        return self

    def stop(self):
        self.hold.close()
        self.lab.close()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
