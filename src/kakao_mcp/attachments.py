"""Pass one local file at a time to the exact room's Windows file picker."""
from pathlib import Path

from . import controller as legacy
from . import qt_controller as qt
from .qt_bridge import bridge
from .ui_lock import operation


def send_files(room, paths):
    completed = []
    try:
        files = [Path(path) for path in paths]
        if not files or any(not p.is_absolute() or not p.is_file() for p in files):
            raise ValueError('Each attachment must be an existing absolute file path')
        files = [p.resolve(strict=True) for p in files]
        with operation():
            impl = qt if qt.available() else legacy
            hwnd = impl.find_chat_window(room)
            if not hwnd:
                raise ValueError('Open the exact room with kakao_open_room first')
            pid = legacy.win32process.GetWindowThreadProcessId(hwnd)[1]
            for path in files:
                if impl.find_chat_window(room) != hwnd:
                    raise ValueError('Target room changed')
                data = bridge.request(dict(action='file', room=room, hwnd=hwnd, process_id=pid,
                                           qt=impl is qt, path=str(path)), 20)
                completed.append({'file': path.name, **data})
        return {'success': True, 'results': completed}
    except Exception as error:
        return {'success': False, 'error': str(error), 'results': completed}
