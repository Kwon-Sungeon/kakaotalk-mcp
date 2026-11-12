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
    result = controller.list_chat_windows()
    titles = [r["title"] for r in result]
    assert "Room A" in titles
    assert "Room B" in titles
    assert config.KAKAO_MAIN_WINDOW_TITLE not in titles


# ---------------------------------------------------------------------------
# send_message_to_room
# ---------------------------------------------------------------------------

@patch("kakao_mcp.controller.win32api")
@patch("kakao_mcp.controller.find_child_window_recursive")
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


@patch("kakao_mcp.controller.find_child_window_recursive")
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
@patch("kakao_mcp.controller.find_child_window_recursive")
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
    with patch.object(controller, "_focus_control"), patch.object(controller, "_require_focus"):
        result = controller.send_image_to_room("TestRoom", "C:\\test\\image.jpg")
    assert result["success"] is True
    assert "sent" in result["message"].lower()
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
            patch.object(controller, 'find_child_window_recursive', return_value=22222), \
            patch.object(controller, '_edit_text', side_effect=['', 'sample']), \
            patch.object(controller, 'bring_window_to_front'), \
            pytest.raises(RuntimeError, match='inactive'):
        controller.send_message_to_room('TestRoom', 'sample')
    controller.win32clipboard.OpenClipboard.assert_not_called()
    controller._user32.keybd_event.assert_not_called()
    controller._user32.mouse_event.assert_not_called()

def test_native_send_button_does_not_activate_window():
    with patch.object(controller, 'find_chat_window', return_value=11), \
            patch.object(controller, 'find_child_window_recursive', return_value=22), \
            patch.object(controller, '_replace_selection'), \
            patch.object(controller, '_native_send_button', return_value=33), \
            patch.object(controller, '_edit_text', side_effect=['', 'sample', 'sample', '']), \
            patch.object(controller, 'bring_window_to_front') as activate:
        assert controller.send_message_to_room('fixture', 'sample')['success']
    activate.assert_not_called()
    controller.win32gui.SendMessageTimeout.assert_called_once_with(
        33, controller.win32con.BM_CLICK, 0, 0, controller.win32con.SMTO_ABORTIFHUNG, 1000)
    controller._user32.keybd_event.assert_not_called()
