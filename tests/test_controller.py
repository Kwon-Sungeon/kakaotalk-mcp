"""Mock-based unit tests for kakao_mcp.controller."""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kakao_mcp import controller, config


# ---------------------------------------------------------------------------
# is_kakaotalk_running
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32process")
@patch("kakao_mcp.controller.win32gui")
def test_is_running_true(mock_gui, mock_proc):
    mock_gui.FindWindow.return_value = 12345
    mock_proc.GetWindowThreadProcessId.return_value = (0, 9876)
    result = controller.is_kakaotalk_running()
    assert result["running"] is True
    assert result["hwnd"] == 12345
    assert result["pid"] == 9876
    mock_gui.FindWindow.assert_called_once_with(
        config.KAKAO_MAIN_WINDOW_CLASS, config.KAKAO_MAIN_WINDOW_TITLE
    )


@patch("kakao_mcp.controller.win32gui")
def test_is_running_false(mock_gui):
    mock_gui.FindWindow.return_value = 0
    result = controller.is_kakaotalk_running()
    assert result["running"] is False
    assert result["hwnd"] is None


# ---------------------------------------------------------------------------
# find_chat_window
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32gui")
def test_find_chat_window_found(mock_gui):
    # Simulate EnumWindows calling the callback with a matching window
    def enum_side_effect(callback, _):
        # Simulate a matching window
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.GetClassName.return_value = config.KAKAO_CHAT_WINDOW_CLASS
        mock_gui.GetWindowText.return_value = "TestRoom"
        callback(99999, None)

    mock_gui.EnumWindows.side_effect = enum_side_effect
    with patch.object(controller, "_is_chat_window", return_value=True):
        result = controller.find_chat_window("TestRoom")
    assert result == 99999


@patch("kakao_mcp.controller.win32gui")
def test_find_chat_window_not_found(mock_gui):
    def enum_side_effect(callback, _):
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.GetClassName.return_value = config.KAKAO_CHAT_WINDOW_CLASS
        mock_gui.GetWindowText.return_value = "OtherRoom"
        callback(99999, None)

    mock_gui.EnumWindows.side_effect = enum_side_effect
    result = controller.find_chat_window("TestRoom")
    assert result is None


# ---------------------------------------------------------------------------
# list_chat_windows
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32gui")
def test_list_chat_windows(mock_gui):
    mock_gui.FindWindow.return_value = 10000  # main window

    call_count = [0]
    rooms_data = [
        (10000, config.KAKAO_MAIN_WINDOW_TITLE),  # main window — should be excluded
        (20001, "Room A"),
        (20002, "Room B"),
    ]

    def enum_side_effect(callback, _):
        for hwnd, title in rooms_data:
            mock_gui.IsWindowVisible.return_value = True
            mock_gui.GetClassName.return_value = config.KAKAO_CHAT_WINDOW_CLASS
            mock_gui.GetWindowText.return_value = title
            callback(hwnd, None)

    mock_gui.EnumWindows.side_effect = enum_side_effect
    with patch.object(controller, "_is_chat_window", return_value=True):
        result = controller.list_chat_windows()
    titles = [r["title"] for r in result]
    assert "Room A" in titles
    assert "Room B" in titles
    assert config.KAKAO_MAIN_WINDOW_TITLE not in titles


# ---------------------------------------------------------------------------
# send_message_to_room
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32api")
@patch("kakao_mcp.controller.find_chat_input")
@patch("kakao_mcp.controller.find_chat_window")
def test_send_message_success(mock_find, mock_child, mock_api):
    mock_find.return_value = 11111
    mock_child.return_value = 22222
    # These are mocks supplied by the autouse fixture, never native Win32 IO.
    from unittest.mock import Mock
    assert isinstance(controller._user32, Mock)
    assert isinstance(controller.win32clipboard, Mock)
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(controller, '_native_send_button', return_value=33))
        stack.enter_context(patch.object(controller, '_edit_text', side_effect=['', 'Hello', 'Hello', '']))
        replace = stack.enter_context(patch.object(controller, '_replace_selection'))
        stack.enter_context(patch.object(controller, '_focus_control'))
        stack.enter_context(patch.object(controller, '_require_focus'))
        result = controller.send_message_to_room("TestRoom", "Hello")
    assert result["success"] is True
    assert "sent" in result["message"].lower()
    controller.win32clipboard.OpenClipboard.assert_not_called()
    replace.assert_called_once_with(22222, "Hello")


@patch("kakao_mcp.controller.find_chat_window")
def test_send_message_room_not_found(mock_find):
    mock_find.return_value = None
    result = controller.send_message_to_room("NoRoom", "Hello")
    assert result["success"] is False
    assert "not found" in result["error"]


@patch("kakao_mcp.controller.find_chat_input")
@patch("kakao_mcp.controller.find_chat_window")
def test_send_message_no_edit_control(mock_find, mock_child):
    mock_find.return_value = 11111
    mock_child.return_value = None
    result = controller.send_message_to_room("TestRoom", "Hello")
    assert result["success"] is False
    assert "edit control" in result["error"].lower()


