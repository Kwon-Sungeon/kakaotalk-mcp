"""Regressions for the NP fixes and additions on top of v0.4.0."""
import os
import threading
import time
from unittest.mock import Mock

import pytest

from kakao_mcp import backend, config, controller
from kakao_mcp.parser import (date_to_iso, enrich_messages, extract_urls_from_messages, find_urls,
                              parse_chat_text)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_join_and_leave_notices_are_not_glued_onto_messages():
    text = (
        '2026년 11월 12일 목요일\n'
        '[김철수] [오전 10:30] 안녕하세요\n'
        '이영희님이 들어왔습니다.\n'
        '[이영희] [오전 10:31] 반갑습니다\n'
        '둘째 줄\n'
        '김철수님이 박민수님을 초대했습니다.\n'
        '박민수님이 나갔습니다.\n'
    )
    result = parse_chat_text(text)
    assert [m['text'] for m in result['messages']] == ['안녕하세요', '반갑습니다\n둘째 줄']
    assert [n['text'] for n in result['notices']] == [
        '이영희님이 들어왔습니다.', '김철수님이 박민수님을 초대했습니다.', '박민수님이 나갔습니다.']
    assert result['notices'][0]['date'] == '2026년 11월 12일 목요일'


def test_timestamps_use_24_hour_clock():
    text = (
        '2025년 2월 27일 목요일\n'
        '[A] [오전 12:05] 자정 직후\n'
        '[A] [오전 9:03] 아침\n'
        '[A] [오후 12:30] 점심\n'
        '[A] [오후 11:59] 밤\n'
    )
    messages = parse_chat_text(text)['messages']
    assert [m['time_24h'] for m in messages] == ['00:05', '09:03', '12:30', '23:59']
    assert messages[3]['timestamp'] == '2025-02-27T23:59'
    assert messages[0]['time'] == '오전 12:05'


def test_message_without_date_has_no_timestamp():
    message = parse_chat_text('[A] [오후 2:15] 날짜 없음')['messages'][0]
    assert message['time_24h'] == '14:15' and message['timestamp'] is None


def test_english_and_24_hour_transcripts_are_parsed():
    english = parse_chat_text('Thursday, February 27, 2025\n[Alice] [2:15 PM] hello\n[Bob] [12:01 am] late')
    assert [m['sender'] for m in english['messages']] == ['Alice', 'Bob']
    assert english['messages'][0]['timestamp'] == '2025-02-27T14:15'
    assert english['messages'][1]['time'] == '12:01 AM' and english['messages'][1]['time_24h'] == '00:01'
    plain = parse_chat_text('[Alice] [14:05] hello')
    assert plain['messages'][0]['time_24h'] == '14:05'


def test_bracketed_line_inside_a_korean_message_stays_in_that_message():
    text = '[김철수] [오전 10:30] 오늘 일정\n[공지] [14:00] 회의\n[이영희] [오전 10:31] 네'
    messages = parse_chat_text(text)['messages']
    assert len(messages) == 2
    assert messages[0]['text'] == '오늘 일정\n[공지] [14:00] 회의'


def test_urls_drop_sentence_punctuation():
    assert find_urls('보세요 https://example.com/a. 그리고 https://example.com/b?x=1, 끝!') == [
        'https://example.com/a', 'https://example.com/b?x=1']
    assert find_urls('(https://example.com/c)') == ['https://example.com/c']
    assert find_urls('https://') == []
    assert find_urls('http://x') == ['http://x']


def test_photo_bundles_and_emoticons_are_flagged():
    messages = parse_chat_text('[A] [오전 9:00] 사진 3장\n[A] [오전 9:01] 이모티콘\n[A] [오전 9:02] 사진 좋네요')['messages']
    assert [m['is_photo'] for m in messages] == [True, False, False]
    assert messages[1]['is_emoticon'] is True


