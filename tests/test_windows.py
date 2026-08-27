"""Tests for the per-window retroactive-dispatch helpers."""

from unittest.mock import MagicMock

import pytest

from hyprland_state import (
    RETROACTIVE_EFFECTS,
    SETPROP_PASSTHROUGH_EFFECTS,
    STATIC_RETROACTIVE_EFFECTS,
    dispatchers_for_effect,
    revert_dispatchers_for_effect,
)


def _window(
    *,
    address: str = "0xabc",
    floating: bool = False,
    pinned: bool = False,
    fullscreen: int = 0,
) -> MagicMock:
    """Build a fake :class:`hyprland_socket.Window` snapshot."""
    win = MagicMock()
    win.address = address
    win.floating = floating
    win.pinned = pinned
    win.fullscreen = fullscreen
    return win


# Args that make an effect emit something, for the catalog sweeps below.
# Anything not listed is a bool and takes "on".
_EFFECT_ARGS = {
    "workspace": "2",
    "monitor": "DP-1",
    "size": "800 600",
    "move": "100 200",
    "opacity": "0.5",
    "border_color": "rgb(ff0000)",
    "border_size": "2",
    "rounding": "8",
    "rounding_power": "2",
    "animation": "popin",
    "idle_inhibit": "focus",
    "scroll_mouse": "1.0",
    "scroll_touchpad": "1.0",
}


class TestRetroactiveCatalogs:
    def test_setprop_catalog_is_subset_of_retroactive(self):
        # Every passthrough effect is by construction also retroactive —
        # the retroactive catalog is the union of static + dynamic effects
        # plus the passthrough set.
        assert SETPROP_PASSTHROUGH_EFFECTS.issubset(RETROACTIVE_EFFECTS)

    def test_static_effects_in_retroactive(self):
        for name in (
            "float",
            "tile",
            "pin",
            "fullscreen",
            "maximize",
            "workspace",
            "monitor",
            "size",
            "move",
            "opacity",
            "border_color",
        ):
            assert name in RETROACTIVE_EFFECTS

    def test_non_retroactive_effects(self):
        # Effects without a meaningful retroactive operation should NOT
        # be in the set — callers rely on this to skip get_windows IPC.
        for name in ("center", "no_initial_focus", "suppress_event", "tag"):
            assert name not in RETROACTIVE_EFFECTS

    def test_static_catalog_is_what_survives_the_compositor_reapplying(self):
        # The predicate callers use in Lua mode has to agree with what the
        # apply path actually emits there, or they skip the window walk for
        # a rule that needed it (or pay for one that dispatches nothing).
        win = _window(floating=True, pinned=True, fullscreen=2)
        for name in sorted(RETROACTIVE_EFFECTS):
            args = _EFFECT_ARGS.get(name, "on")
            reapplied = dispatchers_for_effect(name, args, win, compositor_reapplies_dynamic=True)
            assert not any(d[0] == "setprop" for d in reapplied), name
            if name in STATIC_RETROACTIVE_EFFECTS:
                assert reapplied == dispatchers_for_effect(name, args, win), name
            else:
                assert reapplied == [], name


