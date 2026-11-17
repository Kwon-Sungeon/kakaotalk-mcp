"""Win32 API wrapper for KakaoTalk PC automation."""
import os
import sys
import time
import ctypes
from datetime import datetime
import locale
import re
import struct
import uuid
from contextlib import contextmanager
from ctypes import wintypes
import filecmp
import shutil
import subprocess
import threading
from collections import Counter, deque
from typing import Optional, List, Dict

import pywintypes
import win32gui
import win32api
import win32con
import win32clipboard
import win32process

from . import config
from .ui_lock import operation
from .qt_bridge import bridge


def _win32_errors():
    """Return catchable Win32 exception types even when pywin32 modules are mocked."""
    errs = [pywintypes.error]
    for mod in (win32gui, win32process):
        err = getattr(mod, 'error', None)
        if isinstance(err, type) and issubclass(err, BaseException) and err not in errs:
            errs.append(err)
    return tuple(errs)


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
        time.sleep(.02)


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
    try:
        pid = win32process.GetWindowThreadProcessId(main)[1]
    except _win32_errors():
        return None
    results: List[int] = []

    def _cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                if (hwnd != main and cls == config.KAKAO_CHAT_WINDOW_CLASS and title == room_name
                        and win32process.GetWindowThreadProcessId(hwnd)[1] == pid and _is_chat_window(hwnd)):
                    results.append(hwnd)
        except _win32_errors():
            return True
        return True

    ctypes.windll.kernel32.SetLastError(0)
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
    try:
        pid = win32process.GetWindowThreadProcessId(main_hwnd)[1]
    except _win32_errors():
        return []
    windows: List[Dict] = []

    def _cb(hwnd, _):
        try:
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
        except _win32_errors():
            return True
        return True

    ctypes.windll.kernel32.SetLastError(0)
    win32gui.EnumWindows(_cb, None)
    return windows


def find_child_window_recursive(parent_hwnd: int, class_name: str) -> Optional[int]:
    """Recursively search for a child window by class name.

    Needed because RICHEDIT50W may be nested several levels deep.
    Returns hwnd or None.
    """
    found: List[int] = []

    def _cb(hwnd, _):
        try:
            if win32gui.GetClassName(hwnd) == class_name:
                found.append(hwnd)
                return False  # stop enumeration
        except _win32_errors():
            return True
        return True

    try:
        win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    except _win32_errors() as err:
        if not found:
            winerror = getattr(err, 'winerror', None)
            if winerror not in (None, 0):
                _log(f"EnumChildWindows failed for parent={parent_hwnd}: {err}")
    except Exception as err:
        if not found:
            _log(f"Unexpected error in EnumChildWindows for parent={parent_hwnd}: {err}")
            raise
    return found[0] if found else None


def find_chat_input(room):
    """Prefer KakaoTalk's message control ID, retaining unique legacy layouts."""
    pid = win32process.GetWindowThreadProcessId(room)[1]
    editors = []
    known = []

    def visit(child, _):
        try:
            if (win32gui.GetClassName(child) == config.KAKAO_EDIT_CLASS
                    and win32gui.IsWindowVisible(child) and win32gui.IsWindowEnabled(child)
                    and win32process.GetWindowThreadProcessId(child)[1] == pid):
                editors.append(child)
                if win32gui.GetDlgCtrlID(child) == 1006:
                    known.append(child)
        except _win32_errors():
            return True
        return True

    win32gui.EnumChildWindows(room, visit, None)
    candidates = known or editors
    if len(candidates) > 1:
        raise _PasteError('input_ambiguous', 'Multiple candidate message inputs in the same room')
    return candidates[0] if candidates else None


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
        try:
            cls = win32gui.GetClassName(control).lower()
            if ((cls.startswith('eva_button') if custom else cls == 'button')
                    and win32gui.GetAncestor(control, win32con.GA_ROOT) == window
                    and re.fullmatch(r'(전송|보내기|send)(?:\s*\([a-z]\))?',
                                     win32gui.GetWindowText(control).replace('&', '').strip(), re.I)
                    and win32gui.IsWindowVisible(control) and (include_disabled or win32gui.IsWindowEnabled(control))):
                buttons.append(control)
        except _win32_errors():
            return True
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
        time.sleep(0.02)
        try:
            _require_focus(window, control)
            return
        except RuntimeError:
            pass
    _click_control(window, control)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG), ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', _MOUSEINPUT), ('ki', _KEYBDINPUT), ('hi', _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ('_u',)
    _fields_ = [('type', wintypes.DWORD), ('_u', _INPUT_UNION)]


