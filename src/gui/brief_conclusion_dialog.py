"""Фоновое заключение по файлу и просмотр записей, подтверждающих выводы."""

from html import escape

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QApplication

from core.brief_conclusion import BriefConclusion
from core.thread_lifecycle import retired_threads


def conclusion_html(conclusion):
    """HTML заключения со ссылками на исходные логические записи."""
    parts = ['<h2>Краткое заключение</h2>',
             '<p>Область: выбранная партия или весь текущий файл. '
             'Другие вкладки не включены. Порядок событий — порядок записей файла; '
             'журнал может быть неполным.</p>']
    for item in conclusion:
        parts.append('<p>' + escape(item['text']))
        for index, timestamp, _ in dict.fromkeys(item['evidence']):
            parts.append(f' <a href="entry:{index}">[запись {index + 1}, '
                         f'{escape(timestamp or "без времени")}]</a>')
        parts.append('</p>')
    return ''.join(parts)


class ConclusionWorker(QThread):
    """Обходит журнал вне GUI; закрытие окна отменяет вычисление."""

    ready = pyqtSignal(object)

    def __init__(self, entries, profile):
        super().__init__()
        self.entries = entries
        self.profile = profile

    def run(self):
        conclusion = BriefConclusion(self.profile, None)
        for index in range(len(self.entries)):
            if index % 512 == 0 and self.isInterruptionRequested():
                return
            conclusion.observe(self.entries[index], index)
        if not self.isInterruptionRequested():
            self.ready.emit(conclusion.build())


class BriefConclusionDialog(QDialog):
    """Короткий итог по всему файлу, включая ошибки вне выбранной партии."""

    def __init__(self, entries, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Краткое заключение — текущий файл')
        self.resize(850, 650)
        self.entries = entries
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setText('Формируется заключение…')
        self.browser.anchorClicked.connect(self.show_evidence)
        layout.addWidget(self.browser, 2)
        self.evidence = QTextBrowser()
        self.evidence.setPlaceholderText('Нажмите на ссылку записи, чтобы увидеть основание вывода.')
        self.evidence.setMaximumHeight(160)
        self.evidence.hide()
        layout.addWidget(self.evidence, 1)
        self.copy_button = QPushButton('Копировать заключение')
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.browser.toPlainText()))
        layout.addWidget(self.copy_button)
        close = QPushButton('Закрыть')
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.worker = ConclusionWorker(entries, profile)
        self.worker.ready.connect(self.show_result)
        self.finished.connect(self.stop_worker)
        self.worker.start()

    def show_result(self, result):
        """Публикует готовое заключение в GUI-потоке."""
        self.browser.setHtml(conclusion_html(result))
        self.copy_button.setEnabled(True)

    def show_evidence(self, url):
        """Открывает исходную запись без зависимости от фильтров журнала."""
        if url.scheme() == 'entry':
            index = int(url.path())
            if 0 <= index < len(self.entries):
                self.evidence.setPlainText(self.entries[index].message)
                self.evidence.show()

    def stop_worker(self, _result):
        """Сохраняет поток живым до фактического завершения отмены."""
        self.worker.ready.disconnect(self.show_result)
        retired_threads().retire(self.worker)
