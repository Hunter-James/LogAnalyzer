import sys
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox,
                             QFrame, QLabel, QPushButton, QHBoxLayout, QCheckBox,
                             QGroupBox, QScrollArea, QWidget, QSizePolicy)
from config import (THEMES, APP_VERSION, DEFAULT_UI_FEATURES, UI_FEATURE_LABELS,
                    UI_FEATURE_CATEGORIES)


class _WrapCheckBox(QWidget):
    """QCheckBox с переносом длинной подписи по словам.

    Стандартный QCheckBox не умеет word-wrap (текст уходит в одну строку и
    обрезается / появляется горизонтальный скроллбар). Обходим: рядом с
    голым чекбоксом кладём QLabel(wordWrap=True). Клик по label тоже
    переключает чекбокс - так юзер привычно тыкает в текст."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self._checkbox = QCheckBox()
        self._checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Клик по label = toggle чекбокса (привычное поведение)
        self._label.mousePressEvent = self._on_label_click
        h.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self._label, 1)

    def _on_label_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._checkbox.toggle()
        event.accept()

    def isChecked(self):
        return self._checkbox.isChecked()

    def setChecked(self, value):
        self._checkbox.setChecked(bool(value))


# --- Settings Dialog ---
class SettingsDialog(QDialog):
    # Live-превью: испускается при любой смене темы или размера шрифта в комбо/спине.
    # MainWindow подписывается, чтобы применять выбор сразу, а на Cancel - откатывать.
    previewChanged = pyqtSignal(str, int)  # theme_name, font_size

    def __init__(self, current_theme, current_font_size, current_features,
                 current_group_layout='stack', remember_split_layout=False,
                 fast_open_mode=False, fast_view_engine='list',
                 associate_log_files=True, associate_zip_files=False,
                 parent=None):
        super().__init__(parent)
        # Сохраняем для get_settings - чтобы вернуть как есть, если юзер
        # не трогал соответствующий контрол (для current_group_layout сейчас
        # нет UI - режим меняется через «Переместить в другую панель»).
        self._current_group_layout = current_group_layout
        self._open_default_apps_after_accept = False
        self.setWindowTitle("Настройки")
        # Увеличенный дефолтный размер: все 4 категории видны без скролла,
        # а длинные подписи (вроде «Режим разработчика (RAM-индикатор + ...)»)
        # переносятся на следующую строку благодаря _WrapCheckBox.
        self.resize(560, 720)

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
                cb = _WrapCheckBox(UI_FEATURE_LABELS.get(key, key))
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
                cb = _WrapCheckBox(UI_FEATURE_LABELS.get(key, key))
                cb.setChecked(bool(
                    current_features.get(key, DEFAULT_UI_FEATURES[key])))
                self.feature_checkboxes[key] = cb
                misc_layout.addWidget(cb)
            features_outer.addWidget(misc_box)

        # Категория «Группы» — отдельные опции которые не описываются
        # ui_features (это поведение приложения, а не видимость UI).
        groups_box = QGroupBox("Группы")
        groups_layout = QVBoxLayout(groups_box)
        groups_layout.setContentsMargins(10, 8, 10, 8)
        groups_layout.setSpacing(4)
        self.cb_remember_split = _WrapCheckBox(
            "Запоминать разделение экрана между запусками "
            "(иначе при старте всегда одна группа видна, чтобы не грузить "
            "много вкладок параллельно)")
        self.cb_remember_split.setChecked(bool(remember_split_layout))
        groups_layout.addWidget(self.cb_remember_split)
        features_outer.addWidget(groups_box)

        # Категория «Открытие файлов» — выбор режима парсинга.
        open_box = QGroupBox("Открытие файлов")
        open_layout = QVBoxLayout(open_box)
        open_layout.setContentsMargins(10, 8, 10, 8)
        open_layout.setSpacing(4)

        info_text = QLabel(
            "<b>Классический режим</b> (по умолчанию): полный парсинг "
            "начинается сразу. Все фичи (фильтры, партии, история кода) "
            "доступны через ~1–2 секунды для среднего файла, дольше для "
            "больших.<br>"
            "<b>Быстрый режим</b>: сначала открывается текст файла с "
            "маркерами ERROR/WARN на скроллбаре (~0.5с для среднего файла, "
            "как Notepad++). Полный анализ доезжает в фоне — пока он идёт, "
            "доступно только чтение и встроенный поиск (Ctrl+F). Когда "
            "анализ готов, окно автоматически переключается на полный режим.")
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #888; font-size: 9pt;")
        open_layout.addWidget(info_text)

        self.cb_fast_open = _WrapCheckBox(
            "Быстрый режим открытия (Two-stage: text-view → полный анализ "
            "в фоне)")
        self.cb_fast_open.setChecked(bool(fast_open_mode))
        open_layout.addWidget(self.cb_fast_open)

        # Движок быстрого просмотра (как показывать текст в Stage 1)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Движок просмотра:"))
        self.combo_view_engine = QComboBox()
        self.combo_view_engine.addItem("Список (быстро)", "list")
        self.combo_view_engine.addItem("Текст полностью", "full")
        self.combo_view_engine.addItem("Текст (первые 200K строк)", "limited")
        idx = self.combo_view_engine.findData(fast_view_engine)
        if idx >= 0:
            self.combo_view_engine.setCurrentIndex(idx)
        engine_row.addWidget(self.combo_view_engine)
        engine_row.addStretch()
        open_layout.addLayout(engine_row)

        engine_info = QLabel(
            "<b>Список</b> (рекомендуется): открытие мгновенное даже на "
            "миллионах строк. Выделение и копирование — целыми строками. "
            "Без посимвольного выделения мышью и Ctrl+F (полный поиск "
            "приедет с полным режимом через пару секунд).<br>"
            "<b>Текст полностью</b>: настоящий текстовый редактор — "
            "посимвольное выделение, Ctrl+F. Но на 1M+ строк открытие "
            "занимает несколько секунд (UI подвисает).<br>"
            "<b>Текст (первые 200K)</b>: редактор как выше, но показывает "
            "только начало файла — открытие быстрое, виден не весь лог.")
        engine_info.setWordWrap(True)
        engine_info.setStyleSheet("color: #888; font-size: 9pt;")
        open_layout.addWidget(engine_info)

        features_outer.addWidget(open_box)

        associations_box = QGroupBox("Открытие из Проводника")
        associations_layout = QVBoxLayout(associations_box)
        associations_layout.setContentsMargins(10, 8, 10, 8)
        associations_layout.setSpacing(4)

        self.cb_associate_log = _WrapCheckBox(
            "Добавить Log Analyzer в список приложений для файлов .log")
        self.cb_associate_log.setChecked(bool(associate_log_files))
        associations_layout.addWidget(self.cb_associate_log)

        self.cb_associate_zip = _WrapCheckBox(
            "Добавить Log Analyzer в список приложений для файлов .zip")
        self.cb_associate_zip.setChecked(bool(associate_zip_files))
        associations_layout.addWidget(self.cb_associate_zip)

        btn_default_apps = QPushButton("Выбрать приложение по умолчанию в Windows")
        btn_default_apps.setToolTip(
            "Применить настройки и открыть системную страницу приложений по умолчанию")
        btn_default_apps.clicked.connect(self._accept_and_open_default_apps)
        associations_layout.addWidget(btn_default_apps)
        features_outer.addWidget(associations_box)

        features_outer.addStretch(1)

        # Заворачиваем в QScrollArea, чтобы при добавлении новых категорий
        # диалог не разрастался бесконечно. Горизонтальный скроллбар жёстко
        # выключаем - иначе вместо word-wrap'а текст превратился бы в скролл.
        scroll = QScrollArea()
        scroll.setWidget(features_container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

    def _accept_and_open_default_apps(self):
        self._open_default_apps_after_accept = True
        self.accept()

    def should_open_default_apps(self):
        return self._open_default_apps_after_accept

    def get_settings(self):
        features = {key: cb.isChecked() for key, cb in self.feature_checkboxes.items()}
        # group_layout сейчас управляется не через диалог (Stack/Splitter
        # меняется только через «Переместить в другую панель»), поэтому
        # возвращаем переданное значение как есть.
        return (self.theme_combo.currentText(),
                self.font_spin.value(),
                features,
                self._current_group_layout,
                self.cb_remember_split.isChecked(),
                self.cb_fast_open.isChecked(),
                self.combo_view_engine.currentData(),
                self.cb_associate_log.isChecked(),
                self.cb_associate_zip.isChecked())
