import sys
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox,
                             QFrame, QLabel, QPushButton, QHBoxLayout, QCheckBox,
                             QGroupBox, QScrollArea, QWidget)
from config import THEMES, APP_VERSION, DEFAULT_UI_FEATURES, UI_FEATURE_LABELS


# --- Settings Dialog ---
class SettingsDialog(QDialog):
    # Live-превью: испускается при любой смене темы или размера шрифта в комбо/спине.
    # MainWindow подписывается, чтобы применять выбор сразу, а на Cancel - откатывать.
    previewChanged = pyqtSignal(str, int)  # theme_name, font_size

    def __init__(self, current_theme, current_font_size, current_features,
                 current_group_layout='splitter', parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(460, 540)

        # Стиль сам диалога зависит от темы; применяем в отдельном методе,
        # чтобы пере-применять при live-превью (иначе старая стилизация
        # диалога остаётся и каскадно накладывается на новую тему окна).
        self._apply_theme_stylesheet(current_theme)

        layout = QVBoxLayout(self)

        # --- Внешний вид ---
        appearance_group = QGroupBox("Внешний вид")
        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES.keys())
        self.theme_combo.setCurrentText(current_theme)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 24)
        self.font_spin.setValue(current_font_size)

        # Live-превью: любая смена темы/шрифта сразу испускает previewChanged.
        # MainWindow ловит это и применяет на лету (на Cancel откатывает).
        self.theme_combo.currentTextChanged.connect(self._emit_preview)
        self.font_spin.valueChanged.connect(self._emit_preview)

        # Режим расположения групп: Splitter (рядом) / Stack (стек, переключение
        # по клику на плашку). Применяется по OK (не live).
        self.group_layout_combo = QComboBox()
        self.group_layout_combo.addItem("Рядом (Splitter)", "splitter")
        self.group_layout_combo.addItem("Стек (одна активна)", "stack")
        idx = self.group_layout_combo.findData(current_group_layout or 'splitter')
        if idx < 0:
            idx = 0
        self.group_layout_combo.setCurrentIndex(idx)

        form.addRow("Тема:", self.theme_combo)
        form.addRow("Размер шрифта:", self.font_spin)
        form.addRow("Расположение групп:", self.group_layout_combo)
        appearance_group.setLayout(form)
        layout.addWidget(appearance_group)

        # --- Функции (видимость кнопок) ---
        # Чекбоксы для каждой опциональной фичи. Скрытие убирает виджет из UI,
        # но не теряет состояние - можно вернуть в любой момент.
        features_group = QGroupBox("Функции (отключите неиспользуемые — UI станет чище)")
        features_layout = QVBoxLayout()
        self.feature_checkboxes = {}
        for key in DEFAULT_UI_FEATURES.keys():
            cb = QCheckBox(UI_FEATURE_LABELS.get(key, key))
            cb.setChecked(bool(current_features.get(key, DEFAULT_UI_FEATURES[key])))
            self.feature_checkboxes[key] = cb
            features_layout.addWidget(cb)

        features_group.setLayout(features_layout)

        # Заворачиваем в QScrollArea на случай если фич станет много
        scroll = QScrollArea()
        scroll.setWidget(features_group)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        # --- Версия ---
        version_frame = QFrame()
        version_frame.setFrameShape(QFrame.Shape.StyledPanel)
        version_layout = QVBoxLayout(version_frame)
        self.lbl_version = QLabel(f"Текущая версия: {APP_VERSION}")
        version_layout.addWidget(self.lbl_version)
        layout.addWidget(version_frame)

        # --- Кнопки ---
        btn_box = QHBoxLayout()
        btn_ok = QPushButton("ОК")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)

        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def _apply_theme_stylesheet(self, theme_name):
        """Перерисовывает стиль самого диалога под выбранную тему. Вызывается
        в __init__ и при каждом live-превью, иначе предыдущая стилизация
        каскадно конфликтует с новыми цветами темы основного окна."""
        t = THEMES[theme_name]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QLabel, QCheckBox, QGroupBox {{ color: {t['text_main']}; }}
            QGroupBox {{
                border: 1px solid {t['border']};
                border-radius: 3px;
                margin-top: 10px;
                padding-top: 8px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QComboBox, QSpinBox {{
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
            QScrollArea {{ border: none; background-color: {t['bg_main']}; }}
            QFrame {{ background-color: {t['bg_panel']}; color: {t['text_main']}; }}
        """)

    def _emit_preview(self, *_):
        # Сначала перерисовываем сам диалог под новую тему (чтобы он не
        # конфликтовал с темой главного окна), потом - испускаем сигнал наружу.
        self._apply_theme_stylesheet(self.theme_combo.currentText())
        self.previewChanged.emit(self.theme_combo.currentText(), self.font_spin.value())

    def get_settings(self):
        features = {key: cb.isChecked() for key, cb in self.feature_checkboxes.items()}
        group_layout = self.group_layout_combo.currentData() or 'splitter'
        return (self.theme_combo.currentText(),
                self.font_spin.value(),
                features,
                group_layout)
