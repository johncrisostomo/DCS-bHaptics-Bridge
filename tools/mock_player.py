"""A fake bHaptics Player server, for development without the real thing.

Listens on the Player websocket port and prints every frame the bridge
sends it, so you can watch the effect engine work. Standard library only.

    python tools/mock_player.py [--port 15881]
"""

import argparse
import base64
import hashlib
import json
import socket
import struct
import threading

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _Reader:
    def __init__(self, conn):
        self.conn = conn
        self.buf = bytearray()

    def read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.conn.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_frame(self):
        head = self.read_exact(2)
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.read_exact(8))[0]
        mask = self.read_exact(4) if masked else None
        payload = self.read_exact(length) if length else b""
        if mask is not None:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload


def _send_frame(conn, opcode, payload):
    header = bytearray([0x80 | opcode])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) <= 0xFFFF:
        header.append(126)
        header += struct.pack(">H", len(payload))
    else:
        header.append(127)
        header += struct.pack(">Q", len(payload))
    conn.sendall(bytes(header) + payload)


def _handle_client(conn, sink, log):
    conn.settimeout(30)
    reader = _Reader(conn)
    # HTTP upgrade request: read until end of headers.
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = conn.recv(4096)
        if not chunk:
            return
        raw += chunk
    head, _, rest = bytes(raw).partition(b"\r\n\r\n")
    reader.buf = bytearray(rest)
    key = None
    for line in head.decode("latin-1", "replace").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    if key is None:
        return
    accept = base64.b64encode(
        hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
    conn.sendall((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n" % accept
    ).encode("ascii"))
    if log:
        log("[mock-player] client connected: %s" % head.split(b"\r\n", 1)[0].decode("latin-1"))
    while True:
        try:
            opcode, payload = reader.read_frame()
        except (ConnectionError, OSError, socket.timeout):
            return
        if opcode == 0x8:
            return
        if opcode == 0x9:
            _send_frame(conn, 0xA, payload)
            continue
        if opcode in (0x1, 0x2):
            try:
                message = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if sink is not None:
                sink.append(message)
            if log:
                for item in message.get("Submit", []):
                    frame = item.get("Frame", {})
                    log("[mock-player] key=%s pos=%s points=%d duration=%s" % (
                        item.get("Key"), frame.get("Position"),
                        len(frame.get("DotPoints", [])), frame.get("DurationMillis")))


def run_server(host, port, sink=None, stop_event=None, log=print):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    srv.settimeout(0.25)
    if log:
        log("[mock-player] listening on %s:%d" % (host, port))
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            try:
                _handle_client(conn, sink, log)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
    finally:
        srv.close()


def main():
    parser = argparse.ArgumentParser(description="Fake bHaptics Player server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15881)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
