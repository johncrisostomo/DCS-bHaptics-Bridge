"""Flies a scripted flight profile into the bridge over UDP, so the
effects can be exercised without DCS.

Profile: idle on the ramp, takeoff roll (taxi bumps), liftoff, gear up,
burner climb, a 3.6 G pull, a stall, a weapon release, a gun burst,
warning-panel damage, gear down, a firm touchdown, rollout, a flare.

    python tools/fake_telemetry.py [--loop] [--port 41231]
"""

import argparse
import math
import socket
import time

# (time, {field: value}) — numeric fields interpolate linearly between
# keyframes; "warn" steps. wow is written to all three strut fields.
KEYFRAMES = [
    (0.0,  dict(tas=0, agl=0, vv=0, gy=1.0, aoa=0.02, gear=1.0, wow=1.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (4.0,  dict(tas=0, agl=0, vv=0, gy=1.0, aoa=0.02, gear=1.0, wow=1.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (8.0,  dict(tas=70, agl=0, vv=0, gy=1.1, aoa=0.03, gear=1.0, wow=1.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (9.0,  dict(tas=80, agl=15, vv=4, gy=1.2, aoa=0.10, gear=1.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (10.5, dict(tas=95, agl=80, vv=8, gy=1.1, aoa=0.08, gear=0.5, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (12.0, dict(tas=130, agl=300, vv=12, gy=1.0, aoa=0.06, gear=0.0, wow=0.0, ab=1,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (15.0, dict(tas=180, agl=900, vv=10, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=1,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (16.0, dict(tas=185, agl=1000, vv=2, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (18.0, dict(tas=185, agl=1000, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (20.0, dict(tas=175, agl=1000, vv=0, gy=3.6, aoa=0.18, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (22.0, dict(tas=170, agl=1000, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (23.0, dict(tas=120, agl=980, vv=-2, gy=0.8, aoa=0.40, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (25.0, dict(tas=150, agl=970, vv=0, gy=1.2, aoa=0.06, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (25.9, dict(tas=150, agl=970, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=4, chaff=60, flare=60, warn="-")),
    (26.1, dict(tas=150, agl=970, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=3, chaff=60, flare=60, warn="-")),
    (27.0, dict(tas=150, agl=970, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=200, stn=3, chaff=60, flare=60, warn="-")),
    (28.5, dict(tas=150, agl=970, vv=0, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60, warn="-")),
    (29.0, dict(tas=150, agl=960, vv=-1, gy=1.0, aoa=0.05, gear=0.0, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (31.0, dict(tas=110, agl=120, vv=-3.2, gy=1.0, aoa=0.08, gear=0.0, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (32.5, dict(tas=95, agl=60, vv=-3.2, gy=1.0, aoa=0.09, gear=0.5, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (34.0, dict(tas=85, agl=25, vv=-3.2, gy=1.0, aoa=0.10, gear=1.0, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (35.8, dict(tas=78, agl=1, vv=-3.2, gy=1.0, aoa=0.10, gear=1.0, wow=0.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (36.0, dict(tas=75, agl=0, vv=0, gy=1.3, aoa=0.08, gear=1.0, wow=1.0, ab=0,
                shells=120, stn=3, chaff=60, flare=60,
                warn="MasterWarning,RightEngineFailure")),
    (38.0, dict(tas=25, agl=0, vv=0, gy=1.0, aoa=0.02, gear=1.0, wow=1.0, ab=0,
                shells=120, stn=3, chaff=60, flare=58,
                warn="MasterWarning,RightEngineFailure")),
    (41.0, dict(tas=0, agl=0, vv=0, gy=1.0, aoa=0.02, gear=1.0, wow=1.0, ab=0,
                shells=120, stn=3, chaff=60, flare=58,
                warn="MasterWarning,RightEngineFailure")),
]
DURATION = KEYFRAMES[-1][0]
_NUMERIC = [k for k in KEYFRAMES[0][1] if k != "warn"]

_MARKS = [
    (0.0, "on the ramp"), (4.0, "takeoff roll"), (9.0, "liftoff"),
    (10.0, "gear up"), (12.0, "burner climb"), (19.0, "G pull"),
    (22.5, "stall"), (25.9, "weapon release"), (27.0, "gun burst"),
    (29.0, "damage"), (31.0, "gear down, final"), (35.9, "touchdown"),
    (37.5, "flare on rollout"),
]


def state_at(t):
    if t <= KEYFRAMES[0][0]:
        return dict(KEYFRAMES[0][1])
    for (t0, s0), (t1, s1) in zip(KEYFRAMES, KEYFRAMES[1:]):
        if t0 <= t <= t1:
            frac = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            state = {}
            for key in _NUMERIC:
                state[key] = s0[key] + (s1[key] - s0[key]) * frac
            state["warn"] = s1["warn"] if frac >= 1.0 else s0["warn"]
            return state
    return dict(KEYFRAMES[-1][1])


def packet(state, t):
    # Taxi-bump jitter while rolling on the ground.
    gy = state["gy"]
    if state["wow"] > 0 and state["tas"] > 3:
        gy += 0.13 * math.sin(t * 12.7) * math.sin(t * 3.1)
    sta = ",".join(["1"] * int(state["stn"]))
    return (
        "DBB1;ac=1;name=FAKE-18C;t=%.2f;gx=0;gy=%.3f;gz=0;ias=%.1f;tas=%.1f;"
        "agl=%.1f;asl=1000;vv=%.2f;aoa=%.4f;mach=0.5;rpm1=80;rpm2=80;fuel=3000;"
        "gear=%.3f;flaps=0.5;sbrake=0;canopy=0;wow1=%.2f;wow4=%.2f;wow6=%.2f;"
        "ab1=%.1f;ab2=%.1f;shells=%d;stn=%d;sta=%s;chaff=%d;flare=%d;warn=%s"
        % (t, gy, state["tas"] * 0.9, state["tas"], state["agl"], state["vv"],
           state["aoa"], state["gear"], state["wow"], state["wow"], state["wow"],
           state["ab"], state["ab"], int(state["shells"]), int(state["stn"]),
           sta, int(state["chaff"]), int(state["flare"]), state["warn"])
    ).encode("ascii")


def main():
    parser = argparse.ArgumentParser(description="Scripted telemetry feeder")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41231)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print("fake telemetry -> %s:%d (profile %.0fs%s)"
          % (args.host, args.port, DURATION, ", looping" if args.loop else ""))
    while True:
        start = time.monotonic()
        marks = list(_MARKS)
        while True:
            t = time.monotonic() - start
            if t > DURATION:
                break
            while marks and t >= marks[0][0]:
                print("  t=%4.1f  %s" % (marks.pop(0)))
            sock.sendto(packet(state_at(t), t), (args.host, args.port))
            time.sleep(0.05)
        if not args.loop:
            break
    sock.sendto(b"DBB1;ac=0", (args.host, args.port))
    print("done")


if __name__ == "__main__":
    main()