# ---------------------------------------------------------------------------
# read_chat_messages
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller._read_clipboard_text")
@patch("kakao_mcp.controller._send_ctrl_key_combo")
@patch("kakao_mcp.controller._user32")
@patch("kakao_mcp.controller.win32gui")
@patch("kakao_mcp.controller.find_child_window_recursive")
@patch("kakao_mcp.controller.find_chat_window")
def test_read_messages_success(mock_find, mock_child, mock_gui, mock_user32, mock_ctrl, mock_clip):
    mock_find.return_value = 11111
    mock_child.return_value = 33333
    mock_gui.GetWindowRect.return_value = (0, 0, 100, 100)
    controller.win32clipboard.CountClipboardFormats.return_value = 0
    controller.win32clipboard.GetClipboardSequenceNumber.side_effect = [1, 1, 2, 2, 2]
    mock_clip.return_value = "[Room] [대화상대 2명]\n[A] [오전 10:00] Hello"
    with patch.object(controller, "_focus_control"), patch.object(controller, "_require_focus"):
        result = controller.read_chat_messages("Room", "complete")
    assert result["success"] is True
    assert "Hello" in result["raw_text"]


@patch("kakao_mcp.controller.find_chat_window")
def test_read_messages_room_not_found(mock_find):
    mock_find.return_value = None
    result = controller.read_chat_messages("NoRoom")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# get_kakao_user_hash_dir
# ---------------------------------------------------------------------------

