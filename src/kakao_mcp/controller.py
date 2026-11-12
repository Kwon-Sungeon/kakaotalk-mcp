"""Win32 API wrapper for KakaoTalk PC automation."""
import os
import sys
import time
import ctypes
from datetime import datetime
import re
from contextlib import contextmanager
from ctypes import wintypes
import filecmp
import shutil
import subprocess
import threading
from collections import Counter, deque
from typing import Optional, List, Dict

import win32gui
import win32api
import win32con
import win32clipboard
import win32process

from . import config
from .ui_lock import operation
from .qt_bridge import bridge


def _log(msg: str):
    """Write debug message to stderr (visible in MCP server logs)."""
    print(f"[kakao-controller] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Window discovery
# ---------------------------------------------------------------------------

def _main_window():
    return (win32gui.FindWindow(config.KAKAO_MAIN_WINDOW_CLASS, config.KAKAO_MAIN_WINDOW_TITLE)
            or win32gui.FindWindow(config.KAKAO_MAIN_WINDOW_CLASS, 'KakaoTalk'))


def _wait_until(check, seconds):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(.06)


def is_kakaotalk_running() -> Dict:
    """Check if KakaoTalk main window exists.

    Returns:
        Dict with running (bool), hwnd (int|None), pid (int|None).
    """
    hwnd = _main_window()
    if hwnd == 0:
        return {"running": False, "hwnd": None, "pid": None}
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return {"running": True, "hwnd": hwnd, "pid": pid}


def find_chat_window(room_name: str) -> Optional[int]:
    """Find a chat window by exact title (room name).

    Returns hwnd or None.
    """
    main = _main_window()
    if not main:
        return None
    pid = win32process.GetWindowThreadProcessId(main)[1]
    results: List[int] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if (hwnd != main and cls == config.KAKAO_CHAT_WINDOW_CLASS and title == room_name
                    and win32process.GetWindowThreadProcessId(hwnd)[1] == pid and _is_chat_window(hwnd)):
                results.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    if len(results) > 1:
        raise ValueError(f"Multiple open rooms named {room_name!r}")
    return results[0] if results else None


def list_chat_windows() -> List[Dict]:
    """List all currently open KakaoTalk chat windows.

    Returns list of dicts with hwnd and title.
    """
    main_hwnd = _main_window()
    if not main_hwnd:
        return []
    pid = win32process.GetWindowThreadProcessId(main_hwnd)[1]
    windows: List[Dict] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if (
                cls == config.KAKAO_CHAT_WINDOW_CLASS
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid
                and hwnd != main_hwnd
                and title
                and title != config.KAKAO_MAIN_WINDOW_TITLE
                and _is_chat_window(hwnd)
            ):
                windows.append({"hwnd": hwnd, "title": title})
        return True

    win32gui.EnumWindows(_cb, None)
    return windows


def find_child_window_recursive(parent_hwnd: int, class_name: str) -> Optional[int]:
    """Recursively search for a child window by class name.

    Needed because RICHEDIT50W may be nested several levels deep.
    Returns hwnd or None.
    """
    found: List[int] = []

    def _cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == class_name:
            found.append(hwnd)
            return False  # stop enumeration
        return True

    try:
        win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    except Exception:
        pass
    return found[0] if found else None


def _is_chat_window(hwnd: int) -> bool:
    """A chat room holds both the message input and the message list.

    Board (Moim), profile and viewer windows use the same window class and
    can carry a person's name as their title, so the class and title alone
    would list them as open rooms.
    """
    return (find_child_window_recursive(hwnd, config.KAKAO_EDIT_CLASS) is not None
            and find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS) is not None)


@operation()
def bring_window_to_front(hwnd: int):
    """Restore and bring a window to the foreground.

    Uses ctypes directly (not pywin32) to avoid exceptions on failure,
    and simulates Alt keypress to bypass Windows foreground restrictions.
    """
    VK_MENU = 0x12  # Alt key
    SW_RESTORE = 9
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040

    if _user32.GetForegroundWindow() == hwnd:
        return
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    if _user32.SetForegroundWindow(hwnd) and _user32.GetForegroundWindow() == hwnd:
        return

    # Simulate Alt keypress to unlock SetForegroundWindow
    _user32.keybd_event(VK_MENU, 0, 0, 0)
    _user32.keybd_event(VK_MENU, 0, config.KEYEVENTF_KEYUP, 0)

    # Use ctypes SetForegroundWindow (returns 0 on fail, no exception)
    _user32.SetForegroundWindow(hwnd)

    # Also temporarily set topmost to ensure visibility
    _user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )
    _user32.SetWindowPos(
        hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )


# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
        ('hwndActive', wintypes.HWND), ('hwndFocus', wintypes.HWND),
        ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
        ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND),
        ('rcCaret', wintypes.RECT),
    ]


def _require_focus(window, control):
    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if (not _user32.GetGUIThreadInfo(0, ctypes.byref(info))
            or info.hwndActive != window or info.hwndFocus != control
            or _user32.GetForegroundWindow() != window):
        raise RuntimeError("KakaoTalk target lost keyboard focus")


def _replace_selection(control, text):
    value = ctypes.create_unicode_buffer(text)
    win32gui.SendMessageTimeout(control, win32con.EM_REPLACESEL, 1, ctypes.addressof(value),
                                win32con.SMTO_ABORTIFHUNG, 1500)


def _edit_text(control):
    _, length = win32gui.SendMessageTimeout(control, win32con.WM_GETTEXTLENGTH, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000)
    buffer = ctypes.create_unicode_buffer(length + 1)
    win32gui.SendMessageTimeout(control, win32con.WM_GETTEXT, len(buffer), ctypes.addressof(buffer),
                                win32con.SMTO_ABORTIFHUNG, 1000)
    return buffer.value.replace('\r\n', '\n').replace('\r', '\n')