def test_unique_urls_keep_first_sender():
    messages = parse_chat_text('[A] [오전 9:00] https://a.com\n[B] [오전 9:01] https://a.com https://b.com')['messages']
    assert len(extract_urls_from_messages(messages)) == 3
    unique = extract_urls_from_messages(messages, unique=True)
    assert [(u['url'], u['sender']) for u in unique] == [('https://a.com', 'A'), ('https://b.com', 'B')]


def test_date_to_iso():
    assert date_to_iso('2026년 2월 28일 토요일') == '2026-02-28'
    assert date_to_iso('Saturday, February 28, 2026') == '2026-02-28'
    assert date_to_iso('어제') is None and date_to_iso(None) is None


def test_messages_read_through_uia_get_the_same_time_fields():
    messages = [{'sender': 'A', 'time': '오후 3:07', 'date': '2026년 11월 12일 목요일', 'text': 'x'},
                {'sender': None, 'time': '오전 8:00', 'date': None, 'text': 'y'}]
    enrich_messages(messages)
    assert messages[0]['timestamp'] == '2026-11-12T15:07'
    assert messages[1]['time_24h'] == '08:00' and messages[1]['timestamp'] is None


# ---------------------------------------------------------------------------
# Room discovery (legacy)
# ---------------------------------------------------------------------------

def fake_desktop(monkeypatch, windows):
    """windows: {hwnd: (title, [child classes])}; every window has the chat window class."""
    gui = controller.win32gui
    gui.FindWindow.return_value = 1
    gui.IsWindowVisible.return_value = True
    gui.GetWindowText.side_effect = lambda hwnd: windows[hwnd][0] if hwnd in windows else ''
    children = {hwnd * 100 + index: cls for hwnd, (_, classes) in windows.items()
                for index, cls in enumerate(classes)}
    gui.GetClassName.side_effect = lambda hwnd: children.get(hwnd, config.KAKAO_CHAT_WINDOW_CLASS)

    def enum_windows(callback, _):
        for hwnd in windows:
            callback(hwnd, None)

    def enum_children(parent, callback, _):
        for child in children:
            if child // 100 == parent and not callback(child, None):
                break

    gui.EnumWindows.side_effect = enum_windows
    gui.EnumChildWindows.side_effect = enum_children


CHAT = [config.KAKAO_EDIT_CLASS, config.KAKAO_LIST_CONTROL_CLASS, '_EVA_CustomScrollCtrl', 'Edit']
BOARD = ['EVA_ChildWindow', 'EVA_ChildWindow', 'Edit', '_EVA_CustomScrollCtrl', 'RichEdit20W']


def test_board_and_profile_windows_are_not_listed_as_rooms(monkeypatch):
    fake_desktop(monkeypatch, {1: (config.KAKAO_MAIN_WINDOW_TITLE, []), 2: ('팀방', CHAT),
                               3: ('홍길동', BOARD), 4: ('김철수', CHAT)})
    assert [room['title'] for room in controller.list_chat_windows()] == ['팀방', '김철수']
    assert controller.find_chat_window('팀방') == 2
    assert controller.find_chat_window('홍길동') is None


def test_board_window_with_the_same_title_does_not_make_a_room_ambiguous(monkeypatch):
    fake_desktop(monkeypatch, {1: (config.KAKAO_MAIN_WINDOW_TITLE, []), 2: ('홍길동', CHAT),
                               3: ('홍길동', BOARD)})
    assert controller.find_chat_window('홍길동') == 2


def test_two_real_rooms_with_one_title_are_still_refused(monkeypatch):
    fake_desktop(monkeypatch, {1: (config.KAKAO_MAIN_WINDOW_TITLE, []), 2: ('홍길동', CHAT),
                               3: ('홍길동', CHAT)})
    with pytest.raises(ValueError, match='Multiple open rooms'):
        controller.find_chat_window('홍길동')


JPEG = b'\xff\xd8\xff\xe0' + b'\0' * 2000
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 2000
WEBP = b'RIFF' + b'\0' * 4 + b'WEBP' + b'\0' * 2000


