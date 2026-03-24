# hyprland-state

Live state interface for [Hyprland](https://hyprland.org) — read, write, and inspect the running compositor's configuration.

Bridges the gap between [hyprland-config](https://github.com/BlueManCZ/hyprland-config) (disk), [hyprland-socket](https://github.com/BlueManCZ/hyprland-socket) (IPC), and [hyprland-schema](https://github.com/BlueManCZ/hyprland-schema) (metadata) into a single coherent API.

## What it does

- **Options** — read effective values (IPC > disk > schema default), apply changes, inspect metadata, validate against schema constraints
- **Animations** — read/write animation states, manage bezier curves, navigate the animation tree
- **Monitors** — read monitor layout from IPC, apply monitor configuration
- **Binds** — read keybind definitions, execute dispatchers
- **Devices** — detect input devices (touchpad, etc.)
- **Persistence** — track pending changes, save to disk, discard/revert

## Installation

```
pip install hyprland-state
```

## Quick start

```python
from hyprland_state import HyprlandState

# Schema is auto-loaded for the running Hyprland version
state = HyprlandState()

print(state.version)  # "0.54.2"

# Read the live value (typed via schema — returns int, not str)
border = state.get("general:border_size")  # 2

# Inspect schema metadata
info = state.inspect("general:border_size")
print(info.type, info.default, info.min, info.max)  # int 1 0 20

# Write to the running compositor (validated against schema)
state.apply("general:border_size", 3)
state.apply_batch([("general:gaps_in", 5), ("general:gaps_out", 10)])

# Animations
for anim in state.animations.get_all():
    print(f"{anim.name}: enabled={anim.enabled}, speed={anim.speed}")

state.animations.apply("windows", True, 3.0, "easeOut", "slide")

# Monitors
for mon in state.monitors.get_all():
    print(f"{mon.name}: {mon.width}x{mon.height}@{mon.refresh_rate}Hz")

# Devices
if state.has_touchpad():
    print("Touchpad detected")
```

## Pending state and persistence

Changes made via `apply()` take effect immediately in the compositor but are tracked as pending until explicitly saved or discarded:

```python
state.apply("general:border_size", 5)
state.apply("decoration:rounding", 10)

state.is_dirty()  # True
state.pending()   # ["general:border_size", "decoration:rounding"]

state.save()      # writes to config file and reloads compositor
# or
state.discard()   # reverts compositor to on-disk values
```

## Validation

Values are validated against schema constraints (min/max, enum) before being sent to the compositor. Invalid values raise `ValueError`:

```python
state.apply("general:border_size", 999)  # ValueError: above maximum 20

# Bypass validation when needed
state.apply("general:border_size", 999, validate=False)
```

## Offline mode

Works without a running Hyprland instance — reads from config files and schema:

```python
state = HyprlandState(offline=True)
value = state.get_disk("general:border_size")  # from config file
default = state.get_default("general:border_size")  # from schema
```

Use `reconnect()` to switch to online mode when the compositor becomes available:

```python
state.reconnect()  # True if Hyprland is now reachable
```

## Dependencies

- [hyprland-config](https://github.com/BlueManCZ/hyprland-config) — Hyprlang parser
- [hyprland-monitors](https://github.com/BlueManCZ/hyprland-monitors) — Monitor model and utilities
- [hyprland-schema](https://github.com/BlueManCZ/hyprland-schema) — Option metadata
- [hyprland-socket](https://github.com/BlueManCZ/hyprland-socket) — IPC communication

## License

MIT
