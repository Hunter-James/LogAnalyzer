"""Окно обновления и показ результатов ручной и автоматической проверки."""
from PyQt6.QtCore import QObject, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QMessageBox, QStyle, QVBoxLayout,
)

from config import APP_VERSION
from core.update_checker import UpdateChecker


class UpdateAvailableDialog(QDialog):
    """Предлагает открыть страницу новой версии и её список изменений."""

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release
        self.setWindowTitle("Доступно обновление Log Analyzer")
        self.setMinimumWidth(380)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(18)
        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxQuestion).pixmap(48, 48))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        content = QVBoxLayout()
        message = QLabel(f"Доступна новая версия {release.version}.\nХотите обновить?")
        message.setTextFormat(Qt.TextFormat.PlainText)
        content.addWidget(message)
        link = QLabel('<a href="release">Открыть список изменений…</a>')
        link.linkActivated.connect(self._open_release_page)
        content.addWidget(link)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        yes = buttons.button(QDialogButtonBox.StandardButton.Yes)
        yes.setText("Да")
        yes.setToolTip("Открыть страницу релиза GitHub для скачивания")
        no = buttons.button(QDialogButtonBox.StandardButton.No)
        no.setText("Нет")
        no.setDefault(True)
        buttons.accepted.connect(self._accept_update)
        buttons.rejected.connect(self.reject)
        content.addWidget(buttons)
        layout.addLayout(content)

    def _open_release_page(self):
        opened = QDesktopServices.openUrl(QUrl(self.release.url))
        if not opened:
            QMessageBox.warning(self, "Обновление", "Не удалось открыть браузер.\n" + self.release.url)
        return opened

    def _accept_update(self):
        if self._open_release_page():
            self.accept()


class UpdateController(QObject):
    """Объединяет проверки и показывает только нужные пользователю сообщения."""

    def __init__(self, window):
        super().__init__(window)
        self.checker = UpdateChecker(self)
        self.checker.completed.connect(self._on_completed)
        self.checker.failed.connect(self._on_failed)
        self._manual = False
        self._dialog = None

    def check_manually(self):
        """Ручная проверка всегда сообщает результат, даже если запрос уже шёл."""
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._manual = True
        self.checker.check()

    def check_automatically(self):
        """При запуске сообщает только о новой версии; ошибки сети остаются тихими."""
        if not self.checker.busy and self._dialog is None:
            self._manual = False
            self.checker.check()

    def close(self):
        """Отменяет проверку и закрывает её окно при выходе из приложения."""
        self.checker.cancel()
        if self._dialog is not None:
            self._dialog.reject()

    def _dialog_parent(self):
        return QApplication.activeModalWidget() or self.parent()

    def _show_dialog(self, dialog):
        self._dialog = dialog
        dialog.finished.connect(self._forget_dialog)
        dialog.destroyed.connect(lambda: self._forget_destroyed_dialog(dialog))
        dialog.open()

    def _forget_destroyed_dialog(self, dialog):
        # Родительское модальное окно может быть уничтожено без finished у ребёнка.
        if self._dialog is dialog:
            self._dialog = None

    def _forget_dialog(self):
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.deleteLater()

    def _show_message(self, icon, text):
        message = QMessageBox(icon, "Проверка обновлений", text,
                              QMessageBox.StandardButton.Ok, self._dialog_parent())
        message.setTextFormat(Qt.TextFormat.PlainText)
        self._show_dialog(message)

    def _on_completed(self, release):
        if release is not None:
            self._show_dialog(UpdateAvailableDialog(release, self._dialog_parent()))
        elif self._manual:
            self._show_message(QMessageBox.Icon.Information,
                               f"Новых версий нет.\nТекущая версия: {APP_VERSION}.")

    def _on_failed(self, error):
        if self._manual:
            self._show_message(QMessageBox.Icon.Warning, error)