def _send_unicode_char(ch: str):
    """Send a single Unicode character using Win32 SendInput (KEYEVENTF_UNICODE)."""
    if not ch:
        return
    utf16 = ch.encode('utf-16-le')
    inputs = []
    for i in range(0, len(utf16), 2):
        scan = int.from_bytes(utf16[i:i + 2], 'little')
        inputs.append(_INPUT(type=1, ki=_KEYBDINPUT(wVk=0, wScan=scan, dwFlags=config.KEYEVENTF_UNICODE)))
        inputs.append(_INPUT(
            type=1,
            ki=_KEYBDINPUT(wVk=0, wScan=scan, dwFlags=config.KEYEVENTF_UNICODE | config.KEYEVENTF_KEYUP),
        ))
    arr = (_INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    if isinstance(sent, int) and sent != len(inputs):
        raise RuntimeError(f"SendInput sent {sent}/{len(inputs)} keyboard events")
    time.sleep(0.01)


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

@contextmanager
def _open_clipboard(clip=None, timeout_sec: float = 0.8, interval_sec: float = None):
    """Acquire the Windows clipboard, retrying transient ERROR_ACCESS_DENIED locks."""
    if clip is None:
        clip = win32clipboard
    if interval_sec is None:
        interval_sec = config.CLIPBOARD_POLL_INTERVAL_SEC
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            clip.OpenClipboard()
            break
        except _win32_errors() as exc:
            winerror = getattr(exc, 'winerror', None)
            if winerror is None and getattr(exc, 'args', None):
                winerror = exc.args[0]
            if winerror != 5 or time.monotonic() >= deadline:
                raise
            time.sleep(interval_sec)
    try:
        yield clip
    finally:
        clip.CloseClipboard()


def _plain_clipboard_snapshot():
    """Only snapshot empty or plain-text clipboards that we can reconstruct."""
    try:
        with _open_clipboard(timeout_sec=0.25):
            fmt = 0
            plain_formats = {win32con.CF_TEXT, win32con.CF_OEMTEXT,
                             win32con.CF_UNICODETEXT, win32con.CF_LOCALE}
            for _ in range(win32clipboard.CountClipboardFormats()):
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt not in plain_formats:
                    return None
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if fmt else None
            return win32clipboard.GetClipboardSequenceNumber(), text
    except Exception:
        return None


def _restore_plain_clipboard(snapshot, before_copy, after_copy):
    if snapshot is None or snapshot[0] != before_copy or before_copy == after_copy:
        return
    try:
        with _open_clipboard(timeout_sec=0.25):
            # Compare while holding the clipboard open. A user copy wins.
            if win32clipboard.GetClipboardSequenceNumber() == after_copy:
                win32clipboard.EmptyClipboard()
                if snapshot[1] is not None:
                    win32clipboard.SetClipboardText(snapshot[1], win32con.CF_UNICODETEXT)
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


def _is_real_draft(edit, shown=None):
    """Return True only when the edit holds a non-placeholder user draft."""
    if shown is not None:
        if not shown or shown in config.KAKAO_INPUT_PLACEHOLDERS:
            return False
        return not _empty_input(edit, shown)
    if _empty_input(edit):
        return False
    shown = _edit_text(edit)
    return bool(shown) and shown not in config.KAKAO_INPUT_PLACEHOLDERS


def _matches_draft(edit, expected):
    shown = _edit_text(edit)
    return shown == expected and not _empty_input(edit, shown)


class _PasteError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _paste_into_focused_input(hwnd, edit, room_name, expected, target, replace_legacy_hint):
    def verify_target():
        if target:
            target.verify()
        if (find_chat_window(room_name) != hwnd
                or find_chat_input(hwnd) != edit
                or not win32gui.IsWindowEnabled(hwnd)):
            raise _PasteError('room_changed', 'Room or input changed before paste')
        _require_focus(hwnd, edit)

    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit)
    verify_target()
    literal_hint = None
    shown = _edit_text(edit)
    if not _empty_input(edit, shown):
        if shown not in config.KAKAO_INPUT_PLACEHOLDERS:
            raise _PasteError('draft_exists', 'Chat has an existing draft before paste')
        if replace_legacy_hint and expected != shown:
            literal_hint = shown
        else:
            # Some legacy builds remove the literal hint when the input is clicked,
            # even if SetFocus alone left it visible. Never select or delete it.
            _click_control(hwnd, edit)
            verify_target()
            if not _wait_until(lambda: _empty_input(edit), .4):
                current = _edit_text(edit)
                if current != shown:
                    raise _PasteError('draft_changed', 'Input changed after focus')
                raise _PasteError('input_hint_unresolved', 'Focused input contains the literal placeholder. '
                                 'Only if the user verified the field was empty, retry with replace_legacy_hint=true '
                                 'and a new request ID. The requested message must differ from the placeholder.')
            literal_hint = shown

    snapshot = _plain_clipboard_snapshot()
    clip = win32clipboard
    fmt = win32con.CF_UNICODETEXT
    text = expected.replace('\n', '\r\n')
    with _open_clipboard(clip):
        before = clip.GetClipboardSequenceNumber()
        clip.EmptyClipboard()
        clip.SetClipboardText(text, fmt)
        handle = clip.GetClipboardDataHandle(fmt)

    # Closing publishes the data (and on Windows 11 may re-allocate HGLOBAL
    # while synthesizing ANSI/locale formats). Verify the published text itself.
    with _open_clipboard(clip):
        if not clip.IsClipboardFormatAvailable(fmt) or clip.GetClipboardData(fmt) != text:
            raise _PasteError('clipboard_changed', 'Clipboard text was replaced before paste')
        after = clip.GetClipboardSequenceNumber()

    try:
        verify_target()
        current = _edit_text(edit)
        if not _empty_input(edit, current):
            if current != literal_hint:
                raise _PasteError('draft_changed', 'Input changed while preparing paste')
            # Select only the verified placeholder in this HWND. Never send Ctrl+A globally.
            win32gui.SendMessageTimeout(edit, win32con.EM_SETSEL, 0, -1, win32con.SMTO_ABORTIFHUNG, 500)
            if _edit_text(edit) != literal_hint:
                raise _PasteError('draft_changed', 'Input changed during placeholder selection')
        if clip.GetClipboardSequenceNumber() != after:
            raise _PasteError('clipboard_changed', 'Clipboard changed after text was published')
        _require_focus(hwnd, edit)
        _send_ctrl_key_combo(0x56)
        if not _wait_until(lambda: _matches_draft(edit, expected), 1.5):
            raise _PasteError('paste_text_mismatch', 'Pasted text was not observed in the verified input; Enter was not issued')
    finally:
        _restore_plain_clipboard(snapshot, before, after)
    # A placeholder which disappeared on click may be materialized again after
    # Enter. It is distinct evidence of clearing only for a different payload.
    return shown if shown in config.KAKAO_INPUT_PLACEHOLDERS and shown != expected else None


@operation()
def send_message_to_room(room_name: str, text: str, *, target=None, replace_legacy_hint=False) -> Dict:
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

    edit_hwnd = find_chat_input(hwnd)
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
            hint = _paste_into_focused_input(hwnd, edit_hwnd, room_name, expected, target, replace_legacy_hint)
        except Exception as error:
            return {"success": False, "status": "failed_before_send",
                    "code": getattr(error, "code", "input_not_ready"), "error": str(error)}
        return _submit_draft(room_name, hwnd, edit_hwnd, expected, target, literal_hint=hint)
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


def _submit_draft(room_name, hwnd, edit_hwnd, expected, target=None, *, literal_hint=None):
    if target:
        target.verify()
    if find_chat_input(hwnd) != edit_hwnd:
        return {"success": False, "status": "failed_before_send", "code": "room_changed", "error": "Message input changed"}
    button = _native_send_button(hwnd)
    if button:
        if find_chat_window(room_name) != hwnd or not _matches_draft(edit_hwnd, expected):
            return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Chat draft changed before send"}
        win32gui.SendMessageTimeout(button, win32con.BM_CLICK, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000)
        return _sent_when_cleared(edit_hwnd, room_name, literal_hint)

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
        return _sent_when_cleared(edit_hwnd, room_name, literal_hint)

    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit_hwnd)
    if not _matches_draft(edit_hwnd, expected):
        return {"success": False, "status": "failed_before_send", "code": "draft_changed", "error": "Chat draft changed before send"}
    if find_chat_window(room_name) != hwnd:
        return {"success": False, "error": "Target room changed before send"}
    _require_focus(hwnd, edit_hwnd)
    send_key = os.environ.get('KAKAO_LEGACY_SEND_KEY', 'enter').lower()
    if send_key == 'ctrl_enter':
        # With a nonstandard accelerator, invoke the accessible button if exposed,
        # falling back to Ctrl+Enter only while this exact edit has focus.
        try:
            bridge.request(dict(action='legacy_send_button', room=room_name, hwnd=hwnd, qt=False,
                                edit_hwnd=edit_hwnd, text=expected,
                                process_id=win32process.GetWindowThreadProcessId(hwnd)[1]), 5)
        except Exception:
            _require_focus(hwnd, edit_hwnd)
            _send_ctrl_key_combo(config.VK_RETURN)
    else:
        # Legacy RICHEDIT50W requires hardware keybd_event Enter (PostMessage WM_KEYDOWN
        # inserts a newline instead of triggering send) under verified focus.
        _press_key(config.VK_RETURN)
    return _sent_when_cleared(edit_hwnd, room_name, literal_hint)


