"""All tests deny real sockets, including tests that use httpx.MockTransport."""

import copy
import json
import socket
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Real network access is blocked in every test")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    from content_brief import pipeline

    monkeypatch.setattr(pipeline, "RETRY_DELAYS", (0, 0))
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    # Keep temporary output inside the worktree, including pytest's numbered dirs.
    monkeypatch.setattr(tempfile, "tempdir", str(Path(".cache").resolve()))


@pytest.fixture
def fixture_response():
    def load(name):
        return copy.deepcopy(
            json.loads(
                (Path(__file__).parent / "fixtures" / f"{name}.json").read_text()
            )
        )

    return load


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "fixture-login@example.invalid")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "fixture-password-only")
