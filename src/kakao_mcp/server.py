#!/usr/bin/env python
"""MCP Server for KakaoTalk PC automation via Win32 API."""
import os
from typing import Dict, Optional

from mcp.server.fastmcp import FastMCP

from kakao_mcp import controller
from kakao_mcp import parser
from kakao_mcp import config

app = FastMCP(
    "kakao-mcp-server",
    instructions="MCP Server for KakaoTalk PC automation via Win32 API",
)


@app.tool()
def kakao_health_check() -> Dict:
    """Check if KakaoTalk PC is currently running.
    Returns status, window handle, and process ID."""
    try:
        status = controller.is_kakaotalk_running()
        if status["running"]:
            return {
                "message": "KakaoTalk is running",
                "running": True,
                "hwnd": status["hwnd"],
                "pid": status["pid"],
            }
        return {"message": "KakaoTalk is not running", "running": False}
    except Exception as e:
        return {"error": f"Health check failed: {e}"}


@app.tool()
def kakao_list_open_rooms() -> Dict:
    """List all currently open KakaoTalk chat room windows.
    Returns a list of open chat rooms with their window titles."""
    try:
        status = controller.is_kakaotalk_running()
        if not status["running"]:
            return {"error": "KakaoTalk is not running"}
        rooms = controller.list_chat_windows()
        return {
            "message": f"Found {len(rooms)} open chat room(s)",
            "rooms": [{"title": r["title"], "hwnd": r["hwnd"]} for r in rooms],
        }
    except Exception as e:
        return {"error": f"Failed to list rooms: {e}"}


@app.tool()
def kakao_open_room(room_name: str) -> Dict:
    """Open or bring to front a KakaoTalk chat room by name.
    If the chat window is already open, brings it to foreground.
    If not, searches for the room in KakaoTalk's search bar.

    Args:
        room_name: The name of the chat room or person to open.
    """
    try:
        hwnd = controller.find_chat_window(room_name)
        if hwnd:
            controller.bring_window_to_front(hwnd)
            return {"message": f"Chat room '{room_name}' brought to foreground", "hwnd": hwnd}
        result = controller.search_and_open_room(room_name)
        if result["success"]:
            return {"message": result["message"], "hwnd": result.get("hwnd")}
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to open room '{room_name}': {e}"}


@app.tool()
def kakao_send_message(room_name: str, message: str) -> Dict:
    """Send a text message to a KakaoTalk chat room.
    The chat room window must already be open.

    Args:
        room_name: Exact title of the chat room window.
        message: The text message to send.
    """
    try:
        if not message.strip():
            return {"error": "Message cannot be empty"}
        result = controller.send_message_to_room(room_name, message)
        if result["success"]:
            return {"message": result["message"]}
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to send message: {e}"}


@app.tool()
def kakao_send_bulk(room_names: list[str], message: str, interval_sec: float = 0.5) -> Dict:
    """Send the same message to multiple KakaoTalk chat rooms at once.
    Opens each room if not already open and sends the message sequentially.
    Maintains a safe interval between rooms to ensure reliability.

    Args:
        room_names: List of chat room names or person names to send to.
        message: The text message to send to all rooms.
        interval_sec: Seconds to wait between rooms (default 0.5, minimum 0.3).
    """
    try:
        if not room_names:
            return {"error": "room_names cannot be empty"}
        if not message.strip():
            return {"error": "Message cannot be empty"}
        result = controller.send_bulk_messages(room_names, message, interval_sec)
        if result["success"]:
            return {"message": result["message"], "results": result["results"]}
        return {"error": result.get("message", "Failed to send bulk messages"), "results": result.get("results", [])}
    except Exception as e:
        return {"error": f"Failed to send bulk messages: {e}"}