@patch("os.listdir")
@patch("os.path.isdir")
def test_get_user_hash_dir_found(mock_isdir, mock_listdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["a" * 40, "not_a_hash"]

    # Make only the 40-char entry look like a directory
    def isdir_side(path):
        return True

    mock_isdir.side_effect = isdir_side
    result = controller.get_kakao_user_hash_dir()
    assert result is not None
    assert "a" * 40 in result


@patch("os.path.isdir")
def test_get_user_hash_dir_no_users_dir(mock_isdir):
    mock_isdir.return_value = False
    result = controller.get_kakao_user_hash_dir()
    assert result is None


# ---------------------------------------------------------------------------
# _copy_image_to_clipboard
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32clipboard")
@patch("kakao_mcp.controller.subprocess")
@patch("os.path.isfile", return_value=True)
@patch("os.path.abspath", return_value="C:\\test\\image.jpg")
def test_copy_image_to_clipboard_success(mock_abs, mock_isfile, mock_subprocess,
                                          mock_clipboard):
    # Fake BMP data: 14-byte file header + 40-byte info header + pixel data
    fake_bmp = b"BM" + b"\x00" * 12 + b"\x00" * 40 + b"\xff" * 100
    mock_clipboard.GetClipboardData.return_value = fake_bmp[14:]
    mock_result = mock_subprocess.run.return_value
    mock_result.returncode = 0
    mock_result.stdout = fake_bmp
    controller._copy_image_to_clipboard("C:\\test\\image.jpg")
    mock_subprocess.run.assert_called_once()
    call_args = mock_subprocess.run.call_args[0][0]
    assert call_args[0] == "powershell"
    assert mock_clipboard.OpenClipboard.call_count == 2
    assert mock_clipboard.CloseClipboard.call_count == 2
    mock_clipboard.EmptyClipboard.assert_called_once()
    mock_clipboard.SetClipboardData.assert_called_once()


@patch("os.path.isfile", return_value=False)
@patch("os.path.abspath", return_value="C:\\nonexistent.jpg")
def test_copy_image_to_clipboard_not_found(mock_abs, mock_isfile):
    with pytest.raises(FileNotFoundError):
        controller._copy_image_to_clipboard("C:\\nonexistent.jpg")


# ---------------------------------------------------------------------------
# send_image_to_room
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller._copy_image_to_clipboard")
@patch("kakao_mcp.controller.win32gui")
@patch("kakao_mcp.controller._user32")
@patch("kakao_mcp.controller._send_ctrl_key_combo")
@patch("kakao_mcp.controller.bring_window_to_front")
@patch("kakao_mcp.controller.find_chat_input")
@patch("kakao_mcp.controller.find_chat_window")
@patch("os.path.splitext", return_value=("C:\\test\\image", ".jpg"))
@patch("os.path.isfile", return_value=True)
@patch("os.path.abspath", return_value="C:\\test\\image.jpg")
def test_send_image_success(mock_abs, mock_isfile, mock_split, mock_find,
                            mock_find_child, mock_bring, mock_ctrl,
                            mock_user32, mock_win32gui, mock_copy):
    mock_find.return_value = 11111
    mock_find_child.return_value = 22222
    mock_win32gui.GetWindowRect.return_value = (100, 100, 200, 120)
    # Simulate dialog appearing (foreground changes to a different window)
    mock_user32.GetForegroundWindow.side_effect = [11111, 33333, 33333]
    mock_win32gui.GetWindow.return_value = 11111
    controller.win32clipboard.GetClipboardSequenceNumber.return_value = mock_copy.return_value
    with patch.object(controller, "_focus_control"), patch.object(controller, "_require_focus"), \
            patch.object(controller, "_edit_text", return_value=""), \
            patch.object(controller.bridge, "request", return_value={"sent": True, "confirmation": "verified_photo_preview_closed"}) as confirm:
        result = controller.send_image_to_room("TestRoom", "C:\\test\\image.jpg")
    assert result["success"] is True
    assert "sent" in result["message"].lower()
    assert confirm.call_args.args[0]['action'] == 'legacy_image_confirm'
    mock_user32.keybd_event.assert_not_called()
    mock_copy.assert_called_once()
    mock_bring.assert_called_once()


@patch("os.path.isfile", return_value=False)
@patch("os.path.abspath", return_value="C:\\nonexistent.jpg")
def test_send_image_file_not_found(mock_abs, mock_isfile):
    result = controller.send_image_to_room("TestRoom", "C:\\nonexistent.jpg")
    assert result["success"] is False
    assert "not found" in result["error"]


@patch("os.path.splitext", return_value=("C:\\test\\doc", ".pdf"))
@patch("os.path.isfile", return_value=True)
@patch("os.path.abspath", return_value="C:\\test\\doc.pdf")
def test_send_image_unsupported_format(mock_abs, mock_isfile, mock_split):
    result = controller.send_image_to_room("TestRoom", "C:\\test\\doc.pdf")
    assert result["success"] is False
    assert "unsupported" in result["error"].lower()


@patch("os.path.splitext", return_value=("C:\\test\\image", ".jpg"))
@patch("os.path.isfile", return_value=True)
@patch("os.path.abspath", return_value="C:\\test\\image.jpg")
@patch("kakao_mcp.controller.find_chat_window", return_value=None)
def test_send_image_room_not_found(mock_find, mock_abs, mock_isfile, mock_split):
    result = controller.send_image_to_room("NoRoom", "C:\\test\\image.jpg")
    assert result["success"] is False
    assert "not found" in result["error"]

def test_wrong_foreground_cannot_paste_or_send():
    controller.win32gui.GetWindowRect.return_value = (0, 0, 50, 50)
    controller._user32.GetForegroundWindow.return_value = 33333
    with patch.object(controller, 'find_chat_window', return_value=11111), \
            patch.object(controller, 'find_chat_input', return_value=22222), \
            patch.object(controller, '_edit_text', side_effect=['', 'sample']), \
            patch.object(controller, 'bring_window_to_front'):
        result = controller.send_message_to_room('TestRoom', 'sample')
        assert result['status'] == 'failed_before_send' and 'inactive' in result['error']
    controller.win32clipboard.OpenClipboard.assert_not_called()
    controller._user32.keybd_event.assert_not_called()
    controller._user32.mouse_event.assert_not_called()

def test_native_send_button_does_not_activate_window():
    with patch.object(controller, 'find_chat_window', return_value=11), \
            patch.object(controller, 'find_chat_input', return_value=22), \
            patch.object(controller, '_replace_selection'), \
            patch.object(controller, '_native_send_button', return_value=33), \
            patch.object(controller, '_edit_text', side_effect=['', 'sample', 'sample', '']), \
            patch.object(controller, 'bring_window_to_front') as activate:
        assert controller.send_message_to_room('fixture', 'sample')['success']
    activate.assert_not_called()
    controller.win32gui.SendMessageTimeout.assert_called_once_with(
        33, controller.win32con.BM_CLICK, 0, 0, controller.win32con.SMTO_ABORTIFHUNG, 1000)
    controller._user32.keybd_event.assert_not_called()


# ---------------------------------------------------------------------------
# EnumWindows / EnumChildWindows transient error safety
# ---------------------------------------------------------------------------

def test_enum_windows_ignores_transient_win32_errors(monkeypatch):
    import pywintypes

    gui = controller.win32gui
    gui.FindWindow.return_value = 10000
    controller.win32process.GetWindowThreadProcessId.return_value = (0, 777)

    def enum_side_effect(callback, _):
        for hwnd in (20001, 20002, 20003):
            callback(hwnd, None)

    def is_visible(hwnd):
        if hwnd == 20001:
            raise pywintypes.error(1400, "IsWindowVisible", "Invalid window handle")
        return True

    def get_class(hwnd):
        if hwnd == 20002:
            raise pywintypes.error(1400, "GetClassName", "Invalid window handle")
        return config.KAKAO_CHAT_WINDOW_CLASS

    gui.EnumWindows.side_effect = enum_side_effect
    gui.IsWindowVisible.side_effect = is_visible
    gui.GetClassName.side_effect = get_class
    gui.GetWindowText.return_value = "TargetRoom"

    with patch.object(controller, "_is_chat_window", return_value=True):
        assert controller.find_chat_window("TargetRoom") == 20003
        rooms = controller.list_chat_windows()
    assert rooms == [{"hwnd": 20003, "title": "TargetRoom"}]


def test_find_child_window_recursive_distinguishes_early_stop_and_errors():
    import pywintypes

    gui = controller.win32gui

    # Transient error on child 1, match + intentional early stop on child 2
    def enum_early_stop(parent, cb, _):
        assert cb(11, None) is True
        assert cb(12, None) is False
        raise pywintypes.error(0, "EnumChildWindows", "No error message is available")

    def get_class(hwnd):
        if hwnd == 11:
            raise pywintypes.error(1400, "GetClassName", "Invalid window handle")
        return "RICHEDIT50W"

    gui.EnumChildWindows.side_effect = enum_early_stop
    gui.GetClassName.side_effect = get_class
    assert controller.find_child_window_recursive(10, "RICHEDIT50W") == 12

    # Non-zero Win32 error when nothing was found returns None
    gui.EnumChildWindows.side_effect = pywintypes.error(1400, "EnumChildWindows", "Invalid handle")
    assert controller.find_child_window_recursive(10, "RICHEDIT50W") is None

    # Unexpected non-Win32 error propagates when nothing was found
    gui.EnumChildWindows.side_effect = RuntimeError("unexpected failure")
    with pytest.raises(RuntimeError, match="unexpected failure"):
        controller.find_child_window_recursive(10, "RICHEDIT50W")


# ---------------------------------------------------------------------------
# Mention non-Hangul & mixed typing
# ---------------------------------------------------------------------------

def test_send_unicode_char_uses_sendinput_utf16():
    import ctypes

    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(controller._INPUT) == expected_size

    controller._user32.SendInput.return_value = 2
    controller._send_unicode_char("A")
    controller._user32.SendInput.assert_called_once()
    count, arr, struct_size = controller._user32.SendInput.call_args.args
    assert count == 2
    assert struct_size == expected_size
    assert arr[0].type == 1
    assert arr[0].ki.wVk == 0
    assert arr[0].ki.wScan == ord("A")
    assert arr[0].ki.dwFlags == config.KEYEVENTF_UNICODE
    assert arr[1].ki.dwFlags == (config.KEYEVENTF_UNICODE | config.KEYEVENTF_KEYUP)

    # Non-BMP emoji produces surrogate pair (4 INPUT events)
    controller._user32.SendInput.reset_mock()
    controller._user32.SendInput.return_value = 4
    controller._send_unicode_char("😀")
    count2, arr2, _ = controller._user32.SendInput.call_args.args
    assert count2 == 4
    assert (arr2[0].ki.wScan, arr2[2].ki.wScan) == (0xD83D, 0xDE00)


def test_type_mention_query_mixed_korean_english_digits_symbols(monkeypatch):
    events = []
    monkeypatch.setattr(controller, "_require_focus", lambda h, e: events.append(("focus", h, e)))
    monkeypatch.setattr(controller, "_press_key", lambda vk, shift=False: events.append(("key", vk, shift)))
    monkeypatch.setattr(controller, "_send_unicode_char", lambda ch: events.append(("unicode", ch)))

    # Backward compatibility of _decompose_korean on Hangul syllables
    assert controller._decompose_korean("김") == [(0x52, False), (0x4C, False), (0x41, False)]

    controller._type_mention_query("김철수2_a", 100, 200)
    keys = [item for item in events if item[0] == "key"]
    unicodes = [item[1] for item in events if item[0] == "unicode"]
    assert len(keys) == len(controller._decompose_korean("김철수"))
    assert unicodes == ["2", "_", "a"]
    assert events[0] == ("focus", 100, 200)

    # Space uses VK_SPACE via _press_key
    events.clear()
    controller._type_mention_query("Alice B", 100, 200)
    assert ("key", 0x20, False) in events
    assert [item[1] for item in events if item[0] == "unicode"] == ["A", "l", "i", "c", "e", "B"]


def test_send_mention_message_mixed_name(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    monkeypatch.setattr(controller, "find_chat_input", lambda _: 222)
    monkeypatch.setattr(controller, "bring_window_to_front", MagicMock())
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_replace_selection", MagicMock())
    monkeypatch.setattr(controller, "_submit_draft", lambda *a, **kw: {"success": True})
    monkeypatch.setattr(controller.time, "sleep", MagicMock())
    monkeypatch.setattr(controller.bridge, "request", MagicMock(return_value={"selected": True}))
    monkeypatch.setattr(controller, "_edit_text", MagicMock(side_effect=["", "", "@김철수2 "]))
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check())

    pressed = []
    sent_unicode = []
    monkeypatch.setattr(controller, "_press_key", lambda vk, shift=False: pressed.append((vk, shift)))
    monkeypatch.setattr(controller, "_send_unicode_char", lambda ch: sent_unicode.append(ch))

    res = controller.send_mention_message("Room", "김철수2", "안녕")
    assert res["success"] is True
    assert pressed[0] == (0x32, True)  # '@'
    assert sent_unicode == ["2"]


# ---------------------------------------------------------------------------
# WIC image conversion & WebP fallback
# ---------------------------------------------------------------------------

def _make_synthetic_png_2x2() -> bytes:
    import struct, zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff)

    # Row 0: Red (255,0,0,255), Green (0,255,0,255)
    # Row 1: Blue (0,0,255,255), White (255,255,255,255)
    raw = (
        b"\x00\xff\x00\x00\xff\x00\xff\x00\xff"
        b"\x00\x00\x00\xff\xff\xff\xff\xff\xff"
    )
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _make_synthetic_bmp_2x2() -> bytes:
    import struct

    # Bottom-up 24bpp BGR 2x2 BMP (each row padded to 4-byte boundary: 2*3=6 -> 8 bytes)
    # Row 0 (bottom): Blue (255,0,0), White (255,255,255)
    # Row 1 (top):    Red (0,0,255),  Green (0,255,0)
    pixels = (
        b"\xff\x00\x00\xff\xff\xff\x00\x00"
        b"\x00\x00\xff\x00\xff\x00\x00\x00"
    )
    info = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    file_hdr = struct.pack("<2sIHHI", b"BM", 14 + len(info) + len(pixels), 0, 0, 14 + len(info))
    return file_hdr + info + pixels