def _sent_when_cleared(edit, room, literal_hint=None):
    def cleared():
        shown = _edit_text(edit)
        return shown == literal_hint or _empty_input(edit, shown)

    if _wait_until(cleared, 2):
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

class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', wintypes.DWORD),
        ('Data2', wintypes.WORD),
        ('Data3', wintypes.WORD),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, guid_str: str) -> "_GUID":
        u = uuid.UUID(guid_str)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
        g.Data4[:] = u.bytes[8:]
        return g


_CLSID_WIC_IMAGING_FACTORY = _GUID.from_str('{cacaf262-9370-4615-a13b-9f5539da4c0a}')
_IID_IWIC_IMAGING_FACTORY = _GUID.from_str('{ec5ec8a9-c395-4314-9c77-54d7a935ff70}')
_GUID_WIC_PIXEL_FORMAT_32BPP_BGRA = _GUID.from_str('{6fddc324-4e03-4bfe-b185-3d77768dc90f}')


def _com_call(ptr: ctypes.c_void_p, slot: int, restype, argtypes, *args):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[slot])
    return fn(ptr, *args)


def _check_hr(hr: int, step: str):
    if hr < 0:
        raise OSError(f"WIC {step} failed (HRESULT=0x{hr & 0xffffffff:08x})")


def _image_file_to_dib_wic(abs_path: str) -> bytes:
    """Decode an image file in-process via Windows WIC to bottom-up 32bpp CF_DIB bytes."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    coinit_multithreaded = 0x0
    rpc_e_changed_mode = -2147417850  # 0x80010106
    hr_co = ole32.CoInitializeEx(None, coinit_multithreaded)
    need_uninit = hr_co in (0, 1)
    if hr_co < 0 and hr_co != rpc_e_changed_mode:
        raise OSError(f"CoInitializeEx failed (HRESULT=0x{hr_co & 0xffffffff:08x})")

    factory = ctypes.c_void_p()
    decoder = ctypes.c_void_p()
    frame = ctypes.c_void_p()
    converter = ctypes.c_void_p()
    try:
        _check_hr(
            ole32.CoCreateInstance(
                ctypes.byref(_CLSID_WIC_IMAGING_FACTORY),
                None,
                1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(_IID_IWIC_IMAGING_FACTORY),
                ctypes.byref(factory),
            ),
            "CoCreateInstance",
        )
        # IWICImagingFactory::CreateDecoderFromFilename (slot 3)
        _check_hr(
            _com_call(
                factory, 3, ctypes.c_long,
                [wintypes.LPCWSTR, ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)],
                abs_path, None, 0x80000000, 0, ctypes.byref(decoder),
            ),
            "CreateDecoderFromFilename",
        )
        # IWICBitmapDecoder::GetFrame (slot 13)
        _check_hr(
            _com_call(
                decoder, 13, ctypes.c_long,
                [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)],
                0, ctypes.byref(frame),
            ),
            "GetFrame",
        )
        # IWICImagingFactory::CreateFormatConverter (slot 10)
        _check_hr(
            _com_call(
                factory, 10, ctypes.c_long,
                [ctypes.POINTER(ctypes.c_void_p)],
                ctypes.byref(converter),
            ),
            "CreateFormatConverter",
        )
        # IWICFormatConverter::Initialize (slot 8)
        _check_hr(
            _com_call(
                converter, 8, ctypes.c_long,
                [ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.c_double, wintypes.DWORD],
                frame, ctypes.byref(_GUID_WIC_PIXEL_FORMAT_32BPP_BGRA), 0, None, 0.0, 0,
            ),
            "FormatConverter.Initialize",
        )
        width = wintypes.UINT()
        height = wintypes.UINT()
        # IWICBitmapSource::GetSize (slot 3)
        _check_hr(
            _com_call(
                converter, 3, ctypes.c_long,
                [ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)],
                ctypes.byref(width), ctypes.byref(height),
            ),
            "GetSize",
        )
        w, h = width.value, height.value
        if w == 0 or h == 0:
            raise OSError("Image conversion produced zero-sized image")

        stride = w * 4
        image_size = stride * h
        pixel_buf = bytearray(image_size)
        c_buf = (ctypes.c_ubyte * image_size).from_buffer(pixel_buf)
        # IWICBitmapSource::CopyPixels (slot 7)
        _check_hr(
            _com_call(
                converter, 7, ctypes.c_long,
                [ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p],
                None, stride, image_size, c_buf,
            ),
            "CopyPixels",
        )

        # Standard bottom-up BITMAPINFOHEADER (biHeight > 0, BI_RGB = 0)
        header = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 32, 0, image_size, 0, 0, 0, 0)
        dib = bytearray(40 + image_size)
        dib[:40] = header
        src_view = memoryview(pixel_buf)
        dst_view = memoryview(dib)[40:]
        for row in range(h):
            src_off = (h - 1 - row) * stride
            dst_off = row * stride
            dst_view[dst_off:dst_off + stride] = src_view[src_off:src_off + stride]
        return bytes(dib)
    finally:
        for ptr in (converter, frame, decoder, factory):
            if ptr:
                try:
                    _com_call(ptr, 2, wintypes.ULONG, [])
                except Exception:
                    pass
        if need_uninit:
            ole32.CoUninitialize()


def _decode_process_stderr(raw: bytes) -> str:
    if not raw:
        return ""
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw).strip()
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        enc = locale.getpreferredencoding(False) or "utf-8"
        return raw.decode(enc, errors="replace").strip()


def _image_file_to_dib_powershell(abs_path: str) -> bytes:
    """Fallback image -> CF_DIB conversion via PowerShell (supports WebP via WinRT)."""
    ps_path = abs_path.replace("'", "''")
    ext = os.path.splitext(abs_path)[1].lower()
    if ext == ".webp":
        ps_script = (
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
            "[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Graphics.Imaging.BitmapEncoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null;"
            "function Await($op, $type) {"
            "  $m = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.IsGenericMethod })[0];"
            "  $m.MakeGenericMethod($type).Invoke($null, @($op)).GetAwaiter().GetResult()"
            "};"
            "function AwaitAction($op) {"
            "  $m = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and -not $_.IsGenericMethod })[0];"
            "  $m.Invoke($null, @($op)).GetAwaiter().GetResult()"
            "};"
            f"$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{ps_path}')) ([Windows.Storage.StorageFile]);"
            "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream]);"
            "$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder]);"
            "$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]);"
            "$conv = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bmp, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Straight);"
            "$out = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new();"
            "$enc = Await ([Windows.Graphics.Imaging.BitmapEncoder]::CreateAsync([Windows.Graphics.Imaging.BitmapEncoder]::BmpEncoderId, $out)) ([Windows.Graphics.Imaging.BitmapEncoder]);"
            "$enc.SetSoftwareBitmap($conv);"
            "AwaitAction ($enc.FlushAsync());"
            "$ms = [System.IO.MemoryStream]::new();"
            "$netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($out.GetInputStreamAt(0));"
            "$netStream.CopyTo($ms);"
            "$bytes = $ms.ToArray();"
            "$ms.Dispose(); $out.Dispose(); $conv.Dispose(); $bmp.Dispose(); $stream.Dispose();"
            "[Console]::OpenStandardOutput().Write($bytes, 0, $bytes.Length)"
        )
    else:
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
        capture_output=True, timeout=config.IMAGE_CONVERT_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        raise OSError(
            f"Failed to convert image to BMP: {_decode_process_stderr(result.stderr)}"
        )

    bmp_data = result.stdout
    if len(bmp_data) < 54:  # 14 (file header) + 40 (info header) minimum
        raise OSError("Image conversion produced invalid BMP data")

    # CF_DIB = BMP data minus the 14-byte BITMAPFILEHEADER
    return bmp_data[14:]


def _image_to_dib(abs_path: str) -> bytes:
    """Convert an image file to CF_DIB bytes via WIC, falling back to PowerShell."""
    try:
        return _image_file_to_dib_wic(abs_path)
    except Exception as wic_err:
        _log(f"WIC decode fallback to PowerShell for '{abs_path}': {wic_err}")
        return _image_file_to_dib_powershell(abs_path)


def _copy_image_to_clipboard(file_path: str, previous=None):
    """Copy an image file to clipboard as bitmap data (CF_DIB).

    Decodes the image in-process via Windows WIC (with PowerShell fallback),
    then sets CF_DIB directly from Python so that:
      1. The format matches what screenshot capture tools produce.
      2. The Python process owns the clipboard and stays alive,
         so no OleFlushClipboard() is needed.
      3. Windows auto-synthesizes CF_BITMAP and CF_DIBV5 from CF_DIB.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")

    dib_data = _image_to_dib(abs_path)

    if previous is not None:
        previous['snapshot'] = _plain_clipboard_snapshot()
    with _open_clipboard():
        if previous is not None:
            previous['before'] = win32clipboard.GetClipboardSequenceNumber()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib_data)
    # Closing the clipboard can synthesize formats and advance its sequence.
    with _open_clipboard():
        if win32clipboard.GetClipboardData(win32con.CF_DIB) != dib_data:
            raise RuntimeError("Clipboard image changed")
        return win32clipboard.GetClipboardSequenceNumber()


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

    edit_hwnd = find_chat_input(hwnd)
    if edit_hwnd is None:
        return {"success": False, "status": "failed_before_send", "code": "input_not_found", "error": f"Edit control not found in '{room_name}'"}

    if _is_real_draft(edit_hwnd):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    try:
        with temporary_image_clipboard(abs_path) as sequence:
            return _send_prepared_image(hwnd, edit_hwnd, sequence, room_name, abs_path)
    except Exception as error:
        return {"success": False, "error": str(error)}


