"""bHaptics Player client: the SDK1 websocket protocol, spoken directly.

Connects to ws://127.0.0.1:15881/v2/feedbacks with the app id/name as
query parameters, and submits dot-mode frames as JSON:

    {"Submit": [{"Type": "frame", "Key": <key>, "Frame": {
        "Position": "VestFront" | "VestBack",
        "DotPoints": [{"Index": i, "Intensity": 0-100}, ...],
        "DurationMillis": <ms>}}]}

The field casing follows the format observed from real SDK1 client
traffic. In dry-run mode nothing is sent; frames are printed instead.

Frames arrive in TactSuit Pro motor space (see layout.py) and are
translated to the SDK1 20-point grid here, via pro_map, just before
submission.
"""

import json
import os
import tempfile
import time
import urllib.parse

from . import pro_map
from .wsclient import WebSocketClient, WebSocketError


def load_mapping_with_source():
    """The Pro -> grid mapping + where it came from.

    Falls back to the seed if a measured pro_mapping.json exists but
    cannot be parsed, rather than taking the bridge down.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    has_file = os.path.exists(os.path.join(root, "pro_mapping.json"))
    try:
        mapping = pro_map.load_mapping(root)
    except (ValueError, TypeError, AttributeError, OSError):
        with tempfile.TemporaryDirectory() as tmp:
            mapping = pro_map.load_mapping(root=tmp)
        return mapping, "seed (pro_mapping.json unreadable)"
    return mapping, "pro_mapping.json" if has_file else "seed"


class BhapticsPlayer:
    def __init__(self, cfg, dry_run=False, log=print):
        self.cfg = cfg
        self.dry_run = dry_run
        self.log = log
        self.ws = None
        self._next_retry = 0.0
        self.mapping, self.mapping_source = load_mapping_with_source()

    @property
    def connected(self):
        return self.dry_run or (self.ws is not None and self.ws.connected)

    def _path(self):
        query = urllib.parse.urlencode({
            "appId": self.cfg.app_id,
            "appName": self.cfg.app_name,
        })
        return "/v2/feedbacks?" + query

    def ensure_connected(self):
        if self.dry_run or self.connected:
            return True
        now = time.monotonic()
        if now < self._next_retry:
            return False
        self._next_retry = now + 3.0
        ws = WebSocketClient(self.cfg.player_host, self.cfg.player_port, self._path())
        try:
            ws.connect()
        except (OSError, WebSocketError) as exc:
            self.log("[player] cannot connect to bHaptics Player: %s" % exc)
            return False
        self.ws = ws
        self.log("[player] connected to bHaptics Player")
        return True

    def poll(self):
        if self.dry_run or self.ws is None:
            return
        try:
            self.ws.poll()
        except WebSocketError as exc:
            self.log("[player] connection lost: %s" % exc)
            self.ws.close()
            self.ws = None

    def submit_dot(self, key, position, points, duration_ms):
        """points: dict {index: intensity}. Sends one dot-mode frame."""
        frame = {
            "Submit": [{
                "Type": "frame",
                "Key": key,
                "Frame": {
                    "Position": position,
                    "DotPoints": [
                        {"Index": int(i), "Intensity": int(v)}
                        for i, v in sorted(points.items())
                    ],
                    "DurationMillis": int(duration_ms),
                },
            }]
        }
        payload = json.dumps(frame, separators=(",", ":"))
        if self.dry_run:
            self.log("[dry-run] %s" % payload)
            return True
        if not self.ensure_connected():
            return False
        try:
            self.ws.send_text(payload)
            return True
        except (OSError, WebSocketError) as exc:
            self.log("[player] send failed: %s" % exc)
            self.ws.close()
            self.ws = None
            return False

    def submit(self, frame):
        """Submit an effect frame given in TactSuit Pro motor space."""
        grid_points = pro_map.translate(self.mapping, frame.position, frame.points)
        return self.submit_dot(frame.key, frame.position, grid_points,
                               frame.duration_ms)

    def submit_pro_dot(self, key, position, pro_points, duration_ms):
        """Submit raw points given in TactSuit Pro motor space.

        The single Pro-space entry point for callers that hold Pro
        motor indices (the web console's motor test): translation to
        the SDK1 grid happens here, with the effect frames, so no
        caller re-implements it.
        """
        grid_points = pro_map.translate(self.mapping, position, pro_points)
        return self.submit_dot(key, position, grid_points, duration_ms)

    def turn_off_all(self, keys_positions):
        for key, position in keys_positions:
            self.submit_dot(key, position, {i: 0 for i in range(20)}, 30)

    def close(self):
        if self.ws is not None:
            self.ws.close()
            self.ws = None
