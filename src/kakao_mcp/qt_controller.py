"""Qt KakaoTalk backend using Windows UI Automation and scoped keyboard input."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import msvcrt
import win32gui

from . import controller as legacy

_thread_lock = threading.RLock()
_lock_state = threading.local()


@contextlib.contextmanager
def operation():
    """Serialize UI operations across clients as well as monitor threads."""
    with _thread_lock:
        if getattr(_lock_state, 'held', False):
            yield
            return
        path = Path(tempfile.gettempdir()) / 'kakao-mcp-ui.lock'
        with path.open('a+b') as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 60
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Another KakaoTalk operation is busy')
                    time.sleep(.1)
            try:
                _lock_state.held = True
                yield
            finally:
                _lock_state.held = False
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def call(action, **arguments):
    script = Path(__file__).with_name('qt_uia.ps1').read_text(encoding='utf-8')
    with operation():
        if action in {'copy', 'download', 'image', 'mention'}:
            hwnd = find_chat_window(arguments['room'])
            if hwnd is None:
                raise ValueError('Target room is not open')
            legacy.bring_window_to_front(hwnd)
            arguments['hwnd'] = hwnd
        # Windows PowerShell 5.1 reads BOM-less scripts in the system code page
        # with -File. Supply the Unicode script and send request data on stdin.
        command = "[Console]::InputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); " + script
        timeout = max(45, 15 * arguments.get('limit', 1)) if action == 'download' else 45
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-Sta', '-NonInteractive', '-Command', command],
            input=json.dumps({'action': action, **arguments}, ensure_ascii=False),
            capture_output=True, encoding='utf-8', timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        response = json.loads(result.stdout)
        if not response['success']:
            raise RuntimeError(response['error'])
        return response['data']


def available():
    found = []
    def visit(hwnd, _):
        if win32gui.GetWindowText(hwnd) == '카카오톡' and win32gui.GetClassName(hwnd).startswith('Qt'):
            found.append(hwnd)
    win32gui.EnumWindows(visit, None)
    return bool(found)


def is_kakaotalk_running():
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
        return {'success': False, 'error': str(error)}


def send_message_to_room(room_name, text):
    try:
        if not text.strip():
            raise ValueError('Message cannot be empty')
        call('send', room=room_name, text=text)
        return {'success': True, 'message': f"Sent message to '{room_name}'"}
    except Exception as error:
        return {'success': False, 'error': str(error)}


def read_chat_messages(room_name):
    try:
        with operation():
            if os.environ.get('KAKAO_QT_READ_MODE', 'complete') == 'uia':
                return {'success': True, 'parsed': call('read', room=room_name)}
            from .parser import parse_chat_text
            raw = call('copy', room=room_name)['raw_text']
            parsed = parse_chat_text(raw)
            if raw and not parsed['messages']:
                raise RuntimeError('Message copy returned no parseable messages')
            parsed['room_name'] = room_name
            # KakaoTalk exports locally loaded history, not server-only history.
            parsed['partial'] = True
            parsed['read_method'] = 'uia_clipboard'
            return {'success': True, 'parsed': parsed}
    except Exception as error:
        return {'success': False, 'error': str(error), 'raw_text': ''}


def send_bulk_messages(room_names, message, interval_sec=.5):
    if not room_names or not message.strip():
        return {'success': False, 'error': 'Rooms and a nonempty message are required', 'results': []}
    results = []
    for index, name in enumerate(room_names):
        opened = search_and_open_room(name)
        result = send_message_to_room(name, message) if opened['success'] else opened
        results.append({'room': name, 'success': result['success'], 'detail': result.get('message') or result.get('error')})
        if index < len(room_names) - 1:
            time.sleep(max(.3, interval_sec))
    count = sum(r['success'] for r in results)
    return {'success': count > 0, 'message': f'Sent to {count}/{len(results)} room(s)', 'results': results}


def send_image_to_room(room_name, image_path):
    return send_images_to_room(room_name, [image_path])


def send_images_to_room(room_name, image_paths):
    from .config import SUPPORTED_IMAGE_EXTENSIONS
    paths = [Path(path).resolve() for path in image_paths]
    if not paths or any(not p.is_file() or p.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS for p in paths):
        return {'success': False, 'error': 'All paths must be existing supported image files', 'results': []}
    results = []
    try:
        for path in paths:
            with operation():
                sequence = legacy._copy_image_to_clipboard(str(path))
                data = call('image', room=room_name, sequence=sequence)
            results.append({'path': str(path), **data})
        return {'success': True, 'message': f"Sent {len(paths)} image(s) to '{room_name}'", 'results': results}
    except Exception as error:
        return {'success': False, 'error': str(error), 'results': results}


def send_mention_message(room_name, mention_name, message):
    if not mention_name.strip() or not message.strip():
        return {'success': False, 'error': 'Mention name and message are required'}
    try:
        call('mention', room=room_name, mention=mention_name, text=message)
        return {'success': True, 'message': f"Sent mention to @{mention_name} in '{room_name}'"}
    except Exception as error:
        return {'success': False, 'error': str(error)}


def download_recent_images(room_name, output_dir, max_images=10):
    if max_images < 1:
        return {'success': False, 'error': 'max_images must be positive', 'images': []}
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return call('download', room=room_name, output=str(Path(output_dir).resolve()), limit=max_images)
    except Exception as error:
        return {'success': False, 'error': str(error), 'images': []}
