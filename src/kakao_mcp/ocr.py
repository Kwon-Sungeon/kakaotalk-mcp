"""Windows built-in OCR (Windows.Media.Ocr) for KakaoTalk UI text.

No external OCR installation needed — uses the OS engine via `winsdk`.
Returns recognized lines/words WITH bounding boxes (window-relative),
so callers can locate a member name in the list popup and click it.
"""
import sys
import time
import ctypes
import asyncio
import threading
from typing import Optional, List, Dict

import win32gui

from . import config
from .controller import find_chat_window, bring_window_to_front
from .vision import capture_window, click_at

_user32 = ctypes.windll.user32


def _log(msg: str):
    print(f"[kakao-ocr] {msg}", file=sys.stderr, flush=True)


def _run_async(coro):
    """Run a coroutine safely whether or not an event loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Running inside a loop (shouldn't happen for sync tools, but be safe):
    result = {}

    def _worker():
        result["value"] = asyncio.run(coro)

    th = threading.Thread(target=_worker)
    th.start()
    th.join()
    return result.get("value")


async def _ocr_pil_async(img, lang: str = "ko"):
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import (
        SoftwareBitmap, BitmapPixelFormat,
    )
    from winsdk.windows.security.cryptography import CryptographicBuffer

    engine = None
    if lang:
        try:
            if OcrEngine.is_language_supported(Language(lang)):
                engine = OcrEngine.try_create_from_language(Language(lang))
        except Exception:
            engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError(
            "No OCR engine available. Install the Korean language pack "
            "(설정 > 시간 및 언어 > 언어)."
        )

    rgba = img.convert("RGBA").tobytes()
    ibuf = CryptographicBuffer.create_from_byte_array(rgba)
    sbmp = SoftwareBitmap.create_copy_from_buffer(
        ibuf, BitmapPixelFormat.RGBA8, img.width, img.height
    )
    result = await engine.recognize_async(sbmp)

    lines: List[Dict] = []
    for line in result.lines:
        words = []
        x0 = y0 = 10**9
        x1 = y1 = -1
        for w in line.words:
            r = w.bounding_rect
            wx0, wy0 = int(r.x), int(r.y)
            wx1, wy1 = int(r.x + r.width), int(r.y + r.height)
            x0, y0 = min(x0, wx0), min(y0, wy0)
            x1, y1 = max(x1, wx1), max(y1, wy1)
            words.append({"text": w.text, "rect": [wx0, wy0, wx1, wy1]})
        lines.append({
            "text": line.text,
            "rect": [x0, y0, x1, y1],
            "center": [(x0 + x1) // 2, (y0 + y1) // 2],
            "words": words,
        })
    return lines


def ocr_hwnd(hwnd: int, region: Optional[List[int]] = None,
             lang: str = "ko") -> Dict:
    """OCR a window (by hwnd). Returns lines with window-relative rects."""
    if not win32gui.IsWindow(hwnd):
        return {"success": False, "error": f"Invalid hwnd {hwnd}"}
    img, _ = capture_window(hwnd)
    off_x = off_y = 0
    if region:
        x0, y0, x1, y1 = [int(v) for v in region]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.width, x1), min(img.height, y1)
        img = img.crop((x0, y0, x1, y1))
        off_x, off_y = x0, y0
    try:
        lines = _run_async(_ocr_pil_async(img, lang))
    except ImportError:
        return {"success": False,
                "error": "winsdk not installed — run: uv sync "
                         "(or pip install winsdk)"}
    except Exception as e:
        return {"success": False, "error": f"OCR failed: {e}"}

    if off_x or off_y:
        for ln in lines:
            ln["rect"] = [ln["rect"][0] + off_x, ln["rect"][1] + off_y,
                          ln["rect"][2] + off_x, ln["rect"][3] + off_y]
            ln["center"] = [ln["center"][0] + off_x, ln["center"][1] + off_y]
            for w in ln["words"]:
                w["rect"] = [w["rect"][0] + off_x, w["rect"][1] + off_y,
                             w["rect"][2] + off_x, w["rect"][3] + off_y]

    return {"success": True, "line_count": len(lines), "lines": lines}


def ocr_room(room_name: str, region: Optional[List[int]] = None,
             lang: str = "ko") -> Dict:
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room_name}' not found"}
    res = ocr_hwnd(hwnd, region, lang)
    res["hwnd"] = hwnd
    return res


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def find_text(hwnd: int, text: str, region: Optional[List[int]] = None,
              lang: str = "ko") -> Dict:
    """Find a text in a window via OCR. Partial, whitespace-insensitive."""
    res = ocr_hwnd(hwnd, region, lang)
    if not res.get("success"):
        return res
    target = _norm(text)
    matches = [ln for ln in res["lines"] if target in _norm(ln["text"])]
    if not matches:
        return {
            "success": False,
            "error": f"Text '{text}' not found",
            "all_lines": [ln["text"] for ln in res["lines"]],
        }
    best = min(matches, key=lambda ln: len(_norm(ln["text"])))
    return {"success": True, "match": best,
            "match_count": len(matches),
            "matches": [m["text"] for m in matches]}


def click_text(hwnd: int, text: str, double_click: bool = False,
               region: Optional[List[int]] = None, lang: str = "ko") -> Dict:
    """OCR a window, find the given text, click its center."""
    found = find_text(hwnd, text, region, lang)
    if not found.get("success"):
        return found
    cx, cy = found["match"]["center"]
    res = click_at(hwnd, cx, cy, double_click)
    res["matched_text"] = found["match"]["text"]
    res["match_count"] = found["match_count"]
    return res