def _find_owned_popup(owner_hwnd: int, pid: int) -> Optional[int]:
    """Find a unique visible enabled top-level popup owned by owner_hwnd."""
    matches = []

    def _cb(hwnd, _):
        try:
            if (hwnd != owner_hwnd and win32gui.IsWindowVisible(hwnd)
                    and win32gui.IsWindowEnabled(hwnd)
                    and win32gui.GetWindow(hwnd, win32con.GW_OWNER) == owner_hwnd
                    and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
                matches.append(hwnd)
        except _win32_errors():
            return True
        return True

    ctypes.windll.kernel32.SetLastError(0)
    win32gui.EnumWindows(_cb, None)
    return matches[0] if len(matches) == 1 else None


def _send_prepared_image(hwnd, edit_hwnd, sequence, room_name, abs_path):
    # Require a new preview. An existing modal must never receive paste or Enter.
    if not win32gui.IsWindowEnabled(hwnd) or find_chat_window(room_name) != hwnd:
        return {"success": False, "status": "failed_before_send", "error": "Room changed or has an open dialog"}
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit_hwnd)
    _require_focus(hwnd, edit_hwnd)
    if win32clipboard.GetClipboardSequenceNumber() != sequence or _is_real_draft(edit_hwnd):
        return {"success": False, "status": "failed_before_send", "error": "Clipboard or draft changed"}
    win32gui.PostMessage(edit_hwnd, win32con.WM_PASTE, 0, 0)
    try:
        result = bridge.request(dict(action='legacy_image_confirm', room=room_name, hwnd=hwnd,
                                     process_id=pid, qt=False), 10)
        return {"success": result['sent'], "confirmation": result['confirmation'],
                "message": f"Image sent to '{room_name}'"}
    except Exception as error:
        err_text = str(error)
        if 'Unique enabled send button not found' not in err_text and 'Cannot verify photo preview' not in err_text:
            raise
        popup = _find_owned_popup(hwnd, pid)
        if not popup:
            raise
        btn = _native_send_button(popup)
        if btn:
            win32gui.SendMessageTimeout(btn, win32con.BM_CLICK, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000)
        else:
            custom_btn = _native_send_button(popup, custom=True)
            if custom_btn:
                _, _, width, height = win32gui.GetClientRect(custom_btn)
                if not width or not height:
                    raise
                point = ((height // 2) << 16) | (width // 2)
                for msg, keys in [(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON), (win32con.WM_LBUTTONUP, 0)]:
                    win32gui.SendMessageTimeout(custom_btn, msg, keys, point, win32con.SMTO_ABORTIFHUNG, 1000)
            else:
                ocr_res = bridge.request(dict(action='legacy_photo_ocr_confirm', room=room_name, hwnd=hwnd,
                                              process_id=pid, qt=False), 10)
                return {"success": ocr_res['sent'], "confirmation": ocr_res['confirmation'],
                        "message": f"Image sent to '{room_name}'"}
        if not _wait_until(lambda: not win32gui.IsWindowVisible(popup) and win32gui.IsWindowEnabled(hwnd), 2.2):
            raise RuntimeError('Photo send unconfirmed') from error
        return {"success": True, "confirmation": "verified_photo_preview_closed",
                "message": f"Image sent to '{room_name}'"}


def send_images_to_room(room_name: str, image_paths: List[str]) -> Dict:
    from .delivery import deliver
    return deliver('image', [(room_name, path) for path in image_paths])


# ---------------------------------------------------------------------------
# Message reading
# ---------------------------------------------------------------------------

@operation()
def read_chat_messages(room_name: str, read_mode=None, max_messages=None, *, monitoring=False,
                       all_history=False, target=None, scroll_pages: int = 0) -> Dict:
    """Read the native list text when available, or copy the transcript.

    NOTE: This briefly brings the chat window to the foreground.

    Returns:
        Dict with success (bool), raw_text (str), and error if failed.
    """
    if read_mode == 'background' and scroll_pages > 0:
        return {"success": False, "error": "Cannot scroll chat history in background mode", "raw_text": ""}
    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
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
        except _win32_errors():
            raw = ''
        parsed = parse_chat_text(raw)
        if parsed['room_name'] and parsed['room_name'] != room_name:
            return {"success": False, "error": "Transcript room header changed"}
        if parsed['messages'] or (parsed['room_name'] == room_name and parsed['member_count'] is not None):
            parsed['read_method'] = 'win32_text'
            parsed['partial'] = True
            parsed['room_name'] = room_name
            if target:
                target.verify()
            if scroll_pages <= 0 and (
                    read_mode == 'background'
                    or (not all_history and (not max_messages or len(parsed['messages']) >= max_messages))):
                return {"success": True, "raw_text": raw, "parsed": parsed}
        if read_mode == 'background' and not monitoring and not target:
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                result = bridge.request(dict(action='legacy_ocr', room=room_name, hwnd=hwnd, process_id=pid,
                                             list_hwnd=list_hwnd, qt=False), 6)
                if find_chat_window(room_name) != hwnd:
                    raise RuntimeError("Room window changed during OCR")
                lines = result['lines']
                if not lines:
                    raise RuntimeError("No legible text in the visible message list")
                return {"success": True, "parsed": dict(room_name=room_name, member_count=None, messages=[],
                        ocr_lines=lines, partial=True, scope='visible_list', read_method='win32_ocr',
                        unknown_fields=['message_boundaries', 'sender', 'time', 'date'])}
            except Exception as error:
                return {"success": False, "error": f"No background transcript. {error}. Use complete mode."}
        if read_mode == 'background':
            return {"success": False, "error": "The legacy message list exposes no background transcript; use complete mode"}
        if monitoring:
            # Copying takes focus even if the user is typing in the same room.
            idle_ms = (win32api.GetTickCount() - win32api.GetLastInputInfo()) & 0xffffffff
            if idle_ms < 3000:
                return {"success": False, "deferred": True, "error": "Waiting for idle input before copying legacy history"}

    # Bring the chat window to foreground — required for keybd_event
    if _user32.GetForegroundWindow() != hwnd:
        bring_window_to_front(hwnd)
        _wait_until(lambda: _user32.GetForegroundWindow() == hwnd, config.WINDOW_ACTIVATE_WAIT_SEC)

    # Prefer transferring focus directly to the list.
    if target:
        target.verify()
        if find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS) != list_hwnd:
            raise RuntimeError("Target history list changed")
    _focus_control(hwnd, list_hwnd)

    if scroll_pages > 0:
        for _ in range(scroll_pages):
            _require_focus(hwnd, list_hwnd)
            _press_key(config.VK_PRIOR, shift=False)
            time.sleep(0.05)
        time.sleep(0.1)

    # Ctrl+A (select all)
    _require_focus(hwnd, list_hwnd)
    _send_ctrl_key_combo(config.VK_A)
    time.sleep(config.KEY_COMBO_WAIT_SEC)

    snapshot = _plain_clipboard_snapshot()
    _require_focus(hwnd, list_hwnd)
    before = win32clipboard.GetClipboardSequenceNumber()
    _send_ctrl_key_combo(config.VK_C)
    deadline = time.monotonic() + max(0.5, config.KEY_COMBO_WAIT_SEC * 3)
    after = win32clipboard.GetClipboardSequenceNumber()
    while after == before and time.monotonic() < deadline:
        time.sleep(config.CLIPBOARD_POLL_INTERVAL_SEC)
        after = win32clipboard.GetClipboardSequenceNumber()
    if before == after:
        return {"success": False, "error": "KakaoTalk did not publish a fresh transcript"}
    try:
        owner = win32clipboard.GetClipboardOwner()
    except _win32_errors():
        owner = 0
    if not owner or win32process.GetWindowThreadProcessId(owner)[1] != win32process.GetWindowThreadProcessId(hwnd)[1]:
        return {"success": False, "error": "KakaoTalk did not publish a fresh transcript"}
    raw_text = _read_clipboard_text()
    if win32clipboard.GetClipboardSequenceNumber() != after:
        return {"success": False, "error": "Clipboard changed during transcript copy"}
    _restore_plain_clipboard(snapshot, before, after)
    if target:
        target.verify()

    return {"success": True, "raw_text": raw_text}


# ---------------------------------------------------------------------------
# Room search/open
# ---------------------------------------------------------------------------

def _find_chat_list_view(main_hwnd: int) -> Optional[int]:
    """Find the ChatRoomListView window inside the main window."""
    chat_list_view = None

    def _find_view(hwnd, _):
        nonlocal chat_list_view
        try:
            cls = win32gui.GetClassName(hwnd)
            text = win32gui.GetWindowText(hwnd)
            if cls == "EVA_Window" and "ChatRoomListView" in text:
                chat_list_view = hwnd
                return False
        except _win32_errors():
            return True
        return True

    try:
        win32gui.EnumChildWindows(main_hwnd, _find_view, None)
    except _win32_errors() as err:
        if chat_list_view is None:
            winerror = getattr(err, 'winerror', None)
            if winerror not in (None, 0):
                _log(f"_find_chat_list_view failed: {err}")
    except Exception:
        if chat_list_view is None:
            raise
    return chat_list_view


def _find_search_strip(chat_list_view: int) -> Optional[int]:
    """Find the search strip (EVA_Window_Dblclk, ~390x40) inside ChatRoomListView.

    This strip contains the magnifying glass icon. Clicking it activates the search
    Edit control (which starts out hidden/invisible).
    """
    result = None

    def _cb(hwnd, _):
        nonlocal result
        try:
            if win32gui.GetParent(hwnd) != chat_list_view:
                return True
            cls = win32gui.GetClassName(hwnd)
            if cls == "EVA_Window_Dblclk":
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if w > 200 and 25 < h < 60:
                    result = hwnd
                    return False
        except _win32_errors():
            return True
        return True

    try:
        win32gui.EnumChildWindows(chat_list_view, _cb, None)
    except _win32_errors() as err:
        if result is None:
            winerror = getattr(err, 'winerror', None)
            if winerror not in (None, 0):
                _log(f"_find_search_strip failed: {err}")
    except Exception:
        if result is None:
            raise
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
            try:
                if (win32gui.GetClassName(child).lower() == 'edit' and win32gui.IsWindowVisible(child)
                        and win32gui.IsWindowEnabled(child)):
                    edits.append(child)
            except _win32_errors():
                return True
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
    if _user32.GetForegroundWindow() == hwnd:
        return True
    bring_window_to_front(hwnd)
    return bool(_wait_until(lambda: _user32.GetForegroundWindow() == hwnd, config.WINDOW_ACTIVATE_WAIT_SEC))


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

    existing = _edit_text(edit_hwnd)
    if existing and existing != room_name:
        return {"success": False, "code": "search_draft_exists", "error": "Chat search already contains a different query"}
    wrote_query = False
    try:
        if not existing:
            win32gui.SendMessageTimeout(edit_hwnd, win32con.EM_SETSEL, -1, -1, win32con.SMTO_ABORTIFHUNG, 1500)
            _replace_selection(edit_hwnd, room_name)
            wrote_query = True
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
    finally:
        if wrote_query:
            try:
                if win32gui.IsWindow(edit_hwnd) and _edit_text(edit_hwnd) == room_name:
                    win32gui.SendMessageTimeout(edit_hwnd, win32con.EM_SETSEL, 0, -1, win32con.SMTO_ABORTIFHUNG, 500)
                    _replace_selection(edit_hwnd, "")
            except Exception:
                pass


def _find_visible_chat_room_list(view: int) -> Optional[int]:
    """Find the visible ChatRoomListCtrl inside ChatRoomListView."""
    matches = []

    def visit(hwnd, _):
        try:
            if (win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd)
                    and win32gui.GetClassName(hwnd) == config.KAKAO_LIST_CONTROL_CLASS
                    and "ChatRoomListCtrl" in win32gui.GetWindowText(hwnd)):
                matches.append(hwnd)
        except _win32_errors():
            return True
        return True

    win32gui.EnumChildWindows(view, visit, None)
    return matches[0] if len(matches) == 1 else None