def _click_control(window, control):
    left, top, right, bottom = win32gui.GetWindowRect(control)
    point = ((left + right) // 2, (top + bottom) // 2)
    if _user32.GetForegroundWindow() != window or win32gui.WindowFromPoint(point) != control:
        raise RuntimeError("KakaoTalk target control is covered or inactive")
    previous = win32gui.GetCursorPos()
    _user32.SetCursorPos(*point)
    try:
        if win32gui.GetCursorPos() != point or win32gui.WindowFromPoint(point) != control:
            raise RuntimeError("KakaoTalk target moved before click")
        _user32.mouse_event(2, 0, 0, 0, 0)
        _user32.mouse_event(4, 0, 0, 0, 0)
    finally:
        if win32gui.GetCursorPos() == point:
            _user32.SetCursorPos(*previous)
    time.sleep(config.EDIT_CLICK_WAIT_SEC)
    _require_focus(window, control)


def _native_send_button(window, *, custom=False, include_disabled=False):
    """A standard, enabled send button can receive BM_CLICK without global input."""
    if not win32gui.IsWindowEnabled(window):
        return None
    buttons = []

    def visit(control, _):
        cls = win32gui.GetClassName(control).lower()
        if ((cls.startswith('eva_button') if custom else cls == 'button')
                and win32gui.GetAncestor(control, win32con.GA_ROOT) == window
                and re.fullmatch(r'(전송|보내기|send)(?:\s*\([a-z]\))?',
                                 win32gui.GetWindowText(control).replace('&', '').strip(), re.I)
                and win32gui.IsWindowVisible(control) and (include_disabled or win32gui.IsWindowEnabled(control))):
            buttons.append(control)
        return True

    win32gui.EnumChildWindows(window, visit, None)
    return buttons[0] if len(buttons) == 1 else None


def _focus_control(window, control):
    """Focus the native control without moving the pointer when supported."""
    if _user32.GetForegroundWindow() != window:
        raise RuntimeError("KakaoTalk target control is inactive")
    try:
        _require_focus(window, control)
        return
    except RuntimeError:
        pass
    current = win32api.GetCurrentThreadId()
    target = _user32.GetWindowThreadProcessId(window, None)
    if _user32.AttachThreadInput(current, target, True):
        try:
            _user32.SetFocus(control)
        finally:
            _user32.AttachThreadInput(current, target, False)
        try:
            _require_focus(window, control)
            return
        except RuntimeError:
            pass
    _click_control(window, control)


def _press_key(vk: int, shift: bool = False):
    """Press and release a single key via keybd_event, optionally with Shift."""
    VK_SHIFT = 0x10
    if shift:
        _user32.keybd_event(VK_SHIFT, 0, 0, 0)
        time.sleep(0.01)
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.01)
    _user32.keybd_event(vk, 0, config.KEYEVENTF_KEYUP, 0)
    if shift:
        time.sleep(0.01)
        _user32.keybd_event(VK_SHIFT, 0, config.KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)