@app.tool()
def kakao_send_image(room_name: str, image_paths: list[str]) -> Dict:
    """Send image file(s) to a KakaoTalk chat room.
    Copies the image to clipboard using file-drop format and pastes into the chat.
    KakaoTalk shows a confirmation dialog which is automatically accepted.
    The chat room window must already be open.
    NOTE: This briefly brings the chat window to the foreground.

    Args:
        room_name: Exact title of the chat room window.
        image_paths: List of absolute file paths to images (JPG, PNG, GIF, BMP, WebP).
    """
    try:
        if not image_paths:
            return {"error": "image_paths cannot be empty"}

        for path in image_paths:
            if not os.path.isfile(os.path.abspath(path)):
                return {"error": f"Image file not found: {path}"}

        if len(image_paths) == 1:
            result = controller.send_image_to_room(room_name, image_paths[0])
            if result["success"]:
                return {"message": result["message"]}
            return {"error": result["error"]}
        else:
            result = controller.send_images_to_room(room_name, image_paths)
            if result["success"]:
                return {"message": result["message"], "results": result["results"]}
            return {"error": result.get("message", "Failed to send images"), "results": result.get("results", [])}
    except Exception as e:
        return {"error": f"Failed to send image: {e}"}


@app.tool()
def kakao_read_messages(room_name: str, max_messages: int = 50) -> Dict:
    """Read recent messages from a KakaoTalk chat room.
    Uses clipboard-based reading (Ctrl+A, Ctrl+C on the chat list).
    NOTE: This briefly brings the chat window to the foreground.

    Args:
        room_name: Exact title of the chat room window.
        max_messages: Maximum number of recent messages to return (default 50).
    """
    try:
        result = controller.read_chat_messages(room_name)
        if not result["success"]:
            return {"error": result["error"]}
        parsed = parser.parse_chat_text(result["raw_text"])
        messages = parsed["messages"]
        if len(messages) > max_messages:
            messages = messages[-max_messages:]
        return {
            "message": f"Read {len(messages)} messages from '{room_name}'",
            "room_name": parsed["room_name"],
            "member_count": parsed["member_count"],
            "messages": messages,
        }
    except Exception as e:
        return {"error": f"Failed to read messages: {e}"}


@app.tool()
def kakao_extract_links(room_name: str) -> Dict:
    """Extract all URLs/links from messages in a KakaoTalk chat room.
    Reads the chat first, then extracts URLs from all messages.

    Args:
        room_name: Exact title of the chat room window.
    """
    try:
        result = controller.read_chat_messages(room_name)
        if not result["success"]:
            return {"error": result["error"]}
        parsed = parser.parse_chat_text(result["raw_text"])
        urls = parser.extract_urls_from_messages(parsed["messages"])
        return {
            "message": f"Found {len(urls)} URL(s) in '{room_name}'",
            "links": urls,
        }
    except Exception as e:
        return {"error": f"Failed to extract links: {e}"}


@app.tool()
def kakao_send_mention(room_name: str, mention_name: str, message: str) -> Dict:
    """Send a message with @mention to a KakaoTalk chat room.
    Types '@' to activate the mention popup, selects the target user,
    then sends the message. The chat room window must already be open.
    NOTE: This briefly brings the chat window to the foreground.

    Args:
        room_name: Exact title of the chat room window.
        mention_name: Display name of the person to mention (e.g. '홍길동').
        message: The text message to send after the mention.
    """
    try:
        if not mention_name.strip():
            return {"error": "Mention name cannot be empty"}
        if not message.strip():
            return {"error": "Message cannot be empty"}
        result = controller.send_mention_message(room_name, mention_name, message)
        if result["success"]:
            return {"message": result["message"]}
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to send mention message: {e}"}


@app.tool()
def kakao_download_images(
    room_name: str,
    output_dir: Optional[str] = None,
    max_images: int = 10,
) -> Dict:
    """Download recent images from KakaoTalk's local cache.
    Images are sorted by modification time (newest first).
    Note: Cache is global, not per-room — images are the most recently cached ones.

    Args:
        room_name: Chat room name (for context/logging).
        output_dir: Directory to save images. Defaults to Documents/KakaoMCP_Images.
        max_images: Maximum number of images to download (default 10).
    """
    try:
        if output_dir is None:
            output_dir = config.DEFAULT_IMAGE_OUTPUT_DIR
        os.makedirs(output_dir, exist_ok=True)
        result = controller.download_recent_images(room_name, output_dir, max_images)
        return result
    except Exception as e:
        return {"error": f"Failed to download images: {e}"}


