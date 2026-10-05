"""Загрузка проверенного EXE и замена приложения после штатного выхода."""
import base64
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


def powershell_literal(value):
    """Заключает путь в строку PowerShell без подстановок и исполнения символов."""
    return "'" + str(value).replace("'", "''") + "'"


def replacement_script(target, staged, process_id):
    """Ждёт старый процесс, сохраняет резервную копию и запускает новый EXE."""
    backup = staged.parent / "previous.exe"
    return f"""
$ErrorActionPreference = 'Stop'
$target = {powershell_literal(target)}
$staged = {powershell_literal(staged)}
$backup = {powershell_literal(backup)}
$folder = {powershell_literal(staged.parent)}
$moved = $false
try {{
    $old = Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue
    if ($old -and -not $old.WaitForExit(120000)) {{ throw 'Приложение не завершилось за 2 минуты.' }}
    # PyInstaller может ещё удерживать EXE родительским загрузчиком.
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not $moved) {{
        try {{
            Move-Item -LiteralPath $target -Destination $backup
            $moved = $true
        }} catch {{
            if ([DateTime]::UtcNow -ge $deadline) {{ throw }}
            Start-Sleep -Milliseconds 250
        }}
    }}
    Move-Item -LiteralPath $staged -Destination $target
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $target -WorkingDirectory (Split-Path -LiteralPath $target) -WindowStyle Hidden
    # Ошибка уборки не должна откатывать уже запущенное приложение.
    try {{
        Remove-Item -LiteralPath $backup
        [System.IO.Directory]::Delete($folder, $false)
    }} catch {{ }}
}} catch {{
    $problem = $_.Exception.Message
    if ($moved -and (Test-Path -LiteralPath $backup)) {{
        try {{
            if (Test-Path -LiteralPath $target) {{ Remove-Item -LiteralPath $target }}
            Move-Item -LiteralPath $backup -Destination $target
            $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
            Start-Process -FilePath $target -WorkingDirectory (Split-Path -LiteralPath $target) -WindowStyle Hidden
        }} catch {{ $problem += "`nНе удалось восстановить EXE: " + $_.Exception.Message }}
    }}
    $problem | Set-Content -LiteralPath (Join-Path $folder 'error.txt') -Encoding UTF8
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("Обновление не установлено.`n" + $problem + "`nФайлы: " + $folder, 'Log Analyzer')
}}
"""


def launch_replacement(target, staged, process_id):
    """Запускает отдельный скрытый процесс, не наследующий консоль приложения."""
    script = replacement_script(target, staged, process_id)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # PowerShell должен загружать системные DLL, а не библиотеки пакета PyInstaller.
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded],
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=str(target.parent),
        )
    finally:
        if bundled:
            ctypes.windll.kernel32.SetDllDirectoryW(str(bundled))


class UpdateInstaller(QObject):
    """Потоковая загрузка с прогрессом, отменой, проверкой размера и SHA-256."""

    progress = pyqtSignal(int, int)
    ready = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._file = None
        self._directory = None
        self._handed_off = False

    def start(self, release):
        """Скачивает обновление рядом с установленным EXE, не меняя текущий файл."""
        if self._reply is not None or self._handed_off:
            return
        if os.name != "nt" or not getattr(sys, "frozen", False):
            self.failed.emit("Автоустановка доступна в собранном LogAnalyzer.exe для Windows.")
            return
        if not release.download_url or release.size <= 0:
            self.failed.emit("В релизе отсутствует готовый LogAnalyzer.exe. Попробуйте позже.")
            return
        self._release = release
        self._target = Path(sys.executable).resolve()
        self._received = 0
        self._hash = hashlib.sha256()
        try:
            self._directory = Path(tempfile.mkdtemp(prefix=".log-analyzer-update-", dir=self._target.parent))
            self._staged = self._directory / "LogAnalyzer.exe"
            self._file = self._staged.open("wb")
        except OSError as exc:
            self._fail(f"Не удалось создать файл обновления рядом с приложением.\n{exc}")
            return
        request = QNetworkRequest(QUrl(release.download_url))
        request.setTransferTimeout(30000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._network.get(request)
        reply.setReadBufferSize(1024 * 1024)
        self._reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finish(reply))

    def cancel(self):
        """Удаляет незавершённую загрузку; переданный установщику файл сохраняется."""
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass  # При отмене частичный файл всё равно удаляется.
            finally:
                self._file = None
        if self._directory is not None and not self._handed_off:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    def install(self):
        """Передаёт готовый EXE установщику; вызывающий затем штатно закрывает окно."""
        try:
            launch_replacement(self._target, self._staged, os.getpid())
        except OSError as exc:
            self._fail(f"Не удалось запустить установку.\n{exc}")
            return False
        self._handed_off = True
        return True

    def _fail(self, message):
        self.cancel()
        self.failed.emit(message)

    def _read(self, reply):
        if reply is not self._reply:
            return
        data = bytes(reply.readAll())
        self._received += len(data)
        if self._received > self._release.size:
            self._fail("Размер скачанного файла превышает размер из описания релиза.")
            return
        try:
            self._file.write(data)
        except OSError as exc:
            self._fail(f"Не удалось сохранить обновление.\n{exc}")
            return
        self._hash.update(data)
        self.progress.emit(self._received, self._release.size)

    def _finish(self, reply):
        reply.deleteLater()
        if reply is not self._reply:
            return
        if (reply.error() != QNetworkReply.NetworkError.NoError or
                reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200):
            self._fail(f"Не удалось скачать обновление.\n{reply.errorString()}")
            return
        self._read(reply)
        if reply is not self._reply:
            return
        self._reply = None
        try:
            self._file.close()
            self._file = None
            if self._received != self._release.size:
                raise ValueError("Файл скачан не полностью.")
            if self._release.sha256 and self._hash.hexdigest() != self._release.sha256:
                raise ValueError("Контрольная сумма обновления не совпадает.")
            with self._staged.open("rb") as downloaded:
                if downloaded.read(2) != b"MZ":
                    raise ValueError("Скачанный файл не является Windows-приложением.")
        except (OSError, ValueError) as exc:
            self._fail(str(exc))
            return
        self.ready.emit()