@operation()
def list_chats_legacy(max_chats: int = 50) -> Dict:
    """List visible chat rows from the legacy Chats tab via PrintWindow + Windows OCR."""
    main_hwnd = _main_window()
    if main_hwnd == 0:
        return {"success": False, "error": "KakaoTalk main window not found"}
    view = _find_chat_list_view(main_hwnd)
    if not view or not win32gui.IsWindowVisible(view):
        return {
            "success": False,
            "code": "chats_tab_not_visible",
            "error": "Chats tab is not currently visible in KakaoTalk main window",
        }
    list_hwnd = _find_visible_chat_room_list(view)
    if not list_hwnd:
        return {
            "success": False,
            "code": "chat_list_unavailable",
            "error": "Visible ChatRoomListCtrl not found (clear active search if open)",
        }
    pid = win32process.GetWindowThreadProcessId(main_hwnd)[1]
    data = bridge.request(dict(action='legacy_chat_list_ocr', hwnd=main_hwnd, list_hwnd=list_hwnd,
                               process_id=pid, qt=False), 8)
    all_chats = list(data.get("chats") or [])
    return {
        "chats": all_chats[:max_chats],
        "total_exposed": len(all_chats),
        "read_method": data.get("read_method", "win32_ocr"),
        "partial": True,
    }


