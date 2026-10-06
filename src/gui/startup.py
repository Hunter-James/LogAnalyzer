"""Показ главного окна и доставка файлов после восстановления сессии."""
from collections import deque


def show_main_window(single_instance, startup_files):
    """Сохраняет запросы запуска до готовности окна и восстановленных вкладок."""
    pending_requests = deque()
    if startup_files:
        pending_requests.append(list(startup_files))
    # Конструктор окна обрабатывает события Qt при применении темы. Канал
    # уже слушает, поэтому подключаем очередь до импорта и создания окна.
    single_instance.requestReceived.connect(pending_requests.append)

    from gui.window import MainWindow
    window = MainWindow()

    def handle_external_request(paths):
        window.open_external_files(paths)
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    def finish_startup():
        # show_busy() внутри загрузки тоже обрабатывает события. Пока очередь
        # не опустела, новые запросы добавляются в неё без вложенного открытия.
        while pending_requests:
            handle_external_request(pending_requests.popleft())
        single_instance.requestReceived.disconnect(pending_requests.append)
        single_instance.requestReceived.connect(handle_external_request)
        window.sessionRestored.disconnect(finish_startup)

    window.sessionRestored.connect(finish_startup)
    window.show()
    return window
