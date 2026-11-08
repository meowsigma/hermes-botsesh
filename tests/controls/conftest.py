"""Prevent native imports from reading/writing the worker's live Hermes home."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.dont_write_bytecode = True
# -P intentionally omits cwd. Keep Hermes imports first, but make the plugin's
# flat, transport-independent controls module available to this suite.
sys.path.append(str(Path(__file__).resolve().parents[2]))
_home = TemporaryDirectory(prefix='.test-hermes-', dir=Path(__file__).parent)
os.environ['HERMES_HOME'] = _home.name
os.environ['HERMES_NO_AUTO_UPDATE'] = '1'


def pytest_sessionfinish(session, exitstatus):
    _home.cleanup()


import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    attempted = []
    def deny(*args, **kwargs):
        attempted.append(True)
        raise AssertionError('Network is forbidden in DM controls tests')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    yield
    assert not attempted, 'A network call was attempted, even if its exception was caught'
