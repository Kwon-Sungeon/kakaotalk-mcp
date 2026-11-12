"""Parse KakaoTalk clipboard text into structured messages."""
import re
from datetime import date
from typing import Dict, List, Optional


# Message line: [Sender] [time] Text. KakaoTalk writes the time in the UI
# language, so three styles exist. One transcript uses a single style; the
# style is detected once so that a typed line such as "[공지] [14:00] 회의"
# inside a Korean transcript stays part of the message it belongs to.
_MESSAGE_STYLES = {
    'ko': re.compile(r'^\[(.+?)\]\s*\[(오전|오후)\s*(\d{1,2}:\d{2})\]\s*(.*)'),
    'en': re.compile(r'^\[(.+?)\]\s*\[(\d{1,2}:\d{2})\s*([AaPp][Mm])\]\s*(.*)'),
    '24h': re.compile(r'^\[(.+?)\]\s*\[(\d{1,2}:\d{2})\]\s*(.*)'),
}

# Kept for callers which imported the original Korean pattern.
MESSAGE_PATTERN = _MESSAGE_STYLES['ko']

# Date separator:
#   "--------------- 2025년 2월 27일 목요일 ---------------"  (with dashes, from full export)
#   "2026년 2월 28일 토요일"  (bare date, from Ctrl+A clipboard copy)
#   "Thursday, February 27, 2025"  (English UI)
DATE_SEPARATOR_PATTERN = re.compile(
    r'^-*\s*(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*\S+요일'
    r'|(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})\s*-*$',
    re.MULTILINE,
)
_KOREAN_DATE = re.compile(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일')
_ENGLISH_DATE = re.compile(r'([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})$')
_MONTHS = {name: index + 1 for index, name in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'])}

# Chat header: [RoomName] [대화상대 N명] or [RoomName] [대화상대 N]
HEADER_PATTERN = re.compile(
    r'^\[(.+?)\]\s*\[대화상대\s*(\d+)',
)

# Lines KakaoTalk writes by itself (join, leave, invite ...). They carry no
# sender and must not be glued onto the previous person's message.
NOTICE_PATTERN = re.compile(
    r'^(?:.+님이 (?:들어왔습니다|나갔습니다)\.'
    r'|.+님이 .+님을 초대했습니다\.'
    r'|.+님을 내보냈습니다\.'
    r'|채팅방 관리자가 메시지를 가렸습니다\.)$'
)

# URL extraction
URL_PATTERN = re.compile(
    r'https?://[^\s<>"\')\]]+',
    re.IGNORECASE,
)
_URL_TRAILING = '.,;:!?'
_PHOTO_PATTERN = re.compile(r'^사진(?: \d+장)?$')


def _clock_24h(meridiem: Optional[str], clock: str) -> Optional[str]:
    hour, minute = (int(part) for part in clock.split(':'))
    marker = (meridiem or '').lower()
    if marker and not 1 <= hour <= 12:
        return None
    if marker in ('오후', 'pm') and hour != 12:
        hour += 12
    elif marker in ('오전', 'am') and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def date_to_iso(date_text: Optional[str]) -> Optional[str]:
    """'2026년 2월 28일 토요일' or 'Saturday, February 28, 2026' -> '2026-02-28'."""
    if not date_text:
        return None
    match = _KOREAN_DATE.search(date_text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        match = _ENGLISH_DATE.search(date_text.strip())
        if not match or match.group(1).lower() not in _MONTHS:
            return None
        year, month, day = int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def find_urls(text: str) -> List[str]:
    """URLs in text, without the sentence punctuation that follows them."""
    urls = []
    for url in URL_PATTERN.findall(text or ''):
        url = url.rstrip(_URL_TRAILING)
        if url.partition('://')[2]:
            urls.append(url)
    return urls


def _detect_style(lines: List[str]) -> Optional[str]:
    for line in lines:
        for style, pattern in _MESSAGE_STYLES.items():
            if pattern.match(line) and not HEADER_PATTERN.match(line):
                return style
    return None


def _split_message(style: str, line: str):
    match = _MESSAGE_STYLES[style].match(line)
    if not match:
        return None
    if style == 'ko':
        sender, meridiem, clock, text = match.groups()
        label = f'{meridiem} {clock}'
    elif style == 'en':
        sender, clock, meridiem, text = match.groups()
        label = f'{clock} {meridiem.upper()}'
    else:
        sender, clock, text = match.groups()
        meridiem, label = None, clock
    return sender, label, _clock_24h(meridiem, clock), text


def parse_chat_text(raw_text: str) -> Dict:
    """Parse raw clipboard text from KakaoTalk chat into structured data.

    Returns:
        Dict with keys:
            - room_name: str or None
            - member_count: int or None
            - messages: List[Dict] each with date, sender, time, text, time_24h,
              timestamp, is_photo, is_video, is_file, is_emoticon, urls
            - dates: List[str] date separators found
            - notices: List[Dict] join/leave/invite lines written by KakaoTalk
    """
    result: Dict = {
        "room_name": None,
        "member_count": None,
        "messages": [],
        "dates": [],
        "notices": [],
    }

    if not raw_text or not raw_text.strip():
        return result

    # Extract header
    header_match = HEADER_PATTERN.search(raw_text)
    if header_match:
        result["room_name"] = header_match.group(1)
        result["member_count"] = int(header_match.group(2))

    # Extract date separators
    for date_match in DATE_SEPARATOR_PATTERN.finditer(raw_text):
        result["dates"].append(date_match.group(1))

    # Extract messages
    lines = [line.strip() for line in raw_text.split('\n')]
    style = _detect_style(lines)
    current_message = None
    date = None

    for stripped in lines:
        # Skip empty lines and date separators
        separator = DATE_SEPARATOR_PATTERN.match(stripped)
        if separator:
            date = separator.group(1)
            continue
        if not stripped:
            continue

        # Skip header line
        if HEADER_PATTERN.match(stripped):
            continue

        parts = _split_message(style, stripped) if style else None
        if parts:
            # Finalize previous message
            if current_message is not None:
                _finalize_message(current_message, result["messages"])

            sender, label, clock, text = parts
            current_message = {
                "date": date,
                "sender": sender,
                "time": label,
                "text": text,
                "time_24h": clock,
            }
        elif NOTICE_PATTERN.match(stripped):
            if current_message is not None:
                _finalize_message(current_message, result["messages"])
                current_message = None
            result["notices"].append({"date": date, "text": stripped})
        elif current_message is not None:
            # Continuation line of previous message
            current_message["text"] += "\n" + stripped

    # Finalize last message
    if current_message is not None:
        _finalize_message(current_message, result["messages"])

    return result


def _finalize_message(msg: Dict, messages_list: List[Dict]):
    """Add derived fields (is_photo, urls, etc.) and append to list."""
    text = msg["text"].strip()
    day = date_to_iso(msg.get("date"))
    clock = msg.get("time_24h")
    msg["timestamp"] = f'{day}T{clock}' if day and clock else None
    msg["is_photo"] = bool(_PHOTO_PATTERN.match(text))
    msg["is_video"] = text == "동영상"
    msg["is_file"] = text.startswith("파일:")
    msg["is_emoticon"] = text == "이모티콘"
    msg["urls"] = find_urls(msg["text"])
    messages_list.append(msg)


_TIME_LABEL = re.compile(
    r'^(?:(오전|오후)\s*(\d{1,2}:\d{2})|(\d{1,2}:\d{2})\s*([AaPp][Mm])|(\d{1,2}:\d{2}))$'
)


def enrich_messages(messages: List[Dict]) -> List[Dict]:
    """Add time_24h and timestamp to messages read without this parser (Qt UIA)."""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if "text" in msg:
            msg["urls"] = find_urls(msg["text"])
        if msg.get("timestamp"):
            continue
        clock = msg.get("time_24h")
        if not clock:
            match = _TIME_LABEL.match(str(msg.get("time") or "").strip())
            if match:
                ko_meridiem, ko_clock, en_clock, en_meridiem, plain = match.groups()
                clock = _clock_24h(ko_meridiem or en_meridiem, ko_clock or en_clock or plain)
        day = date_to_iso(msg.get("date"))
        msg["time_24h"] = clock
        msg["timestamp"] = f'{day}T{clock}' if day and clock else None
    return messages


def extract_urls_from_messages(messages: List[Dict], unique: bool = False) -> List[Dict]:
    """Extract all URLs from parsed messages.

    Args:
        unique: keep only the first occurrence of each URL.

    Returns:
        List of dicts with url, sender, time, date and timestamp.
    """
    urls = []
    seen = set()
    for msg in messages:
        for url in msg.get("urls") or []:
            if unique and url in seen:
                continue
            seen.add(url)
            urls.append({
                "url": url,
                "sender": msg.get("sender"),
                "time": msg.get("time"),
                "date": msg.get("date"),
                "timestamp": msg.get("timestamp"),
            })
    return urls


def filter_messages(messages: List[Dict], sender: Optional[str] = None,
                    contains: Optional[str] = None) -> List[Dict]:
    """Keep messages from an exact sender and/or containing a phrase (case-insensitive)."""
    phrase = contains.lower() if contains else None
    return [
        msg for msg in messages
        if (not sender or msg.get("sender") == sender)
        and (not phrase or phrase in (msg.get("text") or "").lower())
    ]
