"""Shared test fixtures."""

from unittest.mock import MagicMock

import pytest


def _ipc_get_online(fn, *args, default=None):
    """Mimic HyprlandState._ipc_get for online mocks."""
    try:
        return fn(*args)
    except Exception:
        return default


@pytest.fixture
def mock_state():
    """A MagicMock HyprlandState with online=True."""
    state = MagicMock()
    state.online = True
    state._ipc_get.side_effect = _ipc_get_online
    return state


@pytest.fixture
def mock_state_offline():
    """A MagicMock HyprlandState with online=False."""
    state = MagicMock()
    state.online = False
    state._ipc_get.side_effect = lambda fn, *args, default=None: default
    return state
