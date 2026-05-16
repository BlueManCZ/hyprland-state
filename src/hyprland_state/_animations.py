"""Animation state + IPC interface.

The animation tree, ``HYPRLAND_NATIVE_CURVES``, and the ``AnimationData``
parse/serialize for the ``animation =`` keyword line live in
``hyprland-config`` — re-exported here for back-compat with existing
consumers and because the ``Animations`` subsystem is what most callers
reach for.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import hyprland_socket
from hyprland_config import (
    ANIM_CHILDREN,
    ANIM_FLAT,
    ANIM_LOOKUP,
    ANIMATION_TREE,
    HYPRLAND_NATIVE_CURVES,
    AnimationData,
    get_styles_for,
)

if TYPE_CHECKING:
    from hyprland_state._state import HyprlandState


# Re-exports — kept in __all__ in :mod:`hyprland_state.__init__`.
__all__ = [
    "ANIM_CHILDREN",
    "ANIM_FLAT",
    "ANIM_LOOKUP",
    "ANIMATION_TREE",
    "HYPRLAND_NATIVE_CURVES",
    "AnimState",
    "Animations",
    "get_styles_for",
]


# ---------------------------------------------------------------------------
# AnimState
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnimState:
    """State of a single animation.

    ``overridden`` is the state-layer addition over :class:`AnimationData`:
    it tracks whether the animation has an explicit ``animation =`` line
    (as reported by Hyprland's IPC) or is using inherited / default values.
    The remaining fields mirror :class:`AnimationData`'s shape.
    """

    name: str
    overridden: bool = False
    enabled: bool = True
    speed: float = 0.0
    curve: str = ""
    style: str = ""

    @classmethod
    def from_ipc(cls, a: hyprland_socket.Animation) -> Self:
        """Create from a ``hyprland_socket.Animation``."""
        return cls(
            name=a.name,
            overridden=a.overridden,
            enabled=a.enabled,
            speed=a.speed,
            curve=a.bezier,
            style=a.style,
        )

    @classmethod
    def from_keyword(cls, name: str, parts: list[str]) -> Self:
        """Parse pre-split ``animation`` keyword fields into an AnimState.

        Expected layout: ``[name, onoff, speed, curve, style?]``. A presence
        in any config line implies the animation was overridden, so
        ``overridden=True``. Delegates the field parsing to
        :meth:`AnimationData.from_parts`.
        """
        data = AnimationData.from_parts(name, parts)
        return cls(
            name=data.name,
            overridden=True,
            enabled=data.enabled,
            speed=data.speed,
            curve=data.curve,
            style=data.style,
        )

    def to_data(self) -> AnimationData:
        """Project to the format-only :class:`AnimationData` shape."""
        return AnimationData(
            name=self.name,
            enabled=self.enabled,
            speed=self.speed,
            curve=self.curve,
            style=self.style,
        )

    def body(self) -> str:
        """Render as an ``animation =`` keyword value (no keyword prefix)."""
        return self.to_data().body()

    def to_line(self) -> str:
        """Render as a full ``animation = ...`` config line."""
        return self.to_data().to_line()


# ---------------------------------------------------------------------------
# Animations interface
# ---------------------------------------------------------------------------


class Animations:
    """Animation subsystem: read, write, and inspect Hyprland animations.

    Accessed via ``HyprlandState.animations``.

    Maintains a cached copy of all animation states, with baseline
    tracking for dirty detection, save, and discard.
    """

    def __init__(self, state: "HyprlandState") -> None:
        self._state = state
        self._cache: dict[str, AnimState] = {}
        self._baseline: dict[str, AnimState] = {}
        self._synced = False

    # -- Read --

    def _fetch(
        self,
    ) -> tuple[list[hyprland_socket.Animation], list[hyprland_socket.BezierCurve]] | None:
        """Fetch raw animations and curves from IPC. Returns (anims, curves) or None."""
        return self._state._ipc_get(hyprland_socket.get_animations, default=None)

    def _ensure_cache(self) -> dict[str, AnimState]:
        """Populate the cache from IPC on first access and return it."""
        if not self._synced:
            self.sync()
        return self._cache

    def get_all(self) -> list[AnimState]:
        """Read all animation states from the running compositor (no cache)."""
        result = self._fetch()
        if result is None:
            return []
        anims, _ = result
        return [AnimState.from_ipc(a) for a in anims]

    def get(self, name: str) -> AnimState | None:
        """Read a single animation state from the compositor (no cache)."""
        result = self._fetch()
        if result is None:
            return None
        anims, _ = result
        for a in anims:
            if a.name == name:
                return AnimState.from_ipc(a)
        return None

    def get_cached(self, name: str) -> AnimState | None:
        """Return a cached animation state.

        Triggers a one-time IPC sync on first access; subsequent calls
        return from cache without IPC.
        """
        return self._ensure_cache().get(name)

    def get_all_cached(self) -> dict[str, AnimState]:
        """Return all cached animation states.

        Triggers a one-time IPC sync on first access; subsequent calls
        return from cache without IPC.
        """
        return dict(self._ensure_cache())

    def update_cached(self, name: str, state: AnimState) -> None:
        """Update a cache entry directly without IPC.

        Used by app-level operations (undo/redo, unmanage) that need
        to set a cache value that may not be overridden.
        """
        self._ensure_cache()[name] = state
        self._state._notify("animations", name)

    # -- Sync / baseline --

    def sync(self) -> None:
        """Re-read all animations from IPC, update cache and baseline.

        Fires ``("animations", name)`` notifications for each animation
        that changed relative to the previous cache state.
        """
        result = self._fetch()
        if result is None:
            # Offline: leave the cache as-is but mark synced so we don't
            # retry on every read. Reconnect() resets _synced.
            self._synced = True
            return
        anims, _ = result
        old_cache = self._cache
        new_cache: dict[str, AnimState] = {}
        for a in anims:
            state = AnimState.from_ipc(a)
            # Skip Hyprland-internal animations (double-underscore prefix)
            if state.name.startswith("__"):
                continue
            new_cache[state.name] = state
        # Ensure all known animations have entries
        for name in ANIM_LOOKUP:
            if name not in new_cache:
                new_cache[name] = AnimState(name=name)
        self._cache = new_cache
        self._baseline = dict(new_cache)
        self._synced = True
        # Notify for changes
        for name, state in new_cache.items():
            if old_cache.get(name) != state:
                self._state._notify("animations", name)

    def get_baseline(self, name: str) -> AnimState | None:
        """Return the saved baseline state for an animation, or ``None``."""
        return self._baseline.get(name)

    def set_baseline(self, name: str, state: AnimState) -> None:
        """Set the saved baseline for an animation."""
        self._baseline[name] = state

    def is_dirty(self, name: str | None = None) -> bool:
        """Check if animation(s) differ from baseline.

        If *name* is given, check only that animation. Otherwise check all.
        """
        cache = self._ensure_cache()
        if name is not None:
            return cache.get(name) != self._baseline.get(name)
        return any(cache.get(k) != self._baseline.get(k) for k in cache)

    def mark_saved(self) -> None:
        """Snapshot the current cache as the saved baseline."""
        self._baseline = dict(self._ensure_cache())

    def discard(self) -> None:
        """Revert cache to baseline and re-apply all baseline states via IPC."""
        cache = self._ensure_cache()
        changed = [name for name in cache if cache[name] != self._baseline.get(name)]
        self._cache = dict(self._baseline)
        batch: list[tuple[str, Any]] = [
            ("animation", state.body())
            for name in changed
            if (state := self._cache.get(name)) is not None and state.overridden
        ]
        if batch and self._state.online:
            self._state._send_keyword_batch(batch)
        for name in changed:
            self._state._notify("animations", name)

    def get_curves(self) -> dict[str, tuple[float, float, float, float]]:
        """Read all bezier curves defined in the running compositor.

        Returns dict of curve_name -> (x0, y0, x1, y1).
        """
        result = self._fetch()
        if result is None:
            return {}
        _, curves = result
        return {c.name: c.points for c in curves if c.name}

    # -- Write --

    def _send_animation(
        self,
        name: str,
        enabled: bool,
        speed: float,
        curve: str,
        style: str = "",
        *,
        curve_points: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """Define the bezier (if needed) and send the animation keyword via IPC.

        Returns ``False`` if offline. Raises
        :class:`hyprland_socket.HyprlandError` if the compositor rejects
        the keyword, matching the failure mode of :meth:`HyprlandState.apply`.
        """
        if not self._state.online:
            return False

        if curve not in HYPRLAND_NATIVE_CURVES and curve_points:
            self.define_bezier(curve, curve_points)

        state = AnimState(
            name=name, overridden=True, enabled=enabled, speed=speed, curve=curve, style=style
        )
        self._state._send_keyword("animation", state.body())
        return True

    def apply(
        self,
        name: str,
        enabled: bool,
        speed: float,
        curve: str,
        style: str = "",
        *,
        curve_points: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """Apply an animation setting to the running compositor.

        If the curve is not a native Hyprland curve, pass *curve_points*
        so the bezier can be defined before use.

        Updates the internal cache and fires a change notification on success.
        """
        if not self._send_animation(name, enabled, speed, curve, style, curve_points=curve_points):
            return False
        self._cache[name] = AnimState(
            name=name,
            overridden=True,
            enabled=enabled,
            speed=speed,
            curve=curve,
            style=style,
        )
        self._state._notify("animations", name)
        return True

    def apply_state(
        self,
        anim: AnimState,
        *,
        curve_points: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """Apply an ``AnimState`` to the running compositor.

        Only meaningful when the animation is overridden.
        Updates the internal cache on success.

        *curve_points*: if the curve is not a Hyprland native curve, pass the
        control points so the bezier can be defined before use.
        """
        if not anim.overridden:
            return False
        return self.apply(
            anim.name,
            anim.enabled,
            anim.speed,
            anim.curve,
            anim.style,
            curve_points=curve_points,
        )

    def preview(
        self,
        name: str,
        enabled: bool,
        speed: float,
        curve: str,
        style: str = "",
        *,
        curve_points: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """Send an animation setting to the compositor without updating the cache.

        Use this for temporary live previews that should not affect dirty state
        or change notifications.
        """
        return self._send_animation(name, enabled, speed, curve, style, curve_points=curve_points)

    def define_bezier(self, name: str, points: tuple[float, float, float, float]) -> bool:
        """Define a bezier curve in the running compositor.

        Returns ``False`` if offline. Raises
        :class:`hyprland_socket.HyprlandError` if the compositor rejects
        the keyword.
        """
        if not self._state.online:
            return False
        x0, y0, x1, y1 = points
        self._state._send_keyword("bezier", f"{name},{x0},{y0},{x1},{y1}")
        return True

    # -- Inspect --

    def get_styles(self, name: str) -> tuple[str, ...]:
        """Return available styles for an animation (with inheritance)."""
        return get_styles_for(name)

    def get_parent(self, name: str) -> str | None:
        """Return the parent animation name, or ``None`` for top-level."""
        entry = ANIM_LOOKUP.get(name)
        return entry[0] if entry else None

    def get_children(self, name: str) -> tuple[str, ...]:
        """Return direct children of an animation node."""
        return ANIM_CHILDREN.get(name, ())

    def get_effective(self, name: str) -> tuple[bool, float, str, str]:
        """Resolve effective values by walking up the parent chain.

        Returns ``(enabled, speed, curve, style)`` — from the animation
        itself if it is overridden, or from the nearest overridden ancestor.
        Falls back to global defaults ``(True, 8.0, "default", "")``.
        """
        cache = self._ensure_cache()
        current: str | None = name
        while current and current in ANIM_LOOKUP:
            state = cache.get(current)
            if state and state.overridden:
                return state.enabled, state.speed, state.curve, state.style
            current = ANIM_LOOKUP[current][0]

        # Global defaults
        return True, 8.0, "default", ""

    def get_fallback(self, name: str, managed_path: str | Path) -> AnimState | None:
        """Resolve what an animation would be without our managed config.

        Reads the config document tree excluding *managed_path* and finds
        the last ``animation`` keyword line for *name*. Returns an
        ``AnimState`` parsed from that line, or ``None`` if no fallback
        exists (the animation would be inherited).
        """
        doc = self._state.document
        excluded = frozenset({Path(managed_path).resolve()})
        lines = doc.find_all("animation", exclude_sources=excluded)
        # Last match for this animation name wins (Hyprland semantics)
        for kw in reversed(lines):
            parts = [p.strip() for p in kw.value.split(",")]
            if parts and parts[0] == name:
                return AnimState.from_keyword(name, parts)
        return None

    @property
    def tree(self) -> tuple[tuple[str, str | None, int, tuple[str, ...]], ...]:
        """Return the flat animation tree: ``((name, parent, depth, styles), ...)``."""
        return ANIM_FLAT

    @property
    def names(self) -> tuple[str, ...]:
        """Return all animation names in tree order."""
        return tuple(name for name, _, _, _ in ANIM_FLAT)
