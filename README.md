# DCS bHaptics Bridge

A Lua export script and a Python companion that drive a bHaptics TactSuit
from DCS World telemetry. The script runs inside DCS and sends flight data
to the companion over UDP on localhost. The companion converts the data
into effects and sends motor commands to bHaptics Player over a websocket.

Source only: no compiled binaries, no installer, no accounts, no API keys.

## How it works

```
DCS World --(Lua export script, UDP 127.0.0.1:41231)--> Python companion --(WebSocket)--> bHaptics Player --> TactSuit
```

1. The Lua script reads flight data 20 times per second (load factor,
   airspeed, sink rate, gear state, weapon station counts, warning panel
   flags) and sends it to `127.0.0.1:41231`. All traffic stays on the
   local machine.
2. The companion derives events from state changes, because the DCS export
   environment has no event callbacks: a station count dropping is a
   weapon release, a shell count dropping is gun fire, strut compression
   after airborne time with a sink rate is a touchdown.
3. The companion sends dot frames to bHaptics Player, which drives the
   suit motors.

## Requirements

- Windows 10 or later
- DCS World (Steam or standalone)
- bHaptics Player, with a TactSuit connected (developed against TactSuit Pro)
- Python 3.10 or later, standard library only

## Install

### DCS side

1. Copy `lua\DCS-bHapticsBridge-Export.lua` into:

   ```
   %USERPROFILE%\Saved Games\DCS\Scripts\
   ```

   If the Saved Games folder is named differently (for example
   `DCS.openbeta`), use that folder instead.

2. Open `Export.lua` in the same folder (create it as a plain text file if
   it does not exist) and add this line at the end:

   ```lua
   dofile(lfs.writedir() .. [[Scripts\DCS-bHapticsBridge-Export.lua]])
   ```

   The script calls any export hooks that were registered before it, so
   other export scripts (SRS, Tacview) keep working.

### Python side

No installation. Keep the folder anywhere.

## Run

1. Start bHaptics Player and connect the suit.
2. Run `run.bat`, or from a terminal:

   ```
   python -m bhaptics_bridge
   ```

3. Start DCS and enter an aircraft. On the first aircraft entry the bridge
   runs a suit check: one wave down the front of the vest, then the back.

To stop, close the console window or press `Ctrl+C`.

### Test modes

- `python -m bhaptics_bridge --test` runs the suit check once, without DCS.
- `python -m bhaptics_bridge --dry-run` prints the frames it would send,
  without connecting to Player.
- `python tools\fake_telemetry.py` sends a scripted flight profile
  (takeoff, gear cycle, G pull, weapon release, gun burst, damage,
  touchdown) to the bridge without DCS.

## Web console

`python -m bhaptics_bridge --web` also serves a web page on the local
network, at `http://<this PC's address>:41232` (port and bind address
are `web_host` / `web_port` in the config). It has three parts:

- **Status.** Whether the bridge is connected to bHaptics Player. The
  protocol cannot report whether the vest itself is powered and
  paired; that is shown in Player's own window.
- **Motor test.** The TactSuit Pro drawn front and back as a vest
  outline, motors positioned where they sit on the body and numbered
  with the official motor index (front in mirror view, as in the
  bHaptics diagram; the wearer's left and right are labelled).
  Pressing and holding a motor vibrates it until released, through
  the same Pro-to-grid translation the effects use (seed table, or
  `pro_mapping.json` when present).
- **Effect lab.** The effect engine fed with synthetic telemetry, so
  every effect — G load, afterburner, stall, gear, touchdown, weapon
  release, gun, countermeasures, damage, taxi — can be tested without
  starting DCS.

There is no authentication: anyone on the LAN who opens the page can
drive the suit. Trusted networks only.

## Effects

| Effect | Trigger | Motors |
|---|---|---|
| Suit check | First aircraft entry | Front rows top to bottom, then back rows |
| Positive G | Load factor at or above `g_onset` (default 2.0) | Front and back; lower rows first, higher rows added as load increases |
| Negative G | Load factor at or below 0.2 | Chest and shoulders, front and back |
| Afterburner | Afterburner lit (draw arguments 28/29) | Lowest back rows |
| Stall buffet | Angle of attack at or above threshold (default 0.30 rad), airborne | All motors, random low intensity |
| Gear transit | Gear position between up and down | Lower rows, alternating front and back |
| Gear locked | Gear reaches fully up or down after a transit | Lower rows, front and back, one thump |
| Touchdown | Strut compression after at least 0.5 s airborne, with sink rate | All motors; intensity scales with sink rate |
| Weapon release | Total station count drops | Mid rows, front and back |
| Gun | Cannon shell count drops | Chest centre, front |
| Countermeasures | Chaff or flare count drops | Shoulder row, front and back |
| Damage | New failure flag on the warning panel | All back motors, three pulses |
| Taxi bumps | Vertical load deviation while rolling on the ground | Lower rows, front and back |

## Configuration

Copy `config.example.ini` to `config.ini` and edit. All settings are
optional. Available settings: master intensity scale, per-effect switches,
stall AoA threshold, G onset and full-scale values, UDP and Player ports,
and `flip_vertical`, which reverses the vest row order (see Notes).

## Notes

- Player protocol: bHaptics' SDK1 websocket interface,
  `ws://127.0.0.1:15881/v2/feedbacks`. JSON field names use PascalCase,
  matching observed SDK1 client traffic. Confirmed working against
  bHaptics Player on 2026-11-16.
- The vest is addressed as two 20-point grids (front and back). bHaptics
  Player maps the grid onto the connected device; a TactSuit Pro has 16
  motors per side.
- Row orientation is a calibration item. If effects feel vertically
  reversed, set `flip_vertical = true` in `config.ini`.
- DCS provides no export signal for ejection. There is no ejection effect.
- Multiplayer: the export script is installed in Saved Games and does not
  modify DCS install files. Servers can restrict which export data is
  available, and servers running pure scripts may reject clients with
  script mods.

## Uninstall

Delete the added line from `Export.lua`, delete the Lua file, and delete
this folder. No other files are modified.

## Development

```
lua\              the DCS export script
bhaptics_bridge\  the companion (telemetry, effects, Player client)
tools\            fake_telemetry.py (scripted flight), mock_player.py (fake Player server)
tests\            python -m unittest discover tests
```

MIT license. See `LICENSE`.
