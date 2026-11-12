"""Qt KakaoTalk backend using Windows UI Automation and scoped keyboard input."""
import os
from pathlib import Path
import subprocess
import time

import win32api
import win32gui
import win32process

from . import controller as legacy
from .ui_lock import operation
from .qt_bridge import bridge


def _qt_windows():
    """Identify this desktop's KakaoTalk by executable, including a hidden main window."""
    windows = []
    executables = {}

    def visit(hwnd, _):
        try:
            if not win32gui.GetClassName(hwnd).startswith('Qt'):
                return
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if pid not in executables:
                handle = win32api.OpenProcess(0x1000, False, pid)
                try:
                    executables[pid] = Path(win32process.GetModuleFileNameEx(handle, 0)).name.lower()
                finally:
                    handle.Close()
            if executables[pid] in {'kakaotalkui.exe', 'kakaotalk.exe'}:
                windows.append((pid, hwnd, win32gui.GetWindowText(hwnd)))
        except win32gui.error:
            # Unrelated protected processes and disappearing windows are normal.
            return

    win32gui.EnumWindows(visit, None)
    return windows


def call(action, **arguments):
    with operation():
        pids = {pid for pid, _, _ in _qt_windows()}
        if len(pids) != 1:
            raise RuntimeError(f'Expected one accessible Qt KakaoTalk process on this desktop, found {len(pids)}')
        timeout = max(45, 15 * arguments.get('limit', 1)) if action == 'download' else (
            10 if action in {'health', 'rooms', 'read'} else 20)
        pid = pids.pop()
        if arguments.get('process_id', pid) != pid:
            raise RuntimeError('Target room process changed')
        return bridge.request({'action': action, 'process_id': pid, **arguments}, timeout)


def available():
    return bool(_qt_windows())


def is_kakaotalk_running():
    windows = _qt_windows()
    main = [(pid, hwnd) for pid, hwnd, title in windows if title in {'카카오톡', 'KakaoTalk'}]
    if len(main) == 1 and len({pid for pid, _, _ in windows}) == 1:
        pid, hwnd = main[0]
        return {'running': True, 'pid': pid, 'hwnd': hwnd}
    return call('health')


def list_chat_windows():
    return call('rooms')['rooms']


def find_chat_window(room_name):
    rooms = [r for r in list_chat_windows() if r['title'] == room_name]
    if len(rooms) > 1:
        raise ValueError('Multiple open rooms have this name')
    return rooms[0]['hwnd'] if rooms else None


bring_window_to_front = legacy.bring_window_to_front


def search_and_open_room(room_name):
    try:
        data = call('open', room=room_name)
        return {'success': True, 'message': f"Opened '{room_name}'", **data}
    except Exception as error:
        return {'success': False, 'code': getattr(error, 'code', 'room_not_verified'), 'error': str(error)}


def send_message_to_room(room_name, text):
    from .delivery import deliver
    return deliver('text', [(room_name, text)])


def read_chat_messages(room_name, read_mode=None, max_messages=None, *, monitoring=False, all_history=False):
    mode = read_mode or os.environ.get('KAKAO_QT_READ_MODE', 'auto')
    if mode == 'uia':  # Existing installations used this name.
        mode = 'background'
    exposed = {}
    if monitoring and mode == 'auto':
        mode = 'background'
    try:
        with operation():
            if mode not in {'auto', 'complete', 'background'}:
                raise ValueError(f'Unknown read mode: {mode}')
            if mode == 'background':
                return {'success': True, 'parsed': call('read', room=room_name)}
            # A request for all links/history needs the full local copy. A
            # bounded read can use the exposed tail when it has enough detail.
            if mode == 'auto' and not all_history:
                exposed = call('read', room=room_name)
                messages = exposed.get('messages', [])
                if max_messages is None and messages and exposed.get('at_end') is not False:
                    if exposed.get('at_end') is None:
                        exposed = {**exposed, 'scope': 'visible_page_latest_unverified'}
                    return {'success': True, 'parsed': exposed}
                tail = messages[-max_messages:] if max_messages else []
                if (exposed.get('at_end') and len(tail) == max_messages
                        and all(m.get('sender') and m.get('date') for m in tail)):
                    return {'success': True, 'parsed': exposed}
            from .parser import parse_chat_text
            snapshot = legacy._plain_clipboard_snapshot()
            pin = {key: exposed[key] for key in ('hwnd', 'process_id') if key in exposed}
            copied = call('copy', room=room_name, **pin)
            legacy._restore_plain_clipboard(snapshot, copied.get('before_copy'), copied.get('after_copy'))
            raw = copied['raw_text']
            parsed = parse_chat_text(raw)
            if raw and not parsed['messages']:
                raise RuntimeError('Message copy returned no parseable messages')
            parsed['room_name'] = room_name
            parsed['partial'] = True
            parsed['read_method'] = 'uia_clipboard'
            parsed['unknown_fields'] = ['member_count'] if parsed['member_count'] is None else []
            return {'success': True, 'parsed': parsed}
    except Exception as error:
        code = getattr(error, 'code', 'read_failed')
        if mode == 'auto' and exposed.get('messages') and code not in {'room_changed', 'room_not_found', 'room_ambiguous'}:
            return {'success': True, 'parsed': {**exposed, 'partial': True, 'detail_error': str(error)}}
        return {'success': False, 'code': code, 'error': str(error), 'raw_text': ''}


send_bulk_messages = legacy.send_bulk_messages
send_images_to_room = legacy.send_images_to_room


def send_image_to_room(room_name, image_path):
    return send_images_to_room(room_name, [image_path])


def send_mention_message(room_name, mention_name, message):
    from .delivery import deliver
    return deliver('mention', [(room_name, message)], mention=mention_name)


def download_recent_images(room_name, output_dir, max_images=10):
    if max_images < 1:
        return {'success': False, 'error': 'max_images must be positive', 'images': []}
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return call('download', room=room_name, output=str(Path(output_dir).resolve()), limit=max_images)
    except Exception as error:
        return {'success': False, 'error': str(error), 'images': []}
