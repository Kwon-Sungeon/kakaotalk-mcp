"""Regressions for Qt operations which must not duplicate outgoing messages."""
from unittest.mock import Mock

from kakao_mcp import qt_controller as qt


def test_image_failure_preserves_completed_sends(tmp_path, monkeypatch):
    image = tmp_path / 'image.png'
    image.write_bytes(b'test image')
    monkeypatch.setattr(qt.legacy, '_copy_image_to_clipboard', Mock())
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
    assert qt._lock_state.held is False
    with qt.operation():
        assert qt._lock_state.held is True

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
    data = qt.read_chat_messages('fixture')['parsed']
    assert data['messages'][0]['date'] == '2026년 11월 12일 목요일'
    assert data['messages'][0]['sender'] == 'A'
    assert data['messages'][0]['urls'] == ['https://example.org']
    assert data['partial'] and data['room_name'] == 'fixture'
    bridge.assert_called_once_with('copy', room='fixture')
