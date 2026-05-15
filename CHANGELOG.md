# Changelog

All notable changes to hyprland-state will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-05-15

### Added

- **Lua-mode bridge** — `HyprlandState` transparently routes live-apply, dispatch, and submap setup through `hyprctl eval` when Hyprland 0.55.0+ runs with `configProvider: lua`. Detection is lazy via `is_live_lua_mode()` and re-probes on `reconnect()`.
- `HyprlandState.define_submap(name, binds)` — atomic submap registration that abstracts over Hyprlang's stateful `submap=…` / `bind=…` / `submap=reset` sequence (now sent as one batch) and Lua's declarative `hl.define_submap(…, function() … end)`. Empty `binds` raises `CommandError` in both modes.
- Support for `cssgap` / `font_weight` schema types.
- Gradient IPC field rename — accepts both 0.54.x's `"custom"` and 0.55+'s `"gradient"`.

### Changed

- **BREAKING** — `OptionInfo` has been removed. `HyprlandState.inspect()` now returns `hyprland_schema.HyprOption` directly; `OptionInfo.validate()` moved onto `HyprOption.validate()` upstream. Update imports from `hyprland_state.OptionInfo` to `hyprland_schema.HyprOption`.
- **BREAKING** — `Animations.apply()`, `Animations.preview()`, and `Animations.define_bezier()` no longer swallow `hyprland_socket.HyprlandError` — they now propagate it, matching `HyprlandState.apply()`'s failure mode. Callers that relied on the `False` return for IPC rejection need an `except HyprlandError`.
- **BREAKING** — `ANIM_FLAT`, `ANIM_CHILDREN`, `Animations.tree`, `Animations.names`, and `Animations.get_children()` now return tuples instead of lists, completing the immutable-tree migration started in 0.2.0.
- `_normalize_gradient_string` moved to `hyprland_config.normalize_gradient_string` — the transform belongs with the config-string parser, not the state library.
- `HyprlandState.reload_config()` now uses `hyprland_config.load_any()` so Lua entrypoints (`hyprland.lua`) reload correctly alongside Hyprlang configs.

### Fixed

- `HyprlandState.save()` short-circuits when nothing is pending — no disk write, no compositor reload, returns `[]`.
- `HyprlandState.save(path=...)` now reports `[Path(path)]` as the written file instead of `dirty_files()`, which could include paths that weren't actually written when an explicit override was used.

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

[0.3.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.3.0
[0.2.1]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.1
[0.2.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.1.0