class TestApplyDispatchers:
    def test_float_on_tiled_window_toggles(self):
        result = dispatchers_for_effect("float", "on", _window(floating=False))
        assert result == [("togglefloating", "address:0xabc")]

    def test_float_on_floating_window_is_noop(self):
        result = dispatchers_for_effect("float", "on", _window(floating=True))
        assert result == []

    def test_tile_on_floating_window_toggles(self):
        result = dispatchers_for_effect("tile", "on", _window(floating=True))
        assert result == [("togglefloating", "address:0xabc")]

    def test_tile_on_tiled_window_is_noop(self):
        result = dispatchers_for_effect("tile", "on", _window(floating=False))
        assert result == []

    def test_pin_on_unpinned_window(self):
        result = dispatchers_for_effect("pin", "on", _window(pinned=False))
        assert result == [("pin", "address:0xabc")]

    def test_pin_on_pinned_window_is_noop(self):
        result = dispatchers_for_effect("pin", "on", _window(pinned=True))
        assert result == []

    def test_fullscreen_skips_already_fullscreen(self):
        assert dispatchers_for_effect("fullscreen", "", _window(fullscreen=2)) == []
        assert dispatchers_for_effect("fullscreen", "", _window(fullscreen=0)) == [
            ("fullscreenstate", "2 -1,address:0xabc")
        ]

    def test_maximize_skips_already_maximized(self):
        assert dispatchers_for_effect("maximize", "", _window(fullscreen=1)) == []
        assert dispatchers_for_effect("maximize", "", _window(fullscreen=0)) == [
            ("fullscreenstate", "1 -1,address:0xabc")
        ]

    def test_workspace_uses_silent_variant(self):
        result = dispatchers_for_effect("workspace", "2", _window())
        assert result == [("movetoworkspacesilent", "2,address:0xabc")]

    def test_workspace_drops_silent_suffix(self):
        # "2 silent" → first token only; we always go silent anyway.
        result = dispatchers_for_effect("workspace", "2 silent", _window())
        assert result == [("movetoworkspacesilent", "2,address:0xabc")]

    def test_workspace_empty_is_noop(self):
        assert dispatchers_for_effect("workspace", "", _window()) == []

    def test_size_needs_two_values(self):
        assert dispatchers_for_effect("size", "1280", _window()) == []
        assert dispatchers_for_effect("size", "1280 720", _window()) == [
            ("resizewindowpixel", "exact 1280 720,address:0xabc")
        ]

    def test_move_needs_two_values(self):
        assert dispatchers_for_effect("move", "100", _window()) == []
        assert dispatchers_for_effect("move", "100 50", _window()) == [
            ("movewindowpixel", "exact 100 50,address:0xabc")
        ]

    def test_opacity_active_only(self):
        result = dispatchers_for_effect("opacity", "0.8", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 0.8"),
            ("setprop", "address:0xabc opacity_inactive 0.8"),
        ]

    def test_opacity_active_and_inactive(self):
        result = dispatchers_for_effect("opacity", "0.9 0.6", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 0.9"),
            ("setprop", "address:0xabc opacity_inactive 0.6"),
        ]

    def test_opacity_active_inactive_fullscreen(self):
        result = dispatchers_for_effect("opacity", "0.9 0.6 1.0", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 0.9"),
            ("setprop", "address:0xabc opacity_inactive 0.6"),
            ("setprop", "address:0xabc opacity_fullscreen 1.0"),
        ]

    def test_opacity_drops_override_keyword(self):
        result = dispatchers_for_effect("opacity", "0.8 override", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 0.8"),
            ("setprop", "address:0xabc opacity_inactive 0.8"),
        ]

    def test_border_color_fans_out_to_active_inactive(self):
        result = dispatchers_for_effect("border_color", "0xffff0000", _window())
        assert result == [
            ("setprop", "address:0xabc active_border_color 0xffff0000"),
            ("setprop", "address:0xabc inactive_border_color 0xffff0000"),
        ]

    @pytest.mark.parametrize("name", ["no_blur", "no_shadow", "opaque", "rounding"])
    def test_passthrough_effects_use_setprop(self, name):
        result = dispatchers_for_effect(name, "on", _window())
        assert result == [("setprop", f"address:0xabc {name} on")]

    def test_passthrough_without_args_is_noop(self):
        # Passthrough effects need an explicit value; rule emitters always
        # supply "on" for bools, so a bare passthrough is malformed input.
        assert dispatchers_for_effect("no_blur", "", _window()) == []

    @pytest.mark.parametrize("name", ["center", "no_initial_focus", "tag", "suppress_event"])
    def test_unsupported_effects_drop_through(self, name):
        assert dispatchers_for_effect(name, "any args", _window()) == []


