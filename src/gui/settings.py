import sys
import os
import urllib.request
import subprocess
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox, 
                             QFrame, QLabel, QPushButton, QHBoxLayout, QMessageBox, QApplication)
from config import THEMES, APP_VERSION
from core.workers import UpdateWorker

# --- Settings Dialog ---
class SettingsDialog(QDialog):
    def __init__(self, current_theme, current_font_size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(350, 250)

        # Apply Theme to Dialog
        t = THEMES[current_theme]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QLabel {{ color: {t['text_main']}; }}
            QComboBox {{ 
                background-color: {t['bg_panel']}; 
                color: {t['text_main']}; 
                border: 1px solid {t['border']};
                padding: 4px;
            }}
            QSpinBox {{ 
                background-color: {t['bg_panel']}; 
                color: {t['text_main']}; 
                border: 1px solid {t['border']};
                padding: 4px;
            }}
            QPushButton {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: {t['accent']}; color: white; }}
        """)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES.keys())
        self.theme_combo.setCurrentText(current_theme)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 24)
        self.font_spin.setValue(current_font_size)

        form.addRow("Theme:", self.theme_combo)
        form.addRow("Font Size:", self.font_spin)

        layout.addLayout(form)

        # Update Section
        update_frame = QFrame()
        update_frame.setFrameShape(QFrame.Shape.StyledPanel)
        update_layout = QVBoxLayout(update_frame)

        self.lbl_version = QLabel(f"Current Version: {APP_VERSION}")
        self.btn_update = QPushButton("Check for Updates")
        self.btn_update.clicked.connect(self.check_for_updates)

        update_layout.addWidget(self.lbl_version)
        update_layout.addWidget(self.btn_update)
        layout.addWidget(update_frame)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        self.update_worker = None

    def get_settings(self):
        return self.theme_combo.currentText(), self.font_spin.value()

    def check_for_updates(self):
        self.btn_update.setEnabled(False)
        self.update_worker = UpdateWorker()
        self.update_worker.status_signal.connect(self.btn_update.setText)
        self.update_worker.finished_signal.connect(self.on_update_checked)
        self.update_worker.start()

    def on_update_checked(self, success, url, message):
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Check for Updates")

        if success:
            reply = QMessageBox.question(
                self, "Update Available",
                f"New version {message} is available.\nDo you want to download and install it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.perform_update(url)
        else:
            QMessageBox.information(self, "Update Info", message)

    # ИСПРАВЛЕНИЕ: Вынес метод из on_update_checked, чтобы он был методом класса
    def perform_update(self, url):
        # 1. Проверяем, запущен ли код как скомпилированный exe
        if not getattr(sys, 'frozen', False):
            QMessageBox.warning(self, "Update Error",
                                "Auto-update is only supported when running as a compiled .exe file.")
            return

        try:
            self.btn_update.setText("Downloading...")
            self.btn_update.setEnabled(False)
            QApplication.processEvents()

            current_exe = sys.executable
            exe_dir = os.path.dirname(current_exe)

            # Делаем пути абсолютными и безопасными
            new_exe_path = os.path.join(exe_dir, "update_temp.exe")
            bat_path = os.path.join(exe_dir, "updater.bat")

            # 2. Скачиваем новый exe
            urllib.request.urlretrieve(url, new_exe_path)

            # 3. Создаем Batch-скрипт
            batch_script = f"""@echo off
timeout /t 2 /nobreak > NUL
move /y "{new_exe_path}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
"""
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(batch_script)

            # 4. Очищаем окружение и запускаем bat-файл отвязанным
            env = os.environ.copy()
            env.pop('_MEIPASS2', None)
            env.pop('_MEIPASS', None)

            # Специфичные для Windows флаги, чтобы запустить скрипт невидимым
            CREATE_NO_WINDOW = 0x08000000

            subprocess.Popen(
                bat_path,
                shell=True,
                env=env,
                creationflags=CREATE_NO_WINDOW
            )

            # 5. Выходим из приложения, чтобы освободить файл
            QApplication.quit()
            sys.exit(0)

        except Exception as e:
            QMessageBox.critical(self, "Update Failed", str(e))
            self.btn_update.setText("Check for Updates")
            self.btn_update.setEnabled(True)
