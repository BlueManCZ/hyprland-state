"""Tests for HyprlandState."""

from unittest.mock import MagicMock, patch

import hyprland_socket
import pytest
from hyprland_schema import HyprOption

from hyprland_state import HyprlandState


@pytest.fixture
def tmp_config(tmp_path):
    """Create a minimal Hyprland config file."""
    conf = tmp_path / "hyprland.conf"
    conf.write_text(
        "general {\n    border_size = 2\n    gaps_in = 5\n}\n\ndecoration {\n    rounding = 10\n}\n"
    )
    return conf


@pytest.fixture
def offline_state(tmp_config):
    """HyprlandState in offline mode."""
    return HyprlandState(tmp_config, offline=True)


@pytest.fixture
def online_mocks():
    """Patch hyprland_socket and hyprland_config for online-mode tests."""
    with (
        patch("hyprland_state._state.hyprland_socket") as mock_socket,
        patch("hyprland_state._state.hyprland_config") as mock_config,
    ):
        mock_socket.HyprlandError = hyprland_socket.HyprlandError
        mock_socket.CommandError = hyprland_socket.CommandError
        mock_socket.SocketError = hyprland_socket.SocketError
        mock_socket.get_version.return_value = MagicMock(version="0.54.2")
        # Default to legacy mode so existing tests don't have to opt in;
        # Lua-mode tests override with ``state._lua_mode = True``.
        mock_socket.get_status.return_value = {"configProvider": "legacy"}
        mock_config.load_any.return_value = MagicMock()
        yield mock_socket, mock_config


class TestOfflineRead:
    def test_get_returns_disk_value(self, offline_state):
        assert offline_state.get("general:border_size") == "2"

    def test_get_missing_returns_hint(self, offline_state):
        assert offline_state.get("nonexistent", hint=99) == 99

    def test_get_raw_returns_none(self, offline_state):
        assert offline_state.get_raw("general:border_size") is None

    def test_get_disk(self, offline_state):
        assert offline_state.get_disk("general:border_size") == "2"

    def test_get_disk_missing(self, offline_state):
        assert offline_state.get_disk("nonexistent") is None

    def test_online_is_false(self, offline_state):
        assert not offline_state.online


class TestOfflineWrite:
    def test_apply_returns_false(self, offline_state):
        assert not offline_state.apply("general:border_size", 5)

    def test_apply_batch_returns_empty(self, offline_state):
        assert offline_state.apply_batch([("general:border_size", 5)]) == []

    def test_reload_compositor_returns_false(self, offline_state):
        assert not offline_state.reload_compositor()


