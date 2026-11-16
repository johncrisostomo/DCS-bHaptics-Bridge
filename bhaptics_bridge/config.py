"""Bridge configuration: built-in defaults, optionally overridden by
config.ini in the project root (copy config.example.ini to start)."""

import configparser
import os

_DEFAULTS = {
    # [bridge]
    "udp_host": "127.0.0.1",
    "udp_port": 41231,
    "web_host": "0.0.0.0",
    "web_port": 41232,
    "player_host": "127.0.0.1",
    "player_port": 15881,
    "app_id": "dcs_bhaptics_bridge",
    "app_name": "DCS bHaptics Bridge",
    # Player transport: "sdk2" (default; the official bhaptics-python
    # package) or "sdk1" (legacy built-in client). The sdk2
    # credentials come from a bHaptics Developer Portal app; the API
    # key is a secret and belongs only in config.ini (gitignored) or
    # the BHAPTICS_APP_ID / BHAPTICS_API_KEY environment variables.
    "transport": "sdk2",
    "sdk2_app_id": "",
    "sdk2_api_key": "",
    "master_scale": 1.0,
    # [effects] thresholds
    "stall_aoa": 0.30,       # radians
    "g_onset": 2.00,         # G where the squeeze starts
    "g_full": 6.50,          # G where it reaches full intensity
    "neg_g_onset": 0.20,     # below this load, the negative-G feel starts
    # [effects] toggles
    "effect_g": True,
    "effect_burner": True,
    "effect_stall": True,
    "effect_gear": True,
    "effect_touchdown": True,
    "effect_release": True,
    "effect_gun": True,
    "effect_cm": True,
    "effect_damage": True,
    "effect_taxi": True,
}

_BOOL_KEYS = {k for k, v in _DEFAULTS.items() if isinstance(v, bool)}
_INT_KEYS = {"udp_port", "player_port", "web_port"}
_FLOAT_KEYS = {k for k, v in _DEFAULTS.items() if isinstance(v, float)}


class Config:
    def __init__(self, values=None):
        merged = dict(_DEFAULTS)
        if values:
            merged.update(values)
        self.__dict__.update(merged)

    def __repr__(self):
        return "Config(%r)" % (self.__dict__,)


def load_config(path=None):
    """Load config.ini (or the given path) over the defaults."""
    values = {}
    if path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(root, "config.ini")
        path = candidate if os.path.exists(candidate) else None
    if path and os.path.exists(path):
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        for section in parser.sections():
            for key, raw in parser.items(section):
                if key not in _DEFAULTS:
                    continue
                if key in _BOOL_KEYS:
                    values[key] = parser.getboolean(section, key)
                elif key in _INT_KEYS:
                    values[key] = parser.getint(section, key)
                elif key in _FLOAT_KEYS:
                    values[key] = parser.getfloat(section, key)
                else:
                    values[key] = raw
    cfg = Config(values)
    cfg.master_scale = min(1.0, max(0.0, cfg.master_scale))
    return cfg
