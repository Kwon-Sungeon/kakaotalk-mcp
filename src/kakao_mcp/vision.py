"""Image-template-based UI targeting for KakaoTalk PC.

KakaoTalk's custom EVA controls expose nothing via UIA, and fixed pixel
offsets break across versions/DPI. This module captures the chat window
(PrintWindow, works even partially obscured), matches a template image
(multi-scale OpenCV), and clicks the matched position.

Workflow:
  1. capture_window_to_file()  -> inspect the PNG, pick the target region
  2. save_template()           -> crop & store template under assets/
  3. click_template()          -> find & click the target reliably
"""
import os
import sys
import time
import ctypes
from typing import Optional, List, Dict, Tuple

import win32gui
import win32ui

from . import config
from .controller import find_chat_window, bring_window_to_front

_user32 = ctypes.windll.user32

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
DEFAULT_SCALES = [0.75, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.35, 1.5]


def _log(msg: str):
    print(f"[kakao-vision] {msg}", file=sys.stderr, flush=True)


def _lazy_cv():
    """Import cv2/numpy lazily with a clear error message."""
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError as e:
        raise RuntimeError(
            "opencv-python / numpy not installed. "
            "Run: uv sync (or pip install opencv-python numpy)"
        ) from e


# ---------------------------------------------------------------------------
# Window capture (PrintWindow)
# ---------------------------------------------------------------------------

def capture_window(hwnd: int):
    """Capture a window into a PIL Image. Returns (image, (left, top))."""
    from PIL import Image

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise RuntimeError(f"Window {hwnd} has invalid size {w}x{h}")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    try:
        PW_RENDERFULLCONTENT = 2
        ok = _user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            _log("PrintWindow returned 0 — capture may be blank")
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]),
            data, "raw", "BGRX", 0, 1,
        )
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img, (l, t)


def capture_window_to_file(room_name: str, save_path: str = "") -> Dict:
    """Capture a chat window and save it as PNG for inspection."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}
    if not save_path:
        save_path = os.path.join(os.path.dirname(ASSETS_DIR), "..", "..",
                                 "debug_capture.png")
        save_path = os.path.abspath(save_path)
    img, (l, t) = capture_window(hwnd)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    img.save(save_path)
    return {
        "success": True,
        "message": f"Captured '{room_name}' ({img.width}x{img.height})",
        "path": save_path,
        "window_rect": [l, t, l + img.width, t + img.height],
        "hint": "Coordinates in this image are window-relative pixels.",
    }


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

def save_template(room_name: str, left: int, top: int, right: int, bottom: int,
                  template_name: str) -> Dict:
    """Crop a region from the live chat window and save it as a template."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}
    img, _ = capture_window(hwnd)
    if not (0 <= left < right <= img.width and 0 <= top < bottom <= img.height):
        return {"success": False,
                "error": f"Region out of bounds (window is {img.width}x{img.height})"}
    crop = img.crop((left, top, right, bottom))
    os.makedirs(ASSETS_DIR, exist_ok=True)
    path = os.path.join(ASSETS_DIR, f"{template_name}.png")
    crop.save(path)
    return {"success": True,
            "message": f"Template '{template_name}' saved ({crop.width}x{crop.height})",
            "path": path}


def list_templates() -> List[str]:
    if not os.path.isdir(ASSETS_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(ASSETS_DIR) if f.endswith(".png"))


# ---------------------------------------------------------------------------
# Template matching & clicking
# ---------------------------------------------------------------------------

