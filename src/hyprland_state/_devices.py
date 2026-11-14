"""Touchpad detection for the input devices Hyprland reports.

``hyprctl devices`` lists pointers by name only: there is no flag saying
which of them is a touchpad. Most touchpads carry "touchpad" or "trackpad"
in the name, but built-in I2C/SMBus ones can be named by their hardware ID
alone (``pnp0c50:0b-0911:5288``), so the name is not enough.

udev knows the answer (``ID_INPUT_TOUCHPAD=1``), keyed by the kernel's
device name. The helpers here read udev's database for the touchpads on the
system and match them back to Hyprland's names, so a touchpad that Hyprland
does not manage never makes a plain mouse count as one.
"""

import re
from collections.abc import Iterable
from pathlib import Path

SYS_INPUT = Path("/sys/class/input")
UDEV_DATA = Path("/run/udev/data")

_TOUCHPAD_PROPERTY = "E:ID_INPUT_TOUCHPAD=1"

# Hyprland appends ``-N`` to a device whose name another device already
# holds (``getNameForNewDevice``), which normalization turns into `` N``.
_DEDUP_SUFFIX = re.compile(r" \d+$")


def normalize_device_name(name: str) -> str:
    """Fold the differences between kernel and Hyprland device names.

    Hyprland lowercases device names and replaces spaces with dashes, so an
    I2C touchpad reported by the kernel as ``PNP0C50:0b 0911:5288`` shows up as
    ``pnp0c50:0b-0911:5288``. Treat whitespace and hyphens as equivalent so the
    two forms compare equal.
    """
    return " ".join(name.lower().replace("-", " ").split())


def udev_touchpad_names(sys_input: Path = SYS_INPUT, udev_data: Path = UDEV_DATA) -> set[str]:
    """Return normalized names of the devices udev marks as touchpads.

    Devices whose sysfs or udev entries cannot be read are skipped, so the
    result is empty where udev's database is unavailable (a sandbox, say).
    """
    names: set[str] = set()
    for event in sys_input.glob("event*"):
        try:
            devnum = (event / "dev").read_text().strip()
            properties = (udev_data / f"c{devnum}").read_text(errors="ignore").splitlines()
            if _TOUCHPAD_PROPERTY not in properties:
                continue
            name = normalize_device_name((event / "device" / "name").read_text(errors="ignore"))
        except OSError:
            continue
        if name:
            names.add(name)
    return names


def any_touchpad(hyprland_names: Iterable[str]) -> bool:
    """Check whether any of Hyprland's pointer names belongs to a touchpad.

    The name is tried first. udev is consulted only when no name settles it,
    and a udev touchpad counts only if it is one of *hyprland_names*.
    """
    names = [normalize_device_name(name) for name in hyprland_names]
    if any("touchpad" in name or "trackpad" in name for name in names):
        return True
    if not names:
        return False

    udev_names = udev_touchpad_names()
    return any(name in udev_names or _DEDUP_SUFFIX.sub("", name) in udev_names for name in names)
