"""Probe: does bHaptics Player report vest connection state anywhere
a client can read it?

Background: the bridge speaks the SDK1 protocol on
ws://127.0.0.1:15881/v2/feedbacks, which carries haptic frames only -
no device status. bHaptics' current SDKs (SDK2) expose device status
(is_bhaptics_device_connected / getConnectedDevices) and their client
core connects to ws://127.0.0.1:15881/v4/feedback and receives
"ServerDevices" messages describing each device (address, battery,
connected, paired, position). That endpoint is documented as taking
Developer Portal credentials (workspace_id, api_key).

This probe answers two empirical questions, sending nothing but the
connection handshakes themselves:

1. Does Player send anything (device status included) on the v2
   socket the bridge already uses?
2. Does Player accept a v4 connection with empty credentials, and
   does it push ServerDevices messages without an authenticated
   session?

Run it with Player up, then turn the vest off and back on while it
listens. Whatever Player sends is printed verbatim.

    python tools\\probe_device_status.py [seconds]
"""

import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bhaptics_bridge.config import load_config
from bhaptics_bridge.wsclient import WebSocketClient, WebSocketError


def main(argv):
    seconds = int(argv[1]) if len(argv) > 1 else 25
    cfg = load_config(None)

    v2_query = urllib.parse.urlencode({
        "appId": cfg.app_id, "appName": cfg.app_name})
    clients = []
    for label, path in (
        ("v2", "/v2/feedbacks?" + v2_query),
        ("v4", "/v4/feedback?workspace_id=&api_key=&version=2.0.4"),
    ):
        client = WebSocketClient(cfg.player_host, cfg.player_port, path)
        try:
            client.connect()
            print("[%s] connected to %s" % (label, path.split("?")[0]))
            clients.append((label, client))
        except (OSError, WebSocketError) as exc:
            print("[%s] could not connect: %s" % (label, exc))

    if not clients:
        print("Neither socket connected. Is bHaptics Player running?")
        return 1

    print("Listening for %d s. Turn the vest OFF, wait a few seconds, "
          "then turn it back ON." % seconds)
    deadline = time.monotonic() + seconds
    total = 0
    # Poll both sockets in one loop so messages interleave in real time.
    live = list(clients)
    while time.monotonic() < deadline and live:
        still_live = []
        for label, client in live:
            try:
                messages = client.poll()
            except WebSocketError as exc:
                print("[%s] connection ended: %s" % (label, exc))
                continue
            still_live.append((label, client))
            for payload in messages:
                total += 1
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", "replace")
                print("[%s] %s" % (label, payload[:2000]))
        live = still_live
        time.sleep(0.05)

    for _label, client in clients:
        client.close()
    print("Done. %d message(s) received." % total)
    if total == 0:
        print("Player sent nothing on either socket while listening.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
