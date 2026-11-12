from unittest.mock import Mock

import pytest

from kakao_mcp import delivery as d, qt_controller as qt, controller as legacy, server


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    room = Mock(ref='win32:17:23')
    monkeypatch.setattr(d, 'resolve', Mock(return_value=room))
    send = Mock(return_value={'sent': True})
    monkeypatch.setattr(d, 'dispatch', send)
    monkeypatch.setattr(d.time, 'sleep', lambda _: None)
    return send


def test_partial_batch_keeps_receipts_across_retries_and_tools(fake):
    fake.side_effect = [{'sent': True}, RuntimeError('provider disconnected')]
    result = server.kakao_send_bulk(['one', 'two', 'three'], 'message', request_id='batch')
    assert [r['status'] for r in result['results']] == ['confirmed', 'unconfirmed', 'not_attempted']
    assert server.kakao_send_bulk(['one', 'two', 'three'], 'message', request_id='batch')['replayed']
    assert server.kakao_send_message('one', 'message')['results'][0]['confirmation'] == 'previous_receipt'
    assert server.kakao_send_message('two', 'message')['code'] == 'previous_send_unconfirmed'
    assert fake.call_count == 2
    assert d.receipt('batch', 1, 'not_sent')['results'][1]['status'] == 'verified_not_sent'
    fake.side_effect = None
    assert server.kakao_send_message('two', 'message', request_id='checked-by-user')['success']
    assert fake.call_count == 3


def test_crash_after_dispatch_never_replays(fake):
    fake.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        server.kakao_send_message('one', 'message', request_id='lost')
    assert server.kakao_send_message('one', 'message', request_id='lost')['code'] == 'send_unconfirmed'
    assert not server.kakao_send_message('one', 'message', request_id='replacement')['success']
    fake.assert_called_once()


def test_exception_while_opening_later_room_keeps_first(fake):
    d.resolve.side_effect = [Mock(ref='test'), ValueError('Multiple open rooms')]
    result = server.kakao_send_bulk(['one', 'two', 'three'], 'test')
    assert [r['status'] for r in result['results']] == ['confirmed', 'failed_before_send', 'not_attempted']
    assert result['code'] == 'room_ambiguous'
    fake.assert_called_once()


def test_id_conflict_and_duplicate_recipients_are_rejected(fake):
    server.kakao_send_message('one', 'message', request_id='key')
    with pytest.raises(d.DeliveryError, match='different content'):
        server.kakao_send_message('two', 'message', request_id='key')
    with pytest.raises(d.DeliveryError, match='Duplicate'):
        server.kakao_send_bulk(['one', 'one'], 'message')
    fake.assert_called_once()


@pytest.mark.parametrize('kind', ['image', 'file'])
def test_attachment_batch_prevalidates_and_pins_window(fake, tmp_path, kind):
    files = [tmp_path / f'{i}.png' for i in range(3)]
    entries = [('one', str(f)) for f in files]
    files[0].touch()
    with pytest.raises(d.DeliveryError):
        d.deliver(kind, entries)
    for f in files:
        f.touch()
    d.resolve.return_value.verify.side_effect = d.DeliveryError('room_changed', 'Window changed')
    result = d.deliver(kind, entries)
    assert [r['status'] for r in result['results']] == ['confirmed', 'failed_before_send', 'not_attempted']
    fake.assert_called_once()


@pytest.mark.parametrize('use_qt', [False, True])
def test_closed_exact_room_opened_then_pinned(monkeypatch, use_qt):
    impl = qt if use_qt else legacy
    monkeypatch.setattr(qt, 'available', lambda: use_qt)
    monkeypatch.setattr(impl, 'find_chat_window', Mock(side_effect=[None, 23, 71, None]))
    open_room = Mock(return_value={'success': True, 'hwnd': 23})
    monkeypatch.setattr(impl, 'search_and_open_room', open_room)
    legacy.win32process.GetWindowThreadProcessId.return_value = (5, 17)
    room = d.resolve('fixture')
    assert (room.hwnd, room.pid) == (23, 17)
    with pytest.raises(d.DeliveryError, match='changed'):
        room.verify()
    with pytest.raises(d.DeliveryError, match='no longer open'):
        d.resolve('fixture', room.ref)
    open_room.assert_called_once_with('fixture')


def test_read_ambiguity_never_copies_and_detail_copy_is_pinned(monkeypatch):
    call = Mock(side_effect=RuntimeError('Multiple open rooms'))
    monkeypatch.setattr(qt, 'call', call)
    assert not qt.read_chat_messages('duplicate')['success']
    call.assert_called_once_with('read', room='duplicate')
    call.side_effect = [{'hwnd': 23, 'process_id': 17, 'messages': []}, {'raw_text': ''}]
    assert qt.read_chat_messages('fixture', max_messages=4)['success']
    call.assert_called_with('copy', room='fixture', hwnd=23, process_id=17)


def test_qt_file_paste_binds_clipboard_path_and_room(monkeypatch, tmp_path):
    import struct
    import win32con
    path = str(tmp_path / '문서 sample.txt')
    clip = legacy.win32clipboard
    monkeypatch.setattr(legacy, '_plain_clipboard_snapshot', lambda: (10, 'original'))
    restore = Mock()
    monkeypatch.setattr(legacy, '_restore_plain_clipboard', restore)
    clip.GetClipboardSequenceNumber.side_effect = [10, 11]
    worker = Mock(return_value={'sent': True})
    monkeypatch.setattr(d.bridge, 'request', worker)
    assert d.dispatch(d.Room('self', 23, 17, True), 'file', path, None)['sent']
    fmt, raw = clip.SetClipboardData.call_args.args
    assert fmt == win32con.CF_HDROP and struct.unpack('<IiiII', raw[:20]) == (20, 0, 0, 0, 1)
    assert raw[20:].decode('utf-16-le') == path + '\0\0'
    worker.assert_called_once_with(dict(action='file', path=path, sequence=11, room='self',
                                        hwnd=23, process_id=17, qt=True), 18)
    restore.assert_called_once_with((10, 'original'), 10, 11)


def test_legacy_mention_preserves_draft_and_never_sends_blind_enter(monkeypatch):
    monkeypatch.setattr(legacy, 'find_chat_window', lambda name: 1)
    monkeypatch.setattr(legacy, 'find_child_window_recursive', lambda *args: 2)
    monkeypatch.setattr(legacy, '_edit_text', lambda _: 'my draft')
    key = Mock()
    monkeypatch.setattr(legacy, '_press_key', key)
    assert legacy.send_mention_message('fixture', 'Alice', 'test')['code'] == 'draft_exists'
    key.assert_not_called()
    legacy.win32api.SendMessage.assert_not_called()
    monkeypatch.setattr(legacy, '_edit_text', lambda _: '')
    for name in ('bring_window_to_front', '_focus_control', '_require_focus'):
        monkeypatch.setattr(legacy, name, Mock())
    monkeypatch.setattr(legacy.time, 'sleep', Mock())
    monkeypatch.setattr(legacy.bridge, 'request', Mock(side_effect=RuntimeError('no exact mention candidate')))
    with pytest.raises(RuntimeError, match='no exact'):
        legacy.send_mention_message('fixture', 'Alice', 'test')
    assert all(args.args[0] != 13 for args in key.call_args_list)