class TestPending:
    def test_apply_tracks_pending(self, online_mocks, tmp_config):
        state = HyprlandState(tmp_config, schema=None)
        state.apply("general:border_size", 5)
        assert state.is_dirty()
        assert state.is_dirty("general:border_size")
        assert "general:border_size" in state.pending()

    def test_apply_batch_tracks_pending(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.keyword_batch.return_value = [None, None]
        state = HyprlandState(tmp_config, schema=None)
        state.apply_batch([("general:border_size", 5), ("decoration:rounding", 10)])
        assert len(state.pending()) == 2

    def test_not_dirty_initially(self, offline_state):
        assert not offline_state.is_dirty()
        assert offline_state.pending() == []

    def test_apply_offline_does_not_track(self, offline_state):
        offline_state.apply("general:border_size", 5)
        assert not offline_state.is_dirty()

    def test_keyword_does_not_track(self, online_mocks, tmp_config):
        state = HyprlandState(tmp_config, schema=None)
        state.keyword("submap", "test")
        assert not state.is_dirty()


class TestSaveDiscard:
    def test_save_writes_pending_to_document(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_doc = mock_config.load_any.return_value
        mock_doc.dirty_files.return_value = [tmp_config]

        state = HyprlandState(tmp_config, schema=None)
        state.apply("general:border_size", 5)
        state.save()

        mock_doc.set.assert_called_with("general:border_size", 5)
        mock_doc.save.assert_called_once()
        mock_socket.reload.assert_called()
        assert not state.is_dirty()

    def test_discard_reverts_and_clears(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_doc = mock_config.load_any.return_value
        mock_doc.get.return_value = "2"

        state = HyprlandState(tmp_config, schema=None)
        state.apply("general:border_size", 5)
        reverted = state.discard()

        assert "general:border_size" in reverted
        assert reverted["general:border_size"] == "2"
        assert not state.is_dirty()
        # Should have sent the on-disk value back via keyword_batch
        mock_socket.keyword_batch.assert_called_once_with([("general:border_size", "2")])

    def test_discard_falls_back_to_schema_default(self, online_mocks, tmp_config):
        """When an option has no on-disk value, discard reverts to the schema default."""
        mock_socket, mock_config = online_mocks
        mock_doc = mock_config.load_any.return_value
        # Document has nothing for this key — simulates an option the user
        # set live but never saved to disk.
        mock_doc.get.return_value = None

        schema = {
            "general:border_size": HyprOption(
                key="general:border_size",
                section=("general",),
                name="border_size",
                description="",
                type="int",
                default=1,
            ),
        }
        state = HyprlandState(tmp_config, schema=schema)
        state.apply("general:border_size", 5, validate=False)
        reverted = state.discard()

        assert reverted == {"general:border_size": 1}
        # The schema default should have been sent back via IPC, so the
        # caller doesn't need to paper over the gap.
        mock_socket.keyword_batch.assert_called_once_with([("general:border_size", 1)])

    def test_discard_returns_none_when_no_disk_and_no_default(self, online_mocks, tmp_config):
        """No on-disk value and no schema default → None, no IPC call."""
        mock_socket, mock_config = online_mocks
        mock_doc = mock_config.load_any.return_value
        mock_doc.get.return_value = None

        state = HyprlandState(tmp_config, schema=None)  # no schema at all
        state.apply("general:border_size", 5, validate=False)
        reverted = state.discard()

        assert reverted == {"general:border_size": None}
        mock_socket.keyword_batch.assert_not_called()

    def test_clear_pending(self, online_mocks, tmp_config):
        state = HyprlandState(tmp_config, schema=None)
        state.apply("general:border_size", 5)
        state.clear_pending()
        assert not state.is_dirty()

    def test_save_offline_writes_to_disk(self, offline_state, tmp_config):
        """Save works offline — writes pending to document and file."""
        offline_state._pending["general:border_size"] = 99
        offline_state.save()
        assert not offline_state.is_dirty()
        content = tmp_config.read_text()
        assert "border_size = 99" in content


def _make_schema_opt(**kwargs) -> HyprOption:
    key = kwargs.get("key", "general:border_size")
    return HyprOption(
        key=key,
        section=tuple(key.split(":")[:-1]),
        name=key.rsplit(":", 1)[-1],
        description=kwargs.get("description", ""),
        type=kwargs.get("type", "int"),
        default=kwargs.get("default", 1),
        min=kwargs.get("min", 0),
        max=kwargs.get("max", 20),
        enum_values=kwargs.get("enum_values"),
    )


class TestSchemaIntegration:
    def test_get_default(self, tmp_config):
        schema = {"general:border_size": _make_schema_opt(default=1)}
        state = HyprlandState(tmp_config, offline=True, schema=schema)
        assert state.get_default("general:border_size") == 1

    def test_get_default_missing(self, tmp_config):
        state = HyprlandState(tmp_config, offline=True, schema={})
        assert state.get_default("nonexistent") is None

    def test_get_default_no_schema(self, offline_state):
        assert offline_state.get_default("anything") is None

    def test_inspect(self, tmp_config):
        opt = _make_schema_opt(description="Border size", min=0, max=20)
        state = HyprlandState(tmp_config, offline=True, schema={"general:border_size": opt})

        info = state.inspect("general:border_size")
        assert isinstance(info, HyprOption)
        assert info is opt
        assert info.type == "int"
        assert info.default == 1

    def test_inspect_missing(self, offline_state):
        assert offline_state.inspect("anything") is None


class TestValidation:
    def test_apply_rejects_below_min(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        opt = _make_schema_opt(min=0, max=20)
        state = HyprlandState(tmp_config, schema={"general:border_size": opt})

        with pytest.raises(ValueError, match="below minimum"):
            state.apply("general:border_size", -1)
        mock_socket.keyword.assert_not_called()

    def test_apply_rejects_above_max(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        opt = _make_schema_opt(min=0, max=20)
        state = HyprlandState(tmp_config, schema={"general:border_size": opt})

        with pytest.raises(ValueError, match="above maximum"):
            state.apply("general:border_size", 50)
        mock_socket.keyword.assert_not_called()

    def test_apply_accepts_valid_value(self, online_mocks, tmp_config):
        opt = _make_schema_opt(min=0, max=20)
        state = HyprlandState(tmp_config, schema={"general:border_size": opt})

        assert state.apply("general:border_size", 5) is True

    def test_apply_skips_validation_when_disabled(self, online_mocks, tmp_config):
        opt = _make_schema_opt(min=0, max=20)
        state = HyprlandState(tmp_config, schema={"general:border_size": opt})

        assert state.apply("general:border_size", 50, validate=False) is True

    def test_apply_no_schema_skips_validation(self, online_mocks, tmp_config):
        state = HyprlandState(tmp_config, schema=None)

        assert state.apply("general:border_size", 9999) is True

    def test_apply_batch_rejects_invalid(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        opt = _make_schema_opt(min=0, max=20)
        state = HyprlandState(tmp_config, schema={"general:border_size": opt})

        with pytest.raises(ValueError):
            state.apply_batch([("general:border_size", 50)])
        mock_socket.keyword_batch.assert_not_called()

    def test_apply_rejects_invalid_enum(self, online_mocks, tmp_config):
        opt = _make_schema_opt(
            key="misc:mode",
            type="string",
            default="a",
            min=None,
            max=None,
            enum_values=("a", "b", "c"),
        )
        state = HyprlandState(tmp_config, schema={"misc:mode": opt})

        with pytest.raises(ValueError, match="not one of"):
            state.apply("misc:mode", "z")


class TestOnlineWithMocks:
    def test_apply_calls_keyword(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        state = HyprlandState(tmp_config, schema=None)
        result = state.apply("general:border_size", 5)
        assert result is True
        mock_socket.keyword.assert_called_once_with("general:border_size", 5)

    def test_apply_failure_raises(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.keyword.side_effect = hyprland_socket.HyprlandError("rejected")

        state = HyprlandState(tmp_config, schema=None)
        with pytest.raises(hyprland_socket.HyprlandError):
            state.apply("general:border_size", 5, validate=False)

    def test_apply_batch(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.keyword_batch.return_value = [None, None]
        state = HyprlandState(tmp_config, schema=None)
        changes = [("general:border_size", 5), ("decoration:rounding", 15)]
        applied = state.apply_batch(changes)
        assert applied == changes
        mock_socket.keyword_batch.assert_called_once_with(changes)

    def test_apply_batch_partial_failure(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        # First succeeds, second fails
        mock_socket.keyword_batch.return_value = [None, "invalid value"]

        state = HyprlandState(tmp_config, schema=None)
        changes = [("general:border_size", 5), ("decoration:rounding", 15)]
        applied = state.apply_batch(changes)

        assert applied == [("general:border_size", 5)]
        assert state.is_dirty("general:border_size")
        assert not state.is_dirty("decoration:rounding")

    def test_apply_batch_total_failure(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.keyword_batch.return_value = ["rejected"]

        state = HyprlandState(tmp_config, schema=None)
        applied = state.apply_batch([("general:border_size", 5)])

        assert applied == []
        assert not state.is_dirty()

    def test_available_true(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {"int": 2}

        state = HyprlandState(tmp_config, schema=None)
        assert state.available("general:border_size")

    def test_available_false(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.side_effect = hyprland_socket.HyprlandError("unknown")

        state = HyprlandState(tmp_config, schema=None)
        assert not state.available("nonexistent:option")

    def test_refresh(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {"int": 7}

        state = HyprlandState(tmp_config, schema=None)
        val = state.refresh("general:border_size", hint=0)
        assert val == 7

    def test_reload_compositor(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        state = HyprlandState(tmp_config, schema=None)
        assert state.reload_compositor()
        mock_socket.reload.assert_called_once()


class TestAnimationsProperty:
    def test_animations_accessible(self, offline_state):
        anims = offline_state.animations
        assert anims is not None
        # Same instance on second access
        assert offline_state.animations is anims


class TestMonitorsProperty:
    def test_monitors_accessible(self, offline_state):
        monitors = offline_state.monitors
        assert monitors is not None
        assert offline_state.monitors is monitors


class TestDevices:
    def test_get_devices_offline(self, offline_state):
        assert offline_state.get_devices() == {}

    def test_has_touchpad_offline(self, offline_state):
        assert offline_state.has_touchpad() is False

    def test_has_touchpad_true(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_devices.return_value = {
            "mice": [{"name": "ELAN Touchpad"}],
        }

        state = HyprlandState(tmp_config, schema=None)
        assert state.has_touchpad() is True

    def test_has_touchpad_false(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_devices.return_value = {
            "mice": [{"name": "Logitech USB Mouse"}],
        }

        state = HyprlandState(tmp_config, schema=None)
        assert state.has_touchpad() is False

    def test_has_trackpad(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_devices.return_value = {
            "mice": [{"name": "Apple Internal Trackpad"}],
        }

        state = HyprlandState(tmp_config, schema=None)
        assert state.has_touchpad() is True


class TestReconnect:
    def test_reconnect_from_offline(self, offline_state):
        assert not offline_state.online
        with (
            patch("hyprland_state._state._detect_version", return_value="0.54.2"),
            patch("hyprland_state._state._load_schema", return_value={}),
        ):
            assert offline_state.reconnect() is True
        assert offline_state.online
        assert offline_state.version == "0.54.2"

    def test_reconnect_still_offline(self, offline_state):
        with patch("hyprland_state._state._detect_version", return_value=None):
            assert offline_state.reconnect() is False
        assert not offline_state.online

    def test_reconnect_resets_subsystems(self, offline_state):
        # Access subsystems to populate them
        _ = offline_state.animations
        _ = offline_state.monitors
        assert offline_state._animations is not None
        assert offline_state._monitors is not None

        with (
            patch("hyprland_state._state._detect_version", return_value="0.54.2"),
            patch("hyprland_state._state._load_schema", return_value={}),
        ):
            offline_state.reconnect()
        assert offline_state._animations is None
        assert offline_state._monitors is None


class TestGradientExtraction:
    """IPC reports gradients as bare hex; we expose them in 0x-prefixed form
    so callers can round-trip the value into a config file."""

    def test_bare_hex_gets_0x_prefix(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {
            "custom": "eeb4e718 ee00ff99 45deg",
            "set": True,
        }
        state = HyprlandState(tmp_config)
        val, available = state.get_live("general:col.active_border")
        assert available
        assert val == "0xeeb4e718 0xee00ff99 45deg"

    def test_already_prefixed_unchanged(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {
            "custom": "0xeeb4e718 45deg",
            "set": True,
        }
        state = HyprlandState(tmp_config)
        val, _ = state.get_live("general:col.active_border")
        assert val == "0xeeb4e718 45deg"

    def test_rgba_wrapped_unchanged(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {
            "custom": "rgba(b4e718ee) 45deg",
            "set": True,
        }
        state = HyprlandState(tmp_config)
        val, _ = state.get_live("general:col.active_border")
        assert val == "rgba(b4e718ee) 45deg"

    def test_three_color_gradient(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {
            "custom": "eeb4e718 ee00ff99 ffffffff 45deg",
            "set": True,
        }
        state = HyprlandState(tmp_config)
        val, _ = state.get_live("general:col.active_border")
        assert val == "0xeeb4e718 0xee00ff99 0xffffffff 45deg"

    def test_hyprland_055_uses_gradient_field(self, online_mocks, tmp_config):
        # Hyprland 0.55 renamed the IPC field from ``custom`` to ``gradient``.
        # We accept either so the value populates on both compositor versions.
        mock_socket, _ = online_mocks
        mock_socket.get_option.return_value = {
            "gradient": "eeb4e718 ee00ff99 45deg",
            "set": True,
        }
        state = HyprlandState(tmp_config)
        val, available = state.get_live("general:col.active_border")
        assert available
        assert val == "0xeeb4e718 0xee00ff99 45deg"


class TestReloadConfig:
    def test_reload_config_reloads_document(self, offline_state, tmp_config):
        tmp_config.write_text("general {\n    border_size = 99\n}\n")
        offline_state.reload_config()
        assert offline_state.get_disk("general:border_size") == "99"


class TestLuaModeDetection:
    """``is_live_lua_mode`` probes ``hyprctl status`` and caches the answer.

    A transient failure must *not* be cached — otherwise the instance
    silently routes through the wrong path for the rest of its life if
    the very first probe hit a temporary socket hiccup.
    """

    def test_returns_true_on_lua_provider(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_status.return_value = {"configProvider": "lua"}
        state = HyprlandState(tmp_config, schema=None)
        assert state.is_live_lua_mode() is True

    def test_returns_false_on_legacy_provider(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_status.return_value = {"configProvider": "legacy"}
        state = HyprlandState(tmp_config, schema=None)
        assert state.is_live_lua_mode() is False

    def test_offline_returns_false_without_ipc(self, offline_state):
        # No get_status call should happen when offline — there's no
        # mock here, so any IPC attempt would raise AttributeError.
        assert offline_state.is_live_lua_mode() is False

    def test_caches_after_successful_probe(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_status.return_value = {"configProvider": "lua"}
        state = HyprlandState(tmp_config, schema=None)
        state.is_live_lua_mode()
        state.is_live_lua_mode()
        state.is_live_lua_mode()
        # One probe for any number of calls.
        assert mock_socket.get_status.call_count == 1

    def test_transient_failure_is_not_cached(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        # First call fails, second succeeds — second answer must win.
        mock_socket.get_status.side_effect = [
            hyprland_socket.HyprlandError("transient"),
            {"configProvider": "lua"},
        ]
        state = HyprlandState(tmp_config, schema=None)
        assert state.is_live_lua_mode() is False  # uncached miss
        assert state.is_live_lua_mode() is True  # recovered on retry

    def test_reconnect_clears_cache(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.get_status.return_value = {"configProvider": "legacy"}
        state = HyprlandState(tmp_config, schema=None)
        assert state.is_live_lua_mode() is False

        # The user reboots Hyprland with a Lua entrypoint — reconnect()
        # must re-probe instead of returning the stale legacy answer.
        mock_socket.get_status.return_value = {"configProvider": "lua"}
        state.reconnect()
        assert state.is_live_lua_mode() is True


class TestDispatch:
    def test_dispatch_offline_returns_false(self, offline_state):
        assert offline_state.dispatch("killactive") is False

    def test_dispatch_legacy_uses_dispatch_ipc(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = False  # force legacy

        state.dispatch("togglefloating", "address:0xabc")

        mock_socket.dispatch.assert_called_once_with("togglefloating", "address:0xabc")
        mock_socket.eval_lua.assert_not_called()

    def test_dispatch_lua_mode_routes_through_eval(self, online_mocks, tmp_config):
        """Lua-mode dispatch goes through ``eval`` with ``hl.dispatch(hl.dsp.*())``.

        ``hl.dispatch`` takes a dispatcher *value* (an ``hl.dsp.*`` call),
        not a string — Hyprland's legacy shorthand was broken in two ways
        (no quoting AND wrong form), so we bypass it entirely.
        """
        mock_socket, mock_config = online_mocks
        mock_config.dispatch_to_lua.return_value = 'hl.dispatch(hl.dsp.submap("reset"))'
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        state.dispatch("submap", "reset")

        mock_socket.dispatch.assert_not_called()
        mock_socket.eval_lua.assert_called_once_with('hl.dispatch(hl.dsp.submap("reset"))')

    def test_dispatch_lua_mode_unmapped_surfaces_as_command_error(self, online_mocks, tmp_config):
        """A dispatch with no Lua mapping must raise ``CommandError`` —
        callers catch ``HyprlandError`` and expect IPC-style failures, not
        ``ValueError`` from the translation layer."""
        mock_socket, mock_config = online_mocks
        mock_config.dispatch_to_lua.side_effect = ValueError("No Lua mapping")
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        with pytest.raises(hyprland_socket.CommandError, match="No Lua mapping"):
            state.dispatch("notarealdispatcher")


class TestDefineSubmap:
    """Atomic submap registration — abstracts over Hyprlang vs Lua mode.

    Lua's submap API is declarative (binds defined inside ``hl.define_submap``'s
    function body), so the streaming Hyprlang ``keyword`` sequence has no
    line-by-line Lua equivalent. ``define_submap`` collapses both into one
    method.
    """

    def test_offline_returns_false(self, offline_state):
        assert offline_state.define_submap("test", [("bind", ", X, noop,")]) is False

    def test_legacy_sends_keyword_batch(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        mock_socket.keyword_batch.return_value = [None, None, None]
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = False

        state.define_submap("capture", [("bind", ", XF86LaunchA, noop,")])

        # ``submap=NAME`` → bind(s) → ``submap=reset`` in a single batch
        # so they land in one event-loop iteration on the compositor.
        mock_socket.keyword_batch.assert_called_once_with(
            [
                ("submap", "capture"),
                ("bind", ", XF86LaunchA, noop,"),
                ("submap", "reset"),
            ]
        )
        mock_socket.eval_lua.assert_not_called()

    def test_legacy_empty_binds_raises(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = False

        with pytest.raises(hyprland_socket.CommandError, match="no binds"):
            state.define_submap("empty", [])
        mock_socket.keyword_batch.assert_not_called()

    def test_legacy_batch_failure_raises(self, online_mocks, tmp_config):
        mock_socket, _ = online_mocks
        # Second command (the bind) is rejected by Hyprland.
        mock_socket.keyword_batch.return_value = [None, "bad bind syntax", None]
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = False

        with pytest.raises(hyprland_socket.CommandError, match="bad bind syntax"):
            state.define_submap("capture", [("bind", ", XF86LaunchA, noop,")])

    def test_lua_mode_sends_one_eval(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_config.define_submap_to_lua.return_value = (
            'hl.define_submap("capture", function()\n  hl.bind("X", hl.dsp.no_op())\nend)'
        )
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        state.define_submap("capture", [("bind", ", XF86LaunchA, noop,")])

        mock_socket.keyword.assert_not_called()
        mock_socket.eval_lua.assert_called_once()
        assert "hl.define_submap" in mock_socket.eval_lua.call_args[0][0]

    def test_lua_mode_untranslatable_bind_raises_command_error(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_config.define_submap_to_lua.side_effect = ValueError(
            "No Lua mapping for 'bind' = 'SUPER, K, bogus,'"
        )
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        with pytest.raises(hyprland_socket.CommandError, match="No Lua mapping"):
            state.define_submap("bad", [("bind", "SUPER, K, bogus,")])


class TestKeywordLuaModeErrors:
    """Unmapped keywords in Lua mode surface as CommandError, not ValueError.

    Callers throughout hyprmod catch ``HyprlandError`` (the legacy-mode
    failure mode); a raw ``ValueError`` from ``keyword_to_lua`` would
    propagate uncaught and crash flows like the bind-capture submap setup.
    """

    def test_unmapped_keyword_in_lua_mode_raises_command_error(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_config.keyword_to_lua.side_effect = ValueError(
            "No Lua mapping for keyword 'submap' = 'foo'"
        )
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        with pytest.raises(hyprland_socket.CommandError, match="No Lua mapping"):
            state.keyword("submap", "foo")

    def test_mapped_keyword_in_lua_mode_evals(self, online_mocks, tmp_config):
        mock_socket, mock_config = online_mocks
        mock_config.keyword_to_lua.return_value = 'hl.env("X", "1")'
        state = HyprlandState(tmp_config, schema=None)
        state._lua_mode = True

        state.keyword("env", "X, 1")

        mock_socket.eval_lua.assert_called_once_with('hl.env("X", "1")')
