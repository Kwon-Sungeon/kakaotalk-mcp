#!/usr/bin/env python
"""MCP Server for KakaoTalk PC automation via Windows UI Automation or Win32."""
import os
import time
import sys
import json
import asyncio
import inspect
from functools import wraps
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from . import delivery
from .ui_lock import operation
from typing import Dict, Literal, Optional

from mcp.server.fastmcp import FastMCP

from kakao_mcp import backend as controller
from kakao_mcp import parser
from kakao_mcp import config, __version__

ReadMode = Literal["auto", "background", "complete"]

app = FastMCP(
    "kakao-mcp-server",
    instructions=("Call the appropriate send tool directly with the exact room title. It opens and verifies "
                  "the room. Pass room_ref if one was returned for this room. For a retry reuse request_id. "
                  "On partial or unconfirmed results inspect the item statuses; never replay via another tool. "
                  "Use send_image for photos, send_file for documents, and send_mention for an actual mention. "
                  "Use dry_run only for local argument and policy checks. It does not resolve rooms or check receipts."),
)


def tool(function):
    @wraps(function)
    async def invoke(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as error:
            result = {"error": str(error), "code": delivery.error_code(error)}
        failed = "error" in result or result.get("success") is False
        if failed:
            result.setdefault("code", delivery.error_code(result.get("error", "")))
        return CallToolResult(isError=failed, structuredContent=result,
                              content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))])
    local_reads = {
        'kakao_health_check',
        'kakao_list_open_rooms',
        'kakao_get_send_log',
        'kakao_list_chats',
        'kakao_inspect_ui',
    }
    annotations = ToolAnnotations(readOnlyHint=True, openWorldHint=False) if function.__name__ in local_reads else None
    app.tool(structured_output=False, annotations=annotations)(invoke)
    return function


@tool
def kakao_health_check(diagnostics: bool = False) -> Dict:
    """Check KakaoTalk status and report the MCP version and detected backend.
    diagnostics also probes read-only window discovery and reports its duration."""
    from kakao_mcp import qt_controller, controller as legacy
    report = {"mcp_version": __version__}
    try:
        impl = qt_controller if qt_controller.available() else legacy
        policy = delivery.send_policy()
        report["send_policy"] = dict(read_only=policy["read_only"], max_bulk_rooms=policy["max_bulk_rooms"],
                                     allowed_room_count=len(policy["allowed_rooms"]),
                                     blocked_room_count=len(policy["blocked_rooms"]))
        status = impl.is_kakaotalk_running()
        running = status["running"]
        report.update(status, backend=("qt_uia" if impl is qt_controller else "win32") if running else None,
                      message="KakaoTalk is running" if running else "KakaoTalk is not running")
        if diagnostics:
            report["windows_build"] = sys.getwindowsversion().build
            report["python"] = sys.version.split()[0]
            if running:
                import win32api, win32process
                try:
                    handle = win32api.OpenProcess(0x1000, False, status["pid"])
                    try:
                        version = win32api.GetFileVersionInfo(win32process.GetModuleFileNameEx(handle, 0), "\\")
                        ms, ls = version["FileVersionMS"], version["FileVersionLS"]
                        report["kakaotalk_version"] = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
                    finally:
                        handle.Close()
                except (OSError, win32api.error) as error:
                    report["version_error"] = str(error)
                start = time.perf_counter()
                report["open_room_count"] = len(impl.list_chat_windows())
                report["discovery_ms"] = round((time.perf_counter() - start) * 1000)
        return report
    except Exception as error:
        return {**report, "error": f"Health check failed: {error}"}


@tool
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


@tool
def kakao_list_chats(max_chats: int = 50) -> Dict:
    """List recent/visible chat rooms from KakaoTalk's Chats tab (Qt UIA or legacy Win32 OCR)."""
    if max_chats < 1:
        return {"error": "max_chats must be positive", "code": "invalid_argument"}
    from . import qt_controller, controller as legacy
    if qt_controller.available():
        data = qt_controller.list_chats()
        all_chats = data.get("chats", [])
        chats = all_chats[:max_chats]
        return {
            "message": f"Found {len(chats)} chat(s)",
            "total_exposed": len(all_chats),
            "chats": chats,
            "read_method": "uia",
            "partial": True,
        }
    with operation():
        data = legacy.list_chats_legacy(max_chats)
    if data.get("success") is False:
        return data
    chats = data.get("chats", [])
    return {
        "message": f"Found {len(chats)} chat(s)",
        "total_exposed": data.get("total_exposed", len(chats)),
        "chats": chats,
        "read_method": data.get("read_method", "win32_ocr"),
        "partial": True,
    }


