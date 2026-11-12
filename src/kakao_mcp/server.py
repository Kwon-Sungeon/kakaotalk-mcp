#!/usr/bin/env python
"""MCP Server for KakaoTalk PC automation via Windows UI Automation or Win32."""
import os
import time
import sys
from typing import Dict, Literal, Optional

from mcp.server.fastmcp import FastMCP

from kakao_mcp import backend as controller
from kakao_mcp import parser
from kakao_mcp import config, __version__

ReadMode = Literal["auto", "background", "complete"]

app = FastMCP(
    "kakao-mcp-server",
    instructions=("Use kakao_open_room with the exact title for a closed room; list_open_rooms only lists "
                  "open windows. Use kakao_send_file for documents and kakao_send_image for photos. "
                  "If sending is unconfirmed, report the result; do not retry using computer use."),
)


@app.tool()
def kakao_health_check(diagnostics: bool = False) -> Dict:
    """Check KakaoTalk status and report the MCP version and detected backend.
    diagnostics also probes read-only window discovery and reports its duration."""
    from kakao_mcp import qt_controller, controller as legacy
    report = {"mcp_version": __version__}
    try:
        impl = qt_controller if qt_controller.available() else legacy
        status = impl.is_kakaotalk_running()
        running = status["running"]
        report.update(status, backend=("qt_uia" if impl is qt_controller else "win32") if running else None,
                      message="KakaoTalk is running" if running else "KakaoTalk is not running")
        if diagnostics:
            report["windows_build"] = sys.getwindowsversion().build
            report["python"] = sys.version.split()[0]
            if running:
                import win32api, win32process
                handle = win32api.OpenProcess(0x1000, False, status["pid"])
                try:
                    version = win32api.GetFileVersionInfo(win32process.GetModuleFileNameEx(handle, 0), "\\")
                    ms, ls = version["FileVersionMS"], version["FileVersionLS"]
                    report["kakaotalk_version"] = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
                finally:
                    handle.Close()
                start = time.perf_counter()
                report["open_room_count"] = len(impl.list_chat_windows())
                report["discovery_ms"] = round((time.perf_counter() - start) * 1000)
        return report
    except Exception as error:
        return {**report, "error": f"Health check failed: {error}"}


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
def kakao_open_room(room_name: str, focus: bool = False) -> Dict:
    """Open a KakaoTalk chat room by name. An existing room keeps its position.
    Set focus=true when the user wants to see the room.
    If not, searches for the room in KakaoTalk's search bar.

    Args:
        room_name: The name of the chat room or person to open.
    """
    try:
        hwnd = controller.find_chat_window(room_name)
        if hwnd:
            if focus:
                controller.bring_window_to_front(hwnd)
            return {"message": f"Chat room '{room_name}' is open", "hwnd": hwnd}
        result = controller.search_and_open_room(room_name)
        if result["success"]:
            if focus and result.get("hwnd"):
                controller.bring_window_to_front(result["hwnd"])
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
        return {"error": result.get("error") or result.get("message", "Failed to send bulk messages"), "results": result.get("results", [])}
    except Exception as e:
        return {"error": f"Failed to send bulk messages: {e}"}


@app.tool()
def kakao_send_file(room_name: str, file_paths: list[str]) -> Dict:
    """Attach documents or other local files through an open room's file picker.
    Use absolute paths. Results identify completed items; never retry an uncertain send automatically."""
    from .attachments import send_files
    return send_files(room_name, file_paths)


