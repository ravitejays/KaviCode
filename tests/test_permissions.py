"""Tests for the permission engine and rule matching."""

from __future__ import annotations

from kavi.config.schema import PermissionConfig
from kavi.permissions.engine import PermissionEngine
from kavi.permissions.rules import parse_rule


def test_parse_rule():
    r = parse_rule("Bash(git*)")
    assert r is not None and r.tool == "Bash" and r.pattern == "git*"
    assert parse_rule("Read").pattern is None


def test_rule_matches():
    r = parse_rule("Bash(git*)")
    assert r.matches("Bash", "git status")
    assert not r.matches("Bash", "rm -rf /")
    assert not r.matches("Write", "git status")


def test_yolo_allows_everything():
    eng = PermissionEngine(PermissionConfig(yolo=True))
    assert eng.decide("Bash", "rm -rf /", "ask") == "allow"


def test_deny_beats_allow():
    cfg = PermissionConfig(allow=["Bash(git*)"], deny=["Bash(git push*)"])
    eng = PermissionEngine(cfg)
    assert eng.decide("Bash", "git status", "ask") == "allow"
    assert eng.decide("Bash", "git push origin", "ask") == "deny"


def test_readonly_default_allows():
    eng = PermissionEngine(PermissionConfig())
    assert eng.decide("Read", "/x", "allow") == "allow"
    assert eng.decide("Write", "/x", "ask") == "ask"


def test_per_tool_config():
    eng = PermissionEngine(PermissionConfig(tools={"Write": "allow"}))
    assert eng.decide("Write", "/x", "ask") == "allow"


def test_session_grant():
    eng = PermissionEngine(PermissionConfig())
    assert eng.decide("Bash", "npm test", "ask") == "ask"
    eng.grant_session("Bash", "npm test")
    assert eng.decide("Bash", "npm test", "ask") == "allow"
    assert eng.decide("Bash", "other", "ask") == "ask"
