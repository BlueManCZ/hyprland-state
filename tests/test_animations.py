"""Tests for animation tree, AnimState, and Animations class."""

from unittest.mock import MagicMock, patch

from hyprland_state import (
    ANIM_CHILDREN,
    ANIM_FLAT,
    ANIM_LOOKUP,
    Animations,
    AnimState,
    get_styles_for,
)


class TestAnimationTree:
    def test_global_is_root(self):
        assert ANIM_FLAT[0] == ("global", None, 0, ())

    def test_top_level_animations_have_depth_1(self):
        top_names = {
            "windows",
            "layers",
            "fade",
            "border",
            "borderangle",
            "workspaces",
            "zoomFactor",
            "monitorAdded",
        }
        for name, parent, depth, _ in ANIM_FLAT:
            if name in top_names:
                assert depth == 1, f"{name} has depth {depth}"
                assert parent == "global"

    def test_children_lookup_consistent(self):
        for name, parent, _, _ in ANIM_FLAT:
            if parent is not None:
                assert name in ANIM_CHILDREN[parent]

    def test_lookup_contains_all(self):
        names = [name for name, _, _, _ in ANIM_FLAT]
        for name in names:
            assert name in ANIM_LOOKUP


class TestGetStylesFor:
    def test_windows_has_styles(self):
        styles = get_styles_for("windows")
        assert "slide" in styles
        assert "popin" in styles

    def test_windowsIn_inherits_from_windows(self):
        styles = get_styles_for("windowsIn")
        assert styles == get_styles_for("windows")

    def test_border_has_no_styles(self):
        assert get_styles_for("border") == ()

    def test_borderangle_has_own_styles(self):
        styles = get_styles_for("borderangle")
        assert "once" in styles
        assert "loop" in styles


class TestAnimState:
    def test_from_ipc(self):
        ipc_anim = MagicMock()
        ipc_anim.name = "windows"
        ipc_anim.overridden = True
        ipc_anim.enabled = True
        ipc_anim.speed = 3.0
        ipc_anim.bezier = "ease"
        ipc_anim.style = "slide"

        state = AnimState.from_ipc(ipc_anim)
        assert state.name == "windows"
        assert state.overridden is True
        assert state.speed == 3.0
        assert state.curve == "ease"
        assert state.style == "slide"

    def test_defaults(self):
        state = AnimState(name="test")
        assert state.overridden is False
        assert state.enabled is True
        assert state.speed == 0.0
        assert state.curve == ""
        assert state.style == ""


