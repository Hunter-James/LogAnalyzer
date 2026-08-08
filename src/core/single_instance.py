import ctypes
import json
import logging
import os
import time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


_log = logging.getLogger("single_instance")

SERVER_NAME = "LogAnalyzerEVOL-single-instance-v1"
DELIVERY_TIMEOUT_MS = 30000
ERROR_ALREADY_EXISTS = 183


class SingleInstanceChannel(QObject):
    requestReceived = pyqtSignal(list)

    def __init__(self, server_name=SERVER_NAME, parent=None):
        super().__init__(parent)
        self.server_name = server_name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._sockets = set()
        self._buffers = {}
        self._mutex_handle = None
        self._kernel32 = None

    def start_or_forward(self, file_paths):
        paths = [os.path.abspath(path) for path in file_paths]
        if os.name == "nt":
            if not self._acquire_windows_mutex():
                if self._forward_until_primary(paths):
                    return False
                if not self._acquire_windows_mutex():
                    raise RuntimeError(
                        "Основной экземпляр запущен, но не принимает файлы"
                    )
            if self._server.listen(self.server_name):
                return True
            self._close_windows_mutex()
            raise RuntimeError(
                f"Не удалось запустить канал одного экземпляра: "
                f"{self._server.errorString()}"
            )

        if self._forward_to_primary(paths, 250):
            return False

        if self._server.listen(self.server_name):
            return True

        # Два процесса могут стартовать почти одновременно. Если другой
        # успел занять имя сервера, повторно передаём ему запрос.
        if self._forward_to_primary(paths, 1000):
            return False

        # После аварийного завершения на Unix может остаться адрес сокета.
        # В Windows removeServer безопасно ничего не делает для живого pipe.
        QLocalServer.removeServer(self.server_name)
        if self._server.listen(self.server_name):
            return True

        raise RuntimeError(
            f"Не удалось запустить канал одного экземпляра: "
            f"{self._server.errorString()}"
        )

    def close(self):
        self._server.close()
        self._close_windows_mutex()

    def _acquire_windows_mutex(self):
        if self._kernel32 is None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32.CreateMutexW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_wchar_p,
            )
            self._kernel32.CreateMutexW.restype = ctypes.c_void_p
            self._kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            self._kernel32.CloseHandle.restype = ctypes.c_int

        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(
            None, False, "Local\\" + self.server_name
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return False
        self._mutex_handle = handle
        return True

    def _close_windows_mutex(self):
        if self._mutex_handle and self._kernel32:
            self._kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    def _forward_until_primary(self, file_paths):
        deadline = time.monotonic() + DELIVERY_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            if self._forward_to_primary(file_paths, 250):
                return True
            time.sleep(0.05)
        return False

    def _forward_to_primary(self, file_paths, timeout_ms):
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            return False

        payload = json.dumps(
            {"files": list(file_paths)}, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        written = socket.write(payload)
        socket.flush()
        if socket.bytesToWrite() > 0:
            socket.waitForBytesWritten(DELIVERY_TIMEOUT_MS)
        if written == len(payload):
            socket.waitForReadyRead(DELIVERY_TIMEOUT_MS)
        socket.disconnectFromServer()
        _log.info("Forwarded %s file(s) to the primary instance", len(file_paths))
        return written == len(payload)

    def _accept_connections(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._sockets.add(socket)
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(
                lambda current=socket: self._read_socket(current)
            )
            socket.disconnected.connect(
                lambda current=socket: self._drop_socket(current)
            )
            self._read_socket(socket)

    def _read_socket(self, socket):
        if socket not in self._buffers:
            return
        self._buffers[socket].extend(bytes(socket.readAll()))
        buffer = self._buffers[socket]
        while b"\n" in buffer:
            raw_message, remainder = buffer.split(b"\n", 1)
            self._buffers[socket] = bytearray(remainder)
            buffer = self._buffers[socket]
            if not raw_message:
                continue
            try:
                message = json.loads(raw_message.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("message must be an object")
                files = message.get("files", [])
                if not isinstance(files, list):
                    raise ValueError("files must be a list")
                socket.write(b"OK\n")
                socket.flush()
                self.requestReceived.emit([str(path) for path in files])
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                _log.exception("Invalid single-instance request")

    def _drop_socket(self, socket):
        self._read_socket(socket)
        self._buffers.pop(socket, None)
        self._sockets.discard(socket)
        socket.deleteLater()
