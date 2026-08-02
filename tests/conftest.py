"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from kavi.config.schema import KaviConfig, PermissionConfig, Provider
from kavi.tools.base import ToolContext


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def config() -> KaviConfig:
    return KaviConfig(provider=Provider.ANTHROPIC, model="claude-sonnet-4-test")


@pytest.fixture
def yolo_config() -> KaviConfig:
    return KaviConfig(
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-test",
        permissions=PermissionConfig(yolo=True),
    )


@pytest.fixture
def tool_ctx(cwd: Path, config: KaviConfig) -> ToolContext:
    return ToolContext(cwd=cwd, config=config)
