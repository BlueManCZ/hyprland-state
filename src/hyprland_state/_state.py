"""HyprlandState — unified interface to Hyprland's live configuration."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import hyprland_config
import hyprland_schema
import hyprland_socket
from hyprland_config import Document, normalize_gradient_string
from hyprland_schema import HyprOption
from hyprland_socket import Bind, extract_ipc_value

from hyprland_state._animations import Animations
from hyprland_state._monitors import Monitors

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
    "cssgap": "",
    "font_weight": "",
}

# Schema defaults lie about storage shape for CSS-shorthand types
# (e.g. ``gaps_in`` default is ``5`` but ``gaps_in = "5 10 5 10"`` is valid).
# For these, prefer the type-derived hint from ``_TYPE_HINTS`` over the default.
_PREFER_TYPE_HINT_OVER_DEFAULT = frozenset({"cssgap", "font_weight"})


def _load_user_document(path: str | Path | None) -> Document:
    """Load the user's entrypoint, picking Hyprlang or Lua by suffix."""
    target = hyprland_config.default_entrypoint() if path is None else path
    return hyprland_config.load_any(target)


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
        schema: Mapping[str, HyprOption] | None = _UNSET,  # type: ignore[assignment]
        offline: bool | None = None,
    ) -> None:
        """Initialise.

        *path*: config file path (defaults to ``~/.config/hypr/hyprland.conf``).
        *schema*: option metadata dict (keyed by dotted option name).  Defaults
            to the schema matching the running Hyprland version (auto-detected).
            Pass ``None`` to disable schema features.
        *offline*: force offline mode (no IPC).  Auto-detected when ``None``.
        """
        self._document = _load_user_document(path)
        self._pending: dict[str, Any] = {}
        self._animations: Animations | None = None
        self._monitors: Monitors | None = None
        self._listeners: list[Callable[[str, str | None], None]] = []
        # Hyprland 0.55.0+ may run with ``configProvider: lua``, where
        # ``hyprctl keyword`` is rejected and we have to go through
        # ``hyprctl eval`` with a Lua snippet instead. Detect lazily on
        # the first live-apply so offline instances and tests that don't
        # touch IPC don't pay for the round-trip.
        self._lua_mode: bool | None = None

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

    def get_raw(self, key: str) -> dict[str, Any] | None:
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

    def get_default(self, key: str) -> Any:
        """Return the schema default for an option, or ``None`` if not in schema."""
        opt = self.inspect(key)
        return opt.default if opt is not None else None

    def get_fallback_value(self, key: str, managed_path: str | Path) -> Any:
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

    # -- Lua-mode bridge --

    def is_live_lua_mode(self) -> bool:
        """Return ``True`` when the running compositor uses the Lua parser.

        Hyprland 0.55.0+ reports ``configProvider: lua`` in
        ``hyprctl status`` when the user's entrypoint is ``hyprland.lua``.
        In that mode the legacy ``hyprctl keyword`` IPC is rejected and
        live-apply has to go through ``hyprctl eval``. The result is
        cached on the first probe that returned a definitive answer
        (Lua *or* legacy); a transient IPC failure does *not* cache so
        a later call can retry once the compositor recovers.
        """
        if self._lua_mode is not None:
            return self._lua_mode
        probed = self._probe_lua_mode()
        if probed is None:
            # Transient IPC failure — treat as legacy for this call, retry next time.
            return False
        self._lua_mode = probed
        return probed

    def _probe_lua_mode(self) -> bool | None:
        """Probe the compositor for its config provider.

        Returns ``True``/``False`` on a clean answer, ``None`` when the
        IPC call failed and the caller should not cache the result.
        """
        if not self._online:
            return False
        try:
            return hyprland_socket.get_status().get("configProvider") == "lua"
        except hyprland_socket.HyprlandError:
            return None

    def _translate_and_eval(self, translator: Callable[..., str], *args: Any) -> None:
        """Translate via *translator(\\*args)* and run the result as ``eval_lua``.

        ``ValueError`` from the emitter (unmapped keyword/dispatcher)
        surfaces as :class:`hyprland_socket.CommandError` so callers
        catching ``HyprlandError`` handle both translation failures and
        IPC failures with one ``except`` — matching the legacy-mode
        failure mode where everything bubbles up as ``CommandError``.
        """
        try:
            snippet = translator(*args)
        except ValueError as exc:
            raise hyprland_socket.CommandError(str(exc)) from exc
        hyprland_socket.eval_lua(snippet)

    def _send_keyword(self, key: str, value: Any) -> None:
        """Apply a single keyword live, routing through ``eval`` in Lua mode."""
        if self.is_live_lua_mode():
            self._translate_and_eval(hyprland_config.keyword_to_lua, key, value)
        else:
            hyprland_socket.keyword(key, value)

    def _send_keyword_batch(self, changes: list[tuple[str, Any]]) -> list[str | None]:
        """Apply a batch of keywords live, mirroring keyword_batch's result shape.

        Grouping matters for the visual result: Hyprland's PropRefresher
        collapses successive ``scheduleRefresh`` calls within one
        event-loop iteration into one refresh, but only if they happen
        inside the *same* eval — separate ``eval`` IPCs cross the loop
        boundary and can produce visible mid-redraw frames.

        Untranslatable changes (no Lua mapping in the emitter) get their
        error in the corresponding result slot; the rest of the batch
        still applies.
        """
        if not self.is_live_lua_mode():
            return hyprland_socket.keyword_batch(changes)

        # ``snippets`` is *not* aligned with ``changes`` — untranslatable
        # entries are skipped here and only show up in ``results``, which
        # stays 1-to-1 with the input list.
        snippets: list[str] = []
        results: list[str | None] = []
        for key, value in changes:
            try:
                snippets.append(hyprland_config.keyword_to_lua(key, value))
                results.append(None)
            except ValueError as exc:
                results.append(str(exc))

        if not snippets:
            return results

        try:
            hyprland_socket.eval_lua("\n".join(snippets))
        except hyprland_socket.CommandError as exc:
            # The eval failed — propagate to every successfully-translated change.
            msg = str(exc)
            return [msg if err is None else err for err in results]
        return results

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
        self._send_keyword(key, value)
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
        results = self._send_keyword_batch(changes)
        applied: list[tuple[str, Any]] = []
        for (key, value), error in zip(changes, results, strict=True):
            if error is None:
                self._pending[key] = value
                self._notify("options", key)
                applied.append((key, value))
        return applied

    def dispatch(self, dispatcher: str, arg: str = "") -> bool:
        """Execute a Hyprland dispatcher.

        In Lua mode the ``/dispatch`` IPC's legacy shorthand is broken
        (it tries to eval ``hl.dispatch(NAME ARG)`` without quoting and
        without the required ``hl.dsp.*`` value form). We route through
        ``/eval`` with ``hl.dispatch(hl.dsp.*())`` instead.

        Unmapped dispatchers — including currently-unsupported
        ``address:`` selectors — surface as
        :class:`hyprland_socket.CommandError` so callers catching
        ``HyprlandError`` handle them like any other IPC rejection.
        """
        if not self._online:
            return False
        if self.is_live_lua_mode():
            self._translate_and_eval(hyprland_config.dispatch_to_lua, dispatcher, arg)
        else:
            hyprland_socket.dispatch(dispatcher, arg)
        return True

    def define_submap(self, name: str, binds: list[tuple[str, str]]) -> bool:
        """Atomically register a submap with the given binds.

        *binds* is a list of ``(keyword, value)`` pairs — typically
        ``[("bind", "SUPER, Q, killactive,")]``. Hyprland refuses to
        register a submap with no binds; an empty list raises
        :class:`hyprland_socket.CommandError` in both modes rather than
        leaving Hyprland's submap state half-built.

        Lua mode: emits one ``hl.define_submap(NAME, function() … end)``
        eval call. The Lua submap API is declarative — binds have to be
        defined inside the function body, not as separate calls — so
        there's no streaming equivalent of the legacy keyword sequence.

        Hyprlang mode: sends the ``submap=NAME`` / ``bind=…`` /
        ``submap=reset`` sequence as a single ``keyword_batch`` so they
        land in one event-loop iteration — closing the atomicity gap
        with the Lua path.

        Returns ``True`` on success. Untranslatable binds in Lua mode
        surface as :class:`hyprland_socket.CommandError`.
        """
        if not self._online:
            return False
        if not binds:
            raise hyprland_socket.CommandError(
                f"Cannot register submap {name!r} with no binds: Hyprland rejects empty submaps"
            )
        if self.is_live_lua_mode():
            self._translate_and_eval(hyprland_config.define_submap_to_lua, name, binds)
        else:
            batch: list[tuple[str, Any]] = [("submap", name), *binds, ("submap", "reset")]
            results = hyprland_socket.keyword_batch(batch)
            for error in results:
                if error is not None:
                    raise hyprland_socket.CommandError(error)
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

        Returns the list of file paths that were written. An empty list
        is returned when there is nothing pending (no disk write, no
        compositor reload).
        """
        if not self._pending:
            return []
        for key, value in self._pending.items():
            self._document.set(key, value)
        if path is not None:
            self._document.save(path)
            dirty = [Path(path)]
        else:
            dirty = self._document.dirty_files()
            self._document.save()
        self.reload_compositor()
        self._pending.clear()
        return dirty

    def discard(self) -> dict[str, Any]:
        """Revert all pending changes in the compositor to saved values.

        For each pending key, the reverted value is the value the option
        would have *without* the pending change — the on-disk value when
        present, the schema default otherwise. That fully restores the
        compositor to its pre-edit state without requiring the caller to
        carry its own baseline.

        Returns a dict of key → reverted value. Entries are ``None`` only
        when neither the document nor the schema has any value to revert
        to (an option with no default that was never written to disk).
        """
        reverted: dict[str, Any] = {}
        batch: list[tuple[str, Any]] = []
        for key in self._pending:
            saved = self._document.get(key)
            if saved is None:
                saved = self.get_default(key)
            reverted[key] = saved
            if saved is not None:
                batch.append((key, saved))
        if batch and self._online:
            self._send_keyword_batch(batch)
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

        Use this for transient IPC commands (e.g. ``"bind"``, ``"unbind"``)
        that are not config option changes. For config options, use
        ``apply()`` instead. For submap registration use
        :meth:`define_submap` — the bare ``submap`` keyword has no Lua
        equivalent and raises :class:`hyprland_socket.CommandError` in
        Lua mode.

        In Lua-mode (Hyprland 0.55.0+), the call is routed through
        ``hyprctl eval`` with the equivalent Lua snippet — the legacy
        keyword IPC is disabled for Lua configs.
        """
        if not self._online:
            return False
        self._send_keyword(key, value)
        return True

    # -- Inspect --

    def inspect(self, key: str) -> HyprOption | None:
        """Return the schema entry for an option, or ``None`` if not in schema."""
        if self._schema is None:
            return None
        return self._schema.get(key)

    def available(self, key: str) -> bool:
        """Check if an option is available in the running compositor."""
        return self.get_raw(key) is not None

    # -- Reconnect --

    def reconnect(self) -> bool:
        """Re-detect the compositor and update online status.

        Useful when Hyprland was not running at init time, or after a
        compositor restart. Also reloads the schema if the compositor
        version has changed, and clears the cached Lua-mode flag so the
        new instance's ``configProvider`` is probed afresh.

        Returns ``True`` if the compositor is now reachable.
        """
        self._version = _detect_version()
        self._online = self._version is not None
        # ``configProvider`` is fixed at startup, so a fresh Hyprland
        # process can flip from legacy to Lua (or back). Re-probe lazily.
        self._lua_mode = None
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
        self._document = _load_user_document(self._document.path)

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
        for m in self.get_devices().get("mice", []):
            name = m.get("name", "").lower()
            if "touchpad" in name or "trackpad" in name:
                return True
        return False

    def has_touchscreen(self) -> bool:
        """Check if any touchscreen device is connected."""
        return bool(self.get_devices().get("touch"))

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
        opt = self.inspect(key)
        return opt.type if opt is not None else None

    def _resolve_hint(self, key: str, hint: Any) -> Any:
        """Derive a type hint from the schema when none is provided."""
        if hint is None:
            opt = self.inspect(key)
            if opt is not None:
                hint = _hint_from_schema(opt)
        return hint

    def _read_ipc(self, key: str, hint: Any) -> Any:
        """Read a value via IPC, returning *hint* on failure."""
        data = self.get_raw(key)
        if data is None:
            return hint
        return self._extract_value(key, data, hint)

    def _extract_value(self, key: str, data: dict[str, Any], hint: Any) -> Any:
        """Extract a typed value from raw IPC option data.

        Handles color conversion from ARGB ints to ``0xAARRGGBB`` hex strings,
        and gradient normalization from bare hex tokens to ``0x``-prefixed
        colors. An unset color (``int: -1`` / ``set: false``) returns *hint*.
        """
        option_type = self._option_type(key)
        if option_type == "color" and "int" in data:
            if not data.get("set", True) and data["int"] < 0:
                return hint
            return f"0x{data['int'] & 0xFFFFFFFF:08x}"
        if option_type == "gradient":
            # Hyprland's IPC field for gradients was renamed in 0.55.0:
            # 0.54.x and earlier returned ``"custom": "..."``; 0.55+ returns
            # ``"gradient": "..."``. Accept either so the value populates
            # across both compositor versions.
            raw = data.get("gradient")
            if raw is None:
                raw = data.get("custom")
            if raw is not None:
                return normalize_gradient_string(raw)
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


def _load_schema(version: str | None) -> Mapping[str, HyprOption]:
    """Load the schema matching *version*, falling back to the bundled latest."""
    if version is None:
        return hyprland_schema.OPTIONS_BY_KEY

    try:
        return hyprland_schema.load(version).options_by_key
    except hyprland_schema.MigrationError:
        return hyprland_schema.OPTIONS_BY_KEY


def _hint_from_schema(opt: HyprOption) -> Any:
    """Derive a type hint value from a schema option."""
    if opt.type in _PREFER_TYPE_HINT_OVER_DEFAULT:
        return _TYPE_HINTS[opt.type]
    if opt.default is not None:
        return opt.default
    return _TYPE_HINTS.get(opt.type)
