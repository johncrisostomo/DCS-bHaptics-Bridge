#!/usr/bin/env python3
"""SDK2 exploration probe - fail-fast experiments against bHaptics Player.

Run this on the machine where bHaptics Player runs, with Player open.
It answers, in order:

  1. Does the SDK2 stack work with NO portal credentials at all?
     (BHAPTICS_APP_ID / BHAPTICS_API_KEY left empty)
  2. Does play_dot reach the suit - i.e. can effects stay code-defined
     via direct motor control, without portal-deployed events?
  3. What does the device info report, and does it change when the
     vest is switched off and on? (--listen)
  4. What does Player push, unsolicited, on the raw v4 websocket?
     (--raw)

Credentials come from the environment only - never from arguments
(they would land in shell history) and never from a file in this repo:

    set BHAPTICS_APP_ID=...        (Windows cmd; may be left empty)
    set BHAPTICS_API_KEY=...

Modes:
    python tools\\probe_sdk2.py --lib [--listen 30] [--no-dot]
        Drive the official bhaptics_python package
        (pip install bhaptics-python). Init, report player/device
        state, fire ONE front motor (index 0) at 70 for 600 ms unless
        --no-dot, then optionally poll device info while you toggle
        the vest.
    python tools\\probe_sdk2.py --raw [--seconds 25]
        Connect a plain websocket to the SDK2 endpoint with the same
        env credentials and print every message Player sends. Sends
        nothing beyond the handshake.
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


def _creds():
    return (os.environ.get("BHAPTICS_APP_ID", ""),
            os.environ.get("BHAPTICS_API_KEY", ""))


def lib_mode(args):
    try:
        import bhaptics_python as bp
    except ImportError:
        print("bhaptics_python is not installed in this Python.")
        print("Install it with:  pip install bhaptics-python")
        return 2
    app_id, api_key = _creds()
    print("app_id: %r   api_key: %s" % (
        app_id, "(set, %d chars)" % len(api_key) if api_key else "(empty)"))

    async def main():
        print("is_bhaptics_player_running ->",
              await _call(bp.is_bhaptics_player_running))
        try:
            ok = await asyncio.wait_for(
                _call(bp.registry_and_initialize, app_id, api_key, ""), 25)
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
            print("polling device info for %d s - toggle the vest "
                  "off and on now" % args.listen)
            last = None
            deadline = time.monotonic() + args.listen
            while time.monotonic() < deadline:
                info = await _call(bp.get_device_info_json)
                if info != last:
                    print("  device info:", info)
                    last = info
                await asyncio.sleep(2)
        try:
            messages = await _call(bp.get_haptic_messages,
                                   app_id, api_key, "0.2.2")
            text = str(messages)
            print("get_haptic_messages ->", text[:2000])
        except Exception as exc:  # exploratory: the error is data too
            print("get_haptic_messages raised:", repr(exc))
        await _call(bp.close)

    asyncio.run(main())
    return 0


def raw_mode(args):
    app_id, api_key = _creds()
    query = urllib.parse.urlencode({
        "workspace_id": app_id,
        "api_key": api_key,
        "version": args.version,
    })
    path = "/v4/feedback?" + query
    shown = path.replace(api_key, "***") if api_key else path
    print("connecting: ws://%s:%d%s" % (PLAYER_HOST, PLAYER_PORT, shown))
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