def test_wic_image_file_to_dib_synthetic_png_and_bmp(tmp_path):
    import struct

    png_path = tmp_path / "sample.png"
    png_path.write_bytes(_make_synthetic_png_2x2())

    dib_png = controller._image_file_to_dib_wic(str(png_path))
    assert len(dib_png) == 40 + 2 * 2 * 4
    hdr = struct.unpack("<IiiHHIIiiII", dib_png[:40])
    assert hdr[:7] == (40, 2, 2, 1, 32, 0, 16)
    # Bottom-up BGRA: bottom row first (Blue, White), then top row (Red, Green)
    expected_pixels = bytes([
        255, 0, 0, 255, 255, 255, 255, 255,
        0, 0, 255, 255, 0, 255, 0, 255,
    ])
    assert dib_png[40:] == expected_pixels

    bmp_path = tmp_path / "sample.bmp"
    bmp_path.write_bytes(_make_synthetic_bmp_2x2())
    dib_bmp = controller._image_file_to_dib_wic(str(bmp_path))
    assert len(dib_bmp) == 40 + 16
    assert dib_bmp[40:] == expected_pixels


def test_copy_image_to_clipboard_uses_wic_without_powershell(tmp_path, monkeypatch):
    png_path = tmp_path / "fast.png"
    png_path.write_bytes(_make_synthetic_png_2x2())

    mock_run = MagicMock(side_effect=AssertionError("PowerShell should not be spawned when WIC succeeds"))
    monkeypatch.setattr(controller.subprocess, "run", mock_run)

    captured = {}

    def set_data(fmt, data):
        captured["fmt"] = fmt
        captured["data"] = data

    controller.win32clipboard.SetClipboardData.side_effect = set_data
    controller.win32clipboard.GetClipboardData.side_effect = lambda _: captured["data"]
    controller.win32clipboard.GetClipboardSequenceNumber.return_value = 99

    seq = controller._copy_image_to_clipboard(str(png_path))
    assert seq == 99
    assert len(captured["data"]) == 56
    mock_run.assert_not_called()


