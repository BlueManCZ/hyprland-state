"""Live state interface for Hyprland — options, animations, monitors, binds, and devices."""

from hyprland_state._animations import (
    ANIM_CHILDREN,
    ANIM_FLAT,
    ANIM_LOOKUP,
    ANIMATION_TREE,
    HYPRLAND_NATIVE_CURVES,
    Animations,
    AnimState,
    get_styles_for,
)
from hyprland_state._monitors import Monitors
from hyprland_state._state import HyprlandState

__all__ = [
    "ANIM_CHILDREN",
    "ANIM_FLAT",
    "ANIM_LOOKUP",
    "ANIMATION_TREE",
    "AnimState",
    "Animations",
    "HYPRLAND_NATIVE_CURVES",
    "HyprlandState",
    "Monitors",
    "get_styles_for",
]