@tool
def kakao_open_room(room_name: str, focus: bool = False, self_only: bool = False) -> Dict:
    """Open a KakaoTalk chat room by name. An existing room keeps its position.
    Set focus=true when the user wants to see the room. On Qt, self_only verifies KakaoTalk’s self-chat label.
    If not, searches for the room in KakaoTalk's search bar.

    Args:
        room_name: The name of the chat room or person to open.
    """
    with operation():
        if self_only:
            from . import qt_controller
            if not qt_controller.available():
                raise delivery.DeliveryError('self_identity_unavailable', 'This client does not expose the self-chat label')
            opened = qt_controller.call('open', room=room_name, self_only=True)
            ref = f"qt:{delivery.legacy.win32process.GetWindowThreadProcessId(opened['hwnd'])[1]}:{opened['hwnd']}"
            room = delivery.resolve(room_name, ref)
        else:
            room = delivery.resolve(room_name)
        if focus:
            room.verify()
            controller.bring_window_to_front(room.hwnd)
        return {"message": f"Opened '{room_name}'", "hwnd": room.hwnd, "room_ref": room.ref}


@tool
def kakao_close_room(room_name: str) -> Dict:
    """Safely close an open KakaoTalk chat room window unless a modal dialog is open."""
    if not room_name.strip():
        return {"error": "room_name cannot be empty", "code": "invalid_argument"}
    with operation():
        from . import qt_controller, controller as legacy
        if qt_controller.available():
            res = qt_controller.close_chat_window(room_name)
            return {"message": f"Closed '{room_name}'", **res}
        return legacy.close_chat_window(room_name)


@tool
def kakao_list_participants(room_name: str) -> Dict:
    """List participants in an open KakaoTalk chat room (read-only via drawer/UIA)."""
    if not room_name.strip():
        return {"error": "room_name cannot be empty", "code": "invalid_argument"}
    with operation():
        from . import qt_controller, controller as legacy
        if qt_controller.available():
            data = qt_controller.list_participants(room_name)
            participants = data.get("participants", [])
            count = data.get("count", len(participants))
            return {"message": f"Found {count} participant(s) in '{room_name}'", **data}
        return legacy.list_participants_legacy(room_name)


@tool
def kakao_inspect_ui(room_name: str = "", max_depth: int = 5) -> Dict:
    """Dump a read-only Win32 or UIA control tree for diagnostics and UI calibration."""
    if not 0 <= max_depth <= 10:
        return {"error": "max_depth must be between 0 and 10", "code": "invalid_argument"}
    with operation():
        from . import qt_controller, controller as legacy
        if qt_controller.available():
            return qt_controller.inspect_ui(room_name, max_depth)
        return legacy.inspect_win32_tree(room_name, max_depth)


@tool
def kakao_send_message(room_name: str, message: str, room_ref: Optional[str] = None,
                       request_id: Optional[str] = None, dry_run: bool = False,
                       replace_legacy_hint: bool = True) -> Dict:
    """Send to one exact room. Reuse request_id for retries. Non-placeholder drafts are always preserved;
    set replace_legacy_hint=false to reject legacy inputs that still report the placeholder cue string."""
    return delivery.deliver('text', [(room_name, message)], refs={room_name: room_ref} if room_ref else None,
                            dry_run=dry_run, request_id=request_id, replace_legacy_hint=replace_legacy_hint)


@tool
def kakao_send_bulk(room_names: list[str], message: str, interval_sec: float = 0.5,
                    request_id: Optional[str] = None, room_refs: Optional[dict[str, str]] = None, dry_run: bool = False) -> Dict:
    """Open each unique exact room and send once. On failure stop and return all per-room statuses."""
    return delivery.deliver('text', [(room, message) for room in room_names], refs=room_refs,
                            dry_run=dry_run, request_id=request_id, interval=max(.3, interval_sec))


