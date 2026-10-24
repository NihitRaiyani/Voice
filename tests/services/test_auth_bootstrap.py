"""Operator provisioning is explicit and never supplies a default password or role."""

from unittest.mock import AsyncMock

import pytest
from scripts import bootstrap_user


def test_bootstrap_requires_an_explicit_role_and_never_calls_storage_by_default(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(bootstrap_user, "_run", run)
    with pytest.raises(SystemExit) as error:
        bootstrap_user.main(["--email", "teacher@example.invalid", "--display-name", "Teacher"])
    assert error.value.code == 2
    run.assert_not_awaited()


def test_bootstrap_prompts_without_echo_and_refuses_mismatched_passwords(monkeypatch, capsys):
    run = AsyncMock()
    monkeypatch.setattr(bootstrap_user, "_run", run)
    passwords = iter(["synthetic password 123!", "different password 123!"])
    monkeypatch.setattr(bootstrap_user.getpass, "getpass", lambda _: next(passwords))
    assert (
        bootstrap_user.main(
            [
                "--email",
                "teacher@example.invalid",
                "--display-name",
                "Teacher",
                "--role",
                "admin",
            ]
        )
        == 1
    )
    run.assert_not_awaited()
    captured = capsys.readouterr()
    assert "synthetic password" not in captured.err + captured.out


def test_bootstrap_password_stdin_is_explicit_and_not_printed(monkeypatch, capsys):
    from io import StringIO

    run = AsyncMock()
    monkeypatch.setattr(bootstrap_user, "_run", run)
    monkeypatch.setattr(bootstrap_user.sys, "stdin", StringIO("synthetic password 123!\n"))
    assert (
        bootstrap_user.main(
            [
                "--email",
                "teacher@example.invalid",
                "--display-name",
                "Teacher",
                "--role",
                "admin",
                "--password-stdin",
            ]
        )
        == 0
    )
    run.assert_awaited_once()
    assert run.await_args.args[1] == "synthetic password 123!"
    captured = capsys.readouterr()
    assert "synthetic password" not in captured.err + captured.out
