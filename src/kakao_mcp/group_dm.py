"""Fully server-side group DM: iterate a group chat's member list and send
a 1:1 message to each member — no client-side computer-use needed.

Flow per member (all via Win32 + Windows OCR, inside the MCP server):
  1. open the member-list popup by clicking the person-count icon
  2. OCR the popup, pick the first member not yet processed
  3. double-click that row -> KakaoTalk opens the 1:1 chat window
  4. read the new window's real title, send the message, close it
  5. repeat (popup is re-opened each time; scrolls down as needed)

OCR names are only used to AVOID re-processing the same row; the actual
send target is the real window title (always correct), so Korean OCR
inaccuracy doesn't matter for delivery.
"""
import re
import sys
import time
import ctypes
from typing import List, Dict, Optional, Tuple

import win32gui
import win32con

from . import config
from . import controller
from .controller import (
    find_chat_window, list_chat_windows, bring_window_to_front,
    send_message_to_room,
)
from .ocr import ocr_hwnd
from .vision import click_at

_user32 = ctypes.windll.user32


def _log(msg: str):
    print(f"[kakao-group-dm] {msg}", file=sys.stderr, flush=True)


# Person-count number position (window-relative) in the chat header.
# Clicking the "13" digits (not the icon at far-left, which opens the room
# profile card) reliably opens the member list popup.
HEADER_ICON_X = 80
HEADER_ICON_Y = 69

# Rows we never treat as members.
_SKIP_LABELS = ("멤버 초대하기", "멤버초대하기", "프로필", "참여 요청", "참여요청")