@tool
def kakao_send_file(room_name: str, file_paths: list[str], room_ref: Optional[str] = None,
                    request_id: Optional[str] = None, dry_run: bool = False,
                    method: Literal['auto', 'clipboard', 'picker'] = 'auto') -> Dict:
    """Open the exact room and attach local documents by absolute path. Reuse request_id on retry."""
    return delivery.deliver('file', [(room_name, path) for path in file_paths],
                            refs={room_name: room_ref} if room_ref else None, dry_run=dry_run,
                            request_id=request_id, file_method=method)


@tool
def kakao_send_image(room_name: str, image_paths: list[str], room_ref: Optional[str] = None,
                     request_id: Optional[str] = None, dry_run: bool = False) -> Dict:
    """Open the exact room and send photos by absolute path. Returns a status for every image."""
    return delivery.deliver('image', [(room_name, path) for path in image_paths],
                            refs={room_name: room_ref} if room_ref else None, dry_run=dry_run, request_id=request_id)


@tool
def kakao_get_send_log(limit: int = 20, room_name: Optional[str] = None) -> Dict:
    """List recent delivery receipts, optionally by exact room. Includes request IDs and per-item states."""
    return delivery.recent_receipts(limit, room_name)


@tool
def kakao_delivery_status(request_id: str, item_index: Optional[int] = None,
                          user_verified: Optional[Literal['sent', 'not_sent']] = None) -> Dict:
    """Read a delivery receipt. Only after the user verifies the chat, record an uncertain item's outcome
    with its zero-based item_index and user_verified. This tool never sends anything."""
    return delivery.receipt(request_id, item_index, user_verified)


@tool
def kakao_read_messages(room_name: str, max_messages: Optional[int] = None, read_mode: Optional[ReadMode] = None,
                        sender: Optional[str] = None, contains: Optional[str] = None,
                        scroll_pages: int = 0) -> Dict:
    """Read recent messages from a KakaoTalk chat room.
    Reads locally loaded messages using the selected UI backend.
    With no count, auto prefers the exposed conversation (up to 50). Specify a
    count to fill missing history or metadata using a full copy when needed.
    Use scroll_pages > 0 to scroll upward and load older visible history pages.
    Use background for exposed messages without input or clipboard, even if partial.
    Use complete for all locally loaded detail; it may activate the room and copy.
    partial indicates that server-only history may not be included.

    Args:
        room_name: Exact title of the chat room window.
        max_messages: Maximum number of recent messages to return (default 50).
        scroll_pages: Number of upward scroll pages to load older messages (default 0).
    """
    try:
        if max_messages is not None and max_messages < 1:
            return {"error": "max_messages must be positive"}
        if scroll_pages < 0:
            return {"error": "scroll_pages must be non-negative"}
        read_kwargs = {"scroll_pages": scroll_pages} if scroll_pages else {}
        result = controller.read_chat_messages(room_name, read_mode, max_messages, **read_kwargs)
        if not result["success"]:
            return {"error": result["error"], "code": result.get("code", "read_failed")}
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        limit = max_messages if max_messages is not None else (None if scroll_pages > 0 else 50)
        if 'ocr_lines' in parsed:
            if sender:
                return {"error": "OCR cannot identify senders. Use complete mode for a sender filter."}
            lines = [line for line in parsed['ocr_lines'] if not contains or contains.casefold() in line.casefold()]
            if limit is not None:
                lines = lines[-limit:]
            return {**parsed, "ocr_lines": lines,
                    "message": "Visible OCR lines; message boundaries and senders are unknown"}
        messages = parser.filter_messages(parser.enrich_messages(parsed["messages"]), sender, contains)
        if limit is not None and len(messages) > limit:
            messages = messages[-limit:]
        return {
            "message": f"Read {len(messages)} messages from '{room_name}'",
            "room_name": parsed["room_name"],
            "member_count": parsed["member_count"],
            "messages": messages,
            "notices": parsed.get("notices", []),
            "partial": parsed.get("partial", False),
            "read_method": parsed.get("read_method", "clipboard"),
            "unknown_fields": parsed.get("unknown_fields", []),
            **({"scope": parsed["scope"]} if "scope" in parsed else {}),
            **({"at_end": parsed["at_end"]} if "at_end" in parsed else {}),
            **({"detail_error": parsed["detail_error"]} if "detail_error" in parsed else {}),
        }
    except Exception as e:
        return {"error": f"Failed to read messages: {e}"}


