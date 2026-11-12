"""Resolve, pin and record outbound operations across desktop MCP clients."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import time
import win32con

from . import config, controller as legacy, qt_controller as qt
from .qt_bridge import bridge
from .ui_lock import operation


class DeliveryError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def error_code(error):
    if getattr(error, 'code', None):
        return error.code
    text = str(error).lower()
    if 'multiple open rooms' in text or 'ambiguous' in text:
        return 'room_ambiguous'
    if 'existing draft' in text:
        return 'draft_exists'
    if 'found 0' in text or 'not found' in text:
        return 'not_found'
    if 'busy' in text:
        return 'busy'
    return 'operation_failed'


@dataclass(frozen=True)
class Room:
    name: str
    hwnd: int
    pid: int
    is_qt: bool

    @property
    def ref(self):
        return f"{'qt' if self.is_qt else 'win32'}:{self.pid}:{self.hwnd}"

    def verify(self):
        impl = qt if self.is_qt else legacy
        if (impl.find_chat_window(self.name) != self.hwnd or
                legacy.win32process.GetWindowThreadProcessId(self.hwnd)[1] != self.pid):
            raise DeliveryError('room_changed', 'The verified room window changed')

    def arguments(self):
        return dict(room=self.name, hwnd=self.hwnd, process_id=self.pid, qt=self.is_qt)


def resolve(name, ref=None):
    if not name.strip():
        raise DeliveryError('invalid_argument', 'An exact room name is required')
    impl = qt if qt.available() else legacy
    hwnd = impl.find_chat_window(name)
    if not hwnd:
        if ref:
            raise DeliveryError('room_changed', 'The referenced room is no longer open')
        opened = impl.search_and_open_room(name)
        if not opened['success']:
            raise DeliveryError(opened.get('code', 'room_not_verified'), opened['error'])
        hwnd = opened['hwnd']
    room = Room(name, hwnd, legacy.win32process.GetWindowThreadProcessId(hwnd)[1], impl is qt)
    if ref is not None and ref != room.ref:
        raise DeliveryError('room_changed', 'Room reference no longer matches the exact window')
    room.verify()
    return room


@contextmanager
def file_clipboard(path):
    clip = legacy.win32clipboard
    snapshot = legacy._plain_clipboard_snapshot()
    clip.OpenClipboard()
    try:
        before = clip.GetClipboardSequenceNumber()
        clip.EmptyClipboard()
        # DROPFILES with a double-NUL-terminated UTF-16 file list.
        clip.SetClipboardData(win32con.CF_HDROP, struct.pack('<IiiII', 20, 0, 0, 0, 1)
                              + (path + '\0\0').encode('utf-16-le'))
        after = clip.GetClipboardSequenceNumber()
    finally:
        clip.CloseClipboard()
    try:
        yield after
    finally:
        legacy._restore_plain_clipboard(snapshot, before, after)


def dispatch(room, kind, value, mention):
    args = room.arguments()
    if kind in ('file', 'file_clipboard', 'file_picker'):
        if kind == 'file_picker' and room.is_qt:
            error = DeliveryError('unsupported_method', 'Use auto or clipboard for Qt file attachments')
            error.before_send = True
            raise error
        if room.is_qt or kind == 'file_clipboard':
            if not room.is_qt:
                edit = legacy.find_child_window_recursive(room.hwnd, config.KAKAO_EDIT_CLASS)
                if not edit or not legacy._empty_input(edit):
                    error = DeliveryError('input_not_ready', 'Empty legacy chat input required')
                    error.before_send = True
                    raise error
                args['edit_hwnd'] = edit
            with file_clipboard(value) as sequence:
                return bridge.request(dict(action='file', path=value, sequence=sequence, **args), 18)
        return bridge.request(dict(action='file', path=value, **args), 22)
    if room.is_qt:
        if kind == 'image':
            with legacy.temporary_image_clipboard(value) as sequence:
                return bridge.request(dict(action='image', sequence=sequence, **args), 15)
        action = 'mention' if kind == 'mention' else 'send'
        return bridge.request(dict(action=action, text=value, mention=mention, **args), 15)
    if kind == 'image':
        return legacy.send_image_to_room(room.name, value, target=room)
    if kind == 'mention':
        return legacy.send_mention_message(room.name, mention, value, target=room)
    return legacy.send_message_to_room(room.name, value, target=room)


def _journal():
    directory = Path(os.environ['LOCALAPPDATA'], 'KakaoTalk-MCP')
    directory.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(directory / 'deliveries.sqlite3')


def recent_receipts(limit=20, room_name=None):
    if not 1 <= limit <= 100:
        raise DeliveryError('invalid_argument', 'limit must be between 1 and 100')
    path = Path(os.environ['LOCALAPPDATA'], 'KakaoTalk-MCP', 'deliveries.sqlite3')
    records = []
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipts'").fetchone():
                for request_id, updated, payload in db.execute(
                        'SELECT id,updated,result FROM receipts ORDER BY updated DESC'):
                    for index, row in enumerate(json.loads(payload)):
                        if room_name is None or row['room'] == room_name:
                            records.append(dict(request_id=request_id, item_index=index, room=row['room'],
                                                status=row['status'], updated_unix=updated))
                            if len(records) == limit:
                                return {'records': records}
    return {'records': records}


def _summary(key, rows, replayed=False):
    for row in rows:
        row['success'] = row['status'] == 'confirmed'
    success = all(row['status'] == 'confirmed' for row in rows)
    result = dict(success=success, request_id=key, results=rows, replayed=replayed)
    if success:
        result['message'] = f'Confirmed {len(rows)} item(s)'
    else:
        failure = next(row for row in rows if row['status'] != 'confirmed')
        result.update(code=failure.get('code', 'send_unconfirmed'),
                      error=failure.get('error', 'Previous operation interrupted; inspect the room before retrying'))
    return result


def send_policy():
    def names(key):
        return [name.strip() for name in os.environ.get(key, '').replace('\n', ';').split(';') if name.strip()]

    raw = os.environ.get('KAKAO_MCP_MAX_BULK_ROOMS', '').strip()
    if raw and (not raw.isascii() or not raw.isdecimal() or int(raw) < 1):
        raise DeliveryError('invalid_policy', 'KAKAO_MCP_MAX_BULK_ROOMS must be a positive integer')
    return dict(read_only=os.environ.get('KAKAO_MCP_READ_ONLY', '').strip().lower() not in
                {'', '0', 'false', 'no', 'off'}, allowed_rooms=names('KAKAO_MCP_ALLOWED_ROOMS'),
                blocked_rooms=names('KAKAO_MCP_BLOCKED_ROOMS'), max_bulk_rooms=int(raw) if raw else None)


def deliver(kind, entries, *, mention=None, refs=None, request_id=None, interval=0, dry_run=False,
            file_method='auto'):
    """Commit uncertainty before dispatch so termination cannot turn into a replay."""
    if kind in {'image', 'file'}:
        entries = [(room, os.fspath(value)) for room, value in entries]
    if (not entries or any(not name.strip() or not value.strip() for name, value in entries)
            or (kind == 'mention' and not (mention or '').strip())
            or (request_id is not None and not request_id.strip()) or not math.isfinite(interval)):
        raise DeliveryError('invalid_argument', 'Nonempty room, content and valid request parameters are required')
    if kind in {'image', 'file'}:
        files = [Path(value) for _, value in entries]
        if any(not p.is_absolute() for p in files):
            raise DeliveryError('invalid_argument', 'Use absolute attachment paths')
        if kind == 'image' and any(p.suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS for p in files):
            raise DeliveryError('invalid_argument', 'Unsupported image format')
        entries = [(room, str(path.resolve())) for (room, _), path in zip(entries, files)]
    if len(set(entries)) != len(entries):
        raise DeliveryError('duplicate_item', 'Duplicate recipients or attachments in a batch')
    if refs and set(refs).difference(room for room, _ in entries):
        raise DeliveryError('invalid_argument', 'Reference supplied for a different room')
    rules = send_policy()
    rooms = {room for room, _ in entries}
    if rules['read_only'] or rooms.intersection(rules['blocked_rooms']) or (
            rules['allowed_rooms'] and rooms.difference(rules['allowed_rooms'])):
        raise DeliveryError('policy_denied', 'Sending to one or more requested rooms is disabled by policy')
    if rules['max_bulk_rooms'] and len(rooms) > rules['max_bulk_rooms']:
        raise DeliveryError('policy_denied', 'Batch exceeds configured room limit')
    if dry_run:
        if kind in {'image', 'file'} and any(not Path(value).is_file() for _, value in entries):
            raise DeliveryError('invalid_argument', 'All attachment files must exist before sending')
        return dict(success=True, dry_run=True, status='inputs_validated', room_verified=False,
                    item_count=len(entries), rooms=list(dict.fromkeys(room for room, _ in entries)))
    payload = json.dumps([kind, entries, mention, refs or {}], sort_keys=True).encode()
    fingerprint = hashlib.sha256(payload).hexdigest()
    key = request_id or 'auto-' + fingerprint
    rows = [dict(room=name, status='not_attempted') for name, _ in entries]
    if kind in {'image', 'file'}:
        for row, (_, value) in zip(rows, entries):
            row['path'] = value
    with operation(), closing(_journal()) as db:
        db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, fingerprint TEXT, updated REAL, result TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS items (digest TEXT PRIMARY KEY, owner TEXT, position INTEGER)')
        prior = db.execute('SELECT fingerprint,updated,result FROM receipts WHERE id=?', (key,)).fetchone()
        if prior:
            digest, updated, saved = prior
            if digest != fingerprint:
                raise DeliveryError('request_id_conflict', 'Request ID already belongs to different content')
            previous = json.loads(saved)
            touched = any(r['status'] in ('confirmed', 'unconfirmed') for r in previous)
            complete = all(r['status'] == 'confirmed' for r in previous)
            if request_id or (touched and (not complete or time.time() - updated < 60)):
                return _summary(key, previous, True)
        if kind in {'image', 'file'} and any(not Path(value).is_file() for _, value in entries):
            raise DeliveryError('invalid_argument', 'All attachment files must exist before sending')

        def save():
            with db:
                db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)',
                           (key, fingerprint, time.time(), json.dumps(rows, ensure_ascii=False)))

        pinned = {}
        for index, ((name, value), row) in enumerate(zip(entries, rows)):
            try:
                # Excludes request ID, batch order and optional window references. A
                # client may retry a partial batch through the single-send tool.
                item = hashlib.sha256(json.dumps([kind, name, value, mention]).encode()).hexdigest()
                claim = db.execute('SELECT r.id,r.updated,r.result,i.position FROM items i '
                                   'JOIN receipts r ON i.owner=r.id WHERE i.digest=?', (item,)).fetchone()
                if claim:
                    owner, updated, history, position = claim
                    previous_rows = json.loads(history)
                    prior_row = previous_rows[position]
                    if prior_row['status'] == 'unconfirmed':
                        raise DeliveryError('previous_send_unconfirmed', f'Check request {owner} before sending again')
                    if prior_row['status'] == 'confirmed' and (
                            time.time() - updated < 60 or (not request_id and
                            any(r['status'] != 'confirmed' for r in previous_rows))):
                        row.update(status='confirmed', confirmation='previous_receipt', previous_request_id=owner)
                        save()
                        continue
                if name not in pinned:
                    pinned[name] = resolve(name, (refs or {}).get(name))
                else:
                    pinned[name].verify()
                room = pinned[name]
                row.update(room_ref=room.ref, status='unconfirmed')
                # The item fence and its pending receipt must commit together.
                db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?)', (item, key, index))
                save()
                action = f'file_{file_method}' if kind == 'file' and file_method != 'auto' else kind
                outcome = dispatch(room, action, value, mention)
                if not (outcome.get('sent') or outcome.get('success')):
                    row['status'] = outcome.get('status', 'unconfirmed')
                    raise DeliveryError(outcome.get('code', 'send_unconfirmed'), outcome.get('error', 'Send not confirmed'))
                row.update(status='confirmed', confirmation=outcome.get('confirmation', 'room_ui'))
            except Exception as error:
                if row['status'] == 'not_attempted' or getattr(error, 'before_send', False):
                    row['status'] = 'failed_before_send'
                row.update(code=error_code(error), error=str(error))
                if row['status'] == 'unconfirmed':
                    row['code'] = 'send_unconfirmed'
                save()
                break
            save()
            if interval and index + 1 < len(rows):
                time.sleep(max(.3, interval))
        return _summary(key, rows)


def receipt(request_id, index=None, observed=None):
    with operation(), closing(_journal()) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipts'").fetchone():
            raise DeliveryError('request_not_found', 'No recorded deliveries')
        found = db.execute('SELECT result FROM receipts WHERE id=?', (request_id,)).fetchone()
        if not found:
            raise DeliveryError('request_not_found', 'Unknown request ID')
        rows = json.loads(found[0])
        if index is not None or observed is not None:
            if (index is None or not 0 <= index < len(rows) or rows[index]['status'] != 'unconfirmed'
                    or observed not in ('sent', 'not_sent')):
                raise DeliveryError('invalid_argument', 'Select an unconfirmed item and its observed outcome')
            if observed == 'sent':
                rows[index].update(status='confirmed', confirmation='user_verified')
                rows[index].pop('error', None)
                rows[index].pop('code', None)
            else:
                rows[index].update(status='verified_not_sent', code='verified_not_sent', error='User verified no send')
            with db:
                db.execute('UPDATE receipts SET result=?,updated=? WHERE id=?',
                           (json.dumps(rows, ensure_ascii=False), time.time(), request_id))
        return dict(request_id=request_id, results=rows)