def _send_ctrl_key_combo(vk_key: int):
    """Send Ctrl+<key> combo using keybd_event (requires foreground focus)."""
    _user32.keybd_event(config.VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, config.KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    _user32.keybd_event(config.VK_CONTROL, 0, config.KEYEVENTF_KEYUP, 0)


# ---------------------------------------------------------------------------
# Clipboard helpers
# ---------------------------------------------------------------------------

def _plain_clipboard_snapshot():
    """Only snapshot empty or plain-text clipboards that we can reconstruct."""
    try:
        win32clipboard.OpenClipboard()
        try:
            fmt = 0
            plain_formats = {win32con.CF_TEXT, win32con.CF_OEMTEXT,
                             win32con.CF_UNICODETEXT, win32con.CF_LOCALE}
            for _ in range(win32clipboard.CountClipboardFormats()):
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt not in plain_formats:
                    return None
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if fmt else None
            return win32clipboard.GetClipboardSequenceNumber(), text
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _restore_plain_clipboard(snapshot, before_copy, after_copy):
    if snapshot is None or snapshot[0] != before_copy or before_copy == after_copy:
        return
    try:
        win32clipboard.OpenClipboard()
        try:
            # Compare while holding the clipboard open. A user copy wins.
            if win32clipboard.GetClipboardSequenceNumber() == after_copy:
                win32clipboard.EmptyClipboard()
                if snapshot[1] is not None:
                    win32clipboard.SetClipboardText(snapshot[1], win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass  # Clipboard restoration is best effort.


def _read_clipboard_text(max_retries: int = None, interval_sec: float = None) -> str:
    """Read text from clipboard with retry logic."""
    if max_retries is None:
        max_retries = config.CLIPBOARD_MAX_RETRIES
    if interval_sec is None:
        interval_sec = config.CLIPBOARD_RETRY_INTERVAL_SEC

    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return data if data else ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(interval_sec)
    return ""


# ---------------------------------------------------------------------------
# Message sending
# ---------------------------------------------------------------------------

def _empty_input(edit, shown=None):
    shown = _edit_text(edit) if shown is None else shown
    if not shown:
        return True
    if shown not in config.KAKAO_INPUT_PLACEHOLDERS:
        return False
    # A cue can be returned by WM_GETTEXT even when the underlying Rich Edit
    # contains zero characters. Protect a real draft whose text equals the cue.
    _, length = win32gui.SendMessageTimeout(edit, win32con.EM_LINELENGTH, 0, 0,
                                            win32con.SMTO_ABORTIFHUNG, 1000)
    return length == 0


def _matches_draft(edit, expected):
    shown = _edit_text(edit)
    return shown == expected and not _empty_input(edit, shown)


def _paste_into_focused_input(hwnd, edit, room_name, expected, target):
    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit)
    if target:
        target.verify()
    if find_chat_window(room_name) != hwnd or not _empty_input(edit):
        return False
    _require_focus(hwnd, edit)
    snapshot = _plain_clipboard_snapshot()
    clip = win32clipboard
    clip.OpenClipboard()
    try:
        before = clip.GetClipboardSequenceNumber()
        clip.EmptyClipboard()
        clip.SetClipboardText(expected.replace('\n', '\r\n'), win32con.CF_UNICODETEXT)
        after = clip.GetClipboardSequenceNumber()
    finally:
        clip.CloseClipboard()
    try:
        if not _empty_input(edit) or clip.GetClipboardSequenceNumber() != after:
            return False
        _require_focus(hwnd, edit)
        _send_ctrl_key_combo(0x56)
        return bool(_wait_until(lambda: _matches_draft(edit, expected), 1.5))
    finally:
        _restore_plain_clipboard(snapshot, before, after)


@operation()
def send_message_to_room(room_name: str, text: str, *, target=None) -> Dict:
    """Send a text message to a KakaoTalk chat room.

    Use the native send button when identifiable. Buttonless clients need a
    focused paste and an Enter message addressed to the verified edit control.

    Returns:
        Dict with success (bool) and message or error.
    """
    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    edit_hwnd = find_child_window_recursive(hwnd, config.KAKAO_EDIT_CLASS)
    if edit_hwnd is None:
        return {"success": False, "status": "failed_before_send", "code": "input_not_found", "error": f"Edit control not found in '{room_name}'"}

    shown = _edit_text(edit_hwnd)
    if shown and shown not in config.KAKAO_INPUT_PLACEHOLDERS:
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    if not win32gui.IsWindowEnabled(hwnd):
        return {"success": False, "status": "failed_before_send", "code": "room_busy", "error": "Room has an open dialog"}
    expected = text.replace('\r\n', '\n').replace('\r', '\n')
    button = _native_send_button(hwnd, include_disabled=True) or _native_send_button(hwnd, custom=True, include_disabled=True)
    if not button:
        try:
            ready = _paste_into_focused_input(hwnd, edit_hwnd, room_name, expected, target)
        except Exception as error:
            return {"success": False, "status": "failed_before_send", "code": "input_not_ready", "error": str(error)}
        if not ready:
            return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Paste target or draft not verified"}
        return _submit_draft(room_name, hwnd, edit_hwnd, expected, target)
    if not _empty_input(edit_hwnd, shown):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    # EM_REPLACESEL goes to this Rich Edit and updates its change listeners.
    _replace_selection(edit_hwnd, expected.replace('\n', '\r\n'))
    draft = _edit_text(edit_hwnd)
    if not draft:
        # Target this edit even if the system clipboard changes to a file or photo.
        buf = ctypes.create_unicode_buffer(expected.replace('\n', '\r\n'))
        win32gui.SendMessageTimeout(edit_hwnd, win32con.WM_SETTEXT, 0, ctypes.addressof(buf),
                                    win32con.SMTO_ABORTIFHUNG, 1500)
        draft = _edit_text(edit_hwnd)
    if draft != expected:
        return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Chat draft changed before send"}

    return _submit_draft(room_name, hwnd, edit_hwnd, expected, target)


def _submit_draft(room_name, hwnd, edit_hwnd, expected, target=None):
    if target:
        target.verify()
    button = _native_send_button(hwnd)
    if button:
        if find_chat_window(room_name) != hwnd or not _matches_draft(edit_hwnd, expected):
            return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Chat draft changed before send"}
        win32gui.SendMessageTimeout(button, win32con.BM_CLICK, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000)
        return _sent_when_cleared(edit_hwnd, room_name)

    custom_button = _native_send_button(hwnd, custom=True)
    if custom_button:
        # Owner-drawn EVA buttons may not implement BM_CLICK. Deliver the mouse
        # messages to this button HWND only; never move the desktop pointer.
        _, _, width, height = win32gui.GetClientRect(custom_button)
        if (not width or not height or find_chat_window(room_name) != hwnd
                or not _matches_draft(edit_hwnd, expected)):
            return {"success": False, "error": "Target or draft changed before send"}
        point = ((height // 2) << 16) | (width // 2)
        for msg, keys in [(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON), (win32con.WM_LBUTTONUP, 0)]:
            win32gui.SendMessageTimeout(custom_button, msg, keys, point, win32con.SMTO_ABORTIFHUNG, 1000)
        return _sent_when_cleared(edit_hwnd, room_name)

    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit_hwnd)
    if not _matches_draft(edit_hwnd, expected):
        return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Chat draft changed before send"}
    if find_chat_window(room_name) != hwnd:
        return {"success": False, "error": "Target room changed before send"}
    _require_focus(hwnd, edit_hwnd)
    send_key = os.environ.get('KAKAO_LEGACY_SEND_KEY', 'enter').lower()
    if send_key == 'ctrl_enter':
        # With a nonstandard accelerator, invoke only this room's accessible button.
        bridge.request(dict(action='legacy_send_button', room=room_name, hwnd=hwnd, qt=False,
                            edit_hwnd=edit_hwnd, text=expected,
                            process_id=win32process.GetWindowThreadProcessId(hwnd)[1]), 5)
    else:
        # The application processes Enter through its message loop. Address the edit
        # explicitly so a foreground switch cannot send into another application.
        win32gui.PostMessage(edit_hwnd, win32con.WM_KEYDOWN, config.VK_RETURN, 0x001C0001)
        win32gui.PostMessage(edit_hwnd, win32con.WM_KEYUP, config.VK_RETURN, 0xC01C0001)
    return _sent_when_cleared(edit_hwnd, room_name)


def _sent_when_cleared(edit, room):
    if _wait_until(lambda: _empty_input(edit), 2):
        return {"success": True, "confirmation": "draft_cleared", "message": f"Message sent to '{room}'"}
    return {"success": False, "error": "Send not confirmed: the draft did not clear. Check the room; "
            "do not retry automatically. If KakaoTalk uses Ctrl+Enter set KAKAO_LEGACY_SEND_KEY=ctrl_enter."}


# ---------------------------------------------------------------------------
# Bulk message sending
# ---------------------------------------------------------------------------

def send_bulk_messages(room_names: List[str], message: str, interval_sec: float = 0.5) -> Dict:
    from .delivery import deliver
    return deliver('text', [(room, message) for room in room_names], interval=max(.3, interval_sec))


# ---------------------------------------------------------------------------
# Image sending
# ---------------------------------------------------------------------------

def _copy_image_to_clipboard(file_path: str, previous=None):
    """Copy an image file to clipboard as bitmap data (CF_DIB).

    Converts the image to BMP via PowerShell/.NET, then sets CF_DIB
    directly from Python so that:
      1. The format matches what screenshot capture tools produce.
      2. The Python process owns the clipboard and stays alive,
         so no OleFlushClipboard() is needed.
      3. Windows auto-synthesizes CF_BITMAP and CF_DIBV5 from CF_DIB.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")

    # Convert image to BMP byte stream using PowerShell/.NET
    ps_path = abs_path.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName System.Drawing;"
        f"$img = [System.Drawing.Image]::FromFile('{ps_path}');"
        "$ms = New-Object System.IO.MemoryStream;"
        "$img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Bmp);"
        "$img.Dispose();"
        "$bytes = $ms.ToArray();"
        "$ms.Dispose();"
        "[Console]::OpenStandardOutput().Write($bytes, 0, $bytes.Length)"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, timeout=15,
    )
    if result.returncode != 0:
        raise OSError(
            f"Failed to convert image to BMP: {result.stderr.decode().strip()}"
        )

    bmp_data = result.stdout
    if len(bmp_data) < 54:  # 14 (file header) + 40 (info header) minimum
        raise OSError("Image conversion produced invalid BMP data")

    # CF_DIB = BMP data minus the 14-byte BITMAPFILEHEADER
    dib_data = bmp_data[14:]

    if previous is not None:
        previous['snapshot'] = _plain_clipboard_snapshot()
    win32clipboard.OpenClipboard()
    try:
        if previous is not None:
            previous['before'] = win32clipboard.GetClipboardSequenceNumber()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib_data)
    finally:
        win32clipboard.CloseClipboard()
    # Closing the clipboard can synthesize formats and advance its sequence.
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.GetClipboardData(win32con.CF_DIB) != dib_data:
            raise RuntimeError("Clipboard image changed")
        return win32clipboard.GetClipboardSequenceNumber()
    finally:
        win32clipboard.CloseClipboard()


@contextmanager
def temporary_image_clipboard(file_path):
    previous = {}
    sequence = _copy_image_to_clipboard(file_path, previous)
    try:
        yield sequence
    finally:
        _restore_plain_clipboard(previous.get('snapshot'), previous.get('before'), sequence)


@operation()
def send_image_to_room(room_name: str, image_path: str, *, target=None) -> Dict:
    """Send an image file to a KakaoTalk chat room.

    Copies the image to clipboard as bitmap data (CF_DIB), pastes via
    Ctrl+V into the chat window, and confirms the send dialog.
    NOTE: This briefly brings the chat window to the foreground.
    """
    abs_path = os.path.abspath(image_path)
    if not os.path.isfile(abs_path):
        return {"success": False, "error": f"Image file not found: {abs_path}"}

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in config.SUPPORTED_IMAGE_EXTENSIONS:
        return {
            "success": False,
            "error": f"Unsupported image format '{ext}'. Supported: {', '.join(sorted(config.SUPPORTED_IMAGE_EXTENSIONS))}",
        }

    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    edit_hwnd = find_child_window_recursive(hwnd, config.KAKAO_EDIT_CLASS)
    if edit_hwnd is None:
        return {"success": False, "status": "failed_before_send", "code": "input_not_found", "error": f"Edit control not found in '{room_name}'"}

    if not _empty_input(edit_hwnd):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    try:
        with temporary_image_clipboard(abs_path) as sequence:
            return _send_prepared_image(hwnd, edit_hwnd, sequence, room_name, abs_path)
    except Exception as error:
        return {"success": False, "error": str(error)}


def _send_prepared_image(hwnd, edit_hwnd, sequence, room_name, abs_path):
    # Require a new preview. An existing modal must never receive paste or Enter.
    if not win32gui.IsWindowEnabled(hwnd) or find_chat_window(room_name) != hwnd:
        return {"success": False, "status": "failed_before_send", "error": "Room changed or has an open dialog"}
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit_hwnd)
    _require_focus(hwnd, edit_hwnd)
    if win32clipboard.GetClipboardSequenceNumber() != sequence or not _empty_input(edit_hwnd):
        return {"success": False, "status": "failed_before_send", "error": "Clipboard or draft changed"}
    win32gui.PostMessage(edit_hwnd, win32con.WM_PASTE, 0, 0)
    result = bridge.request(dict(action='legacy_image_confirm', room=room_name, hwnd=hwnd,
                                 process_id=pid, qt=False), 10)
    return {"success": result['sent'], "confirmation": result['confirmation'],
            "message": f"Image sent to '{room_name}'"}


def send_images_to_room(room_name: str, image_paths: List[str]) -> Dict:
    from .delivery import deliver
    return deliver('image', [(room_name, path) for path in image_paths])


# ---------------------------------------------------------------------------
# Message reading
# ---------------------------------------------------------------------------

@operation()
def read_chat_messages(room_name: str, read_mode=None, max_messages=None, *, monitoring=False,
                       all_history=False) -> Dict:
    """Read the native list text when available, or copy the transcript.

    NOTE: This briefly brings the chat window to the foreground.

    Returns:
        Dict with success (bool), raw_text (str), and error if failed.
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found", "raw_text": ""}

    list_hwnd = find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS)
    if list_hwnd is None:
        return {
            "success": False,
            "error": f"List control not found in '{room_name}'",
            "raw_text": "",
        }

    if read_mode != 'complete':
        from .parser import parse_chat_text
        try:
            raw = _edit_text(list_hwnd)
        except win32gui.error:
            raw = ''
        parsed = parse_chat_text(raw)
        if parsed['messages']:
            parsed['read_method'] = 'win32_text'
            parsed['partial'] = True
            parsed['room_name'] = room_name
            if (read_mode == 'background' or
                    (not all_history and (not max_messages or len(parsed['messages']) >= max_messages))):
                return {"success": True, "raw_text": raw, "parsed": parsed}
        if read_mode == 'background':
            return {"success": False, "error": "The legacy message list exposes no background transcript; use complete mode"}
        if monitoring:
            # Copying takes focus even if the user is typing in the same room.
            idle_ms = (win32api.GetTickCount() - win32api.GetLastInputInfo()) & 0xffffffff
            if idle_ms < 3000:
                return {"success": False, "deferred": True, "error": "Waiting for idle input before copying legacy history"}

    # Bring the chat window to foreground — required for keybd_event
    bring_window_to_front(hwnd)
    time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)

    # Prefer transferring focus directly to the list.
    _focus_control(hwnd, list_hwnd)

    # Ctrl+A (select all)
    _require_focus(hwnd, list_hwnd)
    _send_ctrl_key_combo(config.VK_A)
    time.sleep(config.KEY_COMBO_WAIT_SEC)

    snapshot = _plain_clipboard_snapshot()
    _require_focus(hwnd, list_hwnd)
    before = win32clipboard.GetClipboardSequenceNumber()
    _send_ctrl_key_combo(config.VK_C)
    time.sleep(config.KEY_COMBO_WAIT_SEC)
    after = win32clipboard.GetClipboardSequenceNumber()
    owner = win32clipboard.GetClipboardOwner()
    if (before == after or not owner or
            win32process.GetWindowThreadProcessId(owner)[1] != win32process.GetWindowThreadProcessId(hwnd)[1]):
        return {"success": False, "error": "KakaoTalk did not publish a fresh transcript"}
    raw_text = _read_clipboard_text()
    if win32clipboard.GetClipboardSequenceNumber() != after:
        return {"success": False, "error": "Clipboard changed during transcript copy"}
    _restore_plain_clipboard(snapshot, before, after)

    return {"success": True, "raw_text": raw_text}