def _norm(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    return s.lower()


# ---------------------------------------------------------------------------
# Popup discovery
# ---------------------------------------------------------------------------

def _candidate_windows() -> List[Dict]:
    """All visible top-level windows that could be the member-list popup:
    any class, reasonable popup size, excluding the main + chat windows."""
    out: List[Dict] = []
    main = win32gui.FindWindow(config.KAKAO_MAIN_WINDOW_CLASS,
                               config.KAKAO_MAIN_WINDOW_TITLE)
    chat_hwnds = {w["hwnd"] for w in list_chat_windows()}

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if hwnd == main or hwnd in chat_hwnds:
            return True
        try:
            r = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w, h = r[2] - r[0], r[3] - r[1]
        if 120 < w < 520 and 100 < h < 760:
            out.append({"hwnd": hwnd, "class": win32gui.GetClassName(hwnd),
                        "title": win32gui.GetWindowText(hwnd), "rect": r})
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def _header_click_point(hwnd: int) -> Tuple[int, int]:
    """Find the person-count icon point in the chat header via OCR.
    Falls back to a fixed offset if OCR can't locate it."""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w = r - l
        res = ocr_hwnd(hwnd, region=[0, 40, w, 95], lang="ko")
        if res.get("success"):
            # the person-count line is the top-most line in this band
            lines = sorted(res["lines"], key=lambda ln: ln["center"][1])
            if lines:
                ln = lines[0]
                # click just left of the line start (the 👤 icon area)
                x = max(HEADER_ICON_X, ln["rect"][0] + 6)
                y = ln["center"][1]
                return x, y
    except Exception:
        pass
    return HEADER_ICON_X, HEADER_ICON_Y


def open_member_popup(room_name: str) -> Dict:
    """Click the person-count icon and return the popup window handle."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    before = {p["hwnd"] for p in _candidate_windows()}
    bring_window_to_front(hwnd)
    time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)
    cx, cy = HEADER_ICON_X, HEADER_ICON_Y
    click_at(hwnd, cx, cy, bring_front=False)
    time.sleep(0.8)

    after = _candidate_windows()
    new = [p for p in after if p["hwnd"] not in before]
    if not new:
        return {"success": False,
                "error": "Member popup not detected after clicking icon",
                "clicked": [cx, cy],
                "candidates": [{"class": p["class"], "rect": p["rect"]}
                               for p in after],
                "hint": "Check 'clicked' point and 'candidates' list"}
    popup = max(new, key=lambda p: (p["rect"][3] - p["rect"][1]))
    return {"success": True, "popup_hwnd": popup["hwnd"], "rect": popup["rect"],
            "clicked": [cx, cy]}


def _scroll(hwnd: int, clicks: int = -3):
    """Scroll inside a window (negative = down)."""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cx, cy = (l + r) // 2, (t + b) // 2
    _user32.SetCursorPos(cx, cy)
    time.sleep(0.05)
    _user32.mouse_event(0x0800, 0, 0, ctypes.c_int(clicks * 120), 0)  # WHEEL
    time.sleep(0.3)


def _member_rows(popup_hwnd: int) -> List[Dict]:
    """OCR the popup and return member rows (name + window-relative center),
    excluding non-member labels."""
    res = ocr_hwnd(popup_hwnd, lang="ko")
    if not res.get("success"):
        return []
    rows = []
    for ln in res["lines"]:
        txt = ln["text"].strip()
        if not txt:
            continue
        if any(_norm(s) in _norm(txt) for s in _SKIP_LABELS):
            continue
        # ignore very-right-aligned tiny labels (e.g. '프로필 1' button text)
        rows.append({"name": txt, "center": ln["center"], "rect": ln["rect"]})
    # sort top-to-bottom
    rows.sort(key=lambda r: r["center"][1])
    return rows


# ---------------------------------------------------------------------------
# Main: send to every member
# ---------------------------------------------------------------------------

def dm_all_members(room_name: str, message: str,
                   close_after: bool = True,
                   interval_sec: float = 4.0,
                   max_members: int = 50,
                   skip_names: Optional[List[str]] = None) -> Dict:
    """Send `message` to every member of `room_name` one-by-one."""
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    interval_sec = max(3.0, interval_sec)
    processed = set()
    if skip_names:
        processed.update(_norm(s) for s in skip_names)
    results: List[Dict] = []

    for _ in range(max_members + 5):
        if len([r for r in results if r.get("sent")]) >= max_members:
            break

        op = open_member_popup(room_name)
        if not op.get("success"):
            return {"success": False, "error": op.get("error"),
                    "results": results}
        popup = op["popup_hwnd"]

        # find first unprocessed member, scrolling down if needed
        target = None
        for scroll_step in range(8):
            rows = _member_rows(popup)
            for row in rows:
                if _norm(row["name"]) not in processed:
                    target = row
                    break
            if target is not None:
                break
            _scroll(popup, -3)
        if target is None:
            _log("No more unprocessed members — done")
            break

        processed.add(_norm(target["name"]))

        before = {w["hwnd"]: w["title"] for w in list_chat_windows()}
        click_at(popup, target["center"][0], target["center"][1],
                 double_click=True, bring_front=False)
        time.sleep(1.0)

        after = list_chat_windows()
        new = [w for w in after if w["hwnd"] not in before]
        if not new:
            results.append({"member_ocr": target["name"], "sent": False,
                            "detail": "no 1:1 window opened (self/profile?)"})
            controller._user32  # noop
            continue

        win = new[0]
        title = win["title"]
        send = send_message_to_room(title, message)
        sent = send.get("success", False)
        results.append({"member_ocr": target["name"], "window_title": title,
                        "sent": sent,
                        "detail": send.get("message") or send.get("error")})
        _log(f"{'SENT' if sent else 'FAIL'} -> {title}")

        if close_after and sent:
            try:
                win32gui.PostMessage(win["hwnd"], win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        time.sleep(interval_sec)

    sent_count = sum(1 for r in results if r.get("sent"))
    return {
        "success": sent_count > 0,
        "message": f"Sent to {sent_count} member(s)",
        "sent_count": sent_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Keyboard-driven variant (arrow-key navigation — no click coordinates)
# ---------------------------------------------------------------------------

VK_DOWN = 0x28
VK_RETURN = 0x0D
VK_HOME = 0x24


def _press_vk(vk: int):
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    _user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
    time.sleep(0.08)


def _focus_member_list(popup_hwnd: int):
    """Give keyboard focus to the popup's list control."""
    bring_window_to_front(popup_hwnd)
    time.sleep(0.25)
    # click once near the top-left of the list area to focus it (not a row
    # action — single click only highlights), then Home to go to the top.
    try:
        l, t, r, b = win32gui.GetWindowRect(popup_hwnd)
        _user32.SetCursorPos(l + 20, t + 12)
    except Exception:
        pass


def _member_count(room_name: str) -> int:
    """Read the participant count from the chat header (e.g. '13')."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return 0
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        res = ocr_hwnd(hwnd, region=[0, 55, r - l, 90], lang="ko")
        if res.get("success"):
            for ln in res["lines"]:
                m = re.search(r"\d{1,3}", ln["text"])
                if m:
                    return int(m.group())
    except Exception:
        pass
    return 0


def dm_all_members_kbd(room_name: str, message: str,
                       member_count: int = 0,
                       start_index: int = 0,
                       close_after: bool = True,
                       interval_sec: float = 3.0,
                       max_send: int = 50,
                       skip_titles: Optional[List[str]] = None) -> Dict:
    """Send to every member using ARROW-KEY navigation (no click coordinates).

    For member index i: open popup, focus list, press Down (i+1) times to
    highlight the i-th member, press Enter to open their 1:1 chat, send, close.
    Index-based so the trailing '멤버 초대하기' row is never triggered.
    """
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    interval_sec = max(3.0, interval_sec)
    skip = {re.sub(r"\s+", "", s).lower() for s in (skip_titles or [])}
    if member_count <= 0:
        member_count = _member_count(room_name) or 0
    if member_count <= 0:
        return {"success": False,
                "error": "Could not read member count; pass member_count"}

    results: List[Dict] = []
    sent_count = 0

    for idx in range(start_index, member_count):
        if sent_count >= max_send:
            break

        op = open_member_popup(room_name)
        if not op.get("success"):
            results.append({"index": idx, "sent": False,
                            "detail": op.get("error")})
            break
        popup = op["popup_hwnd"]

        _focus_member_list(popup)
        # press Home so navigation always starts from the first row
        _press_vk(VK_HOME)
        time.sleep(0.15)
        for _ in range(idx):
            _press_vk(VK_DOWN)

        before = {w["hwnd"]: w["title"] for w in list_chat_windows()}
        _press_vk(VK_RETURN)
        time.sleep(1.2)

        after = list_chat_windows()
        new = [w for w in after if w["hwnd"] not in before]
        if not new:
            results.append({"index": idx, "sent": False,
                            "detail": "no 1:1 window (self/own?)"})
            time.sleep(interval_sec)
            continue

        win = new[0]
        title = win["title"]
        if re.sub(r"\s+", "", title).lower() in skip:
            results.append({"index": idx, "window_title": title,
                            "sent": False, "detail": "skipped (already sent)"})
            if close_after:
                try:
                    win32gui.PostMessage(win["hwnd"], win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass
            time.sleep(interval_sec)
            continue

        send = send_message_to_room(title, message)
        ok = send.get("success", False)
        sent_count += 1 if ok else 0
        results.append({"index": idx, "window_title": title, "sent": ok,
                        "detail": send.get("message") or send.get("error")})
        _log(f"[{idx}] {'SENT' if ok else 'FAIL'} -> {title}")

        if close_after and ok:
            try:
                win32gui.PostMessage(win["hwnd"], win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        time.sleep(interval_sec)

    return {
        "success": sent_count > 0,
        "message": f"Sent to {sent_count} member(s) "
                   f"(indices {start_index}..{member_count - 1})",
        "sent_count": sent_count,
        "member_count": member_count,
        "results": results,
    }
