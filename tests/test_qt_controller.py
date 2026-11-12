"""Regressions for Qt operations which must not duplicate outgoing messages."""
from unittest.mock import Mock

from kakao_mcp import qt_controller as qt, ui_lock


def test_image_failure_preserves_completed_sends(tmp_path, monkeypatch):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test image')
    monkeypatch.setattr(qt.legacy, '_copy_image_to_clipboard', Mock())
    monkeypatch.setattr(qt, 'find_chat_window', Mock(return_value=123))
    bridge = Mock(side_effect=[{'sent': True}, RuntimeError('uncertain send')])
    monkeypatch.setattr(qt, 'call', bridge)
    result = qt.send_images_to_room('self', [image, image, image])
    assert result['success'] is False
    assert result['results'] == [{'path': str(image), 'sent': True}]
    assert bridge.call_count == 2


def test_invalid_later_image_prevents_any_send(tmp_path, monkeypatch):
    image = tmp_path / 'image.png'
    image.touch()
    bridge = Mock()
    monkeypatch.setattr(qt, 'call', bridge)
    result = qt.send_images_to_room('self', [image, tmp_path / 'missing.png'])
    assert result['success'] is False
    bridge.assert_not_called()


def test_uncertain_text_send_is_not_retried(monkeypatch):
    bridge = Mock(side_effect=RuntimeError('New message bubble not confirmed'))
    monkeypatch.setattr(qt, 'call', bridge)
    result = qt.send_message_to_room('self', 'hello')
    assert result['success'] is False
    assert 'not confirmed' in result['error']
    bridge.assert_called_once_with('send', room='self', text='hello')


def test_nested_ui_operations_release_lock_after_error():
    try:
        with qt.operation(), qt.operation():
            raise ValueError('interrupted')
    except ValueError:
        pass
    assert ui_lock._lock_state.held is False
    with qt.operation():
        assert ui_lock._lock_state.held is True

def test_monitor_only_emits_new_occurrences(monkeypatch):
    from kakao_mcp import backend
    monitor = qt.legacy.ChatMonitor()
    msg = {'date': 'day', 'time': '10:00', 'text': 'repeat', 'sender': 'A'}
    baseline = {'success': True, 'parsed': {'messages': [msg]}}
    repeated = {'success': True, 'parsed': {'messages': [msg, msg]}}
    read = Mock(side_effect=[baseline, repeated, repeated])
    monkeypatch.setattr(backend, 'read_chat_messages', read)
    monitor._keywords = ['repeat']
    monitor._load_initial_messages()
    monitor._check_for_new_messages()
    monitor._check_for_new_messages()
    assert [e['trigger_message'] for e in monitor.get_events()] == [msg]
    assert monitor.get_events() == []


def test_uia_reads_do_not_activate_room_or_copy(monkeypatch):
    payload = {'messages': [], 'partial': True}
    bridge = Mock(return_value=payload)
    monkeypatch.setenv('KAKAO_QT_READ_MODE', 'uia')
    monkeypatch.setattr(qt, 'call', bridge)
    assert qt.read_chat_messages('fixture')['parsed'] == payload
    bridge.assert_called_once_with('read', room='fixture')


def test_complete_read_parses_sender_date_and_urls(monkeypatch):
    raw = '2026년 11월 12일 목요일\n[A] [오전 9:03] https://example.org'
    bridge = Mock(return_value={'raw_text': raw})
    monkeypatch.setattr(qt, 'call', bridge)
    monkeypatch.delenv('KAKAO_QT_READ_MODE', raising=False)
    data = qt.read_chat_messages('fixture', 'complete')['parsed']
    assert data['messages'][0]['date'] == '2026년 11월 12일 목요일'
    assert data['messages'][0]['sender'] == 'A'
    assert data['messages'][0]['urls'] == ['https://example.org']
    assert data['partial'] and data['room_name'] == 'fixture'
    bridge.assert_called_once_with('copy', room='fixture')

