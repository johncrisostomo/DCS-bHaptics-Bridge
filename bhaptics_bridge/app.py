"""Main loop: telemetry in, effects out, frames to bHaptics Player."""

import argparse
import sys
import threading
import time

from .config import load_config
from .effects import EffectEngine
from .layout import ProLayout
from .player import BhapticsPlayer
from .telemetry import TelemetryReceiver, parse_packet
from .web import WebConsole

TICK = 0.05           # 20 Hz main loop
SESSION_TIMEOUT = 1.5  # seconds without an aircraft before the session ends


def _submit_all(player, frames, lock=None):
    if lock is None:
        for frame in frames:
            player.submit(frame)
    else:
        with lock:
            for frame in frames:
                player.submit(frame)


def run_test(cfg, dry_run):
    """Fire the suit check once, without DCS. For bench testing."""
    player = BhapticsPlayer(cfg, dry_run=dry_run)
    engine = EffectEngine(cfg, ProLayout())
    dummy = parse_packet(
        b"DBB1;ac=1;name=SUIT_CHECK;gy=1.0;gear=1.0;wow1=1;wow4=1;wow6=1;tas=0"
    )
    if not player.ensure_connected():
        print("[test] could not connect to bHaptics Player "
              "(is it running?). Use --dry-run to test without it.")
        return 1
    print("[test] firing suit check...")
    now = time.monotonic()
    engine.start_session(now)
    deadline = now + 3.5
    while time.monotonic() < deadline:
        tick_now = time.monotonic()
        _submit_all(player, engine.update(dummy, tick_now))
        player.poll()
        time.sleep(TICK)
    _submit_all(player, engine.end_session())
    player.close()
    print("[test] done. You should have felt a wave down the front, "
          "then the back, then the whole vest.")
    return 0


def run(cfg, dry_run, web=False):
    player = BhapticsPlayer(cfg, dry_run=dry_run)
    receiver = TelemetryReceiver(cfg)
    engine = EffectEngine(cfg, ProLayout())

    print("DCS bHaptics Bridge")
    print("  telemetry: udp %s:%d" % (cfg.udp_host, cfg.udp_port))
    print("  player:    ws %s:%d (%s)" % (cfg.player_host, cfg.player_port,
                                          "dry-run" if dry_run else cfg.app_name))

    console = None
    submit_lock = None
    if web:
        submit_lock = threading.Lock()
        console = WebConsole(cfg, player, submit_lock=submit_lock).start()
    print("  waiting for DCS telemetry... (Ctrl+C to stop)")

    in_session = False
    last_aircraft = 0.0
    try:
        while True:
            now = time.monotonic()
            player.poll()
            tel = receiver.poll()
            if tel is not None and tel.ac:
                last_aircraft = now
                if not in_session:
                    in_session = True
                    print("[bridge] aircraft: %s - suit check" % (tel.name or "?"))
                    engine.start_session(now)
                _submit_all(player, engine.update(tel, now), submit_lock)
            elif in_session and now - last_aircraft > SESSION_TIMEOUT:
                in_session = False
                print("[bridge] session ended")
                _submit_all(player, engine.end_session(), submit_lock)
            time.sleep(TICK)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if in_session:
            _submit_all(player, engine.end_session(), submit_lock)
        if console is not None:
            console.stop()
        receiver.close()
        player.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="bhaptics_bridge",
        description="DCS World -> bHaptics TactSuit bridge")
    parser.add_argument("--config", default=None,
                        help="path to config.ini (default: project root)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print frames instead of sending them to Player")
    parser.add_argument("--test", action="store_true",
                        help="fire the suit check once and exit (no DCS needed)")
    parser.add_argument("--web", action="store_true",
                        help="also serve the web console (device view, "
                             "motor test, effect lab) on the LAN")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.test:
        return run_test(cfg, args.dry_run)
    return run(cfg, args.dry_run, web=args.web)


if __name__ == "__main__":
    sys.exit(main())
