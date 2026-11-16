"""Tests for the DCS bHaptics Bridge.

Run from the project root:  python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import urllib.request
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from bhaptics_bridge import pro_map
from bhaptics_bridge.app import make_player
from bhaptics_bridge.config import Config
from bhaptics_bridge.effects import EffectEngine, Frame
from bhaptics_bridge.layout import ProLayout, FRONT, BACK, absolute
from bhaptics_bridge.player import BhapticsPlayer
from bhaptics_bridge.player2 import (BhapticsPlayer2, parse_device_info,
                                    vest_status)
from bhaptics_bridge.web import WebConsole
from bhaptics_bridge.telemetry import Telemetry, parse_packet

import mock_player


def make_tel(**kw):
    tel = Telemetry()
    tel.ac = True
    tel.name = "TEST"
    tel.gy = 1.0
    tel.gear = 1.0
    for key, value in kw.items():
        setattr(tel, key, value)
    return tel


def frames_with(frames, key):
    return [f for f in frames if f.key == key]


class ParserTests(unittest.TestCase):
    def test_full_packet(self):
        tel = parse_packet(
            b"DBB1;ac=1;name=FA-18C_hornet;t=12.5;gx=0.1;gy=2.5;gz=-0.2;"
            b"ias=100.5;tas=120.0;agl=300.0;asl=900.0;vv=-3.25;aoa=0.1234;"
            b"mach=0.55;rpm1=85.0;rpm2=84.0;fuel=3100;gear=0.0;flaps=0.5;"
            b"sbrake=0;canopy=0;wow1=0;wow4=0;wow6=0;ab1=1.0;ab2=1.0;"
            b"shells=197;stn=4;sta=1,1,2,0;chaff=60;flare=58;"
            b"warn=MasterWarning,LeftEngineFailure")
        self.assertIsNotNone(tel)
        self.assertTrue(tel.ac)
        self.assertEqual(tel.name, "FA-18C_hornet")
        self.assertAlmostEqual(tel.gy, 2.5)
        self.assertAlmostEqual(tel.vv, -3.25)
        self.assertEqual(tel.shells, 197)
        self.assertEqual(tel.stn, 4)
        self.assertEqual(tel.sta, [1, 1, 2, 0])
        self.assertEqual(tel.warn, frozenset({"MasterWarning", "LeftEngineFailure"}))
        self.assertFalse(tel.on_ground)

    def test_heartbeat_and_garbage(self):
        tel = parse_packet(b"DBB1;ac=0")
        self.assertIsNotNone(tel)
        self.assertFalse(tel.ac)
        self.assertIsNone(parse_packet(b"nonsense"))
        self.assertIsNone(parse_packet(b""))


class LayoutTests(unittest.TestCase):
    def test_pro_regions_match_official_diagram(self):
        layout = ProLayout()
        # Per-side indices are row-major, row 0 at the top.
        self.assertEqual(layout.rows([0]), [0, 1, 2, 3])
        self.assertEqual(layout.rows([3]), [12, 13, 14, 15])
        self.assertEqual(layout.chest(), list(range(0, 8)))
        self.assertEqual(layout.lower(), list(range(8, 16)))
        # Official absolute numbering: front 0-15, back 16-31.
        self.assertEqual(absolute(FRONT, 15), 15)
        self.assertEqual(absolute(BACK, 0), 16)
        self.assertEqual(absolute(BACK, 15), 31)

    def test_seed_mapping_covers_every_pro_motor(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping = pro_map.load_mapping(root=tmp)
        for position in (FRONT, BACK):
            self.assertEqual(set(mapping[position]), set(range(16)))
            for grids in mapping[position].values():
                self.assertTrue(grids)
                self.assertTrue(all(0 <= g < 20 for g in grids))

    def test_translate_merges_shared_grid_points(self):
        mapping = {FRONT: {0: [0], 1: [0]}, BACK: {}}
        out = pro_map.translate(mapping, FRONT, {0: 30, 1: 80})
        self.assertEqual(out, {0: 80})
        # Zero intensities survive translation (stop frames rely on it).
        out = pro_map.translate(mapping, FRONT, {0: 0, 1: 0})
        self.assertEqual(out, {0: 0})

    def test_measured_mapping_overrides_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "pro_mapping.json"), "w") as fh:
                json.dump({"front": {"0": [19]}, "back": {}}, fh)
            mapping = pro_map.load_mapping(root=tmp)
        self.assertEqual(mapping[FRONT][0], [19])
        self.assertEqual(mapping[FRONT][1], [1])  # seed kept
        self.assertEqual(set(mapping[BACK]), set(range(16)))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = EffectEngine(Config(), ProLayout())
        self.engine.start_session(0.0)
        # Drain the suit-check pattern.
        t = 0.0
        dummy = make_tel(wow1=1, wow4=1, wow6=1)
        while t < 3.0:
            self.engine.update(dummy, t)
            t += 0.05
        self.now = 3.0

    def feed(self, tel, dt=0.05):
        self.now += dt
        return self.engine.update(tel, self.now)

    def test_gear_transit_and_lock(self):
        seen_gear = False
        for _ in range(12):
            frames = self.feed(make_tel(gear=0.5, wow1=0, wow4=0, wow6=0, agl=50))
            if frames_with(frames, "gear"):
                seen_gear = True
        self.assertTrue(seen_gear)
        frames = self.feed(make_tel(gear=1.0, wow1=0, wow4=0, wow6=0, agl=50))
        self.assertTrue(frames_with(frames, "gearend"))

    def test_touchdown_scales_with_sink_rate(self):
        def land(vv):
            engine = EffectEngine(Config(), ProLayout())
            engine.start_session(0.0)
            t = 0.0
            ground = make_tel(wow1=1, wow4=1, wow6=1)
            while t < 3.0:
                engine.update(ground, t)
                t += 0.05
            air = make_tel(wow1=0, wow4=0, wow6=0, vv=vv, agl=8)
            for _ in range(30):  # 1.5 s airborne
                t += 0.05
                engine.update(air, t)
            t += 0.05
            frames = engine.update(make_tel(wow1=1, wow4=1, wow6=1, vv=0, agl=0), t)
            td = frames_with(frames, "touchdown")
            self.assertTrue(td, "no touchdown frame for vv=%s" % vv)
            return max(td[0].points.values())

        soft = land(-1.2)
        firm = land(-5.0)
        self.assertGreater(firm, soft)
        self.assertLessEqual(firm, 100)

    def test_weapon_release_and_gun(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)
        self.feed(make_tel(stn=4, shells=200, **base))
        frames = self.feed(make_tel(stn=3, shells=200, **base))
        self.assertTrue(frames_with(frames, "release"))
        frames = self.feed(make_tel(stn=3, shells=185, **base))
        gun = frames_with(frames, "gun")
        self.assertTrue(gun)
        self.assertEqual(gun[0].position, FRONT)

    def test_gun_pulses_at_full_intensity(self):
        # The gun's signature is a rhythm at full intensity, not a
        # steady partial buzz: in a strafing dive the negative-G
        # effect runs the same chest motors at the same level, and
        # a steady frame there measures a zero signature over the
        # chest wash. Short frames on the sustain cadence pulse.
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)
        self.feed(make_tel(shells=200, **base))
        frames = self.feed(make_tel(shells=185, **base))
        gun = frames_with(frames, "gun")
        self.assertTrue(gun)
        self.assertEqual(gun[0].duration_ms, 60)
        self.assertEqual(max(gun[0].points.values()), 100)

    def test_negative_g_is_continuous(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)

        def chest_intensity(gy, feeds=6):
            best = 0
            for _ in range(feeds):
                frames = self.feed(make_tel(gy=gy, **base))
                for f in frames_with(frames, "g"):
                    if f.points:
                        best = max(best, max(f.points.values()))
            return best

        # Above the unload onset (0.85): silent - 1 G is neutral.
        self.assertEqual(chest_intensity(0.95), 0)
        # The feel builds continuously as the load drops; there
        # is no cliff at a threshold for it to chatter across.
        light = chest_intensity(0.5)
        deep = chest_intensity(-0.5)
        self.assertGreater(light, 0)
        self.assertGreater(deep, light)

    def test_g_climbs_the_vest_continuously(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)

        def g_indices(gy):
            # The sustain cadence re-emits every 0.12 s; step past it.
            for _ in range(5):
                frames = self.feed(make_tel(gy=gy, **base))
                g = frames_with(frames, "g")
                if g:
                    return set(g[0].points)
            self.fail("no g frame at gy=%s" % gy)

        # Below the onset the squeeze has not started: silent.
        frames = self.feed(make_tel(gy=1.1, **base))
        self.assertFalse(frames_with(frames, "g"))
        # Just above it the bottom row only, fading in - the
        # climb is continuous, not tiered switching.
        idx = g_indices(1.5)
        self.assertTrue(idx)
        self.assertTrue(all(i >= 12 for i in idx))
        # Moderate pull: the two lower rows (per-side indices 8-15).
        idx = g_indices(3.0)
        self.assertTrue(idx)
        self.assertTrue(all(i >= 8 for i in idx))
        # Hard pull: the shoulders (row 0, indices 0-3) have joined.
        idx = g_indices(5.5)
        self.assertTrue(any(i < 4 for i in idx))
        self.assertTrue(any(i >= 8 for i in idx))

    def test_g_is_silent_on_the_ground(self):
        # On the gear the load reading carries no feel, and a
        # getter that has not started returning reads gy = 0 -
        # inside the negative-G band. Neither may vibrate the
        # vest on the ramp.
        ground = dict(wow1=1, wow4=1, wow6=1, agl=0)
        for gy in (0.0, 1.0, 5.5):
            frames = self.feed(make_tel(gy=gy, **ground))
            self.assertFalse(frames_with(frames, "g"),
                             "g frame on the ground at gy=%s" % gy)

    def test_damage_pattern(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)
        self.feed(make_tel(warn=frozenset(), **base))
        self.feed(make_tel(warn=frozenset({"MasterWarning"}), **base))
        seen = 0
        for _ in range(20):
            frames = self.feed(make_tel(warn=frozenset({"MasterWarning"}), **base))
            seen += len(frames_with(frames, "damage"))
        self.assertEqual(seen, 3)

    def test_g_sustain_and_release(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)
        frames = self.feed(make_tel(gy=3.5, **base))
        g = frames_with(frames, "g")
        self.assertTrue(g)
        # Continuous model: the bottom row is near full at 3.5 G
        # while the next rows are still fading in from zero.
        self.assertGreater(max(g[0].points.values()), 80)
        self.assertTrue(all(v > 0 for v in g[0].points.values()))
        # Returning to 1 G zeroes the effect.
        frames = self.feed(make_tel(gy=3.5, **base))  # cadence: may be empty
        frames = self.feed(make_tel(gy=1.0, **base))
        zeroed = [f for f in frames_with(frames, "g")
                  if f.points and all(v == 0 for v in f.points.values())]
        self.assertTrue(zeroed)


class PlayerIntegrationTests(unittest.TestCase):
    def test_frames_reach_mock_player(self):
        sink = []
        stop = threading.Event()
        port = 15911
        server = threading.Thread(
            target=mock_player.run_server,
            args=("127.0.0.1", port, sink, stop, None),
            daemon=True)
        server.start()
        time.sleep(0.4)
        try:
            cfg = Config({"player_port": port})
            player = BhapticsPlayer(cfg)
            self.assertTrue(player.ensure_connected())
            engine = EffectEngine(cfg, ProLayout())
            now = time.monotonic()
            engine.start_session(now)
            dummy = make_tel(wow1=1, wow4=1, wow6=1)
            deadline = now + 3.0
            while time.monotonic() < deadline:
                for frame in engine.update(dummy, time.monotonic()):
                    player.submit(frame)
                player.poll()
                time.sleep(0.05)
            player.close()
        finally:
            stop.set()
            server.join(timeout=3)
        self.assertTrue(sink, "mock player received nothing")
        first = sink[0]["Submit"][0]
        self.assertEqual(first["Key"], "check")
        frame = first["Frame"]
        self.assertIn(frame["Position"], (FRONT, BACK))
        self.assertEqual(frame["DurationMillis"], 140)
        point = frame["DotPoints"][0]
        self.assertIn("Index", point)
        self.assertIn("Intensity", point)
        positions = {item["Submit"][0]["Frame"]["Position"] for item in sink}
        self.assertEqual(positions, {FRONT, BACK})


class _StubPlayer:
    """Records everything the web console sends to the Player."""

    def __init__(self):
        self.connected = True
        self.dot_calls = []   # (key, position, points, duration_ms)
        self.frames = []      # effect frames via submit()

    def submit_pro_dot(self, key, position, points, duration_ms):
        self.dot_calls.append((key, position, dict(points), duration_ms))
        return True

    def submit(self, frame):
        self.frames.append(frame)
        return True

    def ensure_connected(self):
        return True

    def poll(self):
        pass

    def close(self):
        pass


class _LazyPlayer(_StubPlayer):
    """Mimics the real client: not connected until ensure_connected()."""

    def __init__(self):
        super().__init__()
        self.connected = False
        self.ensure_calls = 0

    def ensure_connected(self):
        self.ensure_calls += 1
        self.connected = True
        return True


class WebTests(unittest.TestCase):
    def make_console(self):
        stub = _StubPlayer()
        cfg = Config({"web_host": "127.0.0.1", "web_port": 0})
        console = WebConsole(cfg, stub, log=lambda *a: None)
        self.addCleanup(console.stop)
        return console, stub

    def test_hold_submits_and_zeroes(self):
        console, stub = self.make_console()
        console.hold.start(FRONT, 5, 80)
        time.sleep(0.35)
        live = [c for c in stub.dot_calls
                if c[0] == "web-hold" and c[2].get(5) == 80]
        self.assertTrue(live, "hold never reached the player")
        self.assertEqual(console.hold.describe()["motor"], 5)
        console.hold.stop()
        time.sleep(0.05)
        zeroed = [c for c in stub.dot_calls
                  if c[0] == "web-hold" and c[2].get(5) == 0]
        self.assertTrue(zeroed, "hold stop never zeroed the motor")
        self.assertIsNone(console.hold.describe())

    def test_lab_drives_the_real_engine(self):
        console, stub = self.make_console()
        console.lab.set_state({"gy": 5.0})
        console.lab.action("gun_start")
        time.sleep(0.5)
        console.lab.action("gun_stop")
        state = console.lab.describe()
        self.assertLess(state["shells"], 570)
        g_frames = [f for f in stub.frames if f.key == "g"]
        self.assertTrue(g_frames, "lab G load produced no engine frames")
        gun_frames = [f for f in stub.frames if f.key == "gun"]
        self.assertTrue(gun_frames, "lab gun produced no engine frames")
        console.lab.action("release")
        self.assertEqual(console.lab.describe()["stn"], 3)

    def test_status_poll_establishes_player_connection(self):
        # Regression: the pill stayed red until the first motor click
        # because the client only connects when a frame is submitted.
        # Polling the status must establish the connection by itself.
        lazy = _LazyPlayer()
        cfg = Config({"web_host": "127.0.0.1", "web_port": 0})
        console = WebConsole(cfg, lazy, log=lambda *a: None)
        self.addCleanup(console.stop)
        self.assertFalse(lazy.connected)
        status = console.status()
        self.assertTrue(status["player_connected"])
        self.assertGreaterEqual(lazy.ensure_calls, 1)

    def test_status_vest_only_when_player_reports_it(self):
        console, stub = self.make_console()
        self.assertIsNone(console.status()["vest"])

        class VestStub(_StubPlayer):
            mapping_source = "direct (SDK2)"

            def device_status(self):
                return {"connected": True, "paired": True, "battery": 82}

        cfg = Config({"web_host": "127.0.0.1", "web_port": 0})
        console2 = WebConsole(cfg, VestStub(), log=lambda *a: None)
        self.addCleanup(console2.stop)
        vest = console2.status()["vest"]
        self.assertEqual(vest["battery"], 82)
        self.assertTrue(vest["connected"])
        self.assertEqual(console2.status()["mapping_source"],
                         "direct (SDK2)")

    def test_http_endpoints(self):
        console, stub = self.make_console()
        console.start()
        port = console.httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port

        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("TactSuit Pro", page)
        self.assertIn("Effect lab", page)
        self.assertIn("vestFront", page)
        self.assertIn("OUTLINE", page)

        with urllib.request.urlopen(base + "/api/status", timeout=5) as resp:
            status = json.loads(resp.read())
        self.assertEqual(status["device"], "TactSuit Pro")
        self.assertTrue(status["player_connected"])
        self.assertIn("lab", status)

        req = urllib.request.Request(
            base + "/api/hold", method="POST",
            data=json.dumps({"side": "back", "motor": 20,
                             "intensity": 60}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(json.loads(resp.read()), {"ok": True})
        self.assertEqual(console.hold.describe()["motor"], 20)


class TransportSelectionTests(unittest.TestCase):
    def test_default_transport_is_sdk2(self):
        player = make_player(Config({}), dry_run=True)
        self.addCleanup(player.close)
        self.assertIsInstance(player, BhapticsPlayer2)

    def test_sdk1_selects_the_legacy_client(self):
        player = make_player(Config({"transport": "sdk1"}), dry_run=True)
        self.addCleanup(player.close)
        self.assertIsInstance(player, BhapticsPlayer)


class _FakeBackend:
    """Async stand-in for _Sdk2Backend: records every call."""

    def __init__(self, init_result=True, device_info="[]",
                 vest_connected=True):
        self.init_result = init_result
        self.device_info = device_info
        self.vest_connected = vest_connected
        self.connected_query = True
        self.init_calls = []
        self.dot_calls = []   # (duration_ms, values)
        self.stopped = False
        self.closed = False

    async def initialize(self):
        self.init_calls.append(True)
        return self.init_result

    async def is_connected(self):
        return self.connected_query

    async def is_device_connected(self, position):
        return self.vest_connected

    async def play_dot(self, duration_ms, values):
        self.dot_calls.append((duration_ms, list(values)))
        return 1

    async def device_info_json(self):
        return self.device_info

    async def stop_all(self):
        self.stopped = True

    async def close(self):
        self.closed = True


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class Player2Tests(unittest.TestCase):
    def make_player(self, backend, **cfg_values):
        player = BhapticsPlayer2(Config(cfg_values),
                                 log=lambda *a: None, backend=backend)
        self.addCleanup(player.close)
        return player

    def test_frame_maps_to_32_values(self):
        backend = _FakeBackend()
        player = self.make_player(backend)
        self.assertTrue(player.ensure_connected())
        self.assertEqual(backend.init_calls, [True])

        player.submit(Frame("g", FRONT, {0: 80, 15: 40}, 60))
        player.submit(Frame("g", BACK, {0: 60}, 200))
        self.assertTrue(_wait_for(lambda: len(backend.dot_calls) >= 2))
        (dur1, values1), (dur2, values2) = backend.dot_calls[:2]
        self.assertEqual(len(values1), 32)
        self.assertEqual(values1[0], 80)    # front motor 0
        self.assertEqual(values1[15], 40)   # front motor 15
        self.assertEqual(sum(values1), 120)
        self.assertEqual(dur1, 100)         # floored at the SDK2 minimum
        # The second call is the composite: the first frame is
        # still live, so its motors are still present alongside
        # the back frame's (back motor 0 -> absolute 16).
        self.assertEqual(values2[0], 80)
        self.assertEqual(values2[16], 60)
        self.assertEqual(sum(values2), 180)
        self.assertGreater(dur2, 150)       # the 200 ms horizon

    def test_submit_pro_dot_is_direct(self):
        backend = _FakeBackend()
        player = self.make_player(backend)
        player.submit_pro_dot("web-hold", BACK, {15: 70}, 150)
        self.assertTrue(_wait_for(lambda: len(backend.dot_calls) >= 1))
        _dur, values = backend.dot_calls[0]
        self.assertEqual(values[31], 70)    # back motor 15 -> absolute 31
        self.assertEqual(sum(values), 70)

    def test_concurrent_frames_compose(self):
        # Regression for the masked gun: each play_dot sets the
        # whole vest, so submitting concurrent effects as separate
        # calls zeroed each other's motors. Frames now compose -
        # the merged call carries both effects at once.
        backend = _FakeBackend()
        player = self.make_player(backend)
        player.submit(Frame("gun", FRONT, {1: 75, 2: 75}, 110))
        player.submit(Frame("g", FRONT, {12: 35, 13: 35, 14: 35,
                                         15: 35}, 160))
        self.assertTrue(_wait_for(lambda: len(backend.dot_calls) >= 2))
        _dur, values = backend.dot_calls[-1]
        self.assertEqual(values[1], 75)     # gun motors survive
        self.assertEqual(values[2], 75)
        self.assertEqual(values[12], 35)    # alongside the G motors
        self.assertEqual(sum(values), 75 + 75 + 4 * 35)

    def test_contribution_expires_to_silence(self):
        backend = _FakeBackend()
        player = self.make_player(backend)
        player.submit(Frame("g", FRONT, {0: 80}, 100))
        self.assertTrue(_wait_for(lambda: len(backend.dot_calls) >= 1))

        def silenced():
            player.poll()
            return any(all(v == 0 for v in vals)
                       for _d, vals in backend.dot_calls[1:])
        self.assertTrue(_wait_for(silenced, timeout=3.0))

    def test_init_refused_means_no_playback(self):
        backend = _FakeBackend(init_result=False)
        player = self.make_player(backend)
        self.assertFalse(player.ensure_connected())
        self.assertFalse(player.connected)
        self.assertFalse(player.submit(Frame("g", FRONT, {0: 50}, 100)))
        time.sleep(0.1)
        self.assertEqual(backend.dot_calls, [])

    def test_device_status_parsing(self):
        raw = json.dumps([
            {"position": 3, "deviceName": "TactVisor",
             "connected": True, "paired": True, "battery": 50},
            {"position": 0, "deviceName": "TactSuit Pro",
             "connected": True, "paired": True, "battery": 82},
        ])
        devices = parse_device_info(raw)
        self.assertEqual(len(devices), 2)
        self.assertEqual(vest_status(devices),
                         {"connected": True, "paired": True,
                          "battery": 82})
        self.assertEqual(parse_device_info("not json"), [])
        self.assertEqual(parse_device_info(None), [])
        self.assertIsNone(vest_status([]))

    def test_device_status_flows_from_backend(self):
        raw = json.dumps([{"position": 0, "connected": False,
                           "paired": True, "battery": 41}])
        backend = _FakeBackend(device_info=raw)
        player = self.make_player(backend)
        # Before any report: a dict with connected=None, not None.
        self.assertIsNone(player.device_status()["connected"])
        self.assertTrue(player.ensure_connected())
        self.assertTrue(_wait_for(
            lambda: player.device_status()["battery"] == 41))
        status = player.device_status()
        # The device-info entry is the primary source: its flag
        # (False here) stands even though the fake's direct query
        # defaults to True.
        self.assertFalse(status["connected"])
        self.assertEqual(status["battery"], 41)

    def test_device_info_entry_is_primary(self):
        # A 45 s probe polling both SDK sources once a second
        # (2026-11-16) showed them agreeing at every step; where
        # they differ, the entry rules - it is also the only
        # source carrying battery and pairing.
        raw = json.dumps([{"position": 0, "connected": True,
                           "paired": True, "battery": 88}])
        backend = _FakeBackend(device_info=raw, vest_connected=False)
        player = self.make_player(backend)
        self.assertTrue(player.ensure_connected())
        self.assertTrue(_wait_for(
            lambda: player.device_status()["connected"] is True))
        self.assertEqual(player.device_status()["battery"], 88)

    def test_fetch_never_tears_down_the_session(self):
        # Regression for the frozen status: the fetch once let a
        # queried liveness answer clear the session flag, which
        # stopped polling and made the next submit re-initialise
        # the SDK (resetting its device tracking). Even with the
        # liveness query answering False throughout, the session
        # must stand, init must run exactly once, and the device
        # list must populate.
        raw = json.dumps([{"position": 0, "connected": True,
                           "paired": True, "battery": 82}])
        backend = _FakeBackend(device_info=raw)
        backend.connected_query = False
        player = self.make_player(backend)
        self.assertTrue(player.ensure_connected())
        self.assertTrue(_wait_for(
            lambda: player.device_status()["battery"] == 82))
        for _ in range(5):
            player._devices_at = 0.0   # make every poll due a fetch
            player.poll()
            self.assertTrue(player.ensure_connected())
        self.assertEqual(len(backend.init_calls), 1)
        self.assertTrue(player.connected)
        self.assertTrue(player.device_status()["connected"])

    def test_device_status_falls_back_to_is_connected(self):
        backend = _FakeBackend(device_info="[]", vest_connected=True)
        player = self.make_player(backend)
        self.assertTrue(player.ensure_connected())
        self.assertTrue(_wait_for(
            lambda: player.device_status()["connected"] is True))
        self.assertIsNone(player.device_status()["battery"])

    def test_vest_transitions_are_logged(self):
        lines = []
        raw = json.dumps([{"position": 0, "connected": True,
                           "paired": True, "battery": 82}])
        backend = _FakeBackend(device_info=raw)
        player = BhapticsPlayer2(Config({}), backend=backend,
                                 log=lines.append)
        self.addCleanup(player.close)
        self.assertTrue(player.ensure_connected())
        self.assertTrue(_wait_for(
            lambda: "[player2] vest: connected (battery 82%)" in lines))


if __name__ == "__main__":
    unittest.main()
