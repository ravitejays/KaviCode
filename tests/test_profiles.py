"""Tests for use-case profiles."""

from __future__ import annotations

from pathlib import Path

from kavi.agent.profiles import (
    BUILTIN_PROFILES,
    all_profiles,
    get_profile,
    load_custom_profiles,
)
from kavi.agent.prompts import build_system_prompt


def test_builtin_profiles_present():
    for name in ("coding", "research", "data", "devops"):
        assert name in BUILTIN_PROFILES


def test_research_profile_is_read_only_toolset():
    research = BUILTIN_PROFILES["research"]
    assert research.allowed_tools is not None
    assert "Write" not in research.allowed_tools
    assert "Edit" not in research.allowed_tools
    assert "Read" in research.allowed_tools


def test_coding_profile_has_all_tools():
    assert BUILTIN_PROFILES["coding"].allowed_tools is None


def test_get_profile_unknown_falls_back_to_coding():
    assert get_profile("nonexistent").name == "coding"


def test_load_custom_markdown_profile(tmp_path: Path):
    (tmp_path / "triage.md").write_text("You are an incident-triage agent.")
    profiles = load_custom_profiles(tmp_path)
    assert "triage" in profiles
    assert "incident-triage" in profiles["triage"].prompt


def test_load_custom_toml_profile(tmp_path: Path):
    (tmp_path / "sql.toml").write_text(
        'description = "SQL only"\nprompt = "You write SQL."\nallowed_tools = ["Bash", "Read"]\n'
    )
    profiles = load_custom_profiles(tmp_path)
    assert profiles["sql"].description == "SQL only"
    assert profiles["sql"].allowed_tools == ["Bash", "Read"]


def test_custom_profile_overrides_builtin(tmp_path: Path):
    (tmp_path / "coding.md").write_text("Custom coding prompt.")
    merged = all_profiles(tmp_path)
    assert "Custom coding prompt." in merged["coding"].prompt


def test_bad_profile_file_is_skipped(tmp_path: Path):
    (tmp_path / "broken.toml").write_text("this is not = valid = toml ===")
    profiles = load_custom_profiles(tmp_path)
    assert "broken" not in profiles


def test_profile_prompt_flows_into_system_prompt(tmp_path: Path):
    prompt = build_system_prompt(
        tmp_path, profile_prompt="You specialize in security audits."
    )
    assert "# Specialization" in prompt
    assert "security audits" in prompt
