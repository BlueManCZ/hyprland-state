"""Live state interface for Hyprland — options, animations, monitors, and bezier."""

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
from hyprland_state._options import OptionInfo
from hyprland_state._state import HyprlandState

__all__ = [
    # State
    "HyprlandState",
    "OptionInfo",
    # Subsystems
    "Animations",
    "Monitors",
    # Animations
    "ANIM_CHILDREN",
    "ANIM_FLAT",
    "ANIM_LOOKUP",
    "ANIMATION_TREE",
    "AnimState",
    "get_styles_for",
    # Bezier
    "HYPRLAND_NATIVE_CURVES",
]
