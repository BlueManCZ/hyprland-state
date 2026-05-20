"""Per-window retroactive dispatch for Hyprland window rules.

Hyprland resolves windowrules to per-window state at map time and never
re-evaluates them when a new rule arrives via IPC. The helpers here
close that gap by translating an effect into the ``hyprctl dispatch``
calls that bring an already-mapped window into the rule's target state —
and the symmetric calls that revert it.

The module deliberately knows nothing about *which* windows a rule
applies to (that's matcher logic, owned by callers): it operates on an
effect name + argument string plus a concrete :class:`hyprland_socket.Window`.

Two surfaces:

- :func:`dispatchers_for_effect` — the apply path. Returns dispatchers
  that mutate the window into the rule's target state.
- :func:`revert_dispatchers_for_effect` — the symmetric revert path.

Both return an empty list for effects that have no retroactive
operation (``no_initial_focus``, ``center``, plugin effects), so callers
can iterate effects without a per-name predicate.

The constants exposed for fast-path predicates:

- :data:`SETPROP_PASSTHROUGH_EFFECTS` — effects whose v3 name *is* a
  valid Hyprland ``setprop`` name; args pass through verbatim.
- :data:`RETROACTIVE_EFFECTS` — every effect for which apply emits at
  least one dispatcher. Use to skip the ``get_windows`` IPC call when
  none of an outgoing rule's effects could mutate existing windows.
"""

from hyprland_socket import Window

# Bool/scalar/string effects whose v3 name is *also* a valid setprop in
# Hyprland 0.54+. Args pass through verbatim — Hyprland's
# ``parsePropTrivial`` accepts the same ``on``/``off``/``true``/``false``
# set rule emitters use for bool effects, the same numeric strings for
# ints/floats, and animation-name strings for ``animation``.
#
# Inverted-direction cases (``persistent_size`` ↔ ``nopersistentsize``,
# ``nearest_neighbor`` ↔ ``nonearestneighbor``) are deliberately
# omitted: re-emitting them as ``setprop noprop 0`` is correct only
# when the window was previously locked to ``noprop 1``, which we
# can't reliably determine from a snapshot.
SETPROP_PASSTHROUGH_EFFECTS: frozenset[str] = frozenset(
    {
        # Bools.
        "allows_input", "decorate", "focus_on_activate",
        "keep_aspect_ratio", "nearest_neighbor",
        "no_anim", "no_blur", "no_dim", "no_focus", "no_max_size",
        "no_shadow", "no_shortcuts_inhibit", "no_follow_mouse",
        "no_screen_share", "no_vrr",
        "dim_around", "opaque", "force_rgbx", "sync_fullscreen",
        "immediate", "xray", "render_unfocused", "persistent_size",
        "stay_focused",
        # Strings / scalars.
        "idle_inhibit", "animation", "scroll_mouse", "scroll_touchpad",
        # Ints/floats.
        "border_size", "rounding", "rounding_power",
    }
)  # fmt: skip


# Effect names whose at-spawn / per-frame behaviour we replicate on
# existing windows. Used as a fast-path predicate so effects without
# a runtime mutation (``no_initial_focus``, ``center``,
# ``suppress_event``, plugin effects) skip the IPC ``get_windows``
# round-trip when nothing in a rule could possibly mutate existing
# windows.
RETROACTIVE_EFFECTS: frozenset[str] = frozenset(
    {
        # Static effects with mutating dispatchers.
        "float", "tile", "pin",
        "fullscreen", "maximize",
        "workspace", "monitor",
        "size", "move",
        # Dynamic effects with multi-value setprop translations.
        "opacity", "border_color",
        # Dynamic effects whose v3 name is the setprop name verbatim.
        *SETPROP_PASSTHROUGH_EFFECTS,
    }
)  # fmt: skip


def _setprop(window: Window, prop: str, value: str) -> tuple[str, str]:
    """Build a ``setprop`` dispatcher tuple for *window*.

    Hyprland 0.54+ overrides setprop values at ``PRIORITY_SET_PROP``
    internally, so they persist without a ``lock`` flag — and 0.54
    actively ignores the flag (its ``CVarList`` parser stops at the
    third token). The next config reload re-resolves rules from the
    document and clears the override, so save+reload still produces
    the canonical state.
    """
    return ("setprop", f"address:{window.address} {prop} {value}")


