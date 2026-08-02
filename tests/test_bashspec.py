"""Tests for the structured bash-command risk classifier."""

from __future__ import annotations

from kavi.permissions.bashspec import (
    EXEC,
    NETWORK,
    READ,
    classify,
    is_destructive,
)


def test_read_only_commands():
    for cmd in ("ls -la", "cat file.txt", "git status", "grep foo bar", "pwd"):
        assert classify(cmd).risk == READ, cmd


def test_exec_commands():
    assert classify("python script.py").risk == EXEC
    assert classify("git commit -m x").risk == EXEC


def test_network_commands():
    assert classify("curl https://example.com").risk == NETWORK


def test_destructive_rm():
    for cmd in ("rm -rf build", "rm -r foo", "rm -fr /tmp/x"):
        assert is_destructive(cmd), cmd


def test_destructive_git_push_force():
    assert is_destructive("git push --force")
    assert is_destructive("git push origin main --force")


def test_destructive_subcommands():
    assert is_destructive("docker rm mycontainer")
    assert is_destructive("kubectl delete pod x")
    assert is_destructive("terraform destroy")


def test_destructive_sudo_and_forkbomb():
    assert is_destructive("sudo apt install foo")
    assert is_destructive(":(){ :|:& };:")


def test_worst_segment_wins():
    # A read then a destructive op -> destructive overall.
    assert is_destructive("ls && rm -rf build")
    assert classify("echo hi | grep hi").risk == READ


def test_windows_destructive():
    assert is_destructive("del /s /q C:\\temp")
    assert is_destructive("Remove-Item foo -Recurse")


def test_empty_command_is_read():
    assert classify("").risk == READ