class TestRevertDispatchers:
    def test_opacity_active_only_reverts_to_1_0(self):
        result = revert_dispatchers_for_effect("opacity", "0.8", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 1.0"),
            ("setprop", "address:0xabc opacity_inactive 1.0"),
        ]

    def test_opacity_three_values_reverts_all_three(self):
        result = revert_dispatchers_for_effect("opacity", "0.9 0.6 1.0", _window())
        assert result == [
            ("setprop", "address:0xabc opacity 1.0"),
            ("setprop", "address:0xabc opacity_inactive 1.0"),
            ("setprop", "address:0xabc opacity_fullscreen 1.0"),
        ]

    def test_border_color_reverts_via_unset(self):
        result = revert_dispatchers_for_effect("border_color", "0xff0000", _window())
        assert result == [
            ("setprop", "address:0xabc active_border_color unset"),
            ("setprop", "address:0xabc inactive_border_color unset"),
        ]

    def test_passthrough_reverts_via_unset(self):
        result = revert_dispatchers_for_effect("no_blur", "on", _window())
        assert result == [("setprop", "address:0xabc no_blur unset")]

    def test_float_reverts_only_when_currently_floating(self):
        # Disabling a float rule un-floats matching windows that are
        # currently floating; symmetric to the apply path which floats
        # only non-floating ones. Same false-positive trade-off in
        # reverse.
        assert revert_dispatchers_for_effect("float", "on", _window(floating=True)) == [
            ("togglefloating", "address:0xabc")
        ]
        assert revert_dispatchers_for_effect("float", "on", _window(floating=False)) == []

    def test_tile_reverts_only_when_currently_tiled(self):
        assert revert_dispatchers_for_effect("tile", "on", _window(floating=False)) == [
            ("togglefloating", "address:0xabc")
        ]
        assert revert_dispatchers_for_effect("tile", "on", _window(floating=True)) == []

    def test_pin_reverts_only_when_currently_pinned(self):
        assert revert_dispatchers_for_effect("pin", "on", _window(pinned=True)) == [
            ("pin", "address:0xabc")
        ]
        assert revert_dispatchers_for_effect("pin", "on", _window(pinned=False)) == []

    def test_fullscreen_reverts_only_when_internally_fullscreen(self):
        # Apply sent fullscreenstate 2 -1; revert clears internal=fullscreen.
        # No-op when the window is in a different internal state (e.g.
        # maximized=1 from another rule) so we don't accidentally
        # un-maximize.
        assert revert_dispatchers_for_effect("fullscreen", "on", _window(fullscreen=2)) == [
            ("fullscreenstate", "0 -1,address:0xabc")
        ]
        assert revert_dispatchers_for_effect("fullscreen", "on", _window(fullscreen=1)) == []
        assert revert_dispatchers_for_effect("fullscreen", "on", _window(fullscreen=0)) == []

    def test_maximize_reverts_only_when_internally_maximized(self):
        assert revert_dispatchers_for_effect("maximize", "on", _window(fullscreen=1)) == [
            ("fullscreenstate", "0 -1,address:0xabc")
        ]
        assert revert_dispatchers_for_effect("maximize", "on", _window(fullscreen=2)) == []
        assert revert_dispatchers_for_effect("maximize", "on", _window(fullscreen=0)) == []

    def test_unrevertable_static_effects_stay_noop(self):
        # ``size`` / ``move`` / ``workspace`` / ``monitor`` mutate state
        # without a per-window inverse we can compute from current state
        # alone (no "previous size" tracking). Save+reload is the escape
        # hatch.
        assert revert_dispatchers_for_effect("size", "100 100", _window()) == []
        assert revert_dispatchers_for_effect("move", "0 0", _window()) == []
        assert revert_dispatchers_for_effect("workspace", "2", _window()) == []
        assert revert_dispatchers_for_effect("monitor", "DP-1", _window()) == []


class TestCompositorReappliesDynamic:
    """The Lua-mode switch: Hyprland re-resolves dynamic effects itself.

    A ``setprop`` lands at ``PRIORITY_SET_PROP``, above the rule's own
    ``PRIORITY_WINDOW_RULE``, and a config reload does not clear it. Where
    the compositor keeps the value current, pinning it here would outlive
    the rule that prompted it.
    """

    def test_off_by_default(self):
        # The Hyprlang keyword path schedules no refresh, so the props are
        # still the only way those effects reach an open window.
        assert dispatchers_for_effect("opacity", "0.5", _window()) == [
            ("setprop", "address:0xabc opacity 0.5"),
            ("setprop", "address:0xabc opacity_inactive 0.5"),
        ]

    def test_apply_drops_the_props(self):
        assert (
            dispatchers_for_effect("opacity", "0.5", _window(), compositor_reapplies_dynamic=True)
            == []
        )
        assert (
            dispatchers_for_effect("no_blur", "on", _window(), compositor_reapplies_dynamic=True)
            == []
        )

    def test_apply_keeps_the_static_half(self):
        assert dispatchers_for_effect(
            "float", "on", _window(floating=False), compositor_reapplies_dynamic=True
        ) == [("togglefloating", "address:0xabc")]

    def test_revert_drops_the_unsets(self):
        # Nothing of ours is on the window, so there is no override to
        # clear; what shows comes from the rule list, and only a config
        # reload shrinks that.
        assert (
            revert_dispatchers_for_effect(
                "opacity", "0.5", _window(), compositor_reapplies_dynamic=True
            )
            == []
        )
        assert (
            revert_dispatchers_for_effect(
                "border_color", "rgb(ff0000)", _window(), compositor_reapplies_dynamic=True
            )
            == []
        )

    def test_revert_keeps_the_inverse_toggles(self):
        assert revert_dispatchers_for_effect(
            "float", "on", _window(floating=True), compositor_reapplies_dynamic=True
        ) == [("togglefloating", "address:0xabc")]