@tool
def kakao_extract_links(room_name: str, read_mode: Optional[ReadMode] = None, unique: bool = False,
                        contains: Optional[str] = None) -> Dict:
    """Extract all URLs/links from messages in a KakaoTalk chat room.
    Auto and complete request the full locally loaded transcript. Background
    extracts links only from messages available without foreground interaction.

    Args:
        room_name: Exact title of the chat room window.
    """
    try:
        result = controller.read_chat_messages(room_name, read_mode, all_history=True)
        if not result["success"]:
            return {"error": result["error"], "code": result.get("code", "read_failed")}
        parsed = result.get('parsed') or parser.parse_chat_text(result["raw_text"])
        if 'ocr_lines' in parsed:
            urls = parser.find_urls('\n'.join(parsed['ocr_lines']))
            if unique:
                urls = list(dict.fromkeys(urls))
            return {"room_name": room_name, "partial": True, "read_method": "win32_ocr",
                    "links": [{"url": url, "sender": None, "time": None, "date": None, "timestamp": None}
                             for url in urls if not contains or contains.casefold() in url.casefold()]}
        urls = parser.extract_urls_from_messages(parser.enrich_messages(parsed["messages"]), unique=unique)
        if contains:
            urls = [row for row in urls if contains.casefold() in row["url"].casefold()]
        return {
            "message": f"Found {len(urls)} URL(s) in '{room_name}'",
            "links": urls,
            "read_method": parsed.get("read_method", "clipboard"),
            "partial": parsed.get("partial", False),
        }
    except Exception as e:
        return {"error": f"Failed to extract links: {e}"}


@tool
def kakao_send_mention(room_name: str, mention_name: str, message: str, room_ref: Optional[str] = None,
                       request_id: Optional[str] = None, dry_run: bool = False) -> Dict:
    """Open the exact room, verify the exact mention candidate and selected draft, then send once."""
    return delivery.deliver('mention', [(room_name, message)], mention=mention_name,
                            refs={room_name: room_ref} if room_ref else None, dry_run=dry_run, request_id=request_id)


@tool
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


@tool
def kakao_start_monitor(
    room_name: str,
    keywords: list[str],
    poll_interval_sec: float = 5.0,
    read_mode: Optional[ReadMode] = None,
) -> Dict:
    """Start monitoring a KakaoTalk chat room for keyword matches (supports multiple concurrent rooms).
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


@tool
def kakao_stop_monitor(room_name: Optional[str] = None) -> Dict:
    """Stop monitoring a specific chat room, or all running monitors when room_name is omitted."""
    try:
        monitor = controller._chat_monitor
        result = monitor.stop(room_name) if room_name is not None else monitor.stop()
        if result["success"]:
            out = {"message": result["message"]}
            if "stopped_rooms" in result:
                out["stopped_rooms"] = result["stopped_rooms"]
            return out
        return {"error": result["error"]}
    except Exception as e:
        return {"error": f"Failed to stop monitor: {e}"}


@tool
async def kakao_get_monitor_events(wait_sec: float = 0, room_name: Optional[str] = None) -> Dict:
    """Get pending keyword match events from the chat monitor (one room or all monitored rooms).
    Returns events detected since last call. Each event includes:
    - keyword: the matched keyword
    - trigger_message: the message that matched (sender, time, text)
    - recent_context: last 10 messages for context understanding
    - room_name: the monitored chat room

    Set wait_sec up to 45 to wait for new events. status includes last_poll, last_error and dropped_events.
    """
    try:
        monitor = controller._chat_monitor
        if room_name is not None:
            events = (await asyncio.to_thread(monitor.get_events, wait_sec, room_name)
                      if wait_sec > 0 else monitor.get_events(0, room_name))
            status = monitor.status(room_name)
            is_running = bool(status.get("monitoring"))
            baseline = bool(status.get("baseline_ready"))
        else:
            events = await asyncio.to_thread(monitor.get_events, wait_sec) if wait_sec > 0 else monitor.get_events()
            status = monitor.status()
            is_running = monitor.is_running
            baseline = is_running and monitor.baseline_ready
        return {
            "monitoring": is_running,
            "baseline_ready": baseline,
            "event_count": len(events),
            "events": events,
            "status": status,
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