def _find_visible_search_list(view: int) -> Optional[int]:
    """Accept only the unique visible results list inside the active Chats view."""
    matches = []

    def visit(hwnd, _):
        try:
            if (win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd)
                    and win32gui.GetClassName(hwnd) == config.KAKAO_LIST_CONTROL_CLASS
                    and "SearchListCtrl" in win32gui.GetWindowText(hwnd)):
                matches.append(hwnd)
        except _win32_errors():
            return True
        return True

    win32gui.EnumChildWindows(view, visit, None)
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Window lifecycle & diagnostics
# ---------------------------------------------------------------------------

@operation()
def close_chat_window(room_name: str, *, target=None) -> Dict:
    """Verify the exact chat window and post WM_CLOSE unless a modal dialog is open."""
    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}
    if find_chat_window(room_name) != hwnd:
        return {"success": False, "code": "room_changed", "error": f"Chat window '{room_name}' changed before close"}
    if not win32gui.IsWindowEnabled(hwnd):
        return {
            "success": False,
            "code": "room_busy",
            "error": f"Chat window '{room_name}' has an open dialog and cannot be closed",
        }
    win32gui.PostMessage(hwnd, config.WM_CLOSE, 0, 0)
    closed = bool(_wait_until(lambda: not win32gui.IsWindow(hwnd), config.WINDOW_CLOSE_WAIT_SEC))
    if not closed:
        return {
            "success": False,
            "closed": False,
            "hwnd": hwnd,
            "error": f"Chat window '{room_name}' did not close within timeout",
        }
    return {
        "success": True,
        "closed": True,
        "hwnd": hwnd,
        "message": f"Closed chat window '{room_name}'",
    }


def _window_node_info(hwnd: int) -> Optional[Dict]:
    try:
        rect = win32gui.GetWindowRect(hwnd)
        return {
            "hwnd": hwnd,
            "class": win32gui.GetClassName(hwnd),
            "title": win32gui.GetWindowText(hwnd),
            "ctrl_id": win32gui.GetDlgCtrlID(hwnd),
            "rect": list(rect) if rect is not None else None,
            "visible": bool(win32gui.IsWindowVisible(hwnd)),
            "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
            "children": [],
        }
    except _win32_errors():
        return None


def _build_win32_subtree(root_hwnd: int, depth_limit: int) -> Optional[Dict]:
    """Build a pruned Win32 control hierarchy dict for a single root HWND."""
    root_node = _window_node_info(root_hwnd)
    if root_node is None:
        return None

    child_order: List[int] = []
    parent_map: Dict[int, int] = {}

    def _cb(hwnd, _):
        try:
            if hwnd not in parent_map and hwnd != root_hwnd:
                parent_map[hwnd] = win32gui.GetParent(hwnd)
                child_order.append(hwnd)
        except _win32_errors():
            return True
        return True

    try:
        win32gui.EnumChildWindows(root_hwnd, _cb, None)
    except _win32_errors():
        pass

    nodes: Dict[int, Dict] = {root_hwnd: root_node}
    for hwnd in child_order:
        info = _window_node_info(hwnd)
        if info is not None:
            nodes[hwnd] = info

    for hwnd in child_order:
        node = nodes.get(hwnd)
        if node is None:
            continue
        parent = parent_map.get(hwnd)
        target_parent = nodes.get(parent) if parent != hwnd else None
        if target_parent is None:
            target_parent = root_node
        target_parent["children"].append(node)

    def _prune(node: Dict, depth: int, seen: set) -> int:
        seen.add(node["hwnd"])
        if depth >= depth_limit:
            node["children"] = []
            return 1
        kept = []
        total = 1
        for child in node["children"]:
            if child["hwnd"] in seen:
                continue
            total += _prune(child, depth + 1, seen)
            kept.append(child)
        node["children"] = kept
        return total

    node_count = _prune(root_node, 0, set())
    return {"node": root_node, "node_count": node_count}


