"""Tests for OptionInfo."""

from unittest.mock import MagicMock

import pytest

from hyprland_state import OptionInfo


class TestOptionInfo:
    def test_from_schema(self):
        opt = MagicMock()
        opt.key = "general:border_size"
        opt.type = "int"
        opt.default = 1
        opt.description = "Border size"
        opt.min = 0
        opt.max = 20
        opt.enum_values = None

        info = OptionInfo.from_schema(opt)
        assert info.key == "general:border_size"
        assert info.type == "int"
        assert info.default == 1
        assert info.description == "Border size"
        assert info.min == 0
        assert info.max == 20
        assert info.enum_values is None

    def test_frozen(self):
        info = OptionInfo(key="test", type="int", default=0)
        with pytest.raises(AttributeError):
            info.key = "other"  # type: ignore[misc]


class TestValidate:
    def test_valid_int_in_range(self):
        info = OptionInfo(key="k", type="int", default=1, min=0, max=10)
        assert info.validate(5) is None

    def test_below_min(self):
        info = OptionInfo(key="k", type="int", default=1, min=0, max=10)
        assert info.validate(-1) is not None

    def test_above_max(self):
        info = OptionInfo(key="k", type="int", default=1, min=0, max=10)
        assert info.validate(11) is not None

    def test_at_boundaries(self):
        info = OptionInfo(key="k", type="int", default=1, min=0, max=10)
        assert info.validate(0) is None
        assert info.validate(10) is None

    def test_float_range(self):
        info = OptionInfo(key="k", type="float", default=0.5, min=0.0, max=1.0)
        assert info.validate(0.5) is None
        assert info.validate(1.5) is not None

    def test_no_constraints_passes(self):
        info = OptionInfo(key="k", type="int", default=1)
        assert info.validate(9999) is None

    def test_enum_valid(self):
        info = OptionInfo(key="k", type="string", default="a", enum_values=("a", "b", "c"))
        assert info.validate("a") is None

    def test_enum_invalid(self):
        info = OptionInfo(key="k", type="string", default="a", enum_values=("a", "b", "c"))
        assert info.validate("z") is not None

    def test_non_numeric_for_int_type(self):
        info = OptionInfo(key="k", type="int", default=0, min=0, max=10)
        assert info.validate("notanumber") is not None

    def test_string_type_skips_numeric_check(self):
        info = OptionInfo(key="k", type="string", default="hello")
        assert info.validate("anything") is None

    def test_choice_validated(self):
        info = OptionInfo(key="k", type="choice", default=0, min=0, max=2)
        assert info.validate(1) is None
        assert info.validate(5) is not None