def test_image_to_dib_webp_fallback_and_safe_stderr_decode(tmp_path, monkeypatch):
    webp_path = tmp_path / "sticker.webp"
    webp_path.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")

    monkeypatch.setattr(controller, "_image_file_to_dib_wic", MagicMock(side_effect=OSError("no codec")))
    fake_bmp = b"BM" + b"\x00" * 12 + b"\x00" * 40 + b"\x01" * 16
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=fake_bmp, stderr=b""))
    monkeypatch.setattr(controller.subprocess, "run", mock_run)

    dib = controller._image_to_dib(str(webp_path))
    assert dib == fake_bmp[14:]
    ps_cmd = mock_run.call_args[0][0][3]
    assert "Windows.Graphics.Imaging.BitmapDecoder" in ps_cmd

    # Non-UTF8 invalid bytes in stderr must never raise UnicodeDecodeError
    mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"\xff\xfe\x80\x81failed")
    with pytest.raises(OSError, match="Failed to convert image to BMP"):
        controller._image_to_dib(str(webp_path))


# ---------------------------------------------------------------------------
# Faster clipboard polling, scroll_pages, close_chat_window, inspect_win32_tree,
# and list_participants_legacy
# ---------------------------------------------------------------------------

def test_read_messages_clipboard_polling_and_scroll_pages(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 11111)
    monkeypatch.setattr(controller, "find_child_window_recursive", lambda *_: 33333)
    monkeypatch.setattr(controller, "bring_window_to_front", MagicMock())
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_send_ctrl_key_combo", MagicMock())
    monkeypatch.setattr(controller, "_read_clipboard_text", lambda: "[Room] [대화상대 2명]\n[A] [오전 10:00] Old")
    monkeypatch.setattr(controller.time, "sleep", MagicMock())
    controller.win32clipboard.CountClipboardFormats.return_value = 0
    # Simulates 2 polling misses before KakaoTalk publishes clipboard sequence 2
    controller.win32clipboard.GetClipboardSequenceNumber.side_effect = [1, 1, 1, 1, 2, 2, 2]

    pressed = []
    monkeypatch.setattr(controller, "_press_key", lambda vk, shift=False: pressed.append((vk, shift)))

    res = controller.read_chat_messages("Room", "complete", scroll_pages=3)
    assert res["success"] is True
    assert "Old" in res["raw_text"]
    assert pressed == [(config.VK_PRIOR, False)] * 3

    # Background mode rejects scroll_pages > 0
    bg_res = controller.read_chat_messages("Room", "background", scroll_pages=2)
    assert bg_res["success"] is False
    assert "background" in bg_res["error"].lower()