class TestAnimations:
    def test_get_all_offline_returns_empty(self, mock_state_offline):
        anims = Animations(mock_state_offline)
        assert anims.get_all() == []

    @patch("hyprland_state._animations.hyprland_socket")
    def test_get_all_returns_anim_states(self, mock_socket, mock_state):
        anim = MagicMock()
        anim.name = "fade"
        anim.overridden = False
        anim.enabled = True
        anim.speed = 2.0
        anim.bezier = "default"
        anim.style = ""
        mock_socket.get_animations.return_value = ([anim], [])

        anims = Animations(mock_state)
        result = anims.get_all()
        assert len(result) == 1
        assert result[0].name == "fade"

    @patch("hyprland_state._animations.hyprland_socket")
    def test_apply_defines_bezier_when_points_provided(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        anims = Animations(mock_state)
        anims.apply("windows", True, 3.0, "myBezier", curve_points=(0.0, 0.0, 0.58, 1.0))

        # Should define bezier then apply animation
        assert mock_socket.keyword.call_count == 2
        bezier_call = mock_socket.keyword.call_args_list[0]
        assert bezier_call[0][0] == "bezier"
        anim_call = mock_socket.keyword.call_args_list[1]
        assert anim_call[0][0] == "animation"

    @patch("hyprland_state._animations.hyprland_socket")
    def test_apply_skips_bezier_without_points(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        anims = Animations(mock_state)
        anims.apply("windows", True, 3.0, "easeOut")

        # Only animation keyword, no bezier (no curve_points provided)
        assert mock_socket.keyword.call_count == 1
        assert mock_socket.keyword.call_args[0][0] == "animation"

    @patch("hyprland_state._animations.hyprland_socket")
    def test_apply_skips_bezier_for_native(self, mock_socket, mock_state):
        mock_socket.HyprlandError = Exception
        anims = Animations(mock_state)
        anims.apply("windows", True, 3.0, "default")

        # Only animation keyword, no bezier
        assert mock_socket.keyword.call_count == 1
        assert mock_socket.keyword.call_args[0][0] == "animation"

    def test_apply_state_skips_non_overridden(self, mock_state):
        anims = Animations(mock_state)
        state = AnimState(name="windows", overridden=False)
        assert anims.apply_state(state) is False

    def test_tree_property(self, mock_state):
        anims = Animations(mock_state)
        assert anims.tree is ANIM_FLAT

    def test_names_property(self, mock_state):
        anims = Animations(mock_state)
        names = anims.names
        assert "global" in names
        assert "windows" in names

    def test_inspect_methods(self, mock_state):
        anims = Animations(mock_state)
        assert anims.get_parent("windowsIn") == "windows"
        assert "windowsIn" in anims.get_children("windows")
        assert "slide" in anims.get_styles("windows")


class TestGetFallback:
    def test_fallback_from_bare_keyword(self, mock_state, tmp_path):
        """get_fallback finds animation lines outside managed config."""
        managed = tmp_path / "gui.conf"
        managed.write_text("animation = windows, 1, 5, default\n")

        user = tmp_path / "user.conf"
        user.write_text("animation = windows, 1, 3, myBezier\n")

        main = tmp_path / "main.conf"
        main.write_text(f"source = {user}\nsource = {managed}\n")

        from hyprland_config import load

        doc = load(main)
        mock_state.document = doc

        anims = Animations(mock_state)
        fb = anims.get_fallback("windows", managed)
        assert fb is not None
        assert fb.speed == 3.0
        assert fb.curve == "myBezier"

    def test_fallback_from_section_keyword(self, mock_state, tmp_path):
        """get_fallback finds animation inside animations { } section."""
        managed = tmp_path / "gui.conf"
        managed.write_text("animation = windows, 1, 5, default\n")

        user = tmp_path / "user.conf"
        user.write_text("animations {\n    animation = windows, 1, 3, ease\n}\n")

        main = tmp_path / "main.conf"
        main.write_text(f"source = {user}\nsource = {managed}\n")

        from hyprland_config import load

        doc = load(main)
        mock_state.document = doc

        anims = Animations(mock_state)
        fb = anims.get_fallback("windows", managed)
        assert fb is not None
        assert fb.speed == 3.0
        assert fb.curve == "ease"

    def test_fallback_returns_none_when_no_user_override(self, mock_state, tmp_path):
        """get_fallback returns None when only managed config has the animation."""
        managed = tmp_path / "gui.conf"
        managed.write_text("animation = windows, 1, 5, default\n")

        main = tmp_path / "main.conf"
        main.write_text(f"source = {managed}\n")

        from hyprland_config import load

        doc = load(main)
        mock_state.document = doc

        anims = Animations(mock_state)
        assert anims.get_fallback("windows", managed) is None

    def test_fallback_last_match_wins(self, mock_state, tmp_path):
        """When multiple user configs set the same animation, last wins."""
        managed = tmp_path / "gui.conf"
        managed.write_text("animation = fade, 1, 8, default\n")

        user1 = tmp_path / "a.conf"
        user1.write_text("animation = fade, 1, 3, linear\n")

        user2 = tmp_path / "b.conf"
        user2.write_text("animation = fade, 1, 7, ease\n")

        main = tmp_path / "main.conf"
        main.write_text(f"source = {user1}\nsource = {user2}\nsource = {managed}\n")

        from hyprland_config import load

        doc = load(main)
        mock_state.document = doc

        anims = Animations(mock_state)
        fb = anims.get_fallback("fade", managed)
        assert fb is not None
        assert fb.speed == 7.0
        assert fb.curve == "ease"