def test_cached_images_get_their_real_extension_and_other_files_are_skipped(monkeypatch, tmp_path):
    cache = tmp_path / 'user' / 'chat_data' / 'cli_http_v2'
    cache.mkdir(parents=True)
    files = {'oldest': JPEG, 'photo': PNG, 'sticker.jpg': WEBP, 'database': b'SQLite format 3' + b'\0' * 2000,
             'tiny': b'\xff\xd8\xff'}
    for index, (name, data) in enumerate(files.items()):
        path = cache / name
        path.write_bytes(data)
        os.utime(path, (10_000 + index, 10_000 + index))
    monkeypatch.setattr(controller, 'get_kakao_user_hash_dir', lambda: str(tmp_path / 'user'))
    output = tmp_path / 'out'
    result = controller.download_recent_images('room', str(output), max_images=2)
    assert result['success'] is True and result['scope'] == 'account_cache'
    assert sorted(os.listdir(output)) == ['photo.png', 'sticker.webp']  # newest two images
    assert result['skipped_non_images'] == 1
    assert (output / 'photo.png').read_bytes() == PNG


def test_download_without_account_folder_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(controller, 'get_kakao_user_hash_dir', lambda: None)
    result = controller.download_recent_images('room', str(tmp_path))
    assert result['success'] is False and result['images'] == []
    assert controller.download_recent_images('room', str(tmp_path), max_images=0)['success'] is False


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

def running_monitor():
    monitor = controller.ChatMonitor()
    monitor._running = True
    monitor._thread = Mock(is_alive=lambda: True)
    return monitor


def message(text, minute):
    return {'date': 'day', 'sender': 'A', 'time': f'10:{minute:02d}', 'text': text}


def test_star_keyword_reports_every_new_message(monkeypatch):
    monitor = controller.ChatMonitor()
    first, second = message('아무 말', 1), message('다른 말', 2)
    read = Mock(side_effect=[{'success': True, 'parsed': {'messages': [first]}},
                             {'success': True, 'parsed': {'messages': [first, second]}}])
    monkeypatch.setattr(backend, 'read_chat_messages', read)
    monitor._keywords = ['*']
    monitor._load_initial_messages()
    monitor._check_for_new_messages()
    assert [event['trigger_message'] for event in monitor.get_events()] == [second]


def test_waiting_for_events_returns_as_soon_as_one_arrives():
    monitor = running_monitor()
    threading.Timer(0.3, monitor._emit, [{'keyword': '회의'}]).start()
    started = time.monotonic()
    assert monitor.get_events(wait_sec=10) == [{'keyword': '회의'}]
    assert 0.2 < time.monotonic() - started < 5


def test_waiting_ends_at_the_timeout_or_when_the_monitor_is_not_running():
    monitor = running_monitor()
    started = time.monotonic()
    assert monitor.get_events(wait_sec=0.3) == []
    assert 0.25 < time.monotonic() - started < 3
    stopped = controller.ChatMonitor()
    started = time.monotonic()
    assert stopped.get_events(wait_sec=10) == []
    assert time.monotonic() - started < 1


def test_wait_is_capped(monkeypatch):
    monkeypatch.setattr(config, 'MONITOR_MAX_WAIT_SEC', 0.2)
    monitor = running_monitor()
    started = time.monotonic()
    assert monitor.get_events(wait_sec=3600) == []
    assert time.monotonic() - started < 3


def test_overflowing_events_are_counted():
    monitor = controller.ChatMonitor()
    for index in range(config.MONITOR_MAX_EVENTS + 3):
        monitor._emit({'n': index})
    assert monitor.status()['dropped_events'] == 3
    events = monitor.get_events()
    assert len(events) == config.MONITOR_MAX_EVENTS and events[0] == {'n': 3}