@app.tool()
def kakao_send_image(room_name: str, image_paths: list[str]) -> Dict:
    """Send image file(s) to a KakaoTalk chat room.
    Copies the image to clipboard as bitmap data and pastes into the chat.
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
            return {"error": result.get("error") or result.get("message", "Failed to send images"), "results": result.get("results", [])}
    except Exception as e:
        return {"error": f"Failed to send image: {e}"}


@app.tool()
def kakao_read_messages(room_name: str, max_messages: Optional[int] = None, read_mode: Optional[ReadMode] = None) -> Dict:
    """Read recent messages from a KakaoTalk chat room.
    Reads locally loaded messages using the selected UI backend.
    With no count, auto prefers the exposed conversation (up to 50). Specify a
    count to fill missing history or metadata using a full copy when needed.
    Use background for exposed messages without input or clipboard, even if partial.
    Use complete for all locally loaded detail; it may activate the room and copy.
    partial indicates that server-only history may not be included.

    Args:
        room_name: Exact title of the chat room window.
        max_messages: Maximum number of recent messages to return (default 50).
    """
    try:
        if max_messages is not None and max_messages < 1:
            return {"error": "max_messages must be positive"}
        result = controller.read_chat_messages(room_name, read_mode, max_messages)
        if not result["success"]:
            return {"error": result["error"]}
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        messages = parsed["messages"]
        max_messages = max_messages or 50
        if len(messages) > max_messages:
            messages = messages[-max_messages:]
        return {
            "message": f"Read {len(messages)} messages from '{room_name}'",
            "room_name": parsed["room_name"],
            "member_count": parsed["member_count"],
            "messages": messages,
            "partial": parsed.get("partial", False),
            "read_method": parsed.get("read_method", "clipboard"),
            "unknown_fields": parsed.get("unknown_fields", []),
            **({"at_end": parsed["at_end"]} if "at_end" in parsed else {}),
            **({"detail_error": parsed["detail_error"]} if "detail_error" in parsed else {}),
        }
    except Exception as e:
        return {"error": f"Failed to read messages: {e}"}


@app.tool()
def kakao_extract_links(room_name: str, read_mode: Optional[ReadMode] = None) -> Dict:
    """Extract all URLs/links from messages in a KakaoTalk chat room.
    Auto and complete request the full locally loaded transcript. Background
    extracts links only from messages available without foreground interaction.

    Args:
        room_name: Exact title of the chat room window.
    """
    try:
        result = controller.read_chat_messages(room_name, read_mode, all_history=True)
        if not result["success"]:
            return {"error": result["error"]}
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        urls = parser.extract_urls_from_messages(parsed["messages"])
        return {
            "message": f"Found {len(urls)} URL(s) in '{room_name}'",
            "links": urls,
            "read_method": parsed.get("read_method", "clipboard"),
            "partial": parsed.get("partial", False),
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
    """Download recent KakaoTalk images.
    Legacy: copy account-wide cached images, sorted newest first.
    Qt saves photos exposed by the named room through its photo viewer.
    scope indicates visible or scrollable loaded room photos; partial is true.

    Args:
        room_name: Exact room title for Qt, or a room label for the legacy cache.
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
    read_mode: Optional[ReadMode] = None,
) -> Dict:
    """Start monitoring a KakaoTalk chat room for keyword matches.
    Runs in background, checking for new messages at the specified interval.
    Use kakao_get_monitor_events() to retrieve detected keyword matches.
    Keep the room open. Qt auto monitors exposed UIA messages without input.
    Complete monitors local history and may activate the room on each poll.
    Legacy auto uses the chat control first, then copies when necessary.

    Args:
        room_name: Exact title of the chat room window to monitor.
        keywords: List of keywords to watch for (case-insensitive).
        poll_interval_sec: Seconds between each poll (minimum 3, default 5).
    """
    try:
        if not keywords:
            return {"error": "Keywords list cannot be empty"}
        result = controller._chat_monitor.start(room_name, keywords, poll_interval_sec, read_mode)
        if result["success"]:
            return {"message": result["message"], "baseline_ready": result["baseline_ready"]}
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
            "baseline_ready": is_running and controller._chat_monitor.baseline_ready,
            "event_count": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": f"Failed to get monitor events: {e}"}


def main():
    if sys.argv[1:] == ["--diagnose"]:
        import json
        report = kakao_health_check(diagnostics=True)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        if not report.get("running") or "error" in report:
            raise SystemExit(1)
        return
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