def test_close_chat_window_safety_and_states(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda name: 111 if name == "Room" else None)
    controller.win32gui.IsWindowEnabled.return_value = False
    busy = controller.close_chat_window("Room")
    assert busy["success"] is False and busy["code"] == "room_busy"
    controller.win32gui.PostMessage.assert_not_called()

    controller.win32gui.IsWindowEnabled.return_value = True
    controller.win32gui.IsWindow.side_effect = [True, False]
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check() or check())
    ok = controller.close_chat_window("Room")
    assert ok["success"] is True and ok["closed"] is True
    controller.win32gui.PostMessage.assert_called_once_with(111, config.WM_CLOSE, 0, 0)

    # Not found
    assert controller.close_chat_window("Missing")["success"] is False


def test_inspect_win32_tree_hierarchy_and_depth(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 100)
    parents = {101: 100, 102: 101, 103: 102}
    classes = {100: "EVA_Window_Dblclk", 101: "EVA_ChildWindow", 102: "RICHEDIT50W", 103: "DeepChild"}
    titles = {100: "Room", 101: "Container", 102: "", 103: "Leaf"}

    gui = controller.win32gui
    gui.EnumChildWindows.side_effect = lambda root, cb, _: [cb(h, None) for h in (101, 102, 103)]
    gui.GetParent.side_effect = lambda h: parents[h]
    gui.GetClassName.side_effect = lambda h: classes[h]
    gui.GetWindowText.side_effect = lambda h: titles[h]
    gui.GetDlgCtrlID.side_effect = lambda h: h + 1000
    gui.GetWindowRect.side_effect = lambda h: (0, 0, h, h)
    gui.IsWindowVisible.return_value = True
    gui.IsWindowEnabled.return_value = True

    tree_full = controller.inspect_win32_tree("Room", max_depth=5)
    assert tree_full["success"] is True
    assert tree_full["node_count"] == 4
    assert tree_full["tree"]["children"][0]["children"][0]["children"][0]["hwnd"] == 103

    tree_shallow = controller.inspect_win32_tree("Room", max_depth=1)
    assert tree_shallow["node_count"] == 2
    assert tree_shallow["tree"]["children"][0]["children"] == []


def test_list_participants_legacy_uia_and_fallback(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 100)
    controller.win32process.GetWindowThreadProcessId.return_value = (1, 999)

    monkeypatch.setattr(controller.bridge, "request", MagicMock(return_value={"participants": ["김철수", "이영희"]}))
    ok = controller.list_participants_legacy("Room")
    assert ok["success"] is True and ok["participants"] == ["김철수", "이영희"] and ok["count"] == 2

    monkeypatch.setattr(controller.bridge, "request", MagicMock(side_effect=RuntimeError("Unknown action participants")))
    monkeypatch.setattr(controller, "find_child_window_recursive", lambda *_: 200)
    monkeypatch.setattr(controller, "_edit_text", lambda _: "[Room] [대화상대 5명]\n")
    fb = controller.list_participants_legacy("Room")
    assert fb["success"] is False and fb["participants"] == [] and fb["member_count"] == 5


def test_list_chats_legacy_uses_ocr_worker(monkeypatch):
    monkeypatch.setattr(controller, "_main_window", lambda: 100)
    monkeypatch.setattr(controller, "_find_chat_list_view", lambda _: 200)
    monkeypatch.setattr(controller, "_find_visible_chat_room_list", lambda _: 300)
    controller.win32gui.IsWindowVisible.return_value = True
    controller.win32process.GetWindowThreadProcessId.return_value = (1, 999)
    monkeypatch.setattr(
        controller.bridge,
        "request",
        MagicMock(return_value={"chats": [{"title": "RoomA"}, {"title": "RoomB"}], "read_method": "win32_ocr"}),
    )
    res = controller.list_chats_legacy(max_chats=1)
    assert res["read_method"] == "win32_ocr"
    assert res["total_exposed"] == 2
    assert [c["title"] for c in res["chats"]] == ["RoomA"]


