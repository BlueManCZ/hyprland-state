"""Tests for touchpad detection against udev's device database."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hyprland_state._devices import any_touchpad, normalize_device_name, udev_touchpad_names

I2C_TOUCHPAD = "PNP0C50:0b 0911:5288"


class FakeInputTree:
    """A throwaway copy of the sysfs and udev layout the lookup reads."""

    def __init__(self, root: Path) -> None:
        self.sys_input = root / "sys" / "class" / "input"
        self.udev_data = root / "run" / "udev" / "data"
        self.sys_input.mkdir(parents=True)
        self.udev_data.mkdir(parents=True)
        self._minor = 64

    def add(self, name: str, *properties: str, udev_record: bool = True) -> None:
        """Add an ``eventN`` node called *name* carrying udev *properties*."""
        event = self.sys_input / f"event{self._minor - 64}"
        (event / "device").mkdir(parents=True)
        (event / "dev").write_text(f"13:{self._minor}\n")
        (event / "device" / "name").write_text(f"{name}\n")
        if udev_record:
            lines = ["I:5229687", "E:ID_INPUT=1", *(f"E:{p}" for p in properties), "V:1"]
            (self.udev_data / f"c13:{self._minor}").write_text("\n".join(lines) + "\n")
        self._minor += 1

    def touchpad_names(self) -> set[str]:
        return udev_touchpad_names(self.sys_input, self.udev_data)


@pytest.fixture
def tree(tmp_path):
    return FakeInputTree(tmp_path)


@pytest.fixture
def udev_touchpads():
    """Stand in for the udev lookup, reporting no touchpads unless told otherwise."""
    with patch("hyprland_state._devices.udev_touchpad_names", return_value=set()) as lookup:
        yield lookup


class TestNormalizeDeviceName:
    @pytest.mark.parametrize(
        ("kernel", "hyprland"),
        [
            ("PNP0C50:0b 0911:5288", "pnp0c50:0b-0911:5288"),
            ("Logitech USB Mouse", "logitech-usb-mouse"),
            ("Razer  Razer DeathAdder V2", "razer--razer-deathadder-v2"),
            ("Wacom Pen-and-Touch", "wacom-pen-and-touch"),
        ],
    )
    def test_kernel_and_hyprland_forms_compare_equal(self, kernel, hyprland):
        assert normalize_device_name(kernel) == normalize_device_name(hyprland)

    def test_strips_surrounding_whitespace(self):
        assert normalize_device_name("  ELAN Touchpad\n") == "elan touchpad"

    def test_different_devices_stay_different(self):
        assert normalize_device_name("PNP0C50:0b 0911:5288") != normalize_device_name(
            "PNP0C50:0b 0911:5289"
        )


class TestUdevTouchpadNames:
    def test_finds_device_tagged_as_touchpad(self, tree):
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1", "ID_INPUT_TOUCHPAD_INTEGRATION=internal")

        assert tree.touchpad_names() == {"pnp0c50:0b 0911:5288"}

    def test_ignores_other_input_devices(self, tree):
        tree.add("Logitech USB Mouse", "ID_INPUT_MOUSE=1")
        tree.add("AT Translated Set 2 keyboard", "ID_INPUT_KEY=1", "ID_INPUT_KEYBOARD=1")
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")

        assert tree.touchpad_names() == {"pnp0c50:0b 0911:5288"}

    def test_requires_the_exact_property(self, tree):
        """Neither a sibling property nor a zero value marks a touchpad."""
        tree.add("Integration Only", "ID_INPUT_TOUCHPAD_INTEGRATION=internal")
        tree.add("Explicitly Off", "ID_INPUT_TOUCHPAD=0")
        tree.add("Longer Value", "ID_INPUT_TOUCHPAD=10")

        assert tree.touchpad_names() == set()

    def test_ignores_non_event_nodes(self, tree):
        """``mouseN``, ``inputN`` and ``mice`` share the directory with ``eventN``."""
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")
        for node in ("mouse0", "input5", "mice"):
            (tree.sys_input / node).mkdir()

        assert tree.touchpad_names() == {"pnp0c50:0b 0911:5288"}

    def test_skips_device_without_udev_record(self, tree):
        tree.add("Not Yet Processed", udev_record=False)
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")

        assert tree.touchpad_names() == {"pnp0c50:0b 0911:5288"}

    def test_skips_touchpad_whose_name_is_unreadable(self, tree):
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")
        (tree.sys_input / "event0" / "device" / "name").unlink()

        assert tree.touchpad_names() == set()

    def test_skips_touchpad_with_blank_name(self, tree):
        tree.add("", "ID_INPUT_TOUCHPAD=1")

        assert tree.touchpad_names() == set()

    def test_collects_every_touchpad(self, tree):
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")
        tree.add("Apple Inc. Magic Trackpad", "ID_INPUT_TOUCHPAD=1")

        assert tree.touchpad_names() == {"pnp0c50:0b 0911:5288", "apple inc. magic trackpad"}

    def test_missing_directories_yield_nothing(self, tmp_path):
        assert udev_touchpad_names(tmp_path / "no-sys", tmp_path / "no-udev") == set()

    def test_missing_udev_database_yields_nothing(self, tree, tmp_path):
        """sysfs is visible in a sandbox even when /run/udev is not."""
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")

        assert udev_touchpad_names(tree.sys_input, tmp_path / "no-udev") == set()


class TestAnyTouchpad:
    @pytest.mark.parametrize(
        "name",
        ["ELAN Touchpad", "syna2ba6:00-06cb:ce2c-touchpad", "Apple Internal Trackpad"],
    )
    def test_descriptive_name_settles_it_without_udev(self, udev_touchpads, name):
        assert any_touchpad(["logitech-usb-mouse", name]) is True
        udev_touchpads.assert_not_called()

    def test_no_devices_means_no_touchpad_without_udev(self, udev_touchpads):
        udev_touchpads.return_value = {"pnp0c50:0b 0911:5288"}

        assert any_touchpad([]) is False
        udev_touchpads.assert_not_called()

    def test_plain_mouse_is_not_a_touchpad(self, udev_touchpads):
        assert any_touchpad(["logitech-usb-mouse"]) is False

    def test_hardware_id_name_is_matched_through_udev(self, udev_touchpads):
        udev_touchpads.return_value = {"pnp0c50:0b 0911:5288"}

        assert any_touchpad(["logitech-usb-mouse", "pnp0c50:0b-0911:5288"]) is True

    @pytest.mark.parametrize("suffix", ["-1", "-2", "-12"])
    def test_hyprland_duplicate_suffix_is_tolerated(self, udev_touchpads, suffix):
        """Hyprland renames a device whose name another device already holds."""
        udev_touchpads.return_value = {"pnp0c50:0b 0911:5288"}

        assert any_touchpad([f"pnp0c50:0b-0911:5288{suffix}"]) is True

    def test_kernel_name_ending_in_a_number_still_matches(self, udev_touchpads):
        udev_touchpads.return_value = {"vendor pad 2"}

        assert any_touchpad(["vendor-pad-2"]) is True

    def test_unrelated_udev_touchpad_is_ignored(self, udev_touchpads):
        """A udev touchpad only counts when it is one of the reported devices."""
        udev_touchpads.return_value = {"pnp0c50:0b 0911:5288"}

        assert any_touchpad(["logitech-usb-mouse", "razer-razer-deathadder-v2-2"]) is False

    def test_similar_hardware_id_is_not_matched(self, udev_touchpads):
        udev_touchpads.return_value = {"pnp0c50:0b 0911:5288"}

        assert any_touchpad(["pnp0c50:0b-0911:5289"]) is False

    def test_udev_is_read_once_for_all_devices(self, udev_touchpads):
        any_touchpad(["mouse-a", "mouse-b", "mouse-c"])

        udev_touchpads.assert_called_once_with()

    def test_end_to_end_against_a_device_tree(self, tree):
        """The whole path: Hyprland's name, udev's tag, the kernel's name."""
        tree.add("Logitech USB Mouse", "ID_INPUT_MOUSE=1")
        tree.add(I2C_TOUCHPAD, "ID_INPUT_TOUCHPAD=1")

        with patch("hyprland_state._devices.udev_touchpad_names", side_effect=tree.touchpad_names):
            assert any_touchpad(["logitech-usb-mouse", "pnp0c50:0b-0911:5288"]) is True
            assert any_touchpad(["logitech-usb-mouse"]) is False
