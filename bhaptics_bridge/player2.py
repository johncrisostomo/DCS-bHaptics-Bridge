"""bHaptics Player client: the SDK2 transport (v2 track).

Same role as player.py, spoken through the official bhaptics-python
package instead of the hand-written SDK1 websocket client:

- Motors are addressed directly: an effect frame in TactSuit Pro
  motor space becomes one play_dot call with a 32-value array
  (front motors 0-15, back motors 16-31). No grid, no translation,
  no calibration.
- Player reports device state, exposed via device_status() - the
  web console's real vest indicator.
- The package is a compiled binary and the project's one third-party
  dependency. It is isolated behind the backend seam below: every
  package call happens in _Sdk2Backend, and tests inject a fake
  backend, so the rest of the bridge never imports the package.

The package's API is async; the bridge is not. A private asyncio
loop runs on a daemon thread and play calls are scheduled onto it
fire-and-forget, mirroring the SDK1 client's behaviour.

Credentials: cfg.sdk2_app_id / cfg.sdk2_api_key, overridden by the
BHAPTICS_APP_ID / BHAPTICS_API_KEY environment variables. They are
sent only to Player on localhost during initialization and are
never logged. Empty values are allowed - whether Player accepts
them for dot playback is an open experiment; the init result is
reported as-is.

Durations: the SDK2 docs recommend >= 100 ms per play_dot call, so
frame durations are floored at 100 ms. Effect frames are re-issued
at 20 Hz by the engine, which keeps sustained effects continuous.
"""

import asyncio
import inspect
import json
import os
import threading
import time

from .layout import FRONT, BACK, absolute

VEST_POSITION = 0          # device type 0 = TactSuit Pro
MOTOR_COUNT = 32
MIN_DURATION_MS = 100      # vendor recommendation for play_dot
STATUS_REFRESH = 2.0       # seconds between device-info polls


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


class _Sdk2Backend:
    """The only place bhaptics_python is imported or called."""

    def __init__(self):
        import bhaptics_python as bp
        self.bp = bp

    async def initialize(self, app_id, api_key):
        return await _maybe_await(
            self.bp.registry_and_initialize(app_id, api_key, ""))

    async def is_connected(self):
        return await _maybe_await(self.bp.is_connected())

    async def play_dot(self, duration_ms, values):
        return await _maybe_await(
            self.bp.play_dot(VEST_POSITION, duration_ms, values))

    async def device_info_json(self):
        return await _maybe_await(self.bp.get_device_info_json())

    async def stop_all(self):
        return await _maybe_await(self.bp.stop_all())

    async def close(self):
        return await _maybe_await(self.bp.close())