def test_search_and_open_room_clears_only_own_query(monkeypatch):
    monkeypatch.setattr(controller, "_main_window", lambda: 100)
    monkeypatch.setattr(controller, "_ensure_foreground", lambda _: True)
    monkeypatch.setattr(controller, "_activate_search_and_get_edit", lambda _: 200)
    monkeypatch.setattr(controller, "_find_chat_list_view", lambda _: 300)
    monkeypatch.setattr(controller, "_find_visible_search_list", lambda _: 400)
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check())
    controller.win32process.GetWindowThreadProcessId.return_value = (1, 999)
    replaced = []
    monkeypatch.setattr(controller, "_replace_selection", lambda _, txt: replaced.append(txt))
    texts = iter(["", "김민수", "김민수", "김민수"])
    monkeypatch.setattr(controller, "_edit_text", lambda _: next(texts))
    monkeypatch.setattr(controller.bridge, "request", MagicMock(return_value={"opened": True}))
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 555)
    res = controller.search_and_open_room("김민수")
    assert res["success"] is True and res["hwnd"] == 555
    assert replaced == ["김민수", ""]


def test_send_prepared_image_win32_fallback(monkeypatch):
    controller.win32gui.IsWindowEnabled.return_value = True
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    controller.win32process.GetWindowThreadProcessId.return_value = (1, 999)
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_empty_input", lambda _: True)
    controller.win32clipboard.GetClipboardSequenceNumber.return_value = 7
    monkeypatch.setattr(controller.bridge, "request", MagicMock(side_effect=RuntimeError("Unique enabled send button not found")))
    monkeypatch.setattr(controller, "_find_owned_popup", lambda *_: 222)
    monkeypatch.setattr(controller, "_native_send_button", lambda popup, custom=False: 333 if custom else None)
    controller.win32gui.GetClientRect.return_value = (0, 0, 80, 30)
    controller.win32gui.IsWindowVisible.return_value = False
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check())
    res = controller._send_prepared_image(111, 444, 7, "Room", "C:\\x.png")
    assert res["success"] is True and res["confirmation"] == "verified_photo_preview_closed"


def test_read_messages_handles_null_clipboard_owner_error(monkeypatch):
    import pywintypes
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    monkeypatch.setattr(controller, "find_child_window_recursive", lambda *_: 222)
    monkeypatch.setattr(controller, "bring_window_to_front", MagicMock())
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check())
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_send_ctrl_key_combo", MagicMock())
    monkeypatch.setattr(controller, "_plain_clipboard_snapshot", lambda: (1, ""))
    monkeypatch.setattr(controller.time, "sleep", lambda _: None)
    controller.win32clipboard.GetClipboardSequenceNumber.side_effect = [1, 2]
    controller.win32clipboard.GetClipboardOwner.side_effect = pywintypes.error(0, "GetClipboardOwner", "No error message is available")
    res = controller.read_chat_messages("Room", "complete")
    assert res["success"] is False and "fresh transcript" in res["error"]


