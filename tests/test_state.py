"""Tests for HyprlandState."""

from unittest.mock import MagicMock, patch

import hyprland_socket
import pytest

from hyprland_state import HyprlandState, OptionInfo


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
        mock_socket.HyprlandError = Exception
        mock_socket.get_version.return_value = MagicMock(version="0.54.2")
        mock_config.load.return_value = MagicMock()
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
        mock_doc = mock_config.load.return_value
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
        mock_doc = mock_config.load.return_value
        mock_doc.get.return_value = "2"

        state = HyprlandState(tmp_config, schema=None)
        state.apply("general:border_size", 5)
        reverted = state.discard()

        assert "general:border_size" in reverted
        assert reverted["general:border_size"] == "2"
        assert not state.is_dirty()
        # Should have sent the on-disk value back via keyword_batch
        mock_socket.keyword_batch.assert_called_once_with([("general:border_size", "2")])

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


class TestSchemaIntegration:
    def test_get_default(self, tmp_config):
        schema = {"general:border_size": MagicMock(type="int", default=1)}
        state = HyprlandState(tmp_config, offline=True, schema=schema)
        assert state.get_default("general:border_size") == 1

    def test_get_default_missing(self, tmp_config):
        state = HyprlandState(tmp_config, offline=True, schema={})
        assert state.get_default("nonexistent") is None

    def test_get_default_no_schema(self, offline_state):
        assert offline_state.get_default("anything") is None

    def test_inspect(self, tmp_config):
        opt = MagicMock()
        opt.key = "general:border_size"
        opt.type = "int"
        opt.default = 1
        opt.description = "Border size"
        opt.min = 0
        opt.max = 20
        opt.enum_values = None
        state = HyprlandState(tmp_config, offline=True, schema={"general:border_size": opt})

        info = state.inspect("general:border_size")
        assert isinstance(info, OptionInfo)
        assert info.type == "int"
        assert info.default == 1

    def test_inspect_missing(self, offline_state):
        assert offline_state.inspect("anything") is None


def _make_schema_opt(**kwargs):
    opt = MagicMock()
    opt.key = kwargs.get("key", "general:border_size")
    opt.type = kwargs.get("type", "int")
    opt.default = kwargs.get("default", 1)
    opt.description = kwargs.get("description", "")
    opt.min = kwargs.get("min", 0)
    opt.max = kwargs.get("max", 20)
    opt.enum_values = kwargs.get("enum_values", None)
    return opt


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
        mock_socket.get_option.side_effect = Exception("unknown")

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


class TestReloadConfig:
    def test_reload_config_reloads_document(self, offline_state, tmp_config):
        tmp_config.write_text("general {\n    border_size = 99\n}\n")
        offline_state.reload_config()
        assert offline_state.get_disk("general:border_size") == "99"
