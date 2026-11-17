"""One STA UI Automation process per MCP server. Requests are serialized by ui_lock."""
import atexit
from concurrent.futures import Future, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import subprocess
import threading


class WorkerError(RuntimeError):
    def __init__(self, response):
        super().__init__(response['error'])
        self.before_send = response.get('before_send', False)
        self.code = response.get('code') or 'operation_failed'


class Bridge:
    def __init__(self):
        self.process = None
        self._script_mtime = None

    def close(self):
        process, self.process = self.process, None
        self._script_mtime = None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def start(self):
        # Explicit UTF-8 also works on PowerShell 5.1 machines with a different
        # ANSI code page. Keep scripts and chat text out of the command line.
        # [IO.File]::ReadAllText strips any UTF-8 BOM automatically.
        command = (
            "[Console]::InputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            "& ([scriptblock]::Create([IO.File]::ReadAllText($env:KAKAO_MCP_UIA_SCRIPT)))"
        )
        return subprocess.Popen(
            [str(Path(os.environ['SystemRoot'], 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')),
             '-NoLogo', '-NoProfile', '-Sta', '-NonInteractive', '-Command', command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, 'KAKAO_MCP_UIA_SCRIPT': str(Path(__file__).with_name('qt_uia.ps1'))},
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _current_script_mtime(self):
        try:
            return Path(__file__).with_name('qt_uia.ps1').stat().st_mtime_ns
        except OSError:
            return None

    def request(self, payload, timeout):
        script_mtime = self._current_script_mtime()
        if (self.process is None or self.process.poll() is not None
                or (self._script_mtime is not None and script_mtime != self._script_mtime)):
            self.close()
            self.process = self.start()
            self._script_mtime = script_mtime
        process = self.process
        reply = Future()

        def exchange():
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=True).encode('ascii') + b'\n')
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError('UI Automation worker closed its output')
                if not reply.done():
                    reply.set_result(line)
            except Exception as error:
                if not reply.done():
                    reply.set_exception(error)

        # Bound pipe writes as well as reads if the provider or process hangs.
        threading.Thread(target=exchange, daemon=True).start()
        try:
            line = reply.result(timeout=timeout).decode('utf-8-sig', errors='replace')
            response = json.loads(line)
            if not isinstance(response, dict) or 'success' not in response:
                raise ValueError('Invalid worker response')
        except FutureTimeout as error:
            self.close()
            raise RuntimeError(f'UI Automation timed out after {timeout}s. Check the room before retrying.') from error
        except Exception as error:
            self.close()
            detail = line.strip()[:300] if 'line' in locals() else str(error)
            raise RuntimeError(f'UI Automation worker failed: {detail}. Check the room before retrying.') from error
        if not response['success']:
            raise WorkerError(response)
        return response['data']


bridge = Bridge()
atexit.register(bridge.close)
