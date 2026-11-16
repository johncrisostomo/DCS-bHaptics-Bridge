"""DCS telemetry: parses the packets sent by the Lua export script and
receives them over UDP.

Packet format (one datagram, semicolon-separated key=value pairs):

    DBB1;ac=1;name=FA-18C_hornet;t=12.34;gx=...;gy=...;...

ac=0 is a heartbeat meaning "DCS is running but no aircraft is active".
"""

import socket
import time

_FLOAT_KEYS = (
    "t", "gx", "gy", "gz", "ias", "tas", "agl", "asl", "vv", "aoa", "mach",
    "rpm1", "rpm2", "fuel", "gear", "flaps", "sbrake", "canopy",
    "wow1", "wow4", "wow6", "ab1", "ab2",
)
_INT_KEYS = ("shells", "stn", "chaff", "flare")


class Telemetry:
    def __init__(self):
        self.ac = False
        self.name = ""
        for key in _FLOAT_KEYS:
            setattr(self, key, 0.0)
        for key in _INT_KEYS:
            setattr(self, key, 0)
        self.sta = []
        self.warn = frozenset()
        self.rx_time = 0.0

    @property
    def wow_max(self):
        return max(self.wow1, self.wow4, self.wow6)

    @property
    def on_ground(self):
        return self.wow_max >= 0.05


def parse_packet(data):
    """Parse one datagram. Returns a Telemetry, or None if malformed."""
    try:
        text = data.decode("ascii", "strict").strip()
    except (UnicodeDecodeError, AttributeError):
        return None
    parts = text.split(";")
    if not parts or parts[0] != "DBB1":
        return None
    tel = Telemetry()
    tel.rx_time = time.monotonic()
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, raw = part.partition("=")
        try:
            if key == "ac":
                tel.ac = raw == "1"
            elif key == "name":
                tel.name = raw
            elif key in _FLOAT_KEYS:
                setattr(tel, key, float(raw))
            elif key in _INT_KEYS:
                setattr(tel, key, int(float(raw)))
            elif key == "sta":
                tel.sta = [int(x) for x in raw.split(",") if x != ""]
            elif key == "warn":
                tel.warn = frozenset() if raw in ("", "-") else frozenset(raw.split(","))
        except ValueError:
            continue
    return tel


class TelemetryReceiver:
    def __init__(self, cfg):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((cfg.udp_host, cfg.udp_port))
        self.sock.setblocking(False)
        self.latest = None
        self.last_rx = 0.0

    def poll(self):
        """Drain pending datagrams. Returns the newest Telemetry or None."""
        newest = None
        while True:
            try:
                data, _addr = self.sock.recvfrom(65535)
            except BlockingIOError:
                break
            except OSError:
                break
            tel = parse_packet(data)
            if tel is not None:
                newest = tel
        if newest is not None:
            self.latest = newest
            self.last_rx = newest.rx_time
        return newest

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
