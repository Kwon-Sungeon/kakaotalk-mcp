"""Exercise the real Windows common dialog on a separate, never activated desktop."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest
import win32api
import win32con
import win32process


@pytest.mark.skipif(os.getenv('KAKAO_TEST_PRIVATE_DESKTOP') != '1', reason='opt-in native dialog fixture')
def test_file_picker_on_private_desktop(tmp_path):
    from kakao_mcp import qt_bridge

    def wait_for(path):
        until = time.monotonic() + 10
        while not path.exists() and time.monotonic() < until:
            time.sleep(.05)
        return path.read_text(encoding='utf-8')

    filename = tmp_path / '첨부 sample.txt'
    filename.touch()
    env = dict(os.environ, FIXTURE_DIR=str(tmp_path), FIXTURE_FILE=str(filename),
               FIXTURE_SCRIPT=str(Path(qt_bridge.__file__).with_name('qt_uia.ps1')))
    desktop = 'KakaoPickerTest' + uuid.uuid4().hex
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    create = user32.CreateDesktopW
    create.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.LPVOID]
    create.restype = wintypes.HANDLE
    handle = create(desktop, None, None, 0, 0x1FF, None)
    assert handle, ctypes.get_last_error()
    processes = []

    def start(code):
        info = win32process.STARTUPINFO()
        info.lpDesktop = desktop
        exe = str(Path(os.environ['SystemRoot'], 'System32/WindowsPowerShell/v1.0/powershell.exe'))
        import base64
        encoded = base64.b64encode(code.encode('utf-16-le')).decode('ascii')
        command = subprocess.list2cmdline([exe, '-Sta', '-NoProfile', '-NonInteractive', '-WindowStyle',
                                           'Hidden', '-EncodedCommand', encoded])
        proc, thread, pid, _ = win32process.CreateProcess(
            exe, command, None, None, False, win32con.CREATE_NO_WINDOW, env, str(tmp_path), info)
        thread.Close()
        processes.append(proc)
        return pid

    try:
        env['FIXTURE_PID'] = str(start(r'''
Add-Type -AssemblyName System.Windows.Forms
$form=[Windows.Forms.Form]::new()
$form.Show()
[IO.File]::WriteAllText("$env:FIXTURE_DIR\hwnd",$form.Handle.ToInt64().ToString())
$picker=[Windows.Forms.OpenFileDialog]::new()
$picker.InitialDirectory=$env:FIXTURE_DIR
$null=$picker.ShowDialog($form)
[IO.File]::WriteAllText("$env:FIXTURE_DIR\selected",$picker.FileName)
'''))
        env['FIXTURE_HWND'] = wait_for(tmp_path / 'hwnd')
        start(r'''
$ErrorActionPreference='Stop'
try {
 $source=[IO.File]::ReadAllText($env:FIXTURE_SCRIPT)
 . ([scriptblock]::Create($source.Substring(0,$source.LastIndexOf('while($null -ne ($line='))))
 InitializeInput
 $request=@{hwnd=[int]$env:FIXTURE_HWND;process_id=[int]$env:FIXTURE_PID;path=$env:FIXTURE_FILE}
 function FileRoom {return [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$request.hwnd)}
 SelectFilePath
 [IO.File]::WriteAllText("$env:FIXTURE_DIR\status",'OK')
}catch{[IO.File]::WriteAllText("$env:FIXTURE_DIR\status",$_.Exception.Message)}
''')
        assert wait_for(tmp_path / 'status') == 'OK'
        assert wait_for(tmp_path / 'selected') == str(filename)
    finally:
        for proc in processes:
            try:
                win32api.TerminateProcess(proc, 0)
            except win32api.error:
                pass
            proc.Close()
        user32.CloseDesktop(wintypes.HANDLE(handle))
