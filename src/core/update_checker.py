"""Асинхронная проверка стабильного релиза Log Analyzer на GitHub."""
from dataclasses import dataclass
import json
import re
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from config import APP_VERSION


REPOSITORY = "Hunter-James/LogAnalyzer"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
CHECK_TIMEOUT_MS = 15000


@dataclass(frozen=True)
class Release:
    """Номер новой версии и страница с описанием и файлами релиза."""

    version: str
    url: str
    download_url: str = ""
    size: int = 0
    sha256: str = ""


def version_numbers(version):
    """Сравнивает компоненты vX.Y[.Z] как числа; X.Y означает X.Y.0."""
    if not isinstance(version, str):
        raise ValueError("Некорректный номер версии")
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if match is None:
        raise ValueError(f"Неизвестный формат версии: {version}")
    return tuple(int(part or 0) for part in match.groups())


def newer_release(payload, current_version=APP_VERSION):
    """Проверяет ответ GitHub и возвращает только более новый стабильный релиз."""
    if not isinstance(payload, dict):
        raise ValueError("GitHub вернул некорректное описание релиза")
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name")
    if version_numbers(tag) <= version_numbers(current_version):
        return None
    # Адрес строится из фиксированного репозитория, а не из HTML ответа сервера.
    page = f"{RELEASES_URL}/tag/{quote(tag, safe='')}"
    assets = payload.get("assets", [])
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("Некорректный список файлов релиза")
    for asset in assets:
        if asset.get("name") == "LogAnalyzer.exe" and asset.get("state") == "uploaded":
            size = asset.get("size")
            if not isinstance(size, int) or size <= 0:
                raise ValueError("Некорректный размер LogAnalyzer.exe")
            digest = asset.get("digest") or ""
            if not isinstance(digest, str) or (digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
                raise ValueError("Некорректная контрольная сумма LogAnalyzer.exe")
            return Release(tag.removeprefix("v"), page,
                           f"{RELEASES_URL}/download/{quote(tag, safe='')}/LogAnalyzer.exe",
                           size, digest.removeprefix("sha256:").lower())
    return Release(tag.removeprefix("v"), page)


class UpdateChecker(QObject):
    """Один сетевой запрос без блокировки GUI, с тайм-аутом и отменой при выходе."""

    completed = pyqtSignal(object)  # Release либо None, если обновления нет.
    failed = pyqtSignal(str)
    busyChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(CHECK_TIMEOUT_MS)
        self._timeout.timeout.connect(self._on_timeout)

    @property
    def busy(self):
        """True, пока выполняется запрос к GitHub."""
        return self._reply is not None

    def check(self):
        """Начинает проверку; повторный вызов не создаёт параллельного запроса."""
        if self.busy:
            return
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"LogAnalyzer/{APP_VERSION}".encode("ascii"))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda: self._on_finished(reply))
        self._timeout.start()
        self.busyChanged.emit(True)

    def cancel(self):
        """Отменяет запрос без сообщений об ошибке или запоздалого результата."""
        self._timeout.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
            self.busyChanged.emit(False)

    def _on_timeout(self):
        if self.busy:
            self.cancel()
            self.failed.emit("GitHub не ответил за 15 секунд. Попробуйте позже.")

    def _on_finished(self, reply):
        reply.deleteLater()
        if reply is not self._reply:
            return
        self._reply = None
        self._timeout.stop()
        self.busyChanged.emit(False)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status in (403, 429):
            self.failed.emit("GitHub ограничил запросы или доступ. Попробуйте позже.")
            return
        if status == 404:
            self.failed.emit("Последний релиз недоступен на GitHub.")
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(f"Не удалось связаться с GitHub.\n{reply.errorString()}")
            return
        if status != 200:
            self.failed.emit(f"GitHub вернул неожиданный ответ (HTTP {status}).")
            return
        try:
            release = newer_release(json.loads(bytes(reply.readAll())))
        except (ValueError, UnicodeError) as exc:
            self.failed.emit(f"Не удалось прочитать сведения об обновлении.\n{exc}")
            return
        self.completed.emit(release)
