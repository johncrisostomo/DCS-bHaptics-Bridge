# DCS bHaptics Bridge

Feel DCS World in your bHaptics TactSuit: G load, afterburner, stall buffet,
gear movement, touchdown, weapon release, gun fire, countermeasures and damage —
driven by the simulator's own telemetry, in real time.

Open source, plain Python + one Lua script. No compiled binaries, no installer,
no accounts, no API keys. You can read every line that runs on your machine.

## How it works

```
DCS World ──(Lua export script, UDP on 127.0.0.1)──▶ Python companion ──(WebSocket)──▶ bHaptics Player ──▶ TactSuit
```

1. A Lua script inside DCS reads flight data 20 times a second (G, airspeed,
   sink rate, gear, weapon stations, warning panel, …) and sends it to
   `127.0.0.1:41231`. Nothing leaves your PC.
2. The Python companion turns that telemetry into events (a station count
   dropping = a weapon left the jet; strut compression starting with a sink
   rate = a touchdown of *that* hardness) and into sustained loads.
3. It sends motor frames to bHaptics Player, which drives the suit.

## Requirements

- Windows 10 or later
- DCS World (Steam or standalone)
- bHaptics Player, with a TactSuit connected (developed against TactSuit Pro)
- Python 3.10 or later — **no pip packages needed**, standard library only

## Install

### 1. The DCS side (one file, one line)

1. Copy `lua\DCS-bHapticsBridge-Export.lua` into:

   ```
   %USERPROFILE%\Saved Games\DCS\Scripts\
   ```

   (If your Saved Games folder is named differently — e.g. `DCS.openbeta` —
   use that folder instead.)

2. Open `Export.lua` in the same folder (create it as a plain text file if it
   doesn't exist) and add this line at the end:

   ```lua
   dofile(lfs.writedir() .. [[Scripts\DCS-bHapticsBridge-Export.lua]])
   ```

   The script chains any hooks that were already there, so SRS, Tacview and
   similar export scripts keep working.

### 2. The Python side

Nothing to install. Keep this folder anywhere you like.

## Run

1. Start **bHaptics Player** and connect your suit.
2. In this folder, double-click **`run.bat`** — or from a terminal:

   ```
   python -m bhaptics_bridge
   ```

3. Start DCS and get into an aircraft. The bridge runs a **suit check**
   (a wave down the front, then the back) the first time it sees you in a jet,
   so you know the chain is alive before you roll.

To stop: close the console window or press `Ctrl+C`.

### No-suit test modes

- `python -m bhaptics_bridge --dry-run` — prints every frame it *would* send.
- `python tools\fake_telemetry.py` — flies a scripted profile (takeoff, gear,
  a G pull, a weapon release, a gun burst, damage, a firm touchdown) into the
  bridge without DCS, so you can watch the effects fire.

## Effects in v1

| Effect | Trigger | Feel |
|---|---|---|
| Suit check | First entering an aircraft | Wave down the front, then the back |
| Positive G | Sustained load above ~2 G | Starts in the lower front + back and climbs the vest as G builds; shoulders join in a hard pull |
| Negative G | Load below ~0.2 G | Chest / shoulders |
| Afterburner | Burner lit (draw args 28/29) | Low back rumble |
| Stall buffet | Angle of attack above threshold, airborne | Whole-vest shudder |
| Gear transit | Gear between up and down | Repeating low thumps |
| Gear locked | Gear reaches up or down | One solid thump |
| Touchdown | Struts compress after flight | Whole-vest thump, harder = firmer arrival |
| Weapon release | A weapon station count drops | Sharp mid-torso thump |
| Gun | Cannon shells decreasing | Rapid chest pulses |
| Countermeasures | Chaff/flare count drops | Shoulder tick |
| Damage | New failure on the warning panel | Three strong back pulses |
| Taxi bumps | Rolling on the ground | Small thumps from vertical jolts |

## Configuration

Copy `config.example.ini` to `config.ini` and edit. Everything is optional;
defaults are sane. Main knobs: master intensity scale, per-effect on/off,
stall AoA threshold, G onset/full-scale, UDP and Player ports, and
`flip_vertical` if your suit's motor rows feel upside down (see below).

## Honest notes (read before judging v1)

- The Player protocol used here is bHaptics' documented SDK1 websocket
  interface (`ws://127.0.0.1:15881/v2/feedbacks`). The JSON field casing follows
  the format observed from real SDK1 client traffic. If the suit check does
  not fire on your first run, that is the first suspect — tell me and it is a
  one-line change.
- The vest is addressed in the SDK1 20-points-per-side grid; bHaptics Player
  maps that onto the TactSuit Pro's 16 motors per side. Row orientation
  (which end is "top") is the second calibration suspect: if G feels like it
  pulls the wrong way, set `flip_vertical = true` in `config.ini`.
- Ejection has no export signal in DCS; it is not faked in v1.
- Multiplayer: the export script lives in Saved Games, which is DCS's
  sanctioned place for it and does not touch install files. Servers can
  restrict export data, and "pure scripts" servers may reject script mods —
  fly solo / your own server to be safe.

## Remove

Delete the line from `Export.lua`, delete the Lua file, delete this folder.
Nothing else was changed anywhere.

## Development

```
lua\        the DCS export script
bhaptics_bridge\   the companion (telemetry, effects, Player client)
tools\      fake_telemetry.py (scripted flight), mock_player.py (fake Player server)
tests\      python -m unittest discover tests
```

MIT licensed. See `LICENSE`.
