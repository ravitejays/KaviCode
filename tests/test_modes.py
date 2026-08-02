"""Tests for permission modes (default / auto / plan / bypass)."""

from __future__ import annotations

from kavi.config.schema import PermissionConfig
from kavi.permissions.engine import PermissionEngine


def _engine(mode: str) -> PermissionEngine:
    return PermissionEngine(PermissionConfig(mode=mode))


# ------------------------------------------------------------- apply_mode logic
def test_read_only_always_allowed():
    for mode in ("default", "auto", "plan", "bypass"):
        eng = _engine(mode)
        out = eng.apply_mode("allow", is_read_only=True, is_destructive=False)
        assert out == "allow", mode


def test_default_mode_leaves_decision():
    eng = _engine("default")
    assert eng.apply_mode("ask", is_read_only=False, is_destructive=False) == "ask"
    assert eng.apply_mode("allow", is_read_only=False, is_destructive=False) == "allow"


def test_auto_mode_accepts_nondestructive_writes():
    eng = _engine("auto")
    # A normal edit that would otherwise prompt is auto-allowed.
    assert eng.apply_mode("ask", is_read_only=False, is_destructive=False) == "allow"


def test_auto_mode_still_prompts_destructive():
    eng = _engine("auto")
    assert eng.apply_mode("ask", is_read_only=False, is_destructive=True) == "ask"
    # Even a broad allow becomes ask for a destructive call.
    assert eng.apply_mode("allow", is_read_only=False, is_destructive=True) == "ask"


def test_plan_mode_denies_mutating_tools():
    eng = _engine("plan")
    assert eng.apply_mode("allow", is_read_only=False, is_destructive=False) == "deny"
    assert eng.apply_mode("ask", is_read_only=False, is_destructive=False) == "deny"
    # But read-only is still fine.
    assert eng.apply_mode("allow", is_read_only=True, is_destructive=False) == "allow"


def test_bypass_mode_allows_everything():
    eng = _engine("bypass")
    assert eng.apply_mode("ask", is_read_only=False, is_destructive=True) == "allow"


def test_explicit_deny_never_loosened():
    for mode in ("default", "auto", "plan", "bypass"):
        eng = _engine(mode)
        assert eng.apply_mode("deny", is_read_only=True, is_destructive=False) == "deny"


# ------------------------------------------------------------- decide + bypass
def test_bypass_via_decide():
    eng = _engine("bypass")
    assert eng.decide("Write", "x.py", "ask") == "allow"


def test_yolo_flag_maps_to_bypass():
    eng = PermissionEngine(PermissionConfig(yolo=True))
    assert eng.config.effective_mode() == "bypass"
    assert eng.decide("Bash", "rm -rf x", "ask") == "allow"


def test_set_mode_updates_config():
    eng = _engine("default")
    eng.set_mode("auto")
    assert eng.config.effective_mode() == "auto"
    eng.set_mode("bypass")
    assert eng.config.effective_mode() == "bypass"
    assert eng.config.yolo is True


def test_effective_mode_prefers_yolo():
    cfg = PermissionConfig(mode="plan", yolo=True)
    assert cfg.effective_mode() == "bypass"
