"""Group chat participant tools: list members, open profile, add friend, 1:1 DM.

KakaoTalk PC uses custom EVA_* controls that expose little via classic Win32
messages, so this module uses a layered strategy:

  1. UIA (uiautomation package) — enumerate drawer list items / popup buttons
     by their accessibility Name.
  2. Coordinate-based fallback — calibrated offsets relative to the chat
     window rect (adjustable in config).
  3. `dump_ui_tree()` — debugging helper that dumps both the Win32 child
     window tree and the UIA tree so offsets/names can be calibrated live.

All functions return rich dicts with `success` and `debug` info.
"""
import sys
import time
import ctypes
from typing import Optional, List, Dict, Tuple

import win32gui
import win32api
import win32con
import win32clipboard

from . import config
from . import controller
from .controller import (
    find_chat_window,
    list_chat_windows,
    bring_window_to_front,
    find_child_window_recursive,
    _send_ctrl_key_combo,
    _read_clipboard_text,
    _press_key,
)

_user32 = ctypes.windll.user32

# Lazy import — server must still run without uiautomation installed
try:
    import uiautomation as uia
    HAS_UIA = True
except Exception:
    uia = None
    HAS_UIA = False


def _log(msg: str):
    print(f"[kakao-participants] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Low-level mouse helpers (screen coordinates)
# ---------------------------------------------------------------------------

def _click_at(x: int, y: int, double: bool = False):
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    _user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    if double:
        time.sleep(0.05)
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.1)


def _window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    return win32gui.GetWindowRect(hwnd)


def _top_level_windows() -> List[Dict]:
    """Snapshot of all visible top-level EVA windows (hwnd, title, rect)."""
    out: List[Dict] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            if cls.startswith("EVA_"):
                out.append({
                    "hwnd": hwnd,
                    "class": cls,
                    "title": win32gui.GetWindowText(hwnd),
                    "rect": win32gui.GetWindowRect(hwnd),
                })
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def _diff_new_windows(before: List[Dict], after: List[Dict]) -> List[Dict]:
    before_hwnds = {w["hwnd"] for w in before}
    return [w for w in after if w["hwnd"] not in before_hwnds]


# ---------------------------------------------------------------------------
# UI tree dump (debug / calibration)
# ---------------------------------------------------------------------------

