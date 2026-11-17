"""Select the installed KakaoTalk UI backend per operation."""
from . import controller as legacy
from . import qt_controller as qt


def list_participants(room_name: str):
    if qt.available():
        return qt.list_participants(room_name)
    return legacy.list_participants_legacy(room_name)


def inspect_ui(room_name: str = "", max_depth: int = 5):
    if qt.available():
        return qt.inspect_ui(room_name, max_depth)
    return legacy.inspect_win32_tree(room_name, max_depth)


def __getattr__(name):
    if name == '_chat_monitor':
        return legacy._chat_monitor
    implementation = qt if qt.available() else legacy
    return getattr(implementation, name)