def inspect_win32_tree(room_name: str = "", max_depth: int = 5) -> Dict:
    """Return a read-only Win32 child control tree dump for diagnostics."""
    if room_name:
        root_hwnd = find_chat_window(room_name)
        if root_hwnd is None:
            return {"success": False, "error": f"Chat window '{room_name}' not found"}
    else:
        root_hwnd = _main_window()
        if not root_hwnd:
            return {"success": False, "error": "KakaoTalk main window not found"}

    depth_limit = max(0, int(max_depth))
    built = _build_win32_subtree(root_hwnd, depth_limit)
    if built is None:
        return {"success": False, "error": f"Window {root_hwnd} is no longer valid"}

    popup_hwnds: List[int] = []

    def _popup_cb(hwnd, _):
        try:
            if hwnd != root_hwnd and win32gui.IsWindowVisible(hwnd):
                owner = hwnd
                for _level in range(5):
                    owner = win32gui.GetWindow(owner, win32con.GW_OWNER)
                    if not isinstance(owner, int) or owner == 0:
                        break
                    if owner == root_hwnd:
                        popup_hwnds.append(hwnd)
                        break
        except _win32_errors():
            return True
        return True

    try:
        ctypes.windll.kernel32.SetLastError(0)
        win32gui.EnumWindows(_popup_cb, None)
    except _win32_errors():
        pass

    owned_popups = []
    for phwnd in popup_hwnds:
        sub = _build_win32_subtree(phwnd, depth_limit)
        if sub is not None:
            owned_popups.append(sub["node"])

    return {
        "success": True,
        "room_name": room_name or None,
        "root_hwnd": root_hwnd,
        "max_depth": depth_limit,
        "node_count": built["node_count"],
        "tree": built["node"],
        **({"owned_popups": owned_popups} if owned_popups else {}),
    }


def list_participants_legacy(room_name: str) -> Dict:
    """Read-only helper using UIA or safe fallback when the legacy drawer isn't exposed."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {
            "success": False,
            "error": f"Chat window '{room_name}' not found",
            "participants": [],
        }

    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    try:
        result = bridge.request(
            dict(action="participants", room=room_name, hwnd=hwnd, process_id=pid, qt=False),
            6,
        )
        participants = list(result.get("participants") or [])
        return {
            "success": True,
            "room_name": room_name,
            "participants": participants,
            "count": len(participants),
            "source": "uia",
        }
    except Exception as error:
        member_count = None
        list_hwnd = find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS)
        if list_hwnd is not None:
            try:
                from .parser import parse_chat_text
                parsed = parse_chat_text(_edit_text(list_hwnd))
                if not parsed.get("room_name") or parsed["room_name"] == room_name:
                    member_count = parsed.get("member_count")
            except Exception:
                pass
        return {
            "success": False,
            "partial": True,
            "room_name": room_name,
            "participants": [],
            "member_count": member_count,
            "error": (
                "Participant list is not exposed by the legacy drawer via UIA; "
                f"open the room member drawer manually ({error})"
            ),
        }


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

def _type_mention_query(mention_name: str, hwnd: int, edit: int):
    """Type mention search text: 2벌식 IME keys for Hangul syllables, SendInput for all other characters."""
    for ch in mention_name:
        if 0xAC00 <= ord(ch) <= 0xD7A3:
            for vk, shift in _decompose_korean(ch):
                _require_focus(hwnd, edit)
                _press_key(vk, shift=shift)
        elif ch == ' ':
            _require_focus(hwnd, edit)
            _press_key(0x20, False)
        else:
            _require_focus(hwnd, edit)
            _send_unicode_char(ch)


@operation()
def send_mention_message(room_name: str, mention_name: str, message: str, *, target=None) -> Dict:
    """Select an exposed exact mention candidate before submitting the verified draft."""
    if target:
        target.verify()
    hwnd = target.hwnd if target else find_chat_window(room_name)
    edit = find_chat_input(hwnd) if hwnd else None
    if not edit:
        return {"success": False, "status": "failed_before_send", "error": "Target chat input not found"}
    if _is_real_draft(edit, _edit_text(edit)):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    bring_window_to_front(hwnd)
    _focus_control(hwnd, edit)
    _require_focus(hwnd, edit)
    shown = _edit_text(edit)
    if _is_real_draft(edit, shown):
        return {"success": False, "status": "failed_before_send", "code": "draft_exists", "error": "Chat has an existing draft"}
    if shown in config.KAKAO_INPUT_PLACEHOLDERS and not _empty_input(edit, shown):
        win32gui.SendMessageTimeout(edit, win32con.EM_SETSEL, 0, -1, win32con.SMTO_ABORTIFHUNG, 500)
    _press_key(0x32, shift=True)
    time.sleep(config.MENTION_AT_WAIT_SEC)
    _type_mention_query(mention_name, hwnd, edit)
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
    using suffix-prefix alignment (with date-loss tolerance and Counter fallback),
    and queues events when keywords are matched.
    """

    def __init__(self, on_notify=None):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._events: deque = deque(maxlen=config.MONITOR_MAX_EVENTS)
        self._events_lock = threading.Lock()
        self._events_ready = threading.Condition(self._events_lock)
        self._on_notify = on_notify
        self._dropped = 0
        self._polls = 0
        self._last_poll: Optional[str] = None
        self._last_error: Optional[str] = None
        self._seen_counts = Counter()
        self._seen_dateless_counts = Counter()
        self._prev_messages: List[Dict] = []
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
        self._poll_interval = max(config.MONITOR_MIN_POLL_INTERVAL_SEC, poll_interval)
        self._stop_event.clear()
        with self._events_lock:
            self._events.clear()
            self._dropped = 0
        self._polls = 0
        self._last_poll = None
        self._last_error = None
        self.baseline_ready = False
        self._seen_counts.clear()
        self._seen_dateless_counts.clear()
        self._prev_messages = []
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
        if self._on_notify:
            self._on_notify()
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
        if self._on_notify:
            self._on_notify()

    @staticmethod
    def _dateless_key(msg: Dict) -> tuple:
        return (
            msg.get('sender'),
            msg.get('time'),
            msg.get('text'),
            bool(msg.get('is_photo', False)),
            bool(msg.get('is_file', False)),
            bool(msg.get('is_video', False)),
        )

    def _message_key(self, msg: Dict) -> tuple:
        return (msg.get('date'),) + self._dateless_key(msg)

    @classmethod
    def _messages_match(cls, prev_msg: Dict, curr_msg: Dict) -> bool:
        if cls._dateless_key(prev_msg) != cls._dateless_key(curr_msg):
            return False
        d1, d2 = prev_msg.get('date'), curr_msg.get('date')
        return d1 == d2 or d1 is None or d2 is None

    def _diff_messages(self, all_messages: List[Dict]) -> List[Dict]:
        prev = self._prev_messages
        max_k = min(len(prev), len(all_messages))
        overlap_k = 0
        for k in range(max_k, 0, -1):
            if all(self._messages_match(prev[len(prev) - k + i], all_messages[i]) for i in range(k)):
                overlap_k = k
                break

        full_counts = Counter(self._message_key(msg) for msg in all_messages)
        dateless_counts = Counter(self._dateless_key(msg) for msg in all_messages)

        if overlap_k > 0:
            new_messages = list(all_messages[overlap_k:])
        else:
            new_messages = []
            seen_full_running: Counter = Counter()
            seen_dateless_running: Counter = Counter()
            for msg in all_messages:
                fkey = self._message_key(msg)
                dkey = self._dateless_key(msg)
                seen_full_running[fkey] += 1
                seen_dateless_running[dkey] += 1
                if msg.get('date') is None:
                    if seen_dateless_running[dkey] > self._seen_dateless_counts[dkey]:
                        new_messages.append(msg)
                else:
                    seen_exact = self._seen_counts[fkey]
                    seen_none = self._seen_counts[(None,) + dkey]
                    if seen_full_running[fkey] > seen_exact + seen_none:
                        new_messages.append(msg)

        self._prev_messages = list(all_messages)
        self._seen_counts |= full_counts
        self._seen_dateless_counts |= dateless_counts
        return new_messages

    def _load_initial_messages(self):
        from . import backend, parser

        result = backend.read_chat_messages(self._room_name, self._read_mode, monitoring=True)
        if result.get('deferred'):
            return
        if not result["success"]:
            raise RuntimeError(result["error"])
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        messages = parsed["messages"]
        self._prev_messages = list(messages)
        self._seen_counts.update(self._message_key(msg) for msg in messages)
        self._seen_dateless_counts.update(self._dateless_key(msg) for msg in messages)
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

        new_messages = self._diff_messages(all_messages)
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