def test_status_shows_the_last_read_error(monkeypatch):
    monitor = controller.ChatMonitor()
    read = Mock(side_effect=[{'success': False, 'error': "Chat window 'fixture' not found"},
                             {'success': False, 'deferred': True, 'error': 'Waiting for idle input'},
                             {'success': True, 'parsed': {'messages': []}}])
    monkeypatch.setattr(backend, 'read_chat_messages', read)
    monitor._check_for_new_messages()
    assert 'not found' in monitor.status()['last_error']
    monitor._check_for_new_messages()
    assert 'not found' in monitor.status()['last_error']  # A deferred poll is not an error.
    monitor._check_for_new_messages()
    status = monitor.status()
    assert status['last_error'] is None and status['polls'] == 3 and status['last_poll']


def test_blank_keywords_are_rejected():
    result = controller.ChatMonitor().start('fixture', ['  ', ''])
    assert result == {'success': False, 'error': 'Keywords list cannot be empty'}


@pytest.mark.parametrize('scenario,code', [
    ('sequence_at_close', None), ('focus_clears_hint', None), ('literal_draft', 'input_hint_unresolved'),
    ('verified_hint', None), ('hint_is_payload', 'input_hint_unresolved'),
    ('different_draft', 'draft_changed'), ('room_change', 'room_changed'),
    ('same_text_new_clip', 'clipboard_changed'), ('clipboard_race', 'clipboard_changed'),
    ('paste_not_applied', 'paste_text_mismatch'),
])
def test_buttonless_paste_and_exact_submission(monkeypatch, scenario, code):
    import win32con
    cue = '메시지 입력'
    expected = cue if scenario == 'hint_is_payload' else '테스트\n본문'
    literal = scenario in {'focus_clears_hint', 'literal_draft', 'verified_hint', 'hint_is_payload', 'different_draft'}
    state = {'draft': cue, 'clip': 'previous', 'seq': 10, 'writing': False, 'open': False, 'handle': 1}
    for name in ('bring_window_to_front', '_focus_control', '_require_focus'):
        monkeypatch.setattr(controller, name, Mock())
    def click(*args):
        if scenario == 'focus_clears_hint':
            state['draft'] = ''
    monkeypatch.setattr(controller, '_click_control', click)
    monkeypatch.setattr(controller, '_wait_until', lambda check, timeout: check())
    monkeypatch.setattr(controller, 'find_chat_window',
                        lambda _: 99 if scenario == 'room_change' and state['seq'] != 10 else 11)
    monkeypatch.setattr(controller, 'find_child_window_recursive', lambda *_: 22)
    monkeypatch.setattr(controller, '_native_send_button', lambda *_, **__: None)
    monkeypatch.setattr(controller, '_edit_text', lambda _: state['draft'])
    def edit_message(edit, msg, w, l, *args):
        assert edit == 22
        if msg == win32con.EM_SETSEL:
            assert scenario == 'verified_hint' and state['draft'] == cue and (w, l) == (0, -1)
            state['selected'] = True
            return (1, 0)
        assert msg == win32con.EM_LINELENGTH
        return (1, len(cue) if literal else 0)
    controller.win32gui.SendMessageTimeout.side_effect = edit_message
    monkeypatch.setattr(controller, '_plain_clipboard_snapshot', lambda: (10, 'previous'))
    restore = Mock()
    monkeypatch.setattr(controller, '_restore_plain_clipboard', restore)
    clip = controller.win32clipboard
    def open_clip():
        assert not state['open']
        state['open'] = True
    def close_clip():
        assert state['open']
        state['open'] = False
        if state['writing']:
            state['seq'] = 12  # Win32 publishes synthesized formats on close.
            state['writing'] = False
            if scenario == 'same_text_new_clip':
                state['handle'] += 1
            if scenario == 'different_draft':
                state['draft'] = 'new private draft'
        elif scenario == 'clipboard_race':
            state['seq'] += 1
    def write_clip(text, fmt):
        assert state['open'] and fmt == win32con.CF_UNICODETEXT
        state.update(clip=text, seq=11, writing=True)
    clip.OpenClipboard.side_effect = open_clip
    clip.CloseClipboard.side_effect = close_clip
    clip.SetClipboardText.side_effect = write_clip
    clip.GetClipboardSequenceNumber.side_effect = lambda: state['seq']
    clip.GetClipboardDataHandle.side_effect = lambda _: state['handle']
    clip.GetClipboardData.side_effect = lambda _: state['clip']
    def paste(key):
        assert key == 0x56 and not state['open'] and (not literal or state['draft'] == '' or state.get('selected'))
        if scenario != 'paste_not_applied':
            state['draft'] = state['clip'].replace('\r\n', '\n')
    def submit(edit, msg, key, flags):
        assert edit == 22 and key == win32con.VK_RETURN and state['draft'] == expected
        if msg == win32con.WM_KEYUP:
            state['draft'] = cue
    key = Mock(side_effect=paste)
    monkeypatch.setattr(controller, '_send_ctrl_key_combo', key)
    controller.win32gui.PostMessage.side_effect = submit
    result = controller.send_message_to_room('fixture', expected,
                    replace_legacy_hint=scenario in {'verified_hint', 'different_draft', 'hint_is_payload'})
    if code:
        assert result['status'] == 'failed_before_send' and result['code'] == code
        controller.win32gui.PostMessage.assert_not_called()
        if scenario != 'paste_not_applied':
            key.assert_not_called()
    else:
        assert result['confirmation'] == 'draft_cleared'
        key.assert_called_once_with(0x56)
        assert controller.win32gui.PostMessage.call_count == 2
        restore.assert_called_once_with((10, 'previous'), 10, 12)