def dump_ui_tree(room_name: str = "", max_depth: int = 6) -> Dict:
    """Dump Win32 child tree + UIA tree of a chat window (or all top-level
    EVA windows if room_name empty). Use this to calibrate names/offsets."""
    lines: List[str] = []

    targets: List[int] = []
    if room_name:
        hwnd = find_chat_window(room_name)
        if hwnd is None:
            return {"success": False, "error": f"Chat window '{room_name}' not found"}
        targets = [hwnd]
    else:
        targets = [w["hwnd"] for w in _top_level_windows()]

    # --- Win32 tree ---
    for root in targets:
        lines.append(f"=== Win32 tree of hwnd={root} "
                     f"title='{win32gui.GetWindowText(root)}' "
                     f"class='{win32gui.GetClassName(root)}' "
                     f"rect={win32gui.GetWindowRect(root)} ===")

        def _walk(hwnd, depth):
            if depth > max_depth:
                return

            children: List[int] = []

            def _cb(h, _):
                if win32gui.GetParent(h) == hwnd:
                    children.append(h)
                return True

            try:
                win32gui.EnumChildWindows(hwnd, _cb, None)
            except Exception:
                pass
            for ch in children:
                try:
                    lines.append(
                        "  " * depth
                        + f"- hwnd={ch} class='{win32gui.GetClassName(ch)}' "
                          f"title='{win32gui.GetWindowText(ch)}' "
                          f"rect={win32gui.GetWindowRect(ch)} "
                          f"visible={bool(win32gui.IsWindowVisible(ch))}"
                    )
                except Exception:
                    continue
                _walk(ch, depth + 1)

        _walk(root, 1)

    # --- UIA tree ---
    if HAS_UIA:
        for root in targets:
            lines.append(f"=== UIA tree of hwnd={root} ===")
            try:
                ctrl = uia.ControlFromHandle(root)

                def _walk_uia(c, depth):
                    if depth > max_depth:
                        return
                    for child in c.GetChildren():
                        try:
                            lines.append(
                                "  " * depth
                                + f"- {child.ControlTypeName} Name='{child.Name}' "
                                  f"Class='{child.ClassName}' "
                                  f"Rect={child.BoundingRectangle}"
                            )
                        except Exception:
                            continue
                        _walk_uia(child, depth + 1)

                _walk_uia(ctrl, 1)
            except Exception as e:
                lines.append(f"  (UIA error: {e})")
    else:
        lines.append("(uiautomation not installed — pip install uiautomation)")

    return {"success": True, "tree": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Side drawer (member list)
# ---------------------------------------------------------------------------

def open_member_drawer(room_name: str) -> Dict:
    """Open the side drawer (≡) of a chat room which contains the member list.

    Strategy: click the hamburger button at the top-right of the chat window.
    Detects success by checking whether new child windows / UIA list appear.
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    bring_window_to_front(hwnd)
    time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)

    l, t, r, b = _window_rect(hwnd)

    # Hamburger (≡) button: top-right corner of the chat window.
    x = r - config.DRAWER_BUTTON_OFFSET_X
    y = t + config.DRAWER_BUTTON_OFFSET_Y
    _click_at(x, y)
    time.sleep(config.DRAWER_OPEN_WAIT_SEC)

    return {
        "success": True,
        "message": f"Clicked drawer toggle of '{room_name}' at ({x},{y})",
        "hwnd": hwnd,
    }


def close_popups():
    """Press ESC a few times to close any popup/drawer."""
    for _ in range(2):
        _press_key(config.VK_ESCAPE)
        time.sleep(0.15)


def list_participants(room_name: str, open_drawer: bool = True) -> Dict:
    """List participants of a group chat room.

    Requires the side drawer to be open (done automatically by default).
    Strategy 1: UIA — find ListControl/ListItems inside the chat window.
    Strategy 2: clipboard — focus member list, Ctrl+A/Ctrl+C, parse.
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    if open_drawer:
        res = open_member_drawer(room_name)
        if not res["success"]:
            return res

    members: List[Dict] = []
    debug: List[str] = []

    # --- Strategy 1: UIA ---
    if HAS_UIA:
        try:
            root = uia.ControlFromHandle(hwnd)
            lists = []

            def _collect(c, depth=0):
                if depth > 8:
                    return
                for child in c.GetChildren():
                    tname = child.ControlTypeName
                    if tname in ("ListControl", "ListItemControl", "TreeControl",
                                 "TreeItemControl", "DataItemControl"):
                        lists.append(child)
                    _collect(child, depth + 1)

            _collect(root)
            debug.append(f"UIA: found {len(lists)} list-like controls")

            for c in lists:
                if c.ControlTypeName in ("ListItemControl", "TreeItemControl",
                                         "DataItemControl"):
                    name = (c.Name or "").strip()
                    if name:
                        rect = c.BoundingRectangle
                        members.append({
                            "name": name,
                            "rect": [rect.left, rect.top, rect.right, rect.bottom],
                            "source": "uia",
                        })
        except Exception as e:
            debug.append(f"UIA strategy failed: {e}")

    # --- Strategy 2: clipboard on the drawer list ---
    if not members:
        try:
            # The drawer list is usually the right-most EVA_VH_ListControl.
            list_hwnds: List[int] = []

            def _cb(h, _):
                if win32gui.GetClassName(h) == config.KAKAO_LIST_CONTROL_CLASS:
                    list_hwnds.append(h)
                return True

            try:
                win32gui.EnumChildWindows(hwnd, _cb, None)
            except Exception:
                pass
            debug.append(f"Win32: {len(list_hwnds)} list controls found")

            if len(list_hwnds) >= 2:
                # Pick the one closest to the right edge (drawer list)
                target = max(list_hwnds,
                             key=lambda h: win32gui.GetWindowRect(h)[0])
                rect = win32gui.GetWindowRect(target)
                _click_at((rect[0] + rect[2]) // 2, rect[1] + 30)
                _send_ctrl_key_combo(config.VK_A)
                time.sleep(config.KEY_COMBO_WAIT_SEC)
                _send_ctrl_key_combo(config.VK_C)
                time.sleep(config.KEY_COMBO_WAIT_SEC)
                raw = _read_clipboard_text()
                debug.append(f"clipboard raw length: {len(raw)}")
                for line in raw.splitlines():
                    line = line.strip()
                    if line and line not in ("대화상대",):
                        members.append({"name": line, "source": "clipboard"})
        except Exception as e:
            debug.append(f"clipboard strategy failed: {e}")

    # Dedupe, keep order
    seen = set()
    unique = []
    for m in members:
        if m["name"] not in seen:
            seen.add(m["name"])
            unique.append(m)

    return {
        "success": bool(unique),
        "message": f"Found {len(unique)} participant(s) in '{room_name}'",
        "participants": unique,
        "debug": debug,
        "hint": ("If empty, call kakao_inspect_ui(room_name) with the drawer "
                 "open and calibrate names/offsets."),
    }


# ---------------------------------------------------------------------------
# Profile popup: open / add friend / 1:1 chat
# ---------------------------------------------------------------------------

def _find_uia_button(root_hwnd: int, names: List[str]) -> Optional["uia.Control"]:
    """Find a UIA button (or any control) whose Name matches one of names."""
    if not HAS_UIA:
        return None
    try:
        root = uia.ControlFromHandle(root_hwnd)
        found = []

        def _walk(c, depth=0):
            if depth > 8 or found:
                return
            for child in c.GetChildren():
                if (child.Name or "").strip() in names:
                    found.append(child)
                    return
                _walk(child, depth + 1)

        _walk(root)
        return found[0] if found else None
    except Exception:
        return None


def open_member_profile(room_name: str, member_name: str) -> Dict:
    """Open a member's profile popup by double-clicking them in the drawer.

    Returns hwnd of the new popup window if detected.
    """
    before = _top_level_windows()

    res = list_participants(room_name, open_drawer=True)
    if not res["success"]:
        return {"success": False, "error": "Could not enumerate participants",
                "detail": res}

    target = None
    for m in res["participants"]:
        if member_name in m["name"]:
            target = m
            break
    if target is None:
        return {
            "success": False,
            "error": f"Member '{member_name}' not found in drawer list",
            "participants": [m["name"] for m in res["participants"]],
        }

    if "rect" in target:
        l, t, r, b = target["rect"]
        _click_at((l + r) // 2, (t + b) // 2, double=True)
    else:
        return {
            "success": False,
            "error": ("Member found via clipboard only (no coordinates). "
                      "UIA needed for clicking — install uiautomation."),
        }

    time.sleep(config.PROFILE_OPEN_WAIT_SEC)
    new_windows = _diff_new_windows(before, _top_level_windows())
    popup = new_windows[0] if new_windows else None

    return {
        "success": popup is not None,
        "message": f"Profile popup for '{member_name}'"
                   + (f" opened (hwnd={popup['hwnd']})" if popup else " NOT detected"),
        "popup_hwnd": popup["hwnd"] if popup else None,
        "new_windows": new_windows,
    }


def profile_click_button(popup_hwnd: int, button_names: List[str]) -> Dict:
    """Click a button in a profile popup by UIA Name, e.g. ['1:1 채팅'] or
    ['친구 추가', '친구추가']."""
    bring_window_to_front(popup_hwnd)
    time.sleep(0.2)
    btn = _find_uia_button(popup_hwnd, button_names)
    if btn is not None:
        try:
            btn.Click(simulateMove=False)
            time.sleep(0.3)
            return {"success": True, "message": f"Clicked '{btn.Name}'"}
        except Exception as e:
            return {"success": False, "error": f"UIA click failed: {e}"}
    return {
        "success": False,
        "error": f"Button {button_names} not found via UIA in popup {popup_hwnd}. "
                 "Use kakao_inspect_ui to see available control names.",
    }


def dm_member(room_name: str, member_name: str, message: str,
              add_friend: bool = True, close_after: bool = False) -> Dict:
    """Send a 1:1 DM to a member of a group chat.

    Flow:
      1. Try the fast path: main-window search (works if already a friend).
      2. Fallback: open member profile from the group chat drawer,
         click '친구 추가' if present (and add_friend=True), then '1:1 채팅'.
      3. Send the message into the opened 1:1 window.
    """
    steps: List[str] = []

    # --- Fast path: friend search ---
    dm_hwnd = find_chat_window(member_name)
    if dm_hwnd is None:
        sr = controller.search_and_open_room(member_name)
        steps.append(f"search: {sr.get('message') or sr.get('error')}")
        # Verify the opened window is really the 1:1 room of this member
        dm_hwnd = find_chat_window(member_name)
        if dm_hwnd is None:
            # Search may have opened a wrong window — close it
            close_popups()

    # --- Fallback: profile popup from group chat drawer ---
    if dm_hwnd is None:
        prof = open_member_profile(room_name, member_name)
        steps.append(f"profile: {prof.get('message') or prof.get('error')}")
        if not prof["success"]:
            return {"success": False, "error": "Could not open member profile",
                    "steps": steps, "detail": prof}

        popup = prof["popup_hwnd"]

        if add_friend:
            fr = profile_click_button(popup, ["친구 추가", "친구추가"])
            steps.append(f"add_friend: {fr.get('message') or fr.get('error')}")
            time.sleep(0.5)

        before = _top_level_windows()
        cr = profile_click_button(popup, ["1:1 채팅", "1:1채팅", "나와의 채팅"])
        steps.append(f"open_dm: {cr.get('message') or cr.get('error')}")
        if not cr["success"]:
            close_popups()
            return {"success": False, "error": "Could not click 1:1 chat button",
                    "steps": steps}
        time.sleep(config.SEARCH_OPEN_WAIT_SEC)

        dm_hwnd = find_chat_window(member_name)
        if dm_hwnd is None:
            news = _diff_new_windows(before, _top_level_windows())
            if news:
                dm_hwnd = news[0]["hwnd"]
                member_name = news[0]["title"]  # actual window title

    if dm_hwnd is None:
        return {"success": False, "error": "1:1 chat window did not open",
                "steps": steps}

    # --- Send ---
    sr = controller.send_message_to_room(win32gui.GetWindowText(dm_hwnd), message)
    steps.append(f"send: {sr.get('message') or sr.get('error')}")

    if close_after and sr["success"]:
        try:
            win32gui.PostMessage(dm_hwnd, win32con.WM_CLOSE, 0, 0)
            steps.append("closed DM window")
        except Exception:
            pass

    return {
        "success": sr["success"],
        "message": f"DM to '{member_name}': "
                   + (sr.get("message") or sr.get("error", "")),
        "steps": steps,
    }
