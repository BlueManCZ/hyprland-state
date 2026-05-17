"""Monitor subsystem: read, apply, and inspect Hyprland monitors."""

from collections.abc import Sequence
from copy import copy
from typing import TYPE_CHECKING

import hyprland_socket
from hyprland_monitors import MonitorState, lines_from_monitors

if TYPE_CHECKING:
    from hyprland_state._state import HyprlandState


class Monitors:
    """Monitor subsystem: read and apply monitor configuration.

    Accessed via ``HyprlandState.monitors``.

    Wraps hyprland-socket IPC for reading and hyprland-monitors
    for the ``MonitorState`` model and config serialization.

    Maintains a cached copy of all monitor states, with baseline
    tracking for dirty detection, save, and discard.
    """

    def __init__(self, state: "HyprlandState") -> None:
        self._state = state
        self._cache: list[MonitorState] = []
        self._baseline: list[MonitorState] = []
        self._synced = False

    # -- Read --

    def get_all(self) -> list[MonitorState]:
        """Read all monitors from the running compositor (no cache).

        Returns a list of ``MonitorState`` objects converted from IPC data.
        """
        raw = self._state._ipc_get(hyprland_socket.get_monitors, default=[])
        return [MonitorState.from_ipc(m) for m in raw]

    def get(self, name: str) -> MonitorState | None:
        """Read a single monitor by name (no cache)."""
        for m in self.get_all():
            if m.name == name:
                return m
        return None

    def get_cached(self, name: str) -> MonitorState | None:
        """Return a cached monitor state by name.

        Triggers a one-time IPC sync on first access; subsequent calls
        return from cache without IPC.
        """
        for m in self.get_all_cached():
            if m.name == name:
                return m
        return None

    def get_all_cached(self) -> list[MonitorState]:
        """Return all cached monitor states.

        Triggers a one-time IPC sync on first access; subsequent calls
        return from cache without IPC.
        """
        if not self._synced:
            self.sync()
        return list(self._cache)

    # -- Sync / baseline --

    def sync(self) -> None:
        """Re-read all monitors from IPC, update cache and baseline.

        Fires a ``("monitors", None)`` notification when state changed.
        """
        was_synced = self._synced
        old_cache = list(self._cache)
        self._cache = sorted(self.get_all(), key=lambda m: m.name)
        self._baseline = [copy(m) for m in self._cache]
        self._synced = True
        if not was_synced or old_cache != self._cache:
            self._state._notify("monitors", None)

    def is_dirty(self) -> bool:
        """Check if monitors differ from baseline."""
        return self._cache != self._baseline

    def mark_saved(self) -> None:
        """Snapshot the current cache as the saved baseline."""
        self._baseline = [copy(m) for m in self._cache]

    def discard(self) -> None:
        """Revert cache to baseline and re-apply via IPC."""
        changed = self.is_dirty()
        self._cache = [copy(m) for m in self._baseline]
        if changed:
            self.apply(self._cache)

    # -- Write --

    def apply(self, monitors: Sequence[MonitorState]) -> bool:
        """Apply monitor configuration to the running compositor.

        Generates ``monitor = ...`` keyword lines from the monitor states
        and applies them via IPC batch. Updates the cache on success.

        Returns ``True`` if all commands succeeded, ``False`` if offline
        or any command was rejected.
        """
        if not self._state.online:
            return False
        # Live-apply needs explicit SDR defaults — Hyprland's ``hl.monitor()`` is
        # additive, so omitted ``sdrbrightness``/``sdrsaturation`` keep the
        # previous value rather than resetting to 1.0.
        lines = lines_from_monitors(monitors, explicit_hdr_defaults=True)
        results = self._state._send_keyword_batch([("monitor", line) for line in lines])
        ok = all(r is None for r in results)
        if ok:
            self._cache = sorted([copy(m) for m in monitors], key=lambda m: m.name)
            self._state._notify("monitors", None)
        return ok

    def apply_one(self, monitor: MonitorState) -> bool:
        """Apply a single monitor's configuration."""
        return self.apply([monitor])

    def disable(self, name: str) -> bool:
        """Disable a monitor by name."""
        if not self._state.online:
            return False
        try:
            self._state._send_keyword("monitor", f"{name}, disable")
            return True
        except hyprland_socket.HyprlandError:
            return False