class MonitorManager:
    """Manage concurrent per-room ChatMonitor instances with single-monitor facade compatibility."""

    def __init__(self):
        self._lock = threading.Lock()
        self._monitors: Dict[str, ChatMonitor] = {}
        self._notify_cond = threading.Condition()
        self._notify_seq = 0

    def _wake_waiters(self):
        with self._notify_cond:
            self._notify_seq += 1
            self._notify_cond.notify_all()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return any(m.is_running for m in self._monitors.values())

    @property
    def baseline_ready(self) -> bool:
        with self._lock:
            running = [m for m in self._monitors.values() if m.is_running]
            if not running:
                return False
            return all(m.baseline_ready for m in running)

    def start(self, room_name: str, keywords: List[str], poll_interval: float = 5.0, read_mode=None) -> Dict:
        """Start monitoring a specific chat room concurrently with other monitored rooms."""
        with self._lock:
            existing = self._monitors.get(room_name)
            if existing and existing.is_running:
                return {"success": False, "error": f"Monitor already running for '{room_name}'"}
            monitor = ChatMonitor(on_notify=self._wake_waiters)
            result = monitor.start(room_name, keywords, poll_interval, read_mode)
            if result.get("success"):
                self._monitors[room_name] = monitor
            return result

    def stop(self, room_name: Optional[str] = None) -> Dict:
        """Stop monitoring a specific room, or all running rooms when room_name is None."""
        with self._lock:
            if room_name is not None:
                monitor = self._monitors.get(room_name)
                if not monitor or not monitor.is_running:
                    return {"success": False, "error": f"Monitor not running for '{room_name}'"}
                targets = [(room_name, monitor)]
            else:
                targets = [(r, m) for r, m in self._monitors.items() if m.is_running]
                if not targets:
                    return {"success": False, "error": "Monitor not running"}

        if len(targets) == 1:
            result = targets[0][1].stop()
            self._wake_waiters()
            return result

        stopped = []
        errors = []
        for room, monitor in targets:
            res = monitor.stop()
            if res.get("success"):
                stopped.append(room)
            else:
                errors.append(f"{room}: {res.get('error', 'stop failed')}")
        self._wake_waiters()
        if errors:
            return {"success": False, "error": "; ".join(errors), "stopped_rooms": stopped}
        return {
            "success": True,
            "message": f"Stopped {len(stopped)} monitor(s): {', '.join(stopped)}",
            "stopped_rooms": stopped,
        }

    def _drain_all_events(self) -> List[Dict]:
        with self._lock:
            monitors = list(self._monitors.values())
        events: List[Dict] = []
        for monitor in monitors:
            events.extend(monitor.get_events(0))
        return events

    def get_events(self, wait_sec: float = 0, room_name: Optional[str] = None) -> List[Dict]:
        """Return pending events for one room or across all monitored rooms."""
        if room_name is not None:
            with self._lock:
                monitor = self._monitors.get(room_name)
            return monitor.get_events(wait_sec) if monitor else []

        wait = min(max(0.0, float(wait_sec or 0)), config.MONITOR_MAX_WAIT_SEC)
        deadline = time.monotonic() + wait
        while True:
            with self._notify_cond:
                seq = self._notify_seq
            events = self._drain_all_events()
            if events or wait <= 0 or not self.is_running:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            with self._notify_cond:
                if self._notify_seq == seq:
                    self._notify_cond.wait(min(remaining, 1.0))

    def status(self, room_name: Optional[str] = None) -> Dict:
        """Return monitor status for one room or a combined summary across all rooms."""
        with self._lock:
            monitors = list(self._monitors.items())
            if room_name is not None:
                target = self._monitors.get(room_name)
                if target is not None:
                    return target.status()
                idle = ChatMonitor().status()
                idle["room_name"] = room_name
                return idle

        if not monitors:
            base = ChatMonitor().status()
            return {**base, "rooms": {}, "active_rooms": []}

        room_statuses = {room: monitor.status() for room, monitor in monitors}
        active_rooms = [room for room, st in room_statuses.items() if st["monitoring"]]
        if len(monitors) == 1:
            base = dict(next(iter(room_statuses.values())))
        elif len(active_rooms) == 1:
            base = dict(room_statuses[active_rooms[0]])
        else:
            last_polls = [st["last_poll"] for st in room_statuses.values() if st["last_poll"]]
            last_errors = [st["last_error"] for st in room_statuses.values() if st["last_error"]]
            base = {
                "monitoring": bool(active_rooms),
                "room_name": None,
                "keywords": [],
                "poll_interval_sec": min((st["poll_interval_sec"] for st in room_statuses.values()), default=5.0),
                "read_mode": None,
                "baseline_ready": bool(active_rooms) and all(room_statuses[r]["baseline_ready"] for r in active_rooms),
                "polls": sum(st["polls"] for st in room_statuses.values()),
                "last_poll": max(last_polls) if last_polls else None,
                "last_error": last_errors[-1] if last_errors else None,
                "dropped_events": sum(st["dropped_events"] for st in room_statuses.values()),
            }
        return {**base, "rooms": room_statuses, "active_rooms": active_rooms}


# Module-level singleton
_chat_monitor = MonitorManager()
