"""Unit tests for kakao_mcp.parser — no Win32 dependency needed."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kakao_mcp.parser import parse_chat_text, extract_urls_from_messages


SAMPLE_CLIPBOARD = """\
[TestRoom] [대화상대 3명]
--------------- 2025년 2월 27일 목요일 ---------------
[김철수] [오전 10:30] 안녕하세요
[이영희] [오전 10:31] 네 안녕하세요!
https://example.com 이거 보세요
[김철수] [오후 2:15] 사진
[이영희] [오후 2:16] 감사합니다
"""


def test_parse_header():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert result["room_name"] == "TestRoom"
    assert result["member_count"] == 3


def test_parse_messages_count():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert len(result["messages"]) == 4


def test_parse_message_sender():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert result["messages"][0]["sender"] == "김철수"
    assert result["messages"][1]["sender"] == "이영희"


def test_parse_time():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert result["messages"][0]["time"] == "오전 10:30"
    assert result["messages"][2]["time"] == "오후 2:15"


def test_parse_photo_detection():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert result["messages"][2]["is_photo"] is True
    assert result["messages"][0]["is_photo"] is False


def test_multiline_message():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    # Message at index 1 has a continuation line with the URL
    assert "https://example.com" in result["messages"][1]["text"]
    assert "이거 보세요" in result["messages"][1]["text"]


def test_extract_urls():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    urls = extract_urls_from_messages(result["messages"])
    assert len(urls) == 1
    assert urls[0]["url"] == "https://example.com"
    assert urls[0]["sender"] == "이영희"


def test_date_separator():
    result = parse_chat_text(SAMPLE_CLIPBOARD)
    assert len(result["dates"]) == 1
    assert "2025년" in result["dates"][0]
    assert "목요일" in result["dates"][0]


def test_empty_input():
    result = parse_chat_text("")
    assert result["messages"] == []
    assert result["room_name"] is None
    assert result["member_count"] is None


def test_none_like_input():
    result = parse_chat_text("   \n  \n  ")
    assert result["messages"] == []


def test_video_detection():
    text = "[TestRoom] [대화상대 2명]\n[홍길동] [오전 9:00] 동영상"
    result = parse_chat_text(text)
    assert result["messages"][0]["is_video"] is True


def test_file_detection():
    text = "[TestRoom] [대화상대 2명]\n[홍길동] [오전 9:00] 파일:report.xlsx"
    result = parse_chat_text(text)
    assert result["messages"][0]["is_file"] is True


def test_multiple_urls_in_one_message():
    text = "[Room] [대화상대 2명]\n[A] [오전 10:00] https://a.com 그리고 https://b.com"
    result = parse_chat_text(text)
    assert len(result["messages"][0]["urls"]) == 2


def test_no_header():
    text = "[김철수] [오전 10:30] 안녕"
    result = parse_chat_text(text)
    assert result["room_name"] is None
    assert result["member_count"] is None
    assert len(result["messages"]) == 1
    assert result["messages"][0]["text"] == "안녕"


def test_bare_date_separator():
    """Clipboard from Ctrl+A copy has bare dates without dashes."""
    text = "2026년 2월 28일 토요일\r\n[김철수] [오후 4:48] 테스트 메시지"
    result = parse_chat_text(text)
    assert len(result["dates"]) == 1
    assert "2026년" in result["dates"][0]
    assert "토요일" in result["dates"][0]
    assert len(result["messages"]) == 1
    assert result["messages"][0]["sender"] == "김철수"
    assert result["messages"][0]["text"] == "테스트 메시지"


def test_bare_date_not_appended_to_message():
    """Bare date line should not be treated as continuation of previous message."""
    text = (
        "[김철수] [오전 10:30] 안녕\r\n"
        "2026년 3월 1일 일요일\r\n"
        "[이영희] [오전 9:00] 좋은 아침"
    )
    result = parse_chat_text(text)
    assert len(result["messages"]) == 2
    assert result["messages"][0]["text"] == "안녕"
    assert result["messages"][1]["text"] == "좋은 아침"


def test_file_detection_variants():
    text = (
        "[Room] [대화상대 2명]\n"
        "[A] [오전 9:00] 파일:report.xlsx\n"
        "[A] [오전 9:01] 파일: quarterly summary.pdf\n"
        "[A] [오전 9:02] 파일: budget (final).xlsx (1.2MB)\n"
        "[A] [오전 9:03] 파일: \n"
    )
    msgs = parse_chat_text(text)["messages"]
    assert [m["is_file"] for m in msgs] == [True, True, True, False]


def test_korean_and_english_notices_not_glued():
    text = (
        "Thursday, February 27, 2025\n"
        "[Alice] [10:00 AM] hello\n"
        "You invited Bob.\n"
        "Alice joined the chatroom.\n"
        "Carol left.\n"
        "[Bob] [10:01 AM] hi\n"
        "삭제된 메시지입니다.\n"
        "채팅방 관리자가 메시지를 가렸습니다.\n"
    )
    result = parse_chat_text(text)
    assert [m["text"] for m in result["messages"]] == ["hello", "hi"]
    assert [n["text"] for n in result["notices"]] == [
        "You invited Bob.",
        "Alice joined the chatroom.",
        "Carol left.",
        "삭제된 메시지입니다.",
        "채팅방 관리자가 메시지를 가렸습니다.",
    ]


def test_urls_preserve_balanced_parentheses():
    from kakao_mcp.parser import find_urls

    assert find_urls("참고 https://en.wikipedia.org/wiki/Foo_(bar).") == [
        "https://en.wikipedia.org/wiki/Foo_(bar)"
    ]
    assert find_urls("(see https://en.wikipedia.org/wiki/Foo_(bar))") == [
        "https://en.wikipedia.org/wiki/Foo_(bar)"
    ]
    assert find_urls("https://example.com/a_(1)_(2), 확인") == [
        "https://example.com/a_(1)_(2)"
    ]
    assert find_urls("(https://example.com/c)") == ["https://example.com/c"]