def dispatchers_for_effect(name: str, args: str, window: Window) -> list[tuple[str, str]]:
    """Dispatchers that retroactively apply *name args* to *window*.

    Returns ``[(dispatcher, arg), …]`` for any effect we can mirror on
    an already-mapped window:

    - **Static effects** (``float``, ``size``, ``workspace``, …) →
      mutating dispatchers (``togglefloating``, ``resizewindowpixel``,
      ``movetoworkspacesilent``, …). All gated by current state where
      a toggle would otherwise undo itself.
    - **Dynamic effects** (``opacity``, ``no_blur``, ``rounding``,
      ``border_color``, …) → ``setprop``. Hyprland resolves dynamic
      windowrules to per-window state at map time, so a fresh
      ``keyword windowrule = …, opacity 0.5`` doesn't update existing
      windows on its own — we need the explicit ``setprop`` to mutate
      each match.

    Returns an empty list for effects with no useful retroactive
    operation (``center``, ``no_initial_focus``) or idempotent cases
    (e.g. ``float`` on an already-floating window — skipping avoids
    un-floating it via toggle semantics).
    """
    addr = f"address:{window.address}"
    args = args.strip()

    # ── Static effects: mutating dispatchers ──────────────────────

    if name == "float":
        # ``togglefloating`` is the universally-supported dispatcher;
        # gate by current state so we don't accidentally un-float.
        return [] if window.floating else [("togglefloating", addr)]

    if name == "tile":
        return [("togglefloating", addr)] if window.floating else []

    if name == "pin":
        # ``pin`` is a toggle; only fire when the window isn't already pinned.
        return [] if window.pinned else [("pin", addr)]

    if name == "fullscreen":
        # ``fullscreenstate <internal> <client>``: 2 = fullscreen, -1 = no-op.
        # Skip if already fullscreen (not just maximized).
        if window.fullscreen == 2:
            return []
        return [("fullscreenstate", f"2 -1,{addr}")]

    if name == "maximize":
        if window.fullscreen == 1:
            return []
        return [("fullscreenstate", f"1 -1,{addr}")]

    if name == "workspace":
        # Rule arg looks like ``2``, ``name:work``, or ``2 silent``.
        # Use the silent variant unconditionally — at-spawn semantics
        # are "don't steal focus", which matches the rule's intent.
        first = args.split()[0] if args else ""
        if not first:
            return []
        return [("movetoworkspacesilent", f"{first},{addr}")]

    if name == "monitor":
        if not args:
            return []
        # ``movewindow mon:NAME`` carries the window to a monitor.
        return [("movewindow", f"mon:{args},{addr}")]

    if name == "size":
        parts = args.split()
        if len(parts) < 2:
            return []
        w, h = parts[0], parts[1]
        return [("resizewindowpixel", f"exact {w} {h},{addr}")]

    if name == "move":
        parts = args.split()
        if len(parts) < 2:
            return []
        x, y = parts[0], parts[1]
        return [("movewindowpixel", f"exact {x} {y},{addr}")]

    # ── Dynamic effects: setprop ──────────────────────────────────

    if name == "opacity":
        # Hyprland's ``setprop opacity`` (and the ``_inactive`` /
        # ``_fullscreen`` variants) each take a single float, so a
        # rule with multiple values has to fan out into one setprop
        # per slot. ``override`` keyword is dropped — there's a
        # separate ``opacity_override`` setprop pair for that, which
        # we don't surface here.
        parts = [p for p in args.split() if p.lower() != "override"]
        if not parts:
            return []
        active = parts[0]
        inactive = parts[1] if len(parts) > 1 else parts[0]
        result = [
            _setprop(window, "opacity", active),
            _setprop(window, "opacity_inactive", inactive),
        ]
        if len(parts) > 2:
            result.append(_setprop(window, "opacity_fullscreen", parts[2]))
        return result

    if name == "border_color":
        # The ``border_color`` rule sets both active and inactive
        # gradients to the same value. Hyprland exposes these as two
        # separate setprops; we emit both so the visible state matches
        # the rule's behaviour on a freshly-mapped window.
        if not args:
            return []
        return [
            _setprop(window, "active_border_color", args),
            _setprop(window, "inactive_border_color", args),
        ]

    if name in SETPROP_PASSTHROUGH_EFFECTS and args:
        return [_setprop(window, name, args)]

    # ``center`` only acts on the focused window (no address-target
    # variant). ``no_initial_focus``, ``suppress_event``, ``tag``,
    # ``min_size``/``max_size`` (expression-parsed, awkward to encode
    # without a parser of our own), and any plugin effect drop
    # through silently — callers using the keyword push will have
    # already registered them for new windows.
    return []