def find_template(room_name: str, template_name: str,
                  threshold: float = 0.75,
                  search_region: Optional[List[int]] = None) -> Dict:
    """Find a template inside the chat window (multi-scale).

    Returns window-relative and screen coordinates of the best match.
    """
    cv2, np = _lazy_cv()

    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    tpath = os.path.join(ASSETS_DIR, f"{template_name}.png")
    if not os.path.isfile(tpath):
        return {"success": False,
                "error": f"Template '{template_name}' not found. "
                         f"Available: {list_templates()}"}

    img, (wl, wt) = capture_window(hwnd)
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)

    off_x = off_y = 0
    if search_region:
        x0, y0, x1, y1 = [int(v) for v in search_region]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
        gray = gray[y0:y1, x0:x1]
        off_x, off_y = x0, y0

    tmpl0 = cv2.imread(tpath, cv2.IMREAD_GRAYSCALE)
    if tmpl0 is None:
        return {"success": False, "error": f"Failed to read template {tpath}"}

    best = None  # (score, (x, y), (h, w), scale)
    for s in DEFAULT_SCALES:
        th, tw = int(tmpl0.shape[0] * s), int(tmpl0.shape[1] * s)
        if th < 4 or tw < 4 or th > gray.shape[0] or tw > gray.shape[1]:
            continue
        tmpl = cv2.resize(tmpl0, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        if best is None or maxv > best[0]:
            best = (float(maxv), maxloc, (th, tw), s)

    if best is None:
        return {"success": False, "error": "Template larger than search area"}

    score, (mx, my), (th, tw), scale = best
    cx = off_x + mx + tw // 2
    cy = off_y + my + th // 2
    result = {
        "success": score >= threshold,
        "score": round(score, 3),
        "scale": scale,
        "window_xy": [cx, cy],
        "screen_xy": [wl + cx, wt + cy],
        "threshold": threshold,
    }
    if score < threshold:
        result["error"] = (f"Best match score {score:.3f} below threshold "
                           f"{threshold} (at window {cx},{cy})")
    return result


def click_template(room_name: str, template_name: str,
                   threshold: float = 0.75,
                   double_click: bool = False,
                   offset_x: int = 0, offset_y: int = 0,
                   search_region: Optional[List[int]] = None) -> Dict:
    """Find a template in the chat window and click its center (+offset)."""
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    found = find_template(room_name, template_name, threshold, search_region)
    if not found.get("success"):
        return found

    bring_window_to_front(hwnd)
    time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)

    sx, sy = found["screen_xy"]
    x, y = sx + offset_x, sy + offset_y
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)
    if double_click:
        time.sleep(0.05)
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)

    found["message"] = (f"Clicked '{template_name}' at screen ({x},{y}) "
                        f"score={found['score']}")
    return found


# ---------------------------------------------------------------------------
# Member list popup (uses template 'member_icon')
# ---------------------------------------------------------------------------

def _all_visible_windows() -> List[Dict]:
    """All visible top-level windows (any class) — for popup diffing."""
    out: List[Dict] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            out.append({
                "hwnd": hwnd,
                "class": win32gui.GetClassName(hwnd),
                "title": win32gui.GetWindowText(hwnd),
                "rect": win32gui.GetWindowRect(hwnd),
            })
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def open_member_list(room_name: str, threshold: float = 0.7) -> Dict:
    """Open the participant list popup by clicking the 👤N header icon.

    Requires a saved template named 'member_icon' (the person icon shown
    under the chat title). Searches only the window's top header strip.
    Returns info about the popup window that appeared.
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    header = [0, 0, r - l, min(120, b - t)]  # top strip only

    before = _all_visible_windows()
    res = click_template(room_name, "member_icon", threshold,
                         search_region=header)
    if not res.get("success"):
        res.setdefault("hint",
                       "Save the template first: kakao_capture_window -> "
                       "kakao_save_template(..., 'member_icon')")
        return res

    time.sleep(0.7)
    after = _all_visible_windows()
    before_h = {w["hwnd"] for w in before}
    new_windows = [w for w in after if w["hwnd"] not in before_h]

    return {
        "success": True,
        "message": f"Member icon clicked (score={res['score']}). "
                   f"{len(new_windows)} new window(s) appeared.",
        "click": res,
        "new_windows": new_windows,
    }


# ---------------------------------------------------------------------------
# Raw hwnd capture / click (for popups without titles)
# ---------------------------------------------------------------------------

def capture_hwnd_to_file(hwnd: int, save_path: str) -> Dict:
    """Capture ANY window by hwnd (e.g. the member list popup) to PNG."""
    if not win32gui.IsWindow(hwnd):
        return {"success": False, "error": f"Invalid hwnd {hwnd}"}
    img, (l, t) = capture_window(hwnd)
    save_path = os.path.abspath(save_path)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    img.save(save_path)
    return {
        "success": True,
        "message": f"Captured hwnd={hwnd} ({img.width}x{img.height})",
        "path": save_path,
        "window_rect": [l, t, l + img.width, t + img.height],
    }


def click_at(hwnd: int, x: int, y: int, double_click: bool = False,
             bring_front: bool = True) -> Dict:
    """Click at window-relative pixel coordinates of the given hwnd."""
    if not win32gui.IsWindow(hwnd):
        return {"success": False, "error": f"Invalid hwnd {hwnd}"}
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    sx, sy = l + int(x), t + int(y)
    if bring_front:
        bring_window_to_front(hwnd)
        time.sleep(config.WINDOW_ACTIVATE_WAIT_SEC)
    _user32.SetCursorPos(sx, sy)
    time.sleep(0.05)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)
    if double_click:
        time.sleep(0.05)
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)
    return {"success": True,
            "message": f"{'Double-c' if double_click else 'C'}licked hwnd={hwnd} "
                       f"at window({x},{y}) = screen({sx},{sy})"}