def test_default_monitor_and_recent_reads_leave_desktop_alone(monkeypatch):
    payload = {'messages': [{'text': 'recent', 'sender': None}], 'partial': True}
    bridge = Mock(return_value=payload)
    monkeypatch.delenv('KAKAO_QT_READ_MODE', raising=False)
    monkeypatch.setattr(qt, 'call', bridge)
    assert qt.read_chat_messages('fixture', monitoring=True)['parsed'] == payload
    assert qt.read_chat_messages('fixture')['parsed'] == payload
    assert [c.args[0] for c in bridge.call_args_list] == ['read', 'read']


def test_auto_uses_complete_transcript_for_missing_messages(monkeypatch):
    bridge = Mock(side_effect=[{'messages': []}, {'raw_text': '[A] [오전 9:01] old'}])
    monkeypatch.setattr(qt, 'call', bridge)
    data = qt.read_chat_messages('fixture', 'auto', 10)['parsed']
    assert data['messages'][0]['text'] == 'old'
    assert [c.args[0] for c in bridge.call_args_list] == ['read', 'copy']


def test_confirmed_tail_avoids_copy_even_for_explicit_count(monkeypatch):
    payload = {'at_end': True, 'messages': [{'text': 'msg', 'sender': 'A', 'date': 'day'}]}
    bridge = Mock(return_value=payload)
    monkeypatch.setattr(qt, 'call', bridge)
    assert qt.read_chat_messages('fixture', 'auto', 1)['parsed'] == payload
    bridge.assert_called_once_with('read', room='fixture')

def test_clipboard_restoration_preserves_a_concurrent_user_copy():
    clipboard = qt.legacy.win32clipboard
    clipboard.GetClipboardSequenceNumber.return_value = 3
    qt.legacy._restore_plain_clipboard((1, 'original'), 1, 2)
    clipboard.EmptyClipboard.assert_not_called()
    clipboard.GetClipboardSequenceNumber.return_value = 2
    qt.legacy._restore_plain_clipboard((1, 'original'), 1, 2)
    clipboard.SetClipboardText.assert_called_once_with('original', qt.legacy.win32con.CF_UNICODETEXT)

def test_server_defaults_and_explicit_detail_select_read_path(monkeypatch):
    from kakao_mcp import server
    monkeypatch.setattr(qt, 'available', lambda: True)
    monkeypatch.delenv('KAKAO_QT_READ_MODE', raising=False)
    visible = {'room_name': 'fixture', 'member_count': None, 'partial': True, 'read_method': 'uia',
               'messages': [{'text': 'latest', 'sender': None}], 'unknown_fields': ['sender']}
    bridge = Mock(side_effect=[visible, visible, {'raw_text': '[A] [오전 9:30] history https://example.org'}])
    monkeypatch.setattr(qt, 'call', bridge)
    assert server.kakao_read_messages('fixture')['read_method'] == 'uia'
    assert server.kakao_read_messages('fixture', max_messages=3)['read_method'] == 'uia_clipboard'
    assert [c.args[0] for c in bridge.call_args_list] == ['read', 'read', 'copy']


def test_default_open_of_existing_room_keeps_focus(monkeypatch):
    from kakao_mcp import server
    monkeypatch.setattr(qt, 'available', lambda: True)
    monkeypatch.setattr(qt, 'find_chat_window', lambda room: 42)
    activate = Mock()
    monkeypatch.setattr(qt, 'bring_window_to_front', activate)
    assert server.kakao_open_room('fixture')['hwnd'] == 42
    activate.assert_not_called()
    server.kakao_open_room('fixture', focus=True)
    activate.assert_called_once_with(42)


def test_deferred_legacy_baseline_does_not_report_existing_messages(monkeypatch):
    from kakao_mcp import backend
    monitor = qt.legacy.ChatMonitor()
    msg = {'sender': 'A', 'text': 'match', 'time': '9:00'}
    initial = {'success': True, 'parsed': {'messages': [msg]}}
    later = {'success': True, 'parsed': {'messages': [msg, msg]}}
    read = Mock(side_effect=[{'success': False, 'deferred': True}, initial, later])
    monkeypatch.setattr(backend, 'read_chat_messages', read)
    monitor._keywords = ['match']
    monitor._load_initial_messages()
    assert not monitor.baseline_ready
    monitor._check_for_new_messages()
    assert monitor.baseline_ready and not monitor.get_events()
    monitor._check_for_new_messages()
    assert len(monitor.get_events()) == 1
