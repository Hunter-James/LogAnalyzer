import sys
import os
import json
import urllib.request
import subprocess
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, 
                             QProgressBar, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# Configuration
GITHUB_REPO = "Hunter-James/LogAnalyzerEVOL"
MAIN_EXE_NAME = "LogAnalyzer.exe"
VERSION_FILE = "../version.txt"

class UpdateWorker(QThread):
    progress = pyqtSignal(int)
    status_msg = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def run(self):
        try:
            # 1. Get current version
            current_version = "0.0.0"
            if os.path.exists(VERSION_FILE):
                with open(VERSION_FILE, "r") as f:
                    current_version = f.read().strip()
            
            self.status_msg.emit(f"Current version: {current_version}")
            self.progress.emit(10)

            # 2. Check GitHub for latest release
            self.status_msg.emit("Checking for updates...")
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={'User-Agent': 'LogAnalyzer-Updater'})
            
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
            
            latest_version = data['tag_name'].lstrip('v')
            self.progress.emit(30)

            if latest_version == current_version:
                self.status_msg.emit("App is up to date.")
                self.progress.emit(100)
                self.finished.emit(True, "No update needed")
                return

            self.status_msg.emit(f"New version found: {latest_version}")
            
            # 3. Find download URL
            download_url = None
            for asset in data['assets']:
                if asset['name'].endswith('.exe'):
                    download_url = asset['browser_download_url']
                    break
            
            if not download_url:
                self.finished.emit(False, "No executable found in release")
                return

            # 4. Download new version
            self.status_msg.emit("Downloading update...")
            temp_file = "update_temp.exe"
            
            def report_hook(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    percent = int((downloaded / total_size) * 50) + 30 # Map 0-100 to 30-80
                    self.progress.emit(min(percent, 80))

            urllib.request.urlretrieve(download_url, temp_file, report_hook)
            self.progress.emit(85)

            # 5. Replace old file
            self.status_msg.emit("Installing update...")
            
            if os.path.exists(MAIN_EXE_NAME):
                try:
                    os.remove(MAIN_EXE_NAME)
                except OSError:
                    pass
            
            if os.path.exists(temp_file):
                os.replace(temp_file, MAIN_EXE_NAME)
            
            # 6. Update version file
            with open(VERSION_FILE, "w") as f:
                f.write(latest_version)
            
            self.progress.emit(100)
            self.status_msg.emit("Update complete!")
            self.finished.emit(True, "Updated successfully")

        except Exception as e:
            self.finished.emit(False, str(e))

class LoaderWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(300, 150)
        
        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Background frame
        self.frame = QWidget()
        self.frame.setObjectName("Frame")
        self.frame.setStyleSheet("""
            #Frame {
                background-color: #2b2b2b;
                border: 1px solid #3e3e42;
                border-radius: 10px;
            }
            QLabel {
                color: #cccccc;
                font-family: 'Segoe UI';
            }
            QProgressBar {
                border: 1px solid #3e3e42;
                border-radius: 5px;
                text-align: center;
                background-color: #1e1e1e;
                color: #cccccc;
            }
            QProgressBar::chunk {
                background-color: #007acc;
                border-radius: 4px;
            }
        """)
        frame_layout = QVBoxLayout(self.frame)
        
        self.lbl_title = QLabel("Log Analyzer Updater")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.lbl_status = QLabel("Checking...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        
        frame_layout.addWidget(self.lbl_title)
        frame_layout.addStretch()
        frame_layout.addWidget(self.lbl_status)
        frame_layout.addWidget(self.progress)
        frame_layout.addStretch()
        
        main_layout.addWidget(self.frame)
        
        # Start update process
        self.worker = UpdateWorker()
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status_msg.connect(self.lbl_status.setText)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, success, message):
        if not success and message != "No update needed":
            QMessageBox.warning(self, "Update Failed", f"Could not update: {message}\nStarting application anyway.")
        
        # Launch Main Application
        if os.path.exists(MAIN_EXE_NAME):
            subprocess.Popen([MAIN_EXE_NAME])
            self.close()
        elif os.path.exists("main.py"): # Dev fallback
             subprocess.Popen([sys.executable, "main.py"])
             self.close()
        else:
             QMessageBox.critical(self, "Error", f"Could not find {MAIN_EXE_NAME}")
             self.close()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LoaderWindow()
    window.show()
    sys.exit(app.exec())
