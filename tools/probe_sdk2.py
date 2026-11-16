#!/usr/bin/env python3
"""SDK2 exploration probe - fail-fast experiments against bHaptics Player.

Run this on the machine where bHaptics Player runs, with Player open.
It answers, in order:

  1. Does the SDK2 stack work with empty application identifiers,
     the way the bridge initializes it?
  2. Does play_dot reach the suit - i.e. can effects stay code-defined
     via direct motor control, without portal-deployed events?
  3. What does the device info report, and does it change when the
     vest is switched off and on? (--listen)
  4. What does Player push, unsolicited, on the raw v4 websocket?
     (--raw)

Modes:
    python tools\\probe_sdk2.py --lib [--listen 30] [--no-dot]
        Drive the official bhaptics_python package
        (pip install bhaptics-python). Init, report player/device
        state, fire ONE front motor (index 0) at 70 for 600 ms unless
        --no-dot, then optionally poll both device-state sources
        (the device info list and the direct is-connected query)
        once a second while you toggle the vest.
    python tools\\probe_sdk2.py --raw [--seconds 25]
        Connect a plain websocket to the SDK2 endpoint with empty
        application identifiers and print every message Player
        sends. Sends nothing beyond the handshake.
"""

import argparse
import asyncio
import inspect
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bhaptics_bridge.wsclient import WebSocketClient, WebSocketError  # noqa: E402

PLAYER_HOST = "127.0.0.1"
PLAYER_PORT = 15881


async def _call(fn, *args):
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


def lib_mode(args):
    try:
        import bhaptics_python as bp
    except ImportError:
        print("bhaptics_python is not installed in this Python.")
        print("Install it with:  pip install bhaptics-python")
        return 2

    async def main():
        print("is_bhaptics_player_running ->",
              await _call(bp.is_bhaptics_player_running))
        try:
            ok = await asyncio.wait_for(
                _call(bp.registry_and_initialize, "", "", ""), 25)
        except asyncio.TimeoutError:
            print("registry_and_initialize TIMED OUT after 25 s")
            return
        print("registry_and_initialize ->", ok)
        print("is_connected ->", await _call(bp.is_connected))
        print("get_device_info_json ->",
              await _call(bp.get_device_info_json))
        try:
            print("is_bhaptics_device_connected(0) ->",
                  await _call(bp.is_bhaptics_device_connected, 0))
        except TypeError:
            print("is_bhaptics_device_connected() ->",
                  await _call(bp.is_bhaptics_device_connected))
        print("ping_all ->", await _call(bp.ping_all))
        if not args.no_dot:
            values = [0] * 32
            values[0] = 70  # front motor 0: top row, wearer's left
            request_id = await _call(bp.play_dot, 0, 600, values)
            print("play_dot(0, 600, motor0=70) -> request", request_id)
            print("(front top-left motor should have buzzed for 0.6 s)")
            await asyncio.sleep(1.0)
            await _call(bp.stop_all)
        if args.listen:
            print("polling BOTH device sources for %d s - toggle the "
                  "vest off and on now" % args.listen)
            started = time.monotonic()
            deadline = started + args.listen
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - started
                try:
                    query = await _call(
                        bp.is_bhaptics_device_connected, 0)
                except TypeError:
                    query = await _call(bp.is_bhaptics_device_connected)
                except Exception as exc:  # the error is data too
                    query = "raised: %r" % (exc,)
                info = await _call(bp.get_device_info_json)
                print("  [t+%4.1fs] is_connected_query=%r device_info=%s"
                      % (elapsed, query, info))
                await asyncio.sleep(1)
        await _call(bp.close)

    asyncio.run(main())
    return 0


def raw_mode(args):
    query = urllib.parse.urlencode({
        "workspace_id": "",
        "api_key": "",
        "version": args.version,
    })
    path = "/v4/feedback?" + query
    print("connecting: ws://%s:%d%s" % (PLAYER_HOST, PLAYER_PORT, path))
    ws = WebSocketClient(PLAYER_HOST, PLAYER_PORT, path)
    try:
        ws.connect(timeout=5.0)
    except (OSError, WebSocketError) as exc:
        print("connect failed:", exc)
        return 1
    print("connected; listening %d s (sending nothing)..." % args.seconds)
    deadline = time.monotonic() + args.seconds
    count = 0
    try:
        while time.monotonic() < deadline:
            for payload in ws.poll():
                count += 1
                print("[v4] %r" % (payload,))
            time.sleep(0.05)
    except WebSocketError as exc:
        print("connection ended early:", exc)
    ws.close()
    print("done; %d message(s) received" % count)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lib", action="store_true",
                        help="drive the official bhaptics_python package")
    parser.add_argument("--raw", action="store_true",
                        help="listen on the raw v4 websocket, send nothing")
    parser.add_argument("--listen", type=int, default=0,
                        help="--lib: poll device info for N seconds")
    parser.add_argument("--no-dot", action="store_true",
                        help="--lib: skip the single-motor play_dot test")
    parser.add_argument("--seconds", type=int, default=25,
                        help="--raw: listen duration (default 25)")
    parser.add_argument("--version", default="0.2.2",
                        help="--raw: version query value (default 0.2.2)")
    args = parser.parse_args()
    if args.lib:
        return lib_mode(args)
    if args.raw:
        return raw_mode(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