def parse_device_info(raw):
    """Device-info payload -> list of device dicts (defensive).

    Accepts the JSON string the package returns, or an already-parsed
    list/dict. Unknown shapes yield an empty list rather than an
    error: device status is advisory, never worth crashing over.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, dict):
        for key in ("devices", "Devices"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return [raw]
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)]
    return []


def vest_status(devices):
    """The TactSuit Pro entry of a parsed device list, or None."""
    for device in devices:
        if device.get("position") == VEST_POSITION:
            return {
                "connected": bool(device.get("connected")),
                "paired": bool(device.get("paired")),
                "battery": device.get("battery"),
            }
    return None


class BhapticsPlayer2:
    """SDK2 transport with the same interface as BhapticsPlayer."""

    mapping_source = "direct (SDK2)"

    def __init__(self, cfg, dry_run=False, log=print, backend=None):
        self.cfg = cfg
        self.dry_run = dry_run
        self.log = log
        self.app_id = os.environ.get("BHAPTICS_APP_ID", cfg.sdk2_app_id)
        self.api_key = os.environ.get("BHAPTICS_API_KEY", cfg.sdk2_api_key)
        self._backend = backend
        self._backend_failed = False
        self._loop = None
        self._connected = False
        self._next_retry = 0.0
        self._devices = []
        self._devices_at = 0.0
        self._fetching = False

    # -- connection ---------------------------------------------------

    @property
    def connected(self):
        return self.dry_run or self._connected

    def _get_backend(self):
        if self._backend is None and not self._backend_failed:
            try:
                self._backend = _Sdk2Backend()
            except ImportError:
                self._backend_failed = True
                self.log("[player2] bhaptics-python is not installed; "
                         "the SDK2 transport needs it: "
                         "pip install bhaptics-python")
        return self._backend

    def _ensure_loop(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            thread = threading.Thread(target=self._loop.run_forever,
                                      daemon=True)
            thread.start()
        return self._loop

    def _run(self, coro, timeout):
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return future.result(timeout)

    def _schedule(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        future.add_done_callback(self._on_done)
        return future

    def _on_done(self, future):
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self.log("[player2] SDK2 call failed: %s" % exc)

    def ensure_connected(self):
        if self.dry_run or self._connected:
            return True
        now = time.monotonic()
        if now < self._next_retry:
            return False
        self._next_retry = now + 3.0
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            ok = self._run(backend.initialize(self.app_id, self.api_key),
                           timeout=25)
        except Exception as exc:  # init failure is data, not a crash
            self.log("[player2] SDK2 initialize failed: %s" % exc)
            return False
        self._connected = bool(ok)
        if self._connected:
            self.log("[player2] connected to bHaptics Player (SDK2)")
            self._fetch_devices()
        else:
            self.log("[player2] SDK2 initialize was refused "
                     "(Player running? credentials accepted?)")
        return self._connected

    def poll(self):
        if self.dry_run or not self._connected:
            return
        if time.monotonic() - self._devices_at >= STATUS_REFRESH:
            self._fetch_devices()

    def _fetch_devices(self):
        if self._fetching or self._backend is None:
            return
        self._fetching = True

        async def fetch():
            try:
                # The SDK maintains the Player connection itself (it
                # auto-reconnects), so liveness is queried, not assumed.
                self._connected = bool(await self._backend.is_connected())
                raw = await self._backend.device_info_json()
                self._devices = parse_device_info(raw)
            except Exception as exc:
                self.log("[player2] device info failed: %s" % exc)
            finally:
                self._devices_at = time.monotonic()
                self._fetching = False

        self._schedule(fetch())

    def device_status(self):
        """Latest known vest state, or None if never reported."""
        if self.dry_run:
            return None
        return vest_status(self._devices)

    # -- submission -----------------------------------------------------

    def _values(self, position, pro_points):
        values = [0] * MOTOR_COUNT
        for pro, intensity in pro_points.items():
            values[absolute(position, pro)] = max(0, min(100,
                                                         int(intensity)))
        return values

    def _play(self, values, duration_ms):
        duration_ms = max(MIN_DURATION_MS, int(duration_ms))
        if self.dry_run:
            self.log("[dry-run] sdk2 play_dot position=%d duration=%d "
                     "values=%s" % (VEST_POSITION, duration_ms, values))
            return True
        if not self.ensure_connected():
            return False
        self._schedule(self._backend.play_dot(duration_ms, values))
        return True

    def submit(self, frame):
        """Submit an effect frame given in TactSuit Pro motor space."""
        return self._play(self._values(frame.position, frame.points),
                          frame.duration_ms)

    def submit_pro_dot(self, key, position, pro_points, duration_ms):
        """Submit raw points in Pro motor space (web console hold).

        SDK2 dot mode has no frame keys; `key` is accepted for
        interface parity with the SDK1 client and ignored.
        """
        return self._play(self._values(position, pro_points), duration_ms)

    def turn_off_all(self, _keys_positions=None):
        return self._play([0] * MOTOR_COUNT, 30)

    def close(self):
        if self._backend is not None and self._connected:
            try:
                self._run(self._backend.stop_all(), timeout=5)
                self._run(self._backend.close(), timeout=5)
            except Exception:
                pass
        self._connected = False
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None
