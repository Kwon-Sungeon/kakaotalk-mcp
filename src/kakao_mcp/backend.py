"""Select the installed KakaoTalk UI backend per operation."""
from . import controller as legacy
from . import qt_controller as qt


def __getattr__(name):
    if name == '_chat_monitor':
        return legacy._chat_monitor
    implementation = qt if qt.available() else legacy
    return getattr(implementation, name)