# ---------------------------------------------------------------------------
# Room search/open
# ---------------------------------------------------------------------------

def _find_chat_list_view(main_hwnd: int) -> Optional[int]:
    """Find the ChatRoomListView window inside the main window."""
    chat_list_view = None

    def _find_view(hwnd, _):
        nonlocal chat_list_view
        cls = win32gui.GetClassName(hwnd)
        text = win32gui.GetWindowText(hwnd)
        if cls == "EVA_Window" and "ChatRoomListView" in text:
            chat_list_view = hwnd
            return False
        return True

    try:
        win32gui.EnumChildWindows(main_hwnd, _find_view, None)
    except Exception:
        pass
    return chat_list_view


def _find_search_strip(chat_list_view: int) -> Optional[int]:
    """Find the search strip (EVA_Window_Dblclk, ~390x40) inside ChatRoomListView.

    This strip contains the magnifying glass icon. Clicking it activates the search
    Edit control (which starts out hidden/invisible).
    """
    result = None

    def _cb(hwnd, _):
        nonlocal result
        if win32gui.GetParent(hwnd) != chat_list_view:
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls == "EVA_Window_Dblclk":
            try:
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if w > 200 and 25 < h < 60:
                    result = hwnd
                    return False
            except Exception:
                pass
        return True

    try:
        win32gui.EnumChildWindows(chat_list_view, _cb, None)
    except Exception:
        pass
    return result


