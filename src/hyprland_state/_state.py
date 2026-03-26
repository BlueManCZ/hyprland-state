"""HyprlandState — unified interface to Hyprland's live configuration."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import hyprland_config
import hyprland_schema
import hyprland_socket
from hyprland_config import Document
from hyprland_socket import Bind, extract_ipc_value

from hyprland_state._animations import Animations
from hyprland_state._monitors import Monitors
from hyprland_state._options import OptionInfo

_UNSET = object()

# Type exemplar values for IPC extraction — tells extract_ipc_value which
# typed field to read.  e.g. 0 means "extract as int", 0.0 means "as float".
_TYPE_HINTS: dict[str, Any] = {
    "bool": False,
    "int": 0,
    "float": 0.0,
    "string": "",
    "color": "",
    "gradient": "",
    "vec2": "",
    "choice": 0,
}


class HyprlandState:
    """Unified interface to Hyprland's live configuration state.

    Combines IPC (hyprland-socket), config files (hyprland-config),
    and schema metadata (hyprland-schema) into a single read/write/inspect API.

    The write flow: ``apply()`` sends a value to the compositor and tracks it
    as pending. ``save()`` writes all pending values to disk. ``discard()``
    reverts the compositor to on-disk values and clears pending state.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        schema: Mapping[str, Any] | None = _UNSET,  # type: ignore[assignment]
        offline: bool | None = None,
    ) -> None:
        """Initialise.

        *path*: config file path (defaults to ``~/.config/hypr/hyprland.conf``).
        *schema*: option metadata dict (keyed by dotted option name).  Defaults
            to the schema matching the running Hyprland version (auto-detected).
            Pass ``None`` to disable schema features.
        *offline*: force offline mode (no IPC).  Auto-detected when ``None``.
        """
        self._document = hyprland_config.load(path)
        self._pending: dict[str, Any] = {}
        self._animations: Animations | None = None
        self._monitors: Monitors | None = None
        self._listeners: list[Callable[[str, str | None], None]] = []

        # Detect online status and compositor version
        if offline is not None:
            self._online = not offline
            self._version: str | None = None
        else:
            self._version = _detect_version()
            self._online = self._version is not None

        # Load schema — match the running compositor version when possible
        if schema is _UNSET:
            schema = _load_schema(self._version)
        self._schema = schema

    # -- Properties --

    @property
    def document(self) -> Document:
        """The underlying hyprland-config Document."""
        return self._document

    @property
    def online(self) -> bool:
        """True if Hyprland IPC is reachable."""
        return self._online

    @property
    def version(self) -> str | None:
        """The running Hyprland version (e.g. ``"0.54.2"``), or ``None`` if offline."""
        return self._version

    @property
    def animations(self) -> Animations:
        """Animation subsystem access."""
        if self._animations is None:
            self._animations = Animations(self)
        return self._animations

    @property
    def monitors(self) -> Monitors:
        """Monitor subsystem access."""
        if self._monitors is None:
            self._monitors = Monitors(self)
        return self._monitors

    # -- Change notifications --

    def on_change(self, callback: Callable[[str, str | None], None]) -> None:
        """Subscribe to state changes.

        *callback* is called as ``callback(category, key)`` where *category*
        is ``"options"``, ``"animations"``, or ``"monitors"``, and *key*
        identifies the specific item that changed (or ``None`` for bulk
        changes like monitor reconfigurations).
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, str | None], None]) -> None:
        """Unsubscribe a previously registered callback."""
        self._listeners.remove(callback)

    def _notify(self, category: str, key: str | None = None) -> None:
        """Fire change notifications to all registered listeners."""
        for cb in self._listeners:
            cb(category, key)

    # -- Read --

    def get(self, key: str, hint: Any = None) -> Any:
        """Read the effective live value of a config option.

        Resolution: IPC (if online) → hint, or config file → hint (offline).
        Type is determined by: explicit hint > schema type > best-guess.
        """
        hint = self._resolve_hint(key, hint)

        if not self._online:
            val = self._document.get(key)
            return val if val is not None else hint

        return self._read_ipc(key, hint)

    def get_raw(self, key: str) -> dict | None:
        """Return raw IPC ``getoption`` response, or ``None`` if offline/unavailable."""
        return self._ipc_get(hyprland_socket.get_option, key, default=None)

    def get_live(self, key: str, hint: Any = None) -> tuple[Any, bool]:
        """Read the live value with availability info.

        Returns ``(value, available)`` where *available* is ``True`` when
        the option was successfully read from the compositor via IPC.
        When unavailable (offline or unknown option), *value* falls back
        to *hint* and *available* is ``False``.
        """
        hint = self._resolve_hint(key, hint)
        if not self._online:
            return hint, False
        data = self.get_raw(key)
        if data is None:
            return hint, False
        return self._extract_value(key, data, hint), True

    def get_disk(self, key: str) -> str | None:
        """Read the value from the config file on disk."""
        return self._document.get(key)

    def get_default(self, key: str) -> Any | None:
        """Return the schema default for an option, or ``None`` if not in schema."""
        if self._schema is None:
            return None
        opt = self._schema.get(key)
        if opt is None:
            return None
        return getattr(opt, "default", None)

    def get_fallback_value(self, key: str, managed_path: str | Path) -> Any | None:
        """Return the value an option would have without our managed config.

        Excludes the Source node pointing to *managed_path* from resolution,
        so only the remaining config tree is considered.
        Falls back to the schema default if no other config sets the key.
        """
        excluded = frozenset({Path(managed_path).resolve()})
        value = self._document.get(key, exclude_sources=excluded)
        if value is not None:
            return value
        return self.get_default(key)

    # -- Write --

    def apply(self, key: str, value: Any, *, validate: bool = True) -> bool:
        """Apply a value to the running compositor and track it as pending.

        The change takes effect immediately in the live session. Call
        ``save()`` to persist pending changes to disk, or ``discard()``
        to revert them.

        When *validate* is ``True`` (the default), the value is checked
        against schema constraints (min/max, enum) before sending to
        the compositor. Set to ``False`` to bypass validation.

        Returns ``True`` on success.
        Raises ``ValueError`` if validation fails.
        """
        if not self._online:
            return False
        if validate:
            self._validate(key, value)
        hyprland_socket.keyword(key, value)
        self._pending[key] = value
        self._notify("options", key)
        return True

    def apply_batch(
        self, changes: list[tuple[str, Any]], *, validate: bool = True
    ) -> list[tuple[str, Any]]:
        """Apply multiple values and track them as pending.

        Sends all changes in a single IPC batch call. Per-command results
        are used to determine which changes succeeded.

        When *validate* is ``True`` (the default), all values are checked
        against schema constraints before any are sent to the compositor.

        Returns the list of ``(key, value)`` pairs that were successfully
        applied. An empty list means nothing succeeded (or offline).
        Raises ``ValueError`` if any value fails validation.
        """
        if not self._online:
            return []
        if validate:
            for key, value in changes:
                self._validate(key, value)
        results = hyprland_socket.keyword_batch(changes)
        applied: list[tuple[str, Any]] = []
        for (key, value), error in zip(changes, results, strict=True):
            if error is None:
                self._pending[key] = value
                self._notify("options", key)
                applied.append((key, value))
        return applied

    def dispatch(self, dispatcher: str, arg: str = "") -> bool:
        """Execute a Hyprland dispatcher."""
        if not self._online:
            return False
        hyprland_socket.dispatch(dispatcher, arg)
        return True

    # -- Pending state --

    def pending(self) -> list[str]:
        """Return keys with unsaved changes."""
        return list(self._pending)

    def is_dirty(self, key: str | None = None) -> bool:
        """Check for unsaved changes, optionally for a specific key."""
        if key is not None:
            return key in self._pending
        return bool(self._pending)

    # -- Persist / revert --

    def save(self, path: Path | None = None) -> list[Path]:
        """Write all pending changes to disk and reload the compositor.

        Pending values are written to the config ``Document``, which is
        then saved atomically. The compositor is reloaded so the on-disk
        config takes effect. Pending state is cleared afterwards.

        *path*: optional alternative file to write to. When ``None``,
        writes to the file(s) the ``Document`` was loaded from.

        Returns the list of file paths that were written.
        """
        for key, value in self._pending.items():
            self._document.set(key, value)
        dirty = self._document.dirty_files()
        if path is not None:
            self._document.save(path)
        else:
            self._document.save()
        self.reload_compositor()
        self._pending.clear()
        return dirty

    def discard(self) -> dict[str, Any]:
        """Revert all pending changes in the compositor to on-disk values.

        Returns a dict of key → reverted on-disk value for each key
        that was successfully reverted.
        """
        reverted: dict[str, Any] = {}
        batch: list[tuple[str, Any]] = []
        for key in self._pending:
            saved = self._document.get(key)
            reverted[key] = saved
            if saved is not None:
                batch.append((key, saved))
        if batch and self._online:
            hyprland_socket.keyword_batch(batch)
        self._pending.clear()
        for key in reverted:
            self._notify("options", key)
        return reverted

    def clear_pending(self) -> None:
        """Clear pending state without reverting or saving.

        Use when the caller handles persistence externally.
        """
        self._pending.clear()

    def keyword(self, key: str, value: Any) -> bool:
        """Send a raw keyword to the compositor without tracking it as pending.

        Use this for transient IPC commands (e.g. ``"submap"``, ``"bind"``,
        ``"unbind"``) that are not config option changes. For config options,
        use ``apply()`` instead.
        """
        if not self._online:
            return False
        hyprland_socket.keyword(key, value)
        return True

    # -- Inspect --

    def inspect(self, key: str) -> OptionInfo | None:
        """Return metadata about an option, or ``None`` if not in schema."""
        if self._schema is None:
            return None
        opt = self._schema.get(key)
        if opt is None:
            return None
        return OptionInfo.from_schema(opt)

    def available(self, key: str) -> bool:
        """Check if an option is available in the running compositor."""
        return self.get_raw(key) is not None

    # -- Reconnect --

    def reconnect(self) -> bool:
        """Re-detect the compositor and update online status.

        Useful when Hyprland was not running at init time, or after a
        compositor restart. Also reloads the schema if the compositor
        version has changed.

        Returns ``True`` if the compositor is now reachable.
        """
        self._version = _detect_version()
        self._online = self._version is not None
        if self._online:
            self._schema = _load_schema(self._version)
            # Reset subsystems so they pick up the new connection state
            self._animations = None
            self._monitors = None
        return self._online

    # -- Sync --

    def sync(self) -> None:
        """Re-read all subsystem state from the running compositor.

        Updates cached state for animations and monitors, and fires
        change notifications for any values that changed. Use this
        after profile activation or compositor reload to bring the
        local state in sync with the compositor.
        """
        if self._animations is not None:
            self._animations.sync()
        if self._monitors is not None:
            self._monitors.sync()

    # -- Refresh --

    def refresh(self, key: str, hint: Any = None) -> Any:
        """Re-read a single key's live value directly from the compositor.

        Unlike ``get()``, this never falls back to the config file — it
        returns *hint* when IPC is unavailable. Use this when you need
        the compositor's actual runtime value without disk fallback.
        """
        hint = self._resolve_hint(key, hint)
        if not self._online:
            return hint
        return self._read_ipc(key, hint)

    def reload_config(self) -> None:
        """Re-parse the on-disk Document from its file path."""
        self._document = hyprland_config.load(self._document.path)

    def reload_compositor(self) -> bool:
        """Tell Hyprland to reload its config."""
        if not self._online:
            return False
        hyprland_socket.reload()
        return True

    # -- Binds --

    def get_binds(self) -> list[Bind]:
        """Read all keybinds from the running compositor."""
        return self._ipc_get(hyprland_socket.get_binds, default=[])

    # -- Devices --

    def get_devices(self) -> dict[str, Any]:
        """Read all input devices from the running compositor."""
        return self._ipc_get(hyprland_socket.get_devices, default={})

    def has_touchpad(self) -> bool:
        """Check if any touchpad or trackpad device is connected."""
        devices = self.get_devices()
        if not devices:
            return False
        return any(
            "touchpad" in m.get("name", "").lower() or "trackpad" in m.get("name", "").lower()
            for m in devices.get("mice", [])
        )

    # -- Internal helpers --

    def _ipc_get[T](self, fn: Callable[..., T], *args: Any, default: T) -> T:
        """Call *fn(*args)* if online, returning its result or *default* on failure."""
        if not self._online:
            return default
        try:
            return fn(*args)
        except hyprland_socket.HyprlandError:
            return default

    def _option_type(self, key: str) -> str | None:
        """Return the schema type string for *key*, or ``None`` if unknown."""
        if self._schema is None:
            return None
        opt = self._schema.get(key)
        return getattr(opt, "type", None) if opt is not None else None

    def _resolve_hint(self, key: str, hint: Any) -> Any:
        """Derive a type hint from the schema when none is provided."""
        if hint is None and self._schema is not None:
            opt = self._schema.get(key)
            if opt is not None:
                hint = _hint_from_schema(opt)
        return hint

    def _read_ipc(self, key: str, hint: Any) -> Any:
        """Read a value via IPC, returning *hint* on failure."""
        data = self.get_raw(key)
        if data is None:
            return hint
        return self._extract_value(key, data, hint)

    def _extract_value(self, key: str, data: dict, hint: Any) -> Any:
        """Extract a typed value from raw IPC option data.

        Handles color conversion from ARGB ints to ``0xAARRGGBB`` hex strings.
        An unset color (``int: -1`` / ``set: false``) returns *hint*.
        """
        if self._option_type(key) == "color" and "int" in data:
            if not data.get("set", True) and data["int"] < 0:
                return hint
            return f"0x{data['int'] & 0xFFFFFFFF:08x}"
        return extract_ipc_value(data, hint)

    # -- Validation --

    def _validate(self, key: str, value: Any) -> None:
        """Check *value* against schema constraints for *key*.

        Raises ``ValueError`` if the value violates a constraint.
        Does nothing when the schema is unavailable or the key is unknown.
        """
        info = self.inspect(key)
        if info is None:
            return
        error = info.validate(value)
        if error is not None:
            raise ValueError(error)


def _detect_version() -> str | None:
    """Query the running Hyprland version, or return ``None`` if unreachable."""
    try:
        return hyprland_socket.get_version().version or None
    except hyprland_socket.HyprlandError:
        return None


def _load_schema(version: str | None) -> Mapping[str, Any]:
    """Load the schema matching *version*, falling back to the bundled latest."""
    if version is None:
        return hyprland_schema.OPTIONS_BY_KEY

    try:
        return hyprland_schema.load(version).options_by_key
    except hyprland_schema.MigrationError:
        return hyprland_schema.OPTIONS_BY_KEY


def _hint_from_schema(opt: Any) -> Any:
    """Derive a type hint value from a schema option object."""
    default = getattr(opt, "default", None)
    if default is not None:
        return default
    return _TYPE_HINTS.get(getattr(opt, "type", ""))
