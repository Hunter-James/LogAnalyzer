import sys
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox, 
                             QFrame, QLabel, QPushButton, QHBoxLayout)
from config import THEMES, APP_VERSION

# --- Settings Dialog ---
class SettingsDialog(QDialog):
    def __init__(self, current_theme, current_font_size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(350, 200)

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

        # Version Info
        version_frame = QFrame()
        version_frame.setFrameShape(QFrame.Shape.StyledPanel)
        version_layout = QVBoxLayout(version_frame)

        self.lbl_version = QLabel(f"Current Version: {APP_VERSION}")
        version_layout.addWidget(self.lbl_version)
        layout.addWidget(version_frame)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def get_settings(self):
        return self.theme_combo.currentText(), self.font_spin.value()