def _activate_search_and_get_edit(main_hwnd: int) -> Optional[int]:
    """Activate the chat search bar using Ctrl+F and return the Edit hwnd.

    KakaoTalk PC uses Ctrl+F to open the search bar in the chat list view.
    After Ctrl+F, the Edit control becomes visible and focused.
    """
    def active_view():
        view = _find_chat_list_view(main_hwnd)
        return view if view and win32gui.IsWindowVisible(view) else None

    view = active_view()
    for _ in range(3):
        if view:
            break
        if _user32.GetForegroundWindow() != main_hwnd:
            raise RuntimeError("Main window lost focus before selecting Chats")
        _send_ctrl_key_combo(win32con.VK_TAB)
        view = _wait_until(active_view, .45)
    if not view:
        raise RuntimeError("Chats tab not accessible. Select the Chats tab in KakaoTalk, then open the room.")

    def visible_edit():
        edits = []
        def visit(child, _):
            if (win32gui.GetClassName(child).lower() == 'edit' and win32gui.IsWindowVisible(child)
                    and win32gui.IsWindowEnabled(child)):
                edits.append(child)
            return True
        win32gui.EnumChildWindows(view, visit, None)
        return edits[0] if len(edits) == 1 else None

    edit = visible_edit()
    if not edit and _user32.GetForegroundWindow() == main_hwnd:
        _send_ctrl_key_combo(ord('F'))
        edit = _wait_until(visible_edit, 2.5)
    if edit:
        _focus_control(main_hwnd, edit)
    return edit


def _ensure_foreground(hwnd: int) -> bool:
    """Ensure a window is in the foreground. Returns True if successful."""
    bring_window_to_front(hwnd)
    time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)
    fg = _user32.GetForegroundWindow()
    return fg == hwnd


@operation()
def search_and_open_room(room_name: str) -> Dict:
    """Search for a chat room in KakaoTalk main window and open it.

    Selects Chats, searches through its visible Edit, and verifies the exact opened room.

    Returns:
        Dict with success (bool) and message or error.
    """
    main_hwnd = _main_window()
    if main_hwnd == 0:
        return {"success": False, "error": "KakaoTalk main window not found"}

    # Ensure KakaoTalk is in the foreground before sending keyboard events
    if not _ensure_foreground(main_hwnd):
        return {"success": False, "error": "Could not activate KakaoTalk main window"}

    # Ctrl+F activates the search bar (Edit becomes visible and focused)
    edit_hwnd = _activate_search_and_get_edit(main_hwnd)
    if edit_hwnd is None:
        return {"success": False, "error": "Search box not found in KakaoTalk main window"}

    win32gui.SendMessageTimeout(edit_hwnd, win32con.EM_SETSEL, 0, -1, win32con.SMTO_ABORTIFHUNG, 1500)
    _replace_selection(edit_hwnd, room_name)
    if _edit_text(edit_hwnd) != room_name:
        return {"success": False, "error": "Could not verify exact room search text"}
    view = _find_chat_list_view(main_hwnd)
    results = _wait_until(lambda: _find_visible_search_list(view), 2) if view else None
    if not results or _edit_text(edit_hwnd) != room_name:
        return {"success": False, "code": "room_not_verified", "error": "Cannot identify search results; open the exact room manually"}
    bridge.request(dict(action='legacy_open_result', room=room_name, hwnd=main_hwnd,
                        list_hwnd=results, edit_hwnd=edit_hwnd,
                        process_id=win32process.GetWindowThreadProcessId(main_hwnd)[1]), 8)
    hwnd = _wait_until(lambda: find_chat_window(room_name), 3)
    if hwnd:
        return {"success": True, "message": f"Opened chat room '{room_name}'", "hwnd": hwnd}
    return {
        "success": False,
        "error": f"Chat room '{room_name}' not found after search. "
                 "The exact room name may differ from search results.",
    }