def revert_dispatchers_for_effect(name: str, args: str, window: Window) -> list[tuple[str, str]]:
    """Dispatchers that revert *name args* on *window*.

    Symmetric to :func:`dispatchers_for_effect`. Behaviour splits by
    which Hyprland 0.54.3 setprop branch the property uses:

    - Properties that flow through ``parsePropTrivial`` (every bool
      effect, plus ``rounding``, ``border_size``, ``rounding_power``,
      ``animation``, ``idle_inhibit``, …) accept the literal value
      ``"unset"`` to clear the ``PRIORITY_SET_PROP`` override and let
      the rule resolver's map-time result take over.
    - ``opacity`` / ``opacity_inactive`` / ``opacity_fullscreen``
      *don't* go through ``parsePropTrivial`` — they call
      ``std::stof(VAL)`` directly, which throws on ``"unset"`` and
      surfaces ``"Error parsing prop value: stof"``. As a fallback we
      send ``1.0`` (Hyprland's compositor default), correct for the
      common single-rule case. Callers with another saved rule of the
      same effect that should still apply re-push it after this revert.
    - **Toggleable static effects** (``float`` / ``tile`` / ``pin`` /
      ``fullscreen`` / ``maximize``) mirror their apply path: if the
      window is currently in the rule's target state, dispatch the
      inverse toggle. Cannot distinguish "rule put it here" from
      "user put it here" any more than the apply path could, so the
      fix-the-common-case trade-off is symmetric — the user's
      just-edited rule is the most plausible cause of the current state.
    - **Non-toggleable static effects** (``size``, ``move``,
      ``workspace``, ``monitor``) need per-rule per-window pre-state
      we don't track and no-op here; the escape hatch is save+reload.
    """
    addr = f"address:{window.address}"

    # ── Toggleable static effects: inverse of the apply path ─────────

    if name == "float":
        return [("togglefloating", addr)] if window.floating else []

    if name == "tile":
        return [] if window.floating else [("togglefloating", addr)]

    if name == "pin":
        # ``pin`` is a toggle dispatcher, same name as the rule.
        return [("pin", addr)] if window.pinned else []

    if name == "fullscreen":
        # Apply sent ``fullscreenstate 2 -1`` (set internal=fullscreen);
        # revert clears the internal state when it's still fullscreen.
        if window.fullscreen != 2:
            return []
        return [("fullscreenstate", f"0 -1,{addr}")]

    if name == "maximize":
        if window.fullscreen != 1:
            return []
        return [("fullscreenstate", f"0 -1,{addr}")]

    # ── Dynamic effects ──────────────────────────────────────────────

    if name == "opacity":
        # Mirror the *count* of setprops the apply path emitted —
        # we don't want to lock ``opacity_fullscreen`` to 1.0 if
        # the rule never set it. Apply emits 2 setprops for 1–2 args
        # and 3 for 3 args (the ``override`` keyword is dropped).
        parts = [p for p in args.split() if p.lower() != "override"]
        if not parts:
            return []
        result = [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
        ]
        if len(parts) > 2:
            result.append(("setprop", f"{addr} opacity_fullscreen 1.0"))
        return result

    if name == "border_color":
        # Hyprland 0.54.3 *does* accept ``unset`` here (the
        # ``configStringToInt`` path silently no-ops on a non-color
        # token, leaving the gradient empty), but the resulting state
        # is "no override at SET_PROP" which is exactly what we want.
        return [
            ("setprop", f"{addr} active_border_color unset"),
            ("setprop", f"{addr} inactive_border_color unset"),
        ]

    if name in SETPROP_PASSTHROUGH_EFFECTS:
        return [("setprop", f"{addr} {name} unset")]

    return []
