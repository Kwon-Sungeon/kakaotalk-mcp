from io import BytesIO
from unittest.mock import Mock

import pytest

from kakao_mcp import attachments, controller, qt_bridge


def worker(output):
    process = Mock()
    process.stdin, process.stdout = BytesIO(), BytesIO(output)
    process.poll.return_value = None
    return process


def test_worker_reused_and_ambiguous_message_never_replayed(monkeypatch):
    first = worker(b'{"success":true,"data":{"count":1}}\n')
    second = worker(b'{"success":true,"data":{"count":2}}\n')
    bridge = qt_bridge.Bridge()
    launch = Mock(side_effect=[first, second])
    monkeypatch.setattr(bridge, 'start', launch)
    assert bridge.request({'action': 'rooms'}, 1)['count'] == 1
    with pytest.raises(RuntimeError, match='before retrying'):
        bridge.request({'action': 'send'}, 1)
    launch.assert_called_once()
    assert bridge.request({'action': 'rooms'}, 1)['count'] == 2
    assert b'send' not in second.stdin.getvalue()
    bridge.close()


def test_existing_draft_is_not_acknowledgement(monkeypatch):
    monkeypatch.setattr(controller, '_edit_text', lambda _: 'sample\n')
    monkeypatch.setattr(controller, '_wait_until', lambda check, _: check())
    assert not controller._sent_when_cleared(1, 'fixture')['success']
    controller._user32.keybd_event.assert_not_called()


def test_search_never_uses_hidden_or_ambiguous_edits(monkeypatch):
    monkeypatch.setattr(controller, '_find_chat_list_view', lambda _: 2)
    monkeypatch.setattr(controller, '_wait_until', lambda check, _: check())
    focus = Mock()
    monkeypatch.setattr(controller, '_focus_control', focus)
    controller.win32gui.GetClassName.return_value = 'Edit'
    controller.win32gui.EnumChildWindows.side_effect = lambda root, visit, _: [visit(i, None) for i in (3, 4)]
    controller.win32gui.IsWindowVisible.side_effect = lambda hwnd: hwnd != 3
    assert controller._activate_search_and_get_edit(1) == 4
    focus.assert_called_once_with(1, 4)
    controller.win32gui.IsWindowVisible.side_effect = lambda hwnd: True
    assert controller._activate_search_and_get_edit(1) is None


@pytest.mark.parametrize('use_qt', [True, False])
def test_files_validate_batch_then_stop_at_first_uncertain_send(tmp_path, monkeypatch, use_qt):
    file = tmp_path / 'document.txt'
    file.touch()
    monkeypatch.setattr(attachments.qt, 'available', lambda: use_qt)
    impl = attachments.qt if use_qt else controller
    monkeypatch.setattr(impl, 'find_chat_window', lambda name: 7)
    controller.win32process.GetWindowThreadProcessId.return_value = (4, 9)
    send = Mock(side_effect=[{'sent': True}, RuntimeError('not confirmed')])
    monkeypatch.setattr(attachments.bridge, 'request', send)
    assert not attachments.send_files('fixture', [file, tmp_path / 'missing'])['success']
    send.assert_not_called()
    result = attachments.send_files('fixture', [file, file, file])
    assert not result['success'] and len(result['results']) == 1 and send.call_count == 2
    request = send.call_args.args[0]
    assert (request['hwnd'], request['process_id'], request['qt']) == (7, 9, use_qt)