def _find_visible_search_list(view: int) -> Optional[int]:
    """Accept only the unique visible results list inside the active Chats view."""
    matches = []

    def visit(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd)
                and win32gui.GetClassName(hwnd) == config.KAKAO_LIST_CONTROL_CLASS
                and "SearchListCtrl" in win32gui.GetWindowText(hwnd)):
            matches.append(hwnd)
        return True

    win32gui.EnumChildWindows(view, visit, None)
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Image download (cache-based)
# ---------------------------------------------------------------------------

def get_kakao_user_hash_dir() -> Optional[str]:
    """Resolve the selected or only account cache under the KakaoTalk users directory.

    KakaoTalk stores per-user data in a directory named with a SHA1 hash.
    Returns the full path or None.
    """
    users_dir = config.KAKAO_USERS_DIR
    if not os.path.isdir(users_dir):
        return None
    accounts = [entry for entry in os.listdir(users_dir) if re.fullmatch(r'[0-9a-fA-F]{40}', entry)
                and os.path.isdir(os.path.join(users_dir, entry))]
    selected = os.environ.get('KAKAO_MCP_CACHE_ACCOUNT')
    if selected:
        if selected not in accounts:
            raise ValueError('Configured cache account not found')
        return os.path.join(users_dir, selected)
    if len(accounts) > 1:
        raise ValueError('Multiple account caches; select one with KAKAO_MCP_CACHE_ACCOUNT')
    return os.path.join(users_dir, accounts[0]) if accounts else None


def _sniff_image_extension(path: str) -> Optional[str]:
    """Return the extension matching the file's signature, or None if it is not an image."""
    try:
        with open(path, 'rb') as stream:
            head = stream.read(16)
    except OSError:
        return None
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    if head[4:8] == b'ftyp' and head[8:12] in (b'heic', b'heix', b'hevc', b'mif1', b'msf1'):
        return '.heic'
    if head[:2] == b'BM':
        return '.bmp'
    return None


def download_recent_images(
    room_name: str,
    output_dir: str,
    max_images: int = 10,
) -> Dict:
    """Download recent images from KakaoTalk cache.

    Collects image files from cache subdirectories sorted by modification time
    (newest first) and copies them to the output directory.

    Args:
        room_name: Chat room name (used for logging; cache is not per-room).
        output_dir: Directory to save images to.
        max_images: Max number of images to copy.

    Returns:
        Dict with message, images list, or error.
    """
    if max_images < 1:
        return {"success": False, "error": "max_images must be positive", "images": []}
    user_dir = get_kakao_user_hash_dir()
    if user_dir is None:
        return {"success": False, "error": "KakaoTalk user data directory not found", "images": []}

    # Collect all image files from cache directories
    image_files: List[Dict] = []
    for subdir in config.IMAGE_CACHE_SUBDIRS:
        cache_path = os.path.join(user_dir, subdir)
        if not os.path.isdir(cache_path):
            continue
        for fname in os.listdir(cache_path):
            full_path = os.path.join(cache_path, fname)
            if not os.path.isfile(full_path):
                continue
            try:
                stat = os.stat(full_path)
                # Skip tiny files (< 1KB, likely not real images)
                if stat.st_size < 1024:
                    continue
                image_files.append({
                    "path": full_path,
                    "name": fname,
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                })
            except OSError:
                continue

    # The cache belongs to the whole account, not to one room.
    scope = {"success": True, "scope": "account_cache", "partial": True}
    if not image_files:
        return {
            **scope,
            "message": "No cached account images found",
            "images": [],
        }

    # Sort by modification time (newest first)
    image_files.sort(key=lambda x: x["mtime"], reverse=True)

    # Copy to output directory. Cache files have no reliable extension, so the
    # real format is read from the file signature and other files are skipped.
    os.makedirs(output_dir, exist_ok=True)
    copied: List[Dict] = []
    skipped = 0
    for img in image_files:
        if len(copied) >= max_images:
            break
        extension = _sniff_image_extension(img["path"])
        if extension is None:
            skipped += 1
            continue
        stem = os.path.splitext(img["name"])[0]
        dest = os.path.join(output_dir, stem + extension)
        try:
            suffix = 1
            while True:
                try:
                    output = open(dest, "xb")
                except FileExistsError:
                    if os.path.isfile(dest) and filecmp.cmp(img["path"], dest, shallow=False):
                        break
                    suffix += 1
                    dest = os.path.join(output_dir, f"{stem}-{suffix}{extension}")
                    continue
                try:
                    with output, open(img["path"], "rb") as source:
                        shutil.copyfileobj(source, output)
                except OSError:
                    os.unlink(dest)
                    raise
                break
            copied.append({
                "source": img["path"],
                "destination": dest,
                "size": img["size"],
                "modified": datetime.fromtimestamp(img["mtime"]).isoformat(timespec="seconds"),
            })
        except OSError:
            continue

    return {
        **scope,
        "message": f"Downloaded {len(copied)} image(s) to {output_dir}",
        "images": copied,
        "skipped_non_images": skipped,
    }


