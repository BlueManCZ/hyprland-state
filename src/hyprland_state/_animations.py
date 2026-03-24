"""Animation tree, state, and IPC interface."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import hyprland_socket

# Curves that Hyprland recognises without a bezier= definition.
HYPRLAND_NATIVE_CURVES = frozenset({"default", "linear"})

if TYPE_CHECKING:
    from hyprland_state._state import HyprlandState

# ---------------------------------------------------------------------------
# Animation tree: (name, styles, children)
# Children inherit parent values when not explicitly overridden.
# ---------------------------------------------------------------------------

ANIMATION_TREE = [
    (
        "windows",
        ["slide", "popin", "gnomed"],
        [
            ("windowsIn", [], []),
            ("windowsOut", [], []),
            ("windowsMove", [], []),
        ],
    ),
    (
        "layers",
        ["slide", "popin", "fade"],
        [
            ("layersIn", [], []),
            ("layersOut", [], []),
        ],
    ),
    (
        "fade",
        [],
        [
            ("fadeIn", [], []),
            ("fadeOut", [], []),
            ("fadeSwitch", [], []),
            ("fadeShadow", [], []),
            ("fadeDim", [], []),
            (
                "fadeLayers",
                [],
                [
                    ("fadeLayersIn", [], []),
                    ("fadeLayersOut", [], []),
                ],
            ),
            (
                "fadePopups",
                [],
                [
                    ("fadePopupsIn", [], []),
                    ("fadePopupsOut", [], []),
                ],
            ),
            ("fadeDpms", [], []),
        ],
    ),
    ("border", [], []),
    ("borderangle", ["once", "loop"], []),
    (
        "workspaces",
        ["slide", "slidevert", "fade", "slidefade", "slidefadevert"],
        [
            ("workspacesIn", [], []),
            ("workspacesOut", [], []),
            (
                "specialWorkspace",
                [],
                [
                    ("specialWorkspaceIn", [], []),
                    ("specialWorkspaceOut", [], []),
                ],
            ),
        ],
    ),
    ("zoomFactor", [], []),
    ("monitorAdded", [], []),
]


def _flatten_tree(
    tree: list[tuple[str, list[str], list]], parent: str | None = None, depth: int = 0
) -> list[tuple[str, str | None, int, list[str]]]:
    """Flatten the animation tree into an ordered list of (name, parent, depth, styles)."""
    result: list[tuple[str, str | None, int, list[str]]] = []
    for name, styles, children in tree:
        result.append((name, parent, depth, styles))
        result.extend(_flatten_tree(children, parent=name, depth=depth + 1))
    return result


# Flat list: [(name, parent_name, depth, own_styles)]
ANIM_FLAT: list[tuple[str, str | None, int, list[str]]] = [("global", None, 0, [])] + _flatten_tree(
    ANIMATION_TREE, parent="global", depth=1
)

# Lookup: name -> (parent, depth, styles)
ANIM_LOOKUP: dict[str, tuple[str | None, int, list[str]]] = {
    name: (parent, depth, styles) for name, parent, depth, styles in ANIM_FLAT
}


# Children lookup: parent_name -> [child_names]
def _build_children(flat: list[tuple[str, str | None, int, list[str]]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for name, parent, _, _ in flat:
        if parent is not None:
            children.setdefault(parent, []).append(name)
    return children


ANIM_CHILDREN: dict[str, list[str]] = _build_children(ANIM_FLAT)


def get_styles_for(name: str) -> list[str]:
    """Get available styles for an animation (inheriting from parent chain)."""
    parent, _, styles = ANIM_LOOKUP[name]
    if styles:
        return styles
    while parent and parent in ANIM_LOOKUP:
        _, _, pstyles = ANIM_LOOKUP[parent]
        if pstyles:
            return pstyles
        parent = ANIM_LOOKUP[parent][0]
    return []


# ---------------------------------------------------------------------------
# AnimState
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnimState:
    """State of a single animation."""

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


# ---------------------------------------------------------------------------
# Animations interface
# ---------------------------------------------------------------------------


def _format_animation_kw(name: str, enabled: bool, speed: float, curve: str, style: str) -> str:
    """Format an ``animation`` keyword value for IPC."""
    onoff = int(enabled)
    val = f"{name},{onoff},{speed},{curve}"
    if style:
        val += f",{style}"
    return val


class Animations:
    """Animation subsystem: read, write, and inspect Hyprland animations.

    Accessed via ``HyprlandState.animations``.

    Maintains a cached copy of all animation states, with baseline
    tracking for dirty detection, save, and discard.
    """

    def __init__(self, state: "HyprlandState") -> None:
        self._state = state
        self._cache: dict[str, AnimState] | None = None
        self._baseline: dict[str, AnimState] = {}

    # -- Read --

    def _fetch(
        self,
    ) -> tuple[list[hyprland_socket.Animation], list[hyprland_socket.BezierCurve]] | None:
        """Fetch raw animations and curves from IPC. Returns (anims, curves) or None."""
        return self._state._ipc_get(hyprland_socket.get_animations, default=None)

    def _ensure_cache(self) -> dict[str, AnimState]:
        """Populate the cache from IPC on first access and return it."""
        if self._cache is None:
            self.sync()
        assert self._cache is not None
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
            if self._cache is None:
                self._cache = {}
            return
        anims, _ = result
        old_cache = self._cache or {}
        self._cache = {}
        for a in anims:
            state = AnimState.from_ipc(a)
            # Skip Hyprland-internal animations (double-underscore prefix)
            if state.name.startswith("__"):
                continue
            self._cache[state.name] = state
        # Ensure all known animations have entries
        for name in ANIM_LOOKUP:
            if name not in self._cache:
                self._cache[name] = AnimState(name=name)
        # Snapshot as baseline
        self._baseline = dict(self._cache)
        # Notify for changes
        for name, state in self._cache.items():
            old = old_cache.get(name)
            if old is None or old != state:
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
        batch = []
        for name in changed:
            state = self._cache.get(name)
            if state and state.overridden:
                batch.append(
                    (
                        "animation",
                        _format_animation_kw(
                            state.name, state.enabled, state.speed, state.curve, state.style
                        ),
                    )
                )
        if batch and self._state.online:
            hyprland_socket.keyword_batch(batch)
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

        Returns ``False`` if offline or the IPC command fails.
        """
        if not self._state.online:
            return False

        if curve not in HYPRLAND_NATIVE_CURVES and curve_points:
            self.define_bezier(curve, curve_points)

        try:
            value = _format_animation_kw(name, enabled, speed, curve, style)
            hyprland_socket.keyword("animation", value)
        except hyprland_socket.HyprlandError:
            return False
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
        if self._cache is not None:
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
        """Define a bezier curve in the running compositor."""
        if not self._state.online:
            return False
        x0, y0, x1, y1 = points
        try:
            hyprland_socket.keyword("bezier", f"{name},{x0},{y0},{x1},{y1}")
            return True
        except hyprland_socket.HyprlandError:
            return False

    # -- Inspect --

    def get_styles(self, name: str) -> list[str]:
        """Return available styles for an animation (with inheritance)."""
        return get_styles_for(name)

    def get_parent(self, name: str) -> str | None:
        """Return the parent animation name, or ``None`` for top-level."""
        entry = ANIM_LOOKUP.get(name)
        return entry[0] if entry else None

    def get_children(self, name: str) -> list[str]:
        """Return direct children of an animation node."""
        return ANIM_CHILDREN.get(name, [])

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

    @property
    def tree(self) -> list[tuple[str, str | None, int, list[str]]]:
        """Return the flat animation tree: ``[(name, parent, depth, styles), ...]``."""
        return ANIM_FLAT

    @property
    def names(self) -> list[str]:
        """Return all animation names in tree order."""
        return [name for name, _, _, _ in ANIM_FLAT]
