from io import BytesIO
from unittest.mock import Mock

import pytest

from kakao_mcp import controller, qt_bridge


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


def test_worker_restarts_when_script_mtime_changes(monkeypatch):
    first = worker(b'{"success":true,"data":{"v":1}}\n')
    second = worker(b'{"success":true,"data":{"v":2}}\n')
    bridge = qt_bridge.Bridge()
    mtimes = iter([100, 100, 200])
    monkeypatch.setattr(bridge, '_current_script_mtime', lambda: next(mtimes))
    launch = Mock(side_effect=[first, second])
    monkeypatch.setattr(bridge, 'start', launch)
    assert bridge.request({'action': 'rooms'}, 1)['v'] == 1
    first.stdout = BytesIO(b'{"success":true,"data":{"v":11}}\n')
    assert bridge.request({'action': 'rooms'}, 1)['v'] == 11
    assert launch.call_count == 1
    assert bridge.request({'action': 'rooms'}, 1)['v'] == 2
    assert launch.call_count == 2
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


def test_server_new_tools_and_scroll_and_multiroom_monitor(monkeypatch):
    import asyncio
    from kakao_mcp import qt_controller, server

    monkeypatch.setattr(qt_controller, 'available', lambda: True)
    monkeypatch.setattr(qt_controller, 'list_chats', lambda: {'chats': [{'title': 'A'}, {'title': 'B'}, {'title': 'C'}]})
    listed = server.kakao_list_chats(max_chats=2)
    assert listed['total_exposed'] == 3 and [c['title'] for c in listed['chats']] == ['A', 'B']
    assert server.kakao_list_chats(max_chats=0)['code'] == 'invalid_argument'

    monkeypatch.setattr(qt_controller, 'close_chat_window', lambda room: {'closed': True, 'hwnd': 11, 'room_name': room})
    assert server.kakao_close_room('A')['closed'] is True

    monkeypatch.setattr(qt_controller, 'list_participants', lambda room: {'room_name': room, 'participants': [{'name': 'Kim'}], 'count': 1})
    assert server.kakao_list_participants('A')['count'] == 1

    monkeypatch.setattr(qt_controller, 'inspect_ui', lambda room, depth: {'diagnostic': True, 'room_name': room, 'max_depth': depth})
    assert server.kakao_inspect_ui('A', 4)['max_depth'] == 4
    assert server.kakao_inspect_ui('A', 11)['code'] == 'invalid_argument'

    # Scroll pages should pass through and not artificially cap at 50 when max_messages is omitted
    many = [{'sender': 'A', 'time': '오전 9:00', 'date': '2026년 11월 17일 화요일', 'text': f'm{i}'} for i in range(60)]
    read_mock = Mock(return_value={'success': True, 'parsed': {'room_name': 'A', 'member_count': 2, 'messages': many}})
    monkeypatch.setattr(qt_controller, 'read_chat_messages', read_mock)
    res = server.kakao_read_messages('A', scroll_pages=2)
    assert len(res['messages']) == 60
    assert read_mock.call_args.kwargs['scroll_pages'] == 2
    assert 'error' in server.kakao_read_messages('A', scroll_pages=-1)

    # Legacy fallbacks for new tools
    monkeypatch.setattr(qt_controller, 'available', lambda: False)
    monkeypatch.setattr(controller, 'list_chats_legacy', lambda limit=50: {'chats': [{'title': 'LegacyRoom'}], 'total_exposed': 1, 'read_method': 'win32_ocr'})
    legacy_chats = server.kakao_list_chats()
    assert legacy_chats['read_method'] == 'win32_ocr' and legacy_chats['chats'][0]['title'] == 'LegacyRoom'
    monkeypatch.setattr(controller, 'close_chat_window', lambda room: {'success': True, 'closed': True, 'hwnd': 5})
    assert server.kakao_close_room('A')['closed'] is True
    monkeypatch.setattr(controller, 'list_participants_legacy', lambda room: {'success': True, 'participants': [], 'count': 0})
    assert server.kakao_list_participants('A')['success'] is True
    monkeypatch.setattr(controller, 'inspect_win32_tree', lambda room, depth: {'success': True, 'node_count': 1})
    assert server.kakao_inspect_ui('A', 3)['node_count'] == 1

    # Multi-room monitor routing on server
    fake_monitor = Mock()
    fake_monitor.stop.return_value = {'success': True, 'message': 'Stopped A'}
    fake_monitor.get_events.return_value = [{'room_name': 'A', 'keyword': 'hi'}]
    fake_monitor.status.return_value = {'monitoring': True, 'baseline_ready': True}
    monkeypatch.setattr(controller, '_chat_monitor', fake_monitor)
    assert server.kakao_stop_monitor('A')['message'] == 'Stopped A'
    fake_monitor.stop.assert_called_once_with('A')
    ev = asyncio.run(server.kakao_get_monitor_events(room_name='A'))
    assert ev['event_count'] == 1 and ev['monitoring'] is True
    fake_monitor.get_events.assert_called_once_with(0, 'A')
