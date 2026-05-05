# Changelog

All notable changes to hyprland-state will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-05-05

### Fixed

- Gradient values read from IPC now have `0x` prepended to bare `AARRGGBB` hex tokens, so they round-trip into config files without being rejected by Hyprland's parser on reload.

## [0.2.0] - 2026-03-26

### Changed

- **BREAKING** — `get_styles_for()` / `Animations.get_styles()` return type now returns `tuple[str, ...]` instead of `list[str]`, matching the immutable nature of animation style data.
- Deduplicated color conversion — extracted shared `_extract_value()` method, eliminating duplicated ARGB-to-hex logic between `_read_ipc()` and `get_live()`.
- Immutable animation constants — `ANIMATION_TREE`, style lists, and flattened tree entries now use tuples instead of lists, preventing accidental mutation of module-level data.

## [0.1.0] - 2026-03-24

Initial release — live state interface for Hyprland — options, animations, monitors, binds, and devices.

### Added

- **Options** — read effective values (IPC > disk > schema default), apply changes, inspect metadata, validate against schema constraints.
- **Animations** — read/write animation states, manage bezier curves, navigate the animation tree.
- **Monitors** — read monitor layout from IPC, apply monitor configuration.
- **Binds** — read keybind definitions, execute dispatchers.
- **Devices** — detect input devices (touchpad, etc.).
- **Persistence** — track pending changes, save to disk, discard/revert.
- **Offline mode** — works without a running Hyprland instance, reads from config files and schema.
- **Schema validation** — values validated against schema constraints (min/max, enum) before being sent to the compositor.

[0.2.1]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.1
[0.2.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.1.0
