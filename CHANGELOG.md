# Changelog

All notable changes to hyprland-state will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.7] - 2026-08-27

### Added

- `dispatchers_for_effect()` and `revert_dispatchers_for_effect()` take `compositor_reapplies_dynamic`, which drops the `setprop` half of the result. Hyprland 0.55+ in Lua mode schedules a window-state refresh from `hl.window_rule`, re-resolving every mapped window against the rule list, so a caller that registers the rule there gets the dynamic effects (opacity, blur, rounding, border colour) applied without asking for them per window. The Hyprlang `keyword` path schedules nothing, so the default stays off. https://github.com/BlueManCZ/hyprmod/issues/79
- `STATIC_RETROACTIVE_EFFECTS` — the subset of `RETROACTIVE_EFFECTS` that still needs a per-window dispatch under `compositor_reapplies_dynamic`. Callers use it as the fast-path predicate in that mode, so a rule of purely dynamic effects skips the `get_windows` round-trip instead of walking the window list to dispatch nothing.

### Fixed

- `_setprop`'s documented premise was wrong, and the docs for both dispatch helpers with it. A `setprop` is stored at `PRIORITY_SET_PROP`, above the `PRIORITY_WINDOW_RULE` a rule resolves to, and a config reload does **not** clear it: verified against Hyprland 0.56.1, a window set to `opacity 0.3` was still 0.3 after `hyprctl reload`. The override outlives the rule that prompted it for as long as the window is open, so a caller that applied one and later edited the effect away had no way back to the rule's value. Behaviour is unchanged for callers that don't pass the new flag; what changes is that the trade-off is now stated accurately.

## [0.4.6] - 2026-08-08

### Fixed

- `monitors.apply()` now resets a monitor's transform, bit depth and colour-management preset when the override is cleared. In Lua mode the monitor keyword is translated to `hl.monitor()`, which seeds the rule from the existing one for that output, so an omitted key kept its old value: setting a rotated monitor back to Normal had no effect until the next config reload. Requires hyprland-monitors 0.9.0, and the floor now enforces it. https://github.com/BlueManCZ/hyprmod/issues/82

## [0.4.5] - 2026-07-31

### Fixed

- `apply()` and `apply_batch()` no longer reject valid `choice` values. Both the integer Hyprland stores (`apply("general:resize_corner", 2)`) and the name from `enum_values` (`apply("general:resize_corner", "top_right")`) were refused by the schema validator; the fix lands in hyprland-schema 0.7.1, and the floor now enforces it. Names are converted to their index before being sent, so the value reaches the compositor in the form both config modes accept and matches what IPC reads back. https://github.com/BlueManCZ/hyprland-state/issues/4
- The schema behind `inspect()`, `get_default()` and validation now matches the running compositor. `hyprctl` reports the version as `0.55.4` while hyprland-schema keys its bundled versions on the git tag `v0.55.4`, so the lookup missed, tried a doomed download, and fell back to the bundled latest — meaning options and types from a Hyprland release the user is not running. Also fixed in hyprland-schema 0.7.1.

## [0.4.4] - 2026-07-30

### Fixed

- `save()` no longer overwrites Lua configs with Hyprlang content. The bug was in `Document.save()` (hyprland-config), which always emitted `.conf` syntax, so a `hyprland.lua` saved through `HyprlandState` came back as a config Hyprland refuses to parse. Requires hyprland-config 0.9.14, and the floor now enforces it. https://github.com/BlueManCZ/hyprland-state/issues/2

## [0.4.3] - 2026-06-24

### Added

- `HyprlandState.has_touchscreen()` — mirrors `has_touchpad()` for touchscreen hardware, reporting whether the running compositor lists any `touch` device. Lets callers gate touchscreen-only settings (e.g. workspace-swipe touch gestures) the same way touchpad settings are gated.

## [0.4.2] - 2026-05-20

### Changed

- `revert_dispatchers_for_effect()` now handles toggleable static effects (`float`, `tile`, `pin`, `fullscreen`, `maximize`) — applies the inverse toggle when the window matches the rule's target state, mirroring the apply path's behavior.

## [0.4.1] - 2026-05-17

### Fixed

- `Monitors.apply()` — resetting `sdrbrightness` / `sdrsaturation` back to their defaults now actually takes effect. Hyprland's `hl.monitor()` IPC is additive, so omitted SDR keys kept the previous live value; live-apply lines now emit them explicitly.

## [0.4.0] - 2026-05-16

### Added

- `hyprland_state.dispatchers_for_effect(name, args, window)` and `revert_dispatchers_for_effect(name, args, window)` — per-window retroactive dispatch translation for Hyprland window-rule effects. Returns the `(dispatcher, arg)` tuples that bring an already-mapped window into a rule's target state (apply path) or back out of it (revert path).
- `hyprland_state.SETPROP_PASSTHROUGH_EFFECTS` and `hyprland_state.RETROACTIVE_EFFECTS` — frozensets exposing the curated effect catalog used by the dispatch helpers. Callers can use `RETROACTIVE_EFFECTS` as a fast-path predicate to skip the `get_windows` IPC round-trip when no outgoing effect could mutate existing windows.
- `Monitors.get_all_cached()` — list-returning counterpart to the renamed `get_cached()` (see Changed).
- `AnimState.from_keyword()`, `body()`, `to_line()`, `to_data()` — parse/serialize helpers that mirror `hyprland_config.AnimationData`'s shape, with `to_data()` projecting to the format-only sibling.

### Changed

- **BREAKING** — `Monitors.get_cached()` now takes a `name` argument and returns `MonitorState | None`, matching `Animations.get_cached(name)`. The previous list-returning form is now `Monitors.get_all_cached()`.
- `HyprlandState.discard()` now falls back to the schema default when an option has no on-disk value, so a single discard call fully restores the compositor to its pre-edit state. Previously it returned `None` for those keys and required the caller to send a fallback value via IPC.

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

[0.4.7]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.7
[0.4.6]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.6
[0.4.5]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.5
[0.4.4]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.4
[0.4.3]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.3
[0.4.2]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.2
[0.4.1]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.1
[0.4.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.4.0
[0.3.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.3.0
[0.2.1]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.1
[0.2.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprland-state/releases/tag/v0.1.0
