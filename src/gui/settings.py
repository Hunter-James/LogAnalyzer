import sys
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox,
                             QFrame, QLabel, QPushButton, QHBoxLayout, QCheckBox,
                             QGroupBox, QScrollArea, QWidget)
from config import (THEMES, APP_VERSION, DEFAULT_UI_FEATURES, UI_FEATURE_LABELS,
                    UI_FEATURE_CATEGORIES)


# --- Settings Dialog ---
class SettingsDialog(QDialog):
    # Live-превью: испускается при любой смене темы или размера шрифта в комбо/спине.
    # MainWindow подписывается, чтобы применять выбор сразу, а на Cancel - откатывать.
    previewChanged = pyqtSignal(str, int)  # theme_name, font_size

    def __init__(self, current_theme, current_font_size, current_features,
                 current_group_layout='splitter', parent=None):
        # current_group_layout оставлен для backward-compat вызывающего кода,
        # но в текущей итерации не используется (Stack-режим - единственный).
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

        form.addRow("Тема:", self.theme_combo)
        form.addRow("Размер шрифта:", self.font_spin)
        appearance_group.setLayout(form)
        layout.addWidget(appearance_group)

        # --- Функции (видимость кнопок) ---
        # Чекбоксы разделены на категории. Скрытие убирает виджет из UI,
        # но не теряет состояние - можно вернуть в любой момент.
        # Каждая категория - QGroupBox внутри scrollable контейнера.
        self.feature_checkboxes = {}
        features_container = QWidget()
        features_outer = QVBoxLayout(features_container)
        features_outer.setContentsMargins(0, 0, 0, 0)
        features_outer.setSpacing(8)

        # Ключи, уже попавшие в какую-то категорию - чтобы случайно не
        # потерять фичу, если её забыли добавить в UI_FEATURE_CATEGORIES.
        categorized_keys = set()

        for category_name, feature_keys in UI_FEATURE_CATEGORIES.items():
            cat_box = QGroupBox(category_name)
            cat_layout = QVBoxLayout(cat_box)
            cat_layout.setContentsMargins(10, 8, 10, 8)
            cat_layout.setSpacing(4)
            for key in feature_keys:
                if key not in DEFAULT_UI_FEATURES:
                    continue  # пропускаем несуществующие в дефолтах
                cb = QCheckBox(UI_FEATURE_LABELS.get(key, key))
                cb.setChecked(bool(
                    current_features.get(key, DEFAULT_UI_FEATURES[key])))
                self.feature_checkboxes[key] = cb
                cat_layout.addWidget(cb)
                categorized_keys.add(key)
            features_outer.addWidget(cat_box)

        # Запасной блок «Прочее» - на случай если в DEFAULT_UI_FEATURES
        # появилась новая фича, а в UI_FEATURE_CATEGORIES её ещё не добавили.
        leftover = [k for k in DEFAULT_UI_FEATURES.keys()
                    if k not in categorized_keys]
        if leftover:
            misc_box = QGroupBox("Прочее")
            misc_layout = QVBoxLayout(misc_box)
            misc_layout.setContentsMargins(10, 8, 10, 8)
            misc_layout.setSpacing(4)
            for key in leftover:
                cb = QCheckBox(UI_FEATURE_LABELS.get(key, key))
                cb.setChecked(bool(
                    current_features.get(key, DEFAULT_UI_FEATURES[key])))
                self.feature_checkboxes[key] = cb
                misc_layout.addWidget(cb)
            features_outer.addWidget(misc_box)

        features_outer.addStretch(1)

        # Заворачиваем в QScrollArea, чтобы при добавлении новых категорий
        # диалог не разрастался бесконечно.
        scroll = QScrollArea()
        scroll.setWidget(features_container)
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
        # group_layout зарезервирован под будущий Splitter-режим;
        # пока всегда 'stack' (это единственный поддерживаемый режим).
        return (self.theme_combo.currentText(),
                self.font_spin.value(),
                features,
                'stack')
