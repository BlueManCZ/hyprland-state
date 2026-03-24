"""Tests for Monitors subsystem."""

from unittest.mock import MagicMock, patch

from hyprland_state import Monitors


def _make_ipc_monitor(name="DP-1", width=1920, height=1080, refresh_rate=60.0):
    m = MagicMock()
    m.name = name
    m.make = "Test"
    m.model = "Monitor"
    m.width = width
    m.height = height
    m.refresh_rate = refresh_rate
    m.x = 0
    m.y = 0
    m.scale = 1.0
    m.id = 0
    m.focused = True
    m.dpms_status = True
    m.vrr = False
    m.disabled = False
    m.mirror_of = ""
    m.available_modes = ("1920x1080@60.00Hz",)
    m.transform = 0
    m.bit_depth = 8
    return m


class TestGetAll:
    def test_offline_returns_empty(self, mock_state_offline):
        monitors = Monitors(mock_state_offline)
        assert monitors.get_all() == []

    @patch("hyprland_state._monitors.hyprland_socket")
    def test_returns_monitor_states(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        mock_socket.get_monitors.return_value = [_make_ipc_monitor()]

        monitors = Monitors(mock_state)
        result = monitors.get_all()
        assert len(result) == 1
        assert result[0].name == "DP-1"

    @patch("hyprland_state._monitors.hyprland_socket")
    def test_ipc_error_returns_empty(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        mock_socket.get_monitors.side_effect = Exception("socket error")

        monitors = Monitors(mock_state)
        assert monitors.get_all() == []


class TestGet:
    @patch("hyprland_state._monitors.hyprland_socket")
    def test_found(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        mock_socket.get_monitors.return_value = [
            _make_ipc_monitor("DP-1"),
            _make_ipc_monitor("HDMI-A-1"),
        ]

        monitors = Monitors(mock_state)
        result = monitors.get("HDMI-A-1")
        assert result is not None
        assert result.name == "HDMI-A-1"

    @patch("hyprland_state._monitors.hyprland_socket")
    def test_not_found(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        mock_socket.get_monitors.return_value = [_make_ipc_monitor("DP-1")]

        monitors = Monitors(mock_state)
        assert monitors.get("nonexistent") is None


class TestApply:
    @patch("hyprland_state._monitors.hyprland_socket")
    @patch("hyprland_state._monitors.lines_from_monitors")
    def test_apply_calls_keyword_batch(self, mock_lines, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        mock_lines.return_value = ["DP-1, 1920x1080@60.00Hz, 0x0, 1"]
        mock_states = [MagicMock()]

        monitors = Monitors(mock_state)
        assert monitors.apply(mock_states)
        mock_socket.keyword_batch.assert_called_once_with(
            [("monitor", "DP-1, 1920x1080@60.00Hz, 0x0, 1")]
        )

    def test_apply_offline_returns_false(self, mock_state_offline):
        monitors = Monitors(mock_state_offline)
        assert not monitors.apply([])


class TestDisable:
    @patch("hyprland_state._monitors.hyprland_socket")
    def test_disable_sends_keyword(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        monitors = Monitors(mock_state)
        assert monitors.disable("DP-2")
        mock_socket.keyword.assert_called_once_with("monitor", "DP-2, disable")

    def test_disable_offline_returns_false(self, mock_state_offline):
        monitors = Monitors(mock_state_offline)
        assert not monitors.disable("DP-2")
