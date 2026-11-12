"""Serialize KakaoTalk desktop operations across MCP hosts and monitor threads."""
import contextlib
from pathlib import Path
import tempfile
import threading
import time

import msvcrt


_thread_lock = threading.RLock()
_lock_state = threading.local()


@contextlib.contextmanager
def operation():
    """Serialize UI operations across clients as well as monitor threads."""
    with _thread_lock:
        if getattr(_lock_state, 'held', False):
            yield
            return
        path = Path(tempfile.gettempdir()) / 'kakao-mcp-ui.lock'
        with path.open('a+b') as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 60
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Another KakaoTalk operation is busy')
                    time.sleep(.1)
            try:
                _lock_state.held = True
                yield
            finally:
                _lock_state.held = False
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