@app.tool()
def kakao_start_monitor(
    room_name: str,
    keywords: list[str],
    poll_interval_sec: float = 5.0,
) -> Dict:
    """Start monitoring a KakaoTalk chat room for keyword matches.
    Runs in background, checking for new messages at the specified interval.
    Use kakao_get_monitor_events() to retrieve detected keyword matches.
    NOTE: The chat room window must be open. Polling brings it to foreground briefly.

    Args:
        room_name: Exact title of the chat room window to monitor.
        keywords: List of keywords to watch for (case-insensitive).
        poll_interval_sec: Seconds between each poll (minimum 3, default 5).
    """
    try:
        if not keywords:
            return {"error": "Keywords list cannot be empty"}
        result = controller._chat_monitor.start(room_name, keywords, poll_interval_sec)
        if result["success"]:
            return {"message": result["message"]}
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to start monitor: {e}"}


@app.tool()
def kakao_stop_monitor() -> Dict:
    """Stop the running chat room monitor."""
    try:
        result = controller._chat_monitor.stop()
        if result["success"]:
            return {"message": result["message"]}
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to stop monitor: {e}"}


@app.tool()
def kakao_get_monitor_events() -> Dict:
    """Get pending keyword match events from the chat monitor.
    Returns events detected since last call. Each event includes:
    - keyword: the matched keyword
    - trigger_message: the message that matched (sender, time, text)
    - recent_context: last 10 messages for context understanding
    - room_name: the monitored chat room

    Call this periodically while monitor is running to check for matches.
    """
    try:
        is_running = controller._chat_monitor.is_running
        events = controller._chat_monitor.get_events()
        return {
            "monitoring": is_running,
            "event_count": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": f"Failed to get monitor events: {e}"}


def main():
    app.run(transport="stdio")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Participant tools (added)
# ---------------------------------------------------------------------------
from kakao_mcp import participants as _participants


@app.tool()
def kakao_list_participants(room_name: str) -> Dict:
    """List participants of a group chat room by opening its side drawer (≡).
    NOTE: Briefly brings the chat window to foreground and opens the drawer.

    Args:
        room_name: Exact title of the group chat window (must be open).
    """
    try:
        return _participants.list_participants(room_name)
    except Exception as e:
        return {"error": f"Failed to list participants: {e}"}


@app.tool()
def kakao_dm_member(
    room_name: str,
    member_name: str,
    message: str,
    add_friend: bool = True,
    close_after: bool = False,
) -> Dict:
    """Send a 1:1 direct message to a member of a group chat.
    Tries friend-search first; falls back to opening the member's profile
    from the group chat drawer, optionally clicking '친구 추가', then '1:1 채팅'.

    Args:
        room_name: Group chat window title the member belongs to.
        member_name: Display name of the member.
        message: Text to send.
        add_friend: Click '친구 추가' in profile popup if present (default True).
        close_after: Close the 1:1 window after sending (default False).
    """
    try:
        if not message.strip():
            return {"error": "Message cannot be empty"}
        return _participants.dm_member(room_name, member_name, message,
                                       add_friend, close_after)
    except Exception as e:
        return {"error": f"Failed to DM member: {e}"}


@app.tool()
def kakao_open_member_profile(room_name: str, member_name: str) -> Dict:
    """Open a member's profile popup from the group chat drawer (debug/manual step).

    Args:
        room_name: Group chat window title.
        member_name: Display name of the member.
    """
    try:
        return _participants.open_member_profile(room_name, member_name)
    except Exception as e:
        return {"error": f"Failed to open profile: {e}"}


@app.tool()
def kakao_inspect_ui(room_name: str = "", max_depth: int = 6) -> Dict:
    """Dump the Win32 + UIA control tree of a chat window (or all KakaoTalk
    windows if room_name is empty). Use to calibrate button names and offsets
    for participant tools.

    Args:
        room_name: Chat window title, or empty for all top-level EVA windows.
        max_depth: Tree depth limit (default 6).
    """
    try:
        return _participants.dump_ui_tree(room_name, max_depth)
    except Exception as e:
        return {"error": f"Failed to inspect UI: {e}"}


@app.tool()
def kakao_close_popups() -> Dict:
    """Press ESC to close any open popup/drawer (cleanup helper)."""
    try:
        _participants.close_popups()
        return {"message": "Sent ESC to close popups"}
    except Exception as e:
        return {"error": f"Failed: {e}"}


# ---------------------------------------------------------------------------
# Vision (template-matching) tools (added)
# ---------------------------------------------------------------------------
from kakao_mcp import vision as _vision


@app.tool()
def kakao_capture_window(room_name: str, save_path: str = "") -> Dict:
    """Capture a chat window screenshot to a PNG file (for template creation).
    Coordinates inside the saved image are window-relative pixels.

    Args:
        room_name: Exact title of the chat window.
        save_path: Where to save the PNG (default: project debug_capture.png).
    """
    try:
        return _vision.capture_window_to_file(room_name, save_path)
    except Exception as e:
        return {"error": f"Capture failed: {e}"}


@app.tool()
def kakao_save_template(room_name: str, left: int, top: int,
                        right: int, bottom: int, template_name: str) -> Dict:
    """Crop a region from the live chat window and save it as a click template.
    Use kakao_capture_window first to determine the region coordinates.

    Args:
        room_name: Exact title of the chat window.
        left/top/right/bottom: Region in window-relative pixels.
        template_name: Template name (e.g. 'member_icon').
    """
    try:
        return _vision.save_template(room_name, left, top, right, bottom,
                                     template_name)
    except Exception as e:
        return {"error": f"Save template failed: {e}"}


@app.tool()
def kakao_click_template(room_name: str, template_name: str,
                         threshold: float = 0.75,
                         double_click: bool = False,
                         offset_x: int = 0, offset_y: int = 0) -> Dict:
    """Find a saved template image inside the chat window and click it.
    Multi-scale matching; robust to small UI/DPI changes.

    Args:
        room_name: Exact title of the chat window.
        template_name: Name of a template saved via kakao_save_template.
        threshold: Min match score 0~1 (default 0.75).
        double_click: Double-click instead of single click.
        offset_x/offset_y: Pixel offset from match center.
    """
    try:
        return _vision.click_template(room_name, template_name, threshold,
                                      double_click, offset_x, offset_y)
    except Exception as e:
        return {"error": f"Click template failed: {e}"}


@app.tool()
def kakao_open_member_list(room_name: str, threshold: float = 0.7) -> Dict:
    """Open the participant list popup by clicking the person-count icon
    (👤N) under the chat room title, located via the 'member_icon' template.
    Returns any new popup window that appeared.

    Args:
        room_name: Exact title of the group chat window.
        threshold: Min match score (default 0.7).
    """
    try:
        return _vision.open_member_list(room_name, threshold)
    except Exception as e:
        return {"error": f"Open member list failed: {e}"}


@app.tool()
def kakao_list_templates() -> Dict:
    """List saved click templates."""
    try:
        return {"templates": _vision.list_templates()}
    except Exception as e:
        return {"error": f"{e}"}


@app.tool()
def kakao_capture_hwnd(hwnd: int, save_path: str) -> Dict:
    """Capture ANY window by hwnd to a PNG file (e.g. member list popup,
    profile popup). Use the hwnd returned by kakao_open_member_list.

    Args:
        hwnd: Window handle.
        save_path: Where to save the PNG.
    """
    try:
        return _vision.capture_hwnd_to_file(hwnd, save_path)
    except Exception as e:
        return {"error": f"Capture failed: {e}"}


@app.tool()
def kakao_click_at(hwnd: int, x: int, y: int,
                   double_click: bool = False) -> Dict:
    """Click at window-relative pixel coordinates of a window (by hwnd).
    Use with kakao_capture_hwnd: read the image, pick the target pixel,
    then click it (e.g. double-click a member row in the list popup).

    Args:
        hwnd: Window handle.
        x, y: Window-relative pixel coordinates.
        double_click: Double-click instead of single (default False).
    """
    try:
        return _vision.click_at(hwnd, x, y, double_click)
    except Exception as e:
        return {"error": f"Click failed: {e}"}


# ---------------------------------------------------------------------------
# OCR tools (Windows built-in OCR, added)
# ---------------------------------------------------------------------------
from kakao_mcp import ocr as _ocr


@app.tool()
def kakao_ocr_window(room_name: str = "", hwnd: int = 0,
                     lang: str = "ko") -> Dict:
    """Read all text from a window using Windows built-in OCR.
    Returns lines with window-relative bounding rects and centers.
    Pass either room_name (chat window title) or hwnd (e.g. a popup).

    Args:
        room_name: Chat window title (used if hwnd is 0).
        hwnd: Window handle (takes priority over room_name).
        lang: OCR language (default 'ko').
    """
    try:
        if hwnd:
            return _ocr.ocr_hwnd(hwnd, lang=lang)
        if room_name:
            return _ocr.ocr_room(room_name, lang=lang)
        return {"error": "Provide room_name or hwnd"}
    except Exception as e:
        return {"error": f"OCR failed: {e}"}


@app.tool()
def kakao_click_text(text: str, room_name: str = "", hwnd: int = 0,
                     double_click: bool = False, lang: str = "ko") -> Dict:
    """Find text in a window via OCR and click its center.
    Partial and whitespace-insensitive matching. Use for clicking a member
    name in the participant popup or buttons like '1:1 채팅' in profiles.

    Args:
        text: Text to find (e.g. member name, '1:1 채팅').
        room_name: Chat window title (used if hwnd is 0).
        hwnd: Window handle (takes priority).
        double_click: Double-click instead of single click.
        lang: OCR language (default 'ko').
    """
    try:
        if not hwnd:
            if not room_name:
                return {"error": "Provide room_name or hwnd"}
            h = None
            from kakao_mcp.controller import find_chat_window
            h = find_chat_window(room_name)
            if h is None:
                return {"error": f"Chat window '{room_name}' not found"}
            hwnd = h
        return _ocr.click_text(hwnd, text, double_click)
    except Exception as e:
        return {"error": f"Click text failed: {e}"}


# ---------------------------------------------------------------------------
# Group DM (fully server-side member iteration, added)
# ---------------------------------------------------------------------------
from kakao_mcp import group_dm as _group_dm


@app.tool()
def kakao_open_member_popup(room_name: str) -> Dict:
    """Open the member-list popup of a group chat and return its window handle
    (server-side; clicks the person-count icon via Win32).

    Args:
        room_name: Exact title of the group chat window.
    """
    try:
        return _group_dm.open_member_popup(room_name)
    except Exception as e:
        return {"error": f"Open member popup failed: {e}"}


@app.tool()
def kakao_dm_all_members(room_name: str, message: str,
                         close_after: bool = True,
                         interval_sec: float = 4.0,
                         max_members: int = 50,
                         skip_names: list[str] = None) -> Dict:
    """Send a 1:1 message to EVERY member of a group chat, fully server-side
    (no computer-use). Opens the member popup, OCRs it, double-clicks each
    member to open their 1:1 chat, sends the message, and closes the window.

    Args:
        room_name: Group chat window title (must be open).
        message: Text to send to each member.
        close_after: Close each 1:1 window after sending (default True).
        interval_sec: Seconds between members (min 3, default 4).
        max_members: Safety cap on how many to send (default 50).
        skip_names: OCR names to skip (e.g. already-sent members).
    """
    try:
        if not message.strip():
            return {"error": "Message cannot be empty"}
        return _group_dm.dm_all_members(room_name, message, close_after,
                                        interval_sec, max_members, skip_names)
    except Exception as e:
        return {"error": f"DM all members failed: {e}"}


@app.tool()
def kakao_dm_all_members_kbd(room_name: str, message: str,
                             member_count: int = 0,
                             start_index: int = 0,
                             close_after: bool = True,
                             interval_sec: float = 3.0,
                             max_send: int = 50,
                             skip_titles: list[str] = None) -> Dict:
    """Send a 1:1 message to every member of a group chat using ARROW-KEY
    navigation (no click coordinates — avoids mis-clicking '멤버 초대하기').
    For member index i: opens the popup, focuses the list, presses Home then
    Down i times to highlight the i-th member, Enter to open the 1:1 chat,
    sends, and closes. Index-based, so the trailing invite row is never hit.

    Args:
        room_name: Group chat window title (must be open).
        message: Text to send to each member.
        member_count: Number of members (0 = auto-read from header).
        start_index: Member index to start from (for resuming in batches).
        close_after: Close each 1:1 window after sending (default True).
        interval_sec: Seconds between members (min 3, default 3).
        max_send: Cap on how many sends in this call (default 50).
        skip_titles: Exact 1:1 window titles to skip (already-sent members).
    """
    try:
        if not message.strip():
            return {"error": "Message cannot be empty"}
        return _group_dm.dm_all_members_kbd(
            room_name, message, member_count, start_index,
            close_after, interval_sec, max_send, skip_titles)
    except Exception as e:
        return {"error": f"DM all (kbd) failed: {e}"}
