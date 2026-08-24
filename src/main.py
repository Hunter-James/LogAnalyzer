import sys
import os
import atexit
import logging
import traceback
import tracemalloc

# Add the src directory to sys.path to allow imports from src
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# tracemalloc включаем ДО создания QApplication: чем раньше старт, тем
# точнее snapshot покажет реальные потребители памяти. frames=1 - храним
# только верхний фрейм аллокации (достаточно для отчёта file:line),
# overhead минимальный по сравнению с дефолтом frames=25.
if not tracemalloc.is_tracing():
    tracemalloc.start(1)


# --- Crash log ---
# Логи пишутся ВСЕГДА в crash_log.txt рядом с приложением. При нормальном
# выходе (юзер закрыл окно крестиком / Alt+F4 / тип exit() из меню) файл
# удаляется через atexit + QApplication.aboutToQuit. При краше Python
# (необработанное исключение) или OS (SIGKILL, OOM, segfault и т.п.) cleanup
# не происходит - файл остаётся для последующей отправки разработчику.

def _log_file_path():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'crash_log.txt')


LOG_PATH = _log_file_path()

_log_handler = None
_logging_configured = False
_logger = logging.getLogger('main')


def _configure_logging():
    global _log_handler, _logging_configured
    if _logging_configured:
        return

    # Вторичные процессы не открывают этот файл: иначе повторный запуск из
    # Проводника обнулил бы crash-log уже работающего основного экземпляра.
    _log_handler = logging.FileHandler(LOG_PATH, mode='w', encoding='utf-8')
    _log_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-5s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logging.basicConfig(level=logging.DEBUG, handlers=[_log_handler])
    _logging_configured = True
    _logger.info("Log Analyzer starting (frozen=%s, path=%s)",
                 getattr(sys, 'frozen', False),
                 sys.executable if getattr(sys, 'frozen', False) else __file__)

# Флаг краша. Устанавливается в excepthook + qt-message-handler.
# Если True - cleanup в atexit/aboutToQuit НЕ удалит файл.
_crashed = False


def _excepthook(exc_type, exc_value, exc_tb):
    """Перехват необработанных Python-исключений. Пишем stack trace в лог
    и ставим флаг _crashed чтобы файл сохранился после выхода."""
    global _crashed
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl+C - не считаем крашем, передаём стандартному обработчику
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _crashed = True
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _logger.critical("UNHANDLED EXCEPTION:\n%s", tb_text)


sys.excepthook = _excepthook


def _qt_message_handler(msg_type, context, message):
    """Логирует Qt-сообщения (Critical / Fatal помечают сессию как крах)."""
    global _crashed
    try:
        from PyQt6.QtCore import QtMsgType
        type_name = {
            QtMsgType.QtDebugMsg: 'QDEBUG',
            QtMsgType.QtInfoMsg: 'QINFO',
            QtMsgType.QtWarningMsg: 'QWARN',
            QtMsgType.QtCriticalMsg: 'QCRIT',
            QtMsgType.QtFatalMsg: 'QFATAL',
        }.get(msg_type, 'QT')
        if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            _logger.critical("[%s] %s", type_name, message)
            _crashed = True
        else:
            _logger.debug("[%s] %s", type_name, message)
    except Exception:
        pass


def _cleanup_log_if_clean_exit():
    """Удаляет crash_log.txt если приложение завершилось нормально.
    Вызывается через atexit (отрабатывает в самом конце Python-runtime)
    и через QApplication.aboutToQuit (срабатывает раньше)."""
    if not _logging_configured:
        return
    if _crashed:
        # Лог оставляем - есть на что посмотреть
        return
    try:
        # Закрываем все handler'ы чтобы Windows отпустил файл
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.close()
                root.removeHandler(h)
            except Exception:
                pass
        if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)
    except Exception:
        # Не падаем при cleanup - лог уже не нужен
        pass


atexit.register(_cleanup_log_if_clean_exit)

from gui.window import MainWindow  # noqa: E402
from core.single_instance import SingleInstanceChannel  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QTimer, qInstallMessageHandler  # noqa: E402


if __name__ == "__main__":
    startup_files = [os.path.abspath(arg) for arg in sys.argv[1:]
                     if os.path.isfile(arg)]

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    try:
        single_instance = SingleInstanceChannel()
        if not single_instance.start_or_forward(startup_files):
            sys.exit(0)

        _configure_logging()
        # Подключаем Qt-message-handler только в основном экземпляре.
        qInstallMessageHandler(_qt_message_handler)
        app.aboutToQuit.connect(_cleanup_log_if_clean_exit)

        window = MainWindow()
        window.show()

        def handle_external_request(paths):
            window.open_external_files(paths)
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()

        single_instance.requestReceived.connect(handle_external_request)
        if startup_files:
            QTimer.singleShot(
                0, lambda paths=tuple(startup_files): window.open_external_files(paths))
        _logger.info("Main window shown, entering event loop")
        exit_code = app.exec()
        _logger.info("Event loop exited with code %s", exit_code)
        sys.exit(exit_code)
    except Exception:
        # Дублируем catch здесь на случай если sys.excepthook не сработал
        # (например исключение из конструктора QApplication). traceback
        # пойдёт в _excepthook через raise, но на всякий случай явно
        # пометим краш.
        _crashed = True
        _logger.exception("Exception in main()")
        raise