def test_multiple_account_caches_require_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'KAKAO_USERS_DIR', tmp_path)
    monkeypatch.delenv('KAKAO_MCP_CACHE_ACCOUNT', raising=False)
    for name in ['a' * 40, 'b' * 40, 'unrelated']:
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match='Multiple account'):
        controller.get_kakao_user_hash_dir()
    monkeypatch.setenv('KAKAO_MCP_CACHE_ACCOUNT', 'b' * 40)
    assert controller.get_kakao_user_hash_dir() == str(tmp_path / ('b' * 40))


def test_cached_copy_preserves_existing_unrelated_output(monkeypatch, tmp_path):
    cache = tmp_path / 'user' / 'chat_data' / 'cli_http_v2'
    cache.mkdir(parents=True)
    (cache / 'photo.dat').write_bytes(PNG)
    (tmp_path / 'photo.png').write_bytes(b'keep me')
    monkeypatch.setattr(controller, 'get_kakao_user_hash_dir', lambda: str(tmp_path / 'user'))
    for attempt in range(2):
        items = controller.download_recent_images('room', str(tmp_path))['images']
        assert [i['destination'] for i in items] == [str(tmp_path / 'photo-2.png')]
    assert (tmp_path / 'photo.png').read_bytes() == b'keep me'
    assert (tmp_path / 'photo-2.png').read_bytes() == PNG


def test_health_keeps_diagnostics_if_version_lookup_fails(monkeypatch):
    import win32api
    from kakao_mcp import server, qt_controller
    monkeypatch.setattr(qt_controller, 'available', lambda: False)
    monkeypatch.setattr(controller, 'is_kakaotalk_running', lambda: dict(running=True, pid=17))
    monkeypatch.setattr(controller, 'list_chat_windows', lambda: [42])
    monkeypatch.setattr(win32api, 'OpenProcess', Mock(side_effect=win32api.error(5, 'OpenProcess', 'denied')))
    health = server.kakao_health_check(diagnostics=True)
    assert health['running'] and health['open_room_count'] == 1 and 'denied' in health['version_error']

def test_placeholder_itself_is_only_a_sendable_draft_when_it_has_characters(monkeypatch):
    cue = '메시지 입력'
    monkeypatch.setattr(controller, '_edit_text', lambda _: cue)
    controller.win32gui.SendMessageTimeout.side_effect = [(1, 0), (1, len(cue))]
    assert not controller._matches_draft(11, cue)
    assert controller._matches_draft(11, cue)
