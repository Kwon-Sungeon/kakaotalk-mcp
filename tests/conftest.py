"""Keep unit tests isolated from the Windows desktop and outgoing messages."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolate_desktop_io(monkeypatch):
    from kakao_mcp import controller, qt_controller

    for name in ('_user32', 'win32gui', 'win32api', 'win32clipboard', 'win32process'):
        monkeypatch.setattr(controller, name, MagicMock(name=name))
    monkeypatch.setattr(qt_controller, 'win32gui', MagicMock(name='qt_win32gui'))

    def reject_process(*args, **kwargs):
        raise AssertionError('Unit tests must mock external process execution')

    monkeypatch.setattr(qt_controller.subprocess, 'run', reject_process)
