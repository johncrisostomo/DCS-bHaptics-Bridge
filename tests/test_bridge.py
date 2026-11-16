"""Tests for the DCS bHaptics Bridge.

Run from the project root:  python -m unittest discover tests
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from bhaptics_bridge.config import Config
from bhaptics_bridge.effects import EffectEngine
from bhaptics_bridge.layout import VestLayout, FRONT, BACK
from bhaptics_bridge.player import BhapticsPlayer
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


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = EffectEngine(Config(), VestLayout())
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
            engine = EffectEngine(Config(), VestLayout())
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

    def test_g_tiers_climb_the_vest(self):
        base = dict(wow1=0, wow4=0, wow6=0, agl=500)

        def g_indices(gy):
            # The sustain cadence re-emits every 0.12 s; step past it.
            for _ in range(5):
                frames = self.feed(make_tel(gy=gy, **base))
                g = frames_with(frames, "g")
                if g:
                    return set(g[0].points)
            self.fail("no g frame at gy=%s" % gy)

        # Ordinary maneuvering below onset: silent.
        frames = self.feed(make_tel(gy=1.5, **base))
        self.assertFalse(frames_with(frames, "g"))
        # Moderate pull: lower rows only (indices 12-19).
        idx = g_indices(3.0)
        self.assertTrue(idx)
        self.assertTrue(all(i >= 12 for i in idx))
        # Hard pull: the shoulders (row 0, indices 0-3) have joined.
        idx = g_indices(5.5)
        self.assertTrue(any(i < 4 for i in idx))
        self.assertTrue(any(i >= 12 for i in idx))

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
        self.assertTrue(all(v > 30 for v in g[0].points.values()))
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
            engine = EffectEngine(cfg, VestLayout())
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


if __name__ == "__main__":
    unittest.main()
