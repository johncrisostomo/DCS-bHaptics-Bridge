"""The effect engine: telemetry in, vest frames out.

Two kinds of effects:
- Sustained: tied to a continuing state (G load, burner, stall, gun).
  Re-submitted on a short cadence while the state holds, and zeroed
  when it ends.
- Events: one-shot or short scheduled patterns (gear lock, touchdown,
  weapon release, countermeasures, damage).

Detection is state-change based because the DCS export environment has
no event callbacks: a weapon release is a station count dropping, the
gun firing is the shell count dropping, damage is a new failure flag on
the warning panel, a touchdown is strut compression arriving after a
period airborne with a sink rate.
"""

import random
from collections import namedtuple

from .layout import FRONT, BACK

Frame = namedtuple("Frame", ["key", "position", "points", "duration_ms"])

_ALL_POINTS = list(range(16))  # TactSuit Pro motors per side


class EffectEngine:
    def __init__(self, cfg, layout):
        self.cfg = cfg
        self.layout = layout
        self.prev = None
        self.pattern = []        # [(due_time, Frame)]
        self.active = {}         # sustain key -> set of positions in use
        self.sustain_next = {}   # sustain key -> next emit time
        self.gear_transit = False
        self.gear_next_thump = 0.0
        self.gear_side = False
        self.last_gun_time = -1e9
        self.last_cm_time = -1e9
        self.taxi_next = 0.0
        self.airborne_since = None
        self.in_session = False

    # -- helpers ---------------------------------------------------------
    def _scale(self, value):
        return max(0, min(100, int(round(value * self.cfg.master_scale))))

    def _frame(self, key, position, indices, intensity, duration_ms):
        iv = self._scale(intensity)
        points = {i: iv for i in indices} if iv > 0 else {}
        return Frame(key, position, points, duration_ms)

    def _frame_points(self, key, position, points, duration_ms):
        scaled = {}
        for i, v in points.items():
            sv = self._scale(v)
            if sv > 0:
                scaled[i] = sv
        return Frame(key, position, scaled, duration_ms)

    def _schedule(self, delay, frame, now):
        self.pattern.append((now + delay, frame))

    def _sustain(self, key, frames, now, period=0.12):
        """Emit sustained frames on cadence; zero them when they stop."""
        if frames:
            self.active[key] = {f.position for f in frames}
            if now >= self.sustain_next.get(key, 0.0):
                self.sustain_next[key] = now + period
                return frames
            return []
        if key in self.active:
            positions = self.active.pop(key)
            return [Frame(key, pos, {i: 0 for i in _ALL_POINTS}, 30) for pos in positions]
        return []

    # -- session ---------------------------------------------------------
    def start_session(self, now):
        """Suit check: a wave down the front, then the back, then all on."""
        self.in_session = True
        self.prev = None
        self.pattern = []
        layout = self.layout
        t = 0.0
        for row in range(4):
            self._schedule(t, self._frame("check", FRONT, layout.rows([row]), 75, 140), now)
            t += 0.15
        for row in range(4):
            self._schedule(t, self._frame("check", BACK, layout.rows([row]), 75, 140), now)
            t += 0.15
        self._schedule(t + 0.1, self._frame("check", FRONT, layout.all(), 60, 220), now)
        self._schedule(t + 0.1, self._frame("check", BACK, layout.all(), 60, 220), now)

    def end_session(self):
        self.in_session = False
        frames = []
        for key, positions in self.active.items():
            for pos in positions:
                frames.append(Frame(key, pos, {i: 0 for i in _ALL_POINTS}, 30))
        self.active = {}
        self.pattern = []
        self.prev = None
        self.airborne_since = None
        return frames

    # -- main update -------------------------------------------------------
    def update(self, cur, now):
        out = []
        due = [f for (t, f) in self.pattern if t <= now]
        self.pattern = [(t, f) for (t, f) in self.pattern if t > now]
        out.extend(due)

        prev = self.prev
        cfg = self.cfg
        layout = self.layout

        # Sustained: G load. Positive G starts low and climbs the vest as
        # the pull builds: the two lower rows from g_onset, row 1 from
        # ~45% of the range, the shoulder row from ~72%. Ordinary
        # maneuvering (below g_onset) stays silent. Negative G pulls high.
        if cfg.effect_g:
            frames = None
            if cur.gy >= cfg.g_onset:
                span = max(0.1, cfg.g_full - cfg.g_onset)
                frac = min(1.0, (cur.gy - cfg.g_onset) / span)
                points = {i: 25 + 45 * frac for i in layout.lower()}
                g_mid = cfg.g_onset + 0.45 * span
                if cur.gy >= g_mid:
                    mid_i = 25 + 60 * min(1.0, (cur.gy - g_mid) / (cfg.g_full - g_mid))
                    points.update({i: mid_i for i in layout.rows([1])})
                g_high = cfg.g_onset + 0.72 * span
                if cur.gy >= g_high:
                    high_i = 30 + 70 * min(1.0, (cur.gy - g_high) / (cfg.g_full - g_high))
                    points.update({i: high_i for i in layout.rows([0])})
                frames = [
                    self._frame_points("g", FRONT, points, 160),
                    self._frame_points("g", BACK, points, 160),
                ]
            elif cur.gy <= cfg.neg_g_onset:
                intensity = 30 + (cfg.neg_g_onset - cur.gy) * 50.0
                frames = [
                    self._frame("g", FRONT, layout.chest(), intensity, 160),
                    self._frame("g", BACK, layout.chest(), intensity, 160),
                ]
            out += self._sustain("g", frames, now)

        # Sustained: afterburner rumble, low back.
        if cfg.effect_burner:
            frames = None
            if cur.ab1 > 0.5 or cur.ab2 > 0.5:
                frames = [self._frame("ab", BACK, layout.rows([2, 3]),
                                      38 + random.randint(-6, 6), 160)]
            out += self._sustain("ab", frames, now)

        # Sustained: stall buffet, whole vest shudder.
        if cfg.effect_stall:
            frames = None
            if not cur.on_ground and cur.agl > 3 and cur.aoa >= cfg.stall_aoa:
                frames = [
                    self._frame_points("stall", FRONT,
                                       {i: random.randint(14, 30) for i in layout.all()}, 110),
                    self._frame_points("stall", BACK,
                                       {i: random.randint(14, 30) for i in layout.all()}, 110),
                ]
            out += self._sustain("stall", frames, now, period=0.10)

        # Gun: shell count dropping means the cannon is firing.
        if prev is not None and cur.shells < prev.shells:
            self.last_gun_time = now
        if cfg.effect_gun:
            frames = None
            if now - self.last_gun_time < 0.18:
                gun_points = {i: 75 for i in layout.center([0])}
                gun_points.update({i: 40 for i in layout.center([1])})
                frames = [self._frame_points("gun", FRONT, gun_points, 110)]
            out += self._sustain("gun", frames, now, period=0.11)

        # Gear transit thumps, and one solid thump when it locks.
        if cfg.effect_gear and prev is not None:
            if 0.02 < cur.gear < 0.98:
                self.gear_transit = True
                if now >= self.gear_next_thump:
                    self.gear_next_thump = now + 0.28
                    pos = FRONT if self.gear_side else BACK
                    self.gear_side = not self.gear_side
                    out.append(self._frame("gear", pos, layout.lower(), 65, 120))
            elif self.gear_transit:
                self.gear_transit = False
                out.append(self._frame("gearend", FRONT, layout.lower(), 95, 180))
                out.append(self._frame("gearend", BACK, layout.lower(), 95, 180))

        # Touchdown: struts compress after real time airborne; the thump
        # scales with the sink rate of the last airborne sample.
        if cur.wow_max < 0.05:
            if self.airborne_since is None:
                self.airborne_since = now
        else:
            if (cfg.effect_touchdown and prev is not None
                    and self.airborne_since is not None
                    and now - self.airborne_since > 0.5
                    and prev.wow_max < 0.05 and prev.vv <= -0.8 and prev.agl <= 25):
                intensity = max(45, min(100, 45 + (-prev.vv - 1.0) * 11))
                out.append(self._frame("touchdown", FRONT, layout.all(), intensity, 260))
                out.append(self._frame("touchdown", BACK, layout.all(), intensity, 260))
            self.airborne_since = None

        # Weapon release: total station count dropped.
        if cfg.effect_release and prev is not None and cur.stn < prev.stn:
            out.append(self._frame("release", FRONT, layout.mid(), 90, 170))
            out.append(self._frame("release", BACK, layout.mid(), 90, 170))

        # Countermeasures: chaff or flare count dropped.
        if (cfg.effect_cm and prev is not None
                and (cur.chaff < prev.chaff or cur.flare < prev.flare)
                and now - self.last_cm_time > 0.25):
            self.last_cm_time = now
            out.append(self._frame("cm", FRONT, layout.shoulders(), 55, 110))
            out.append(self._frame("cm", BACK, layout.shoulders(), 55, 110))

        # Damage: a new failure flag on the warning panel.
        if cfg.effect_damage and prev is not None:
            new_warns = (cur.warn - prev.warn) - {"CanopyOpen"}
            if new_warns:
                for k in range(3):
                    self._schedule(k * 0.3, self._frame("damage", BACK, layout.all(), 100, 200), now)

        # Taxi bumps: vertical jolts while rolling on the ground.
        if cfg.effect_taxi and cur.on_ground and cur.tas > 3:
            dev = abs(cur.gy - 1.0)
            if dev > 0.10 and now >= self.taxi_next:
                self.taxi_next = now + 0.15
                intensity = max(20, min(60, dev * 220))
                out.append(self._frame("taxi", FRONT, layout.lower(), intensity, 90))
                out.append(self._frame("taxi", BACK, layout.lower(), intensity, 90))

        self.prev = cur
        return out
