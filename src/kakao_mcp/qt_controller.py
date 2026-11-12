"""Qt KakaoTalk backend using Windows UI Automation and scoped keyboard input."""
import json
import os
from pathlib import Path
import subprocess
import time

import win32gui

from . import controller as legacy
from .ui_lock import operation

def call(action, **arguments):
    script = Path(__file__).with_name('qt_uia.ps1').read_text(encoding='utf-8')
    with operation():
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
                try:
                    exposed = call('read', room=room_name)
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass
                messages = exposed.get('messages', [])
                if max_messages is None and messages and exposed.get('at_end') is not False:
                    return {'success': True, 'parsed': exposed}
                tail = messages[-max_messages:] if max_messages else []
                if (exposed.get('at_end') and len(tail) == max_messages
                        and all(m.get('sender') and m.get('date') for m in tail)):
                    return {'success': True, 'parsed': exposed}
            from .parser import parse_chat_text
            snapshot = legacy._plain_clipboard_snapshot()
            copied = call('copy', room=room_name)
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
        if mode == 'auto' and exposed.get('messages'):
            return {'success': True, 'parsed': {**exposed, 'partial': True, 'detail_error': str(error)}}
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
        with operation():
            hwnd = find_chat_window(room_name)
            if hwnd is None:
                raise ValueError('Target room is not open')
            for path in paths:
                with legacy.temporary_image_clipboard(str(path)) as sequence:
                    data = call('image', room=room_name, hwnd=hwnd, sequence=sequence)
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
