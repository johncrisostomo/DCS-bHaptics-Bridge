"""A minimal WebSocket client (RFC 6455), standard library only.

Just enough to talk to bHaptics Player: connect with an HTTP upgrade,
send masked text frames, answer pings, and drain whatever the server
sends back. No dependencies, nothing to install, easy to audit.
"""

import base64
import os
import select
import socket
import struct

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    pass


class WebSocketClient:
    def __init__(self, host, port, path):
        self.host = host
        self.port = port
        self.path = path
        self.sock = None
        self._buf = bytearray()

    def connect(self, timeout=5.0):
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET {path} HTTP/1.1\r\n"
            "Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).format(path=self.path, host=self.host, port=self.port, key=key)
        sock.sendall(request.encode("ascii"))

        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            response += chunk
            if len(response) > 65536:
                raise WebSocketError("handshake response too large")
        head, _, rest = bytes(response).partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101 " not in status_line:
            raise WebSocketError("handshake refused: " + status_line)
        self._buf = bytearray(rest)
        sock.settimeout(0.2)
        self.sock = sock
        return True

    @property
    def connected(self):
        return self.sock is not None

    def _send_frame(self, opcode, payload):
        if self.sock is None:
            raise WebSocketError("not connected")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text):
        self._send_frame(0x1, text)

    def poll(self):
        """Drain incoming frames. Answers pings. Returns received text payloads.

        Raises WebSocketError if the connection is gone.
        """
        if self.sock is None:
            raise WebSocketError("not connected")
        try:
            while True:
                readable, _, _ = select.select([self.sock], [], [], 0)
                if not readable:
                    break
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise WebSocketError("connection closed by server")
                self._buf += chunk
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            raise WebSocketError(str(exc))

        messages = []
        while True:
            frame = self._next_frame()
            if frame is None:
                break
            opcode, payload = frame
            if opcode == 0x9:  # ping
                try:
                    self._send_frame(0xA, payload)
                except OSError as exc:
                    raise WebSocketError(str(exc))
            elif opcode == 0x8:  # close
                raise WebSocketError("close frame received")
            elif opcode in (0x1, 0x2):
                messages.append(payload)
        return messages

    def _next_frame(self):
        buf = self._buf
        if len(buf) < 2:
            return None
        opcode = buf[0] & 0x0F
        masked = buf[1] & 0x80
        length = buf[1] & 0x7F
        idx = 2
        if length == 126:
            if len(buf) < idx + 2:
                return None
            length = struct.unpack(">H", buf[idx:idx + 2])[0]
            idx += 2
        elif length == 127:
            if len(buf) < idx + 8:
                return None
            length = struct.unpack(">Q", buf[idx:idx + 8])[0]
            idx += 8
        mask = None
        if masked:
            if len(buf) < idx + 4:
                return None
            mask = bytes(buf[idx:idx + 4])
            idx += 4
        if len(buf) < idx + length:
            return None
        payload = bytes(buf[idx:idx + length])
        del buf[:idx + length]
        if mask is not None:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