# ---------------------------------------------------------------------------
# Korean 2벌식 keyboard mapping (for @mention input)
# ---------------------------------------------------------------------------

# Map jamo → (VK code, needs_shift)
_KOREAN_KEY_MAP = {
    # Consonants (초성/종성)
    'ㅂ': (0x51, False), 'ㅈ': (0x57, False), 'ㄷ': (0x45, False),
    'ㄱ': (0x52, False), 'ㅅ': (0x54, False), 'ㅛ': (0x59, False),
    'ㅕ': (0x55, False), 'ㅑ': (0x49, False), 'ㅐ': (0x4F, False),
    'ㅔ': (0x50, False), 'ㅁ': (0x41, False), 'ㄴ': (0x53, False),
    'ㅇ': (0x44, False), 'ㄹ': (0x46, False), 'ㅎ': (0x47, False),
    'ㅗ': (0x48, False), 'ㅓ': (0x4A, False), 'ㅏ': (0x4B, False),
    'ㅣ': (0x4C, False), 'ㅋ': (0x5A, False), 'ㅌ': (0x58, False),
    'ㅊ': (0x43, False), 'ㅍ': (0x56, False), 'ㅠ': (0x42, False),
    'ㅜ': (0x4E, False), 'ㅡ': (0x4D, False),
    # Shift consonants (쌍자음)
    'ㅆ': (0x54, True), 'ㄲ': (0x52, True), 'ㄸ': (0x45, True),
    'ㅃ': (0x51, True), 'ㅉ': (0x57, True),
    # Shift vowels
    'ㅒ': (0x4F, True), 'ㅖ': (0x50, True),
}

_INITIALS = list('ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
_MEDIALS = list('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')
_FINALS = [''] + list('ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ')

_COMPOUND_MEDIALS = {
    'ㅘ': ['ㅗ', 'ㅏ'], 'ㅙ': ['ㅗ', 'ㅐ'], 'ㅚ': ['ㅗ', 'ㅣ'],
    'ㅝ': ['ㅜ', 'ㅓ'], 'ㅞ': ['ㅜ', 'ㅔ'], 'ㅟ': ['ㅜ', 'ㅣ'],
    'ㅢ': ['ㅡ', 'ㅣ'],
}

_COMPOUND_FINALS = {
    'ㄳ': ['ㄱ', 'ㅅ'], 'ㄵ': ['ㄴ', 'ㅈ'], 'ㄶ': ['ㄴ', 'ㅎ'],
    'ㄺ': ['ㄹ', 'ㄱ'], 'ㄻ': ['ㄹ', 'ㅁ'], 'ㄼ': ['ㄹ', 'ㅂ'],
    'ㄽ': ['ㄹ', 'ㅅ'], 'ㄾ': ['ㄹ', 'ㅌ'], 'ㄿ': ['ㄹ', 'ㅍ'],
    'ㅀ': ['ㄹ', 'ㅎ'], 'ㅄ': ['ㅂ', 'ㅅ'],
}


def _decompose_korean(text: str) -> List[tuple]:
    """Decompose Korean text into a sequence of (VK_code, shift) keypresses.

    Handles Hangul syllables (decomposed into jamo via 2벌식 mapping),
    spaces, and skips non-Hangul characters.
    """
    keys: List[tuple] = []
    for ch in text:
        if ch == ' ':
            keys.append((0x20, False))  # VK_SPACE
            continue

        code = ord(ch) - 0xAC00
        if code < 0 or code > 11171:
            # Non-Hangul character — skip
            continue

        initial = code // (21 * 28)
        medial = (code % (21 * 28)) // 28
        final = code % 28

        # Initial consonant
        ini = _INITIALS[initial]
        if ini in _KOREAN_KEY_MAP:
            keys.append(_KOREAN_KEY_MAP[ini])

        # Medial vowel (may be compound)
        med = _MEDIALS[medial]
        if med in _COMPOUND_MEDIALS:
            for m in _COMPOUND_MEDIALS[med]:
                keys.append(_KOREAN_KEY_MAP[m])
        elif med in _KOREAN_KEY_MAP:
            keys.append(_KOREAN_KEY_MAP[med])

        # Final consonant (may be compound or empty)
        if final > 0:
            fin = _FINALS[final]
            if fin in _COMPOUND_FINALS:
                for f in _COMPOUND_FINALS[fin]:
                    keys.append(_KOREAN_KEY_MAP[f])
            elif fin in _KOREAN_KEY_MAP:
                keys.append(_KOREAN_KEY_MAP[fin])

    return keys


# ---------------------------------------------------------------------------
# Mention message sending
# ---------------------------------------------------------------------------

@operation()
def send_mention_message(room_name: str, mention_name: str, message: str, *, target=None) -> Dict:
    """Select an exposed exact mention candidate before submitting the verified draft."""
    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
    edit = find_child_window_recursive(hwnd, config.KAKAO_EDIT_CLASS) if hwnd else None
    if not edit:
        return {"success": False, "status": "failed_before_send", "error": "Target chat input not found"}
    if not _empty_input(edit):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit)
    _require_focus(hwnd, edit)
    if not _empty_input(edit):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    _press_key(0x32, shift=True)
    time.sleep(config.MENTION_AT_WAIT_SEC)
    for vk, shift in _decompose_korean(mention_name):
        _require_focus(hwnd, edit)
        _press_key(vk, shift=shift)
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    bridge.request(dict(action='legacy_select_mention', hwnd=hwnd, process_id=pid, qt=False,
                        room=room_name, mention=mention_name), 8)
    prefix = '@' + mention_name + ' '
    if not _wait_until(lambda: _edit_text(edit) == prefix, 1.5):
        return {"success": False, "status": "failed_before_send", "error": "Selected mention token not verified"}
    _replace_selection(edit, message)
    return _submit_draft(room_name, hwnd, edit, prefix + message, target)


# ---------------------------------------------------------------------------
# Chat room monitoring (background thread)
# ---------------------------------------------------------------------------