def test_open_clipboard_retries_transient_access_denied(monkeypatch):
    import pywintypes
    monkeypatch.setattr(controller.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def flaky_open():
        calls["n"] += 1
        if calls["n"] < 3:
            raise pywintypes.error(5, "OpenClipboard", "Access is denied.")

    controller.win32clipboard.OpenClipboard.side_effect = flaky_open
    with controller._open_clipboard(timeout_sec=0.5, interval_sec=0.01):
        pass
    assert calls["n"] == 3
    controller.win32clipboard.CloseClipboard.assert_called_once()


def test_open_clipboard_raises_non_access_denied_immediately(monkeypatch):
    import pywintypes
    sleep = MagicMock()
    monkeypatch.setattr(controller.time, "sleep", sleep)
    controller.win32clipboard.OpenClipboard.side_effect = pywintypes.error(1418, "OpenClipboard", "Thread does not have a clipboard open.")
    with pytest.raises(pywintypes.error):
        with controller._open_clipboard(timeout_sec=0.5, interval_sec=0.01):
            pass
    sleep.assert_not_called()
    controller.win32clipboard.CloseClipboard.assert_not_called()


def test_send_image_allows_legacy_placeholder_cue_with_nonzero_length(monkeypatch):
    import win32con
    monkeypatch.setattr(controller.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(controller.os.path, "abspath", lambda p: p)
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    monkeypatch.setattr(controller, "find_chat_input", lambda _: 222)
    monkeypatch.setattr(controller, "bring_window_to_front", MagicMock())
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_edit_text", lambda _: "메시지 입력")
    controller.win32gui.SendMessageTimeout.side_effect = lambda h, m, *_: (1, 6) if m == win32con.EM_LINELENGTH else (1, 0)
    controller.win32gui.IsWindowEnabled.return_value = True
    monkeypatch.setattr(controller, "_copy_image_to_clipboard", lambda *a, **kw: 99)
    monkeypatch.setattr(controller, "_restore_plain_clipboard", MagicMock())
    controller.win32clipboard.GetClipboardSequenceNumber.return_value = 99
    monkeypatch.setattr(controller.bridge, "request", MagicMock(return_value={"sent": True, "confirmation": "verified_photo_preview_closed"}))
    res = controller.send_image_to_room("Room", "C:\\x.png")
    assert res["success"] is True


def test_send_mention_selects_legacy_placeholder_before_at(monkeypatch):
    import win32con
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    monkeypatch.setattr(controller, "find_chat_input", lambda _: 222)
    monkeypatch.setattr(controller, "bring_window_to_front", MagicMock())
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_replace_selection", MagicMock())
    monkeypatch.setattr(controller, "_submit_draft", lambda *a, **kw: {"success": True})
    monkeypatch.setattr(controller.time, "sleep", MagicMock())
    monkeypatch.setattr(controller.bridge, "request", MagicMock(return_value={"selected": True}))
    monkeypatch.setattr(controller, "_edit_text", MagicMock(side_effect=["메시지 입력", "메시지 입력", "@김철수 "]))
    monkeypatch.setattr(controller, "_wait_until", lambda check, _: check())
    monkeypatch.setattr(controller, "_press_key", MagicMock())
    monkeypatch.setattr(controller, "_type_mention_query", MagicMock())
    calls = []

    def send_msg(h, m, w, l, *rest):
        calls.append((h, m, w, l))
        return (1, 6) if m == win32con.EM_LINELENGTH else (1, 0)

    controller.win32gui.SendMessageTimeout.side_effect = send_msg
    res = controller.send_mention_message("Room", "김철수", "안녕")
    assert res["success"] is True
    assert (222, win32con.EM_SETSEL, 0, -1) in calls


def test_inspect_win32_tree_includes_owned_popups(monkeypatch):
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 100)
    gui = controller.win32gui
    gui.EnumChildWindows.side_effect = lambda root, cb, _: [cb(201, None)] if root == 200 else []
    gui.EnumWindows.side_effect = lambda cb, _: cb(200, None)
    gui.GetWindow.side_effect = lambda h, cmd: 100 if h == 200 else 0
    gui.GetParent.side_effect = lambda h: 200 if h == 201 else 0
    gui.GetClassName.side_effect = lambda h: {100: "EVA_Window_Dblclk", 200: "#32770", 201: "Button"}[h]
    gui.GetWindowText.side_effect = lambda h: {100: "Room", 200: "열기", 201: "열기(&O)"}[h]
    gui.GetDlgCtrlID.side_effect = lambda h: 1 if h == 201 else 0
    gui.GetWindowRect.return_value = (0, 0, 100, 100)
    gui.IsWindowVisible.return_value = True
    gui.IsWindowEnabled.side_effect = lambda h: h != 100
    res = controller.inspect_win32_tree("Room", max_depth=3)
    assert res["success"] is True and len(res["owned_popups"]) == 1
    assert res["owned_popups"][0]["class"] == "#32770"
    assert res["owned_popups"][0]["children"][0]["ctrl_id"] == 1


def test_send_prepared_image_owner_drawn_ocr_fallback(monkeypatch):
    controller.win32gui.IsWindowEnabled.return_value = True
    monkeypatch.setattr(controller, "find_chat_window", lambda _: 111)
    controller.win32process.GetWindowThreadProcessId.return_value = (1, 999)
    monkeypatch.setattr(controller, "_focus_control", MagicMock())
    monkeypatch.setattr(controller, "_require_focus", MagicMock())
    monkeypatch.setattr(controller, "_empty_input", lambda _: True)
    controller.win32clipboard.GetClipboardSequenceNumber.return_value = 7
    monkeypatch.setattr(controller, "_find_owned_popup", lambda *_: 222)
    monkeypatch.setattr(controller, "_native_send_button", lambda popup, custom=False: None)
    calls = []

    def fake_bridge(payload, timeout):
        calls.append(payload["action"])
        if payload["action"] == "legacy_image_confirm":
            raise RuntimeError("Cannot verify photo preview in the exact room")
        return {"sent": True, "confirmation": "verified_photo_preview_closed"}

    monkeypatch.setattr(controller.bridge, "request", fake_bridge)
    res = controller._send_prepared_image(111, 444, 7, "Room", "C:\\x.png")
    assert res["success"] is True and res["confirmation"] == "verified_photo_preview_closed"
    assert calls == ["legacy_image_confirm", "legacy_photo_ocr_confirm"]
