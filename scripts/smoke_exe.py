"""Проверка запуска EXE и передачи файла вторым экземпляром на Windows."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil


def main():
    exe = Path(sys.argv[1]).resolve()
    sample = Path(sys.argv[2]).resolve()
    if any(p.info['exe'] and Path(p.info['exe']) == exe
           for p in psutil.process_iter(['exe'])):
        raise RuntimeError('Закройте запущенный проверяемый EXE')
    settings = exe.parent / 'settings.json'
    original = settings.read_bytes() if settings.exists() else None
    config = json.loads(original) if original else {}
    config.update(files_per_group=[[], []], files_left=[], files_right=[])
    log = exe.parent / 'crash_log.txt'
    old_log = log.read_bytes() if log.exists() else None
    proc = None
    secondary = None
    descendants = []
    try:
        settings.write_text(json.dumps(config), encoding='utf-8')
        log.unlink(missing_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM='windows')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        started = time.perf_counter()
        proc = subprocess.Popen([str(exe)], env=env, startupinfo=startup)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'EXE exited: {proc.returncode}')
            if log.exists() and 'Main window shown' in log.read_text(encoding='utf-8', errors='replace'):
                break
            time.sleep(.02)
        else:
            raise TimeoutError('Window startup')
        startup_s = time.perf_counter() - started
        started = time.perf_counter()
        print(json.dumps(dict(startup_s=startup_s)), flush=True)
        secondary = subprocess.Popen([str(exe), str(sample)], env=env, startupinfo=startup)
        secondary.wait(timeout=40)
        assert secondary.returncode == 0, secondary.returncode
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if 'LogLoader finished: path=' in log.read_text(encoding='utf-8', errors='replace'):
                break
            time.sleep(.02)
        else:
            raise TimeoutError('Forwarded file load')
        print(json.dumps(dict(startup_s=startup_s, second_instance_and_file_s=time.perf_counter() - started)))
        descendants = psutil.Process(proc.pid).children(recursive=True)
        ids = {proc.pid, *(p.pid for p in descendants)}
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        @callback_type
        def close_window(hwnd, param):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(hwnd, title, len(title))
            if pid.value in ids and 'Log Analyzer' in title.value:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
        user32.EnumWindows(close_window, 0)
        proc.wait(timeout=30)
        assert proc.returncode == 0, proc.returncode
        assert not log.exists(), 'Crash log left after closing'
        print('clean_exit=OK')
    finally:
        if secondary and secondary.poll() is None:
            for child in psutil.Process(secondary.pid).children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            secondary.kill()
            secondary.wait()
        if proc and proc.poll() is None:
            descendants = psutil.Process(proc.pid).children(recursive=True)
            for child in reversed(descendants):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            proc.kill()
            proc.wait()
        if original is None:
            settings.unlink(missing_ok=True)
        else:
            settings.write_bytes(original)
        if old_log is not None:
            log.write_bytes(old_log)


if __name__ == '__main__':
    main()