class ChatMonitor:
    """Background chat room monitor with keyword detection.

    Polls a single chat room at a configurable interval, detects new messages
    by comparing message occurrence counts, and queues events when keywords are matched.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._events: deque = deque(maxlen=config.MONITOR_MAX_EVENTS)
        self._events_lock = threading.Lock()
        self._events_ready = threading.Condition(self._events_lock)
        self._dropped = 0
        self._polls = 0
        self._last_poll: Optional[str] = None
        self._last_error: Optional[str] = None
        self._seen_counts = Counter()
        self._room_name: str = ""
        self._keywords: List[str] = []
        self.baseline_ready = False
        self._read_mode = None
        self._poll_interval: float = 5.0
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self, room_name: str, keywords: List[str], poll_interval: float = 5.0, read_mode=None) -> Dict:
        """Start monitoring a chat room for keywords."""
        if self.is_running:
            return {"success": False, "error": "Monitor already running"}

        self._room_name = room_name
        self._read_mode = read_mode
        self._keywords = [kw.strip().lower() for kw in keywords if kw and kw.strip()]
        if not self._keywords:
            return {"success": False, "error": "Keywords list cannot be empty"}
        self._poll_interval = max(3.0, poll_interval)
        self._stop_event.clear()
        with self._events_lock:
            self._events.clear()
            self._dropped = 0
        self._polls = 0
        self._last_poll = None
        self._last_error = None
        self.baseline_ready = False
        self._seen_counts.clear()
        self._running = True

        # Load existing messages so they don't trigger events
        try:
            self._load_initial_messages()
        except Exception as error:
            self._running = False
            return {"success": False, "error": str(error)}

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

        return {
            "success": True,
            "baseline_ready": self.baseline_ready,
            "message": f"Monitoring '{room_name}' for keywords: {keywords} (interval: {self._poll_interval}s)",
        }

    def stop(self) -> Dict:
        """Stop the monitoring thread."""
        if not self.is_running:
            return {"success": False, "error": "Monitor not running"}
        self._stop_event.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            return {"success": False, "error": "Monitor is finishing its current UI operation"}
        self._running = False
        with self._events_ready:
            self._events_ready.notify_all()
        return {"success": True, "message": "Monitor stopped"}

    def get_events(self, wait_sec: float = 0) -> List[Dict]:
        """Return and clear all pending keyword-match events.

        With wait_sec, block until an event arrives, the monitor stops or the
        time is up. One waiting call replaces many empty polls by the client.
        """
        wait = min(max(0.0, float(wait_sec or 0)), config.MONITOR_MAX_WAIT_SEC)
        deadline = time.monotonic() + wait
        with self._events_ready:
            while not self._events and self.is_running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._events_ready.wait(min(remaining, 1.0))
            events = list(self._events)
            self._events.clear()
        return events

    def status(self) -> Dict:
        """What the monitor is watching and how the last poll went."""
        running = self.is_running
        return {
            "monitoring": running,
            "room_name": self._room_name or None,
            "keywords": list(self._keywords),
            "poll_interval_sec": self._poll_interval,
            "read_mode": self._read_mode,
            "baseline_ready": running and self.baseline_ready,
            "polls": self._polls,
            "last_poll": self._last_poll,
            "last_error": self._last_error,
            "dropped_events": self._dropped,
        }

    def _emit(self, event: Dict):
        with self._events_ready:
            if len(self._events) == self._events.maxlen:
                self._dropped += 1  # The oldest event is about to be pushed out.
            self._events.append(event)
            self._events_ready.notify_all()

    def _message_key(self, msg):
        return tuple(msg.get(field) for field in ('date', 'sender', 'time', 'text'))

    def _load_initial_messages(self):
        from . import backend, parser

        result = backend.read_chat_messages(self._room_name, self._read_mode, monitoring=True)
        if result.get('deferred'):
            return
        if not result["success"]:
            raise RuntimeError(result["error"])
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        self._seen_counts.update(self._message_key(msg) for msg in parsed["messages"])
        self.baseline_ready = True

    def _monitor_loop(self):
        """Background polling loop."""
        _log(f"Monitor started: room='{self._room_name}', keywords={self._keywords}")
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            try:
                self._check_for_new_messages()
            except Exception as e:
                self._last_error = str(e)
                _log(f"Monitor poll error: {e}")
        _log("Monitor stopped")

    def _check_for_new_messages(self):
        """Poll for new messages and check for keyword matches."""
        from . import backend
        result = backend.read_chat_messages(self._room_name, self._read_mode, monitoring=True)
        self._polls += 1
        self._last_poll = datetime.now().astimezone().isoformat(timespec="seconds")
        if not result["success"]:
            # A closed room or a failed read used to pass without a trace.
            if not result.get("deferred"):
                self._last_error = result.get("error") or "Read failed"
            return
        self._last_error = None

        from . import parser
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        all_messages = parsed["messages"]

        new_messages = []
        counts = Counter()
        for msg in all_messages:
            key = self._message_key(msg)
            counts[key] += 1
            if counts[key] > self._seen_counts[key]:
                new_messages.append(msg)
        self._seen_counts |= counts
        if not self.baseline_ready:
            self.baseline_ready = True
            return

        if not new_messages:
            return

        _log(f"Monitor: {len(new_messages)} new message(s)")

        for msg in new_messages:
            text_lower = msg.get("text", "").lower()
            for kw in self._keywords:
                if kw == config.MONITOR_MATCH_ALL or kw in text_lower:
                    context_start = max(0, len(all_messages) - 10)
                    event = {
                        "keyword": kw,
                        "trigger_message": msg,
                        "read_method": parsed.get("read_method", "clipboard"),
                        "partial": parsed.get("partial", False),
                        "recent_context": all_messages[context_start:],
                        "room_name": self._room_name,
                    }
                    self._emit(event)
                    _log(f"Monitor: keyword '{kw}' matched in message from {msg.get('sender')}")
                    break  # One event per message


# Module-level singleton
_chat_monitor = ChatMonitor()
