"""Модальный диалог «Сводка партий по группе».

Запускает GroupStatsWorker над списком файлов активной группы, показывает
прогресс по файлам, по окончании — дерево партий с агрегированной
статистикой, списком файлов, временным диапазоном и кодами."""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QStackedWidget, QWidget,
                             QAbstractItemView, QLineEdit, QFileDialog,
                             QMessageBox, QMenu)

from core.group_stats_worker import GroupStatsWorker
from core.group_stats_export import (
    export_csv, export_json, export_html, export_xlsx)
from core.models import NO_BATCH
from config import THEMES


class GroupStatsDialog(QDialog):
    """Окно: при открытии стартует worker; пока он считает — виден прогресс,
    после — дерево результатов. Кнопка «Отмена» прерывает worker, дерево
    показывает то что успели насчитать."""

    # Лимит уникальных кодов на партию в дереве - больше класть нет смысла,
    # юзер всё равно столько вручную не просмотрит.
    MAX_CODES_PER_BATCH = 5000

    def __init__(self, file_paths, group_name='', theme_name='Default', parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Сводка партий — {group_name}" if group_name else "Сводка партий")
        self.resize(900, 700)
        self._theme_name = theme_name if theme_name in THEMES else 'Default'
        # Хранится после _on_finished — нужно для экспорта/сравнения/фильтра
        self._batches = {}

        v = QVBoxLayout(self)

        # Прогресс-блок: показывается пока worker работает
        self._progress_widget = self._build_progress_widget()
        # Результат-блок: дерево + кнопка закрытия
        self._result_widget, self._tree = self._build_result_widget()

        # Stack чтобы не плодить условную видимость
        self._stack = QStackedWidget()
        self._stack.addWidget(self._progress_widget)
        self._stack.addWidget(self._result_widget)
        self._stack.setCurrentWidget(self._progress_widget)
        v.addWidget(self._stack)

        # Стилизация под тему окна
        self._apply_theme()

        # Запускаем worker
        self._worker = GroupStatsWorker(file_paths)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._total_files = len(file_paths)
        self._worker.start()

    # ----- UI builders -----

    def _build_progress_widget(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addStretch()

        self._lbl_progress_title = QLabel("Анализ партий по всем файлам группы …")
        self._lbl_progress_title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self._lbl_progress_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_progress_title)

        self._lbl_progress_file = QLabel("")
        self._lbl_progress_file.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_progress_file)

        self._pbar = QProgressBar()
        self._pbar.setRange(0, 100)
        layout.addWidget(self._pbar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("Отмена")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    def _build_result_widget(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)

        self._lbl_summary = QLabel("")
        layout.addWidget(self._lbl_summary)

        # Фильтр + кнопки экспорта в одной строке над деревом
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтр:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(
            "Часть batch_id или кода — скроет партии без совпадений")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit, 1)

        self._btn_export = QPushButton("💾 Экспорт …")
        self._btn_export.setToolTip(
            "Сохранить результат в HTML (с раскрывающимися блоками), "
            "XLSX (с группировкой строк), CSV (3 файла: сводка + файлы + коды) "
            "или JSON.")
        self._btn_export.clicked.connect(self._on_export)
        filter_row.addWidget(self._btn_export)
        layout.addLayout(filter_row)

        tree = QTreeWidget()
        tree.setHeaderLabels(["Партия / Категория", "Значение"])
        tree.setColumnWidth(0, 480)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Контекстное меню: «Сравнить с одним файлом»
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        layout.addWidget(tree, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        return w, tree

    def _apply_theme(self):
        t = THEMES[self._theme_name]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QLabel {{ color: {t['text_main']}; }}
            QProgressBar {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                text-align: center;
            }}
            QProgressBar::chunk {{ background-color: {t['accent']}; }}
            QPushButton {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                padding: 6px 16px;
            }}
            QPushButton:hover {{ background-color: {t['accent']}; color: white; }}
            QTreeWidget {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
            }}
        """)

    # ----- Worker callbacks -----

    def _on_progress(self, current, total, filename):
        # Worker эмитит current=total с пустым filename как «финальный 100%».
        if current >= total:
            self._pbar.setValue(100)
            self._lbl_progress_file.setText("Все файлы обработаны, собираю результат …")
            return
        pct = int((current + 1) / max(1, total) * 100)
        self._pbar.setValue(pct)
        self._lbl_progress_file.setText(f"Файл {current + 1} из {total}: {filename}")

    def _on_finished(self, batches, error_msg):
        if error_msg == '__CANCELLED__':
            # Юзер нажал «Отмена» — показываем что успели насчитать.
            self._lbl_summary.setText(
                f"<i>Анализ отменён. Показаны частичные данные по "
                f"{len(batches)} партии(ям).</i>"
            )
        elif error_msg:
            self._lbl_summary.setText(f"<i>Ошибка: {error_msg}</i>")
        else:
            self._lbl_summary.setText(
                f"<b>Найдено партий: {len(batches)}.</b> "
                f"Двойной клик по партии — развернуть/свернуть. "
                f"Правый клик — сравнить с одним файлом."
            )
        # Сохраняем для экспорта / сравнения / фильтра
        self._batches = batches
        self._populate_tree(batches)
        self._stack.setCurrentWidget(self._result_widget)

    def _on_cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._btn_cancel.setEnabled(False)
            self._btn_cancel.setText("Останавливаем …")

    # ----- Tree population -----

    def _populate_tree(self, batches):
        """Раскладываем batches в дерево. ЛЕНИВО: для каждой партии создаём
        только root + placeholder («…»). Реальные дети — статистика, файлы,
        диапазон, коды — строятся в _on_item_expanded при первом раскрытии
        партии. Без этого на 1000+ партиях × 5000 кодов дерево создавало бы
        миллионы QTreeWidgetItem на UI потоке — окно «не отвечает» на
        десятки минут."""
        self._tree.clear()
        if not batches:
            return

        # Сортируем партии: сначала с известным first_ts (хронологически),
        # потом NO_BATCH («вне партии») в самый конец.
        def sort_key(item):
            bid, data = item
            no_batch_flag = 1 if bid == NO_BATCH else 0
            ts = data['first_ts'] or '99:99:99.999'
            first_file = data.get('first_file', '')
            return (no_batch_flag, first_file, ts)

        # itemExpanded подключаем здесь (а не в _build_result_widget),
        # чтобы не висел сигнал при пустом дереве.
        try:
            self._tree.itemExpanded.disconnect(self._on_item_expanded)
        except (TypeError, RuntimeError):
            pass
        self._tree.itemExpanded.connect(self._on_item_expanded)

        self._tree.setUpdatesEnabled(False)
        try:
            for bid, data in sorted(batches.items(), key=sort_key):
                counters = data['counters']
                files = data['files']
                codes = data['codes']
                total_events = sum(counters.values())

                if bid == NO_BATCH:
                    title = (f"Вне партии  —  {total_events:,} событий, "
                             f"{len(files)} файл(ов), {len(codes):,} уникальных кодов")
                else:
                    title = (f"Партия {bid}  —  {total_events:,} событий, "
                             f"{len(files)} файл(ов), {len(codes):,} уникальных кодов")
                root = QTreeWidgetItem(self._tree, [title, ""])
                # Ассоциация: batch_id хранится в UserRole, нужен для
                # контекстного меню «Сравнить с файлом», фильтра и
                # lazy-expand'а.
                root.setData(0, Qt.ItemDataRole.UserRole, ('batch', bid))
                # Placeholder («…») — нужен чтобы Qt показал стрелочку
                # раскрытия. Реальные дети строятся в _on_item_expanded.
                placeholder = QTreeWidgetItem(root, ["…", ""])
                placeholder.setData(0, Qt.ItemDataRole.UserRole,
                                    ('placeholder', None))
        finally:
            self._tree.setUpdatesEnabled(True)

    def _on_item_expanded(self, item):
        """Lazy-builds. Обрабатывает два типа узлов:
          ('batch', bid)        → строим стат/файлы/диапазон/codes_node
          ('codes', bucket)     → строим code-items под codes_node
        Идемпотентно — повторное раскрытие не строит второй раз."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data[0]
        if kind not in ('batch', 'codes'):
            return

        # Ищем placeholder среди прямых детей
        placeholder_kind = ('placeholder' if kind == 'batch'
                            else 'codes_placeholder')
        ph_idx = -1
        for i in range(item.childCount()):
            ch = item.child(i)
            cd = ch.data(0, Qt.ItemDataRole.UserRole)
            if cd and cd[0] == placeholder_kind:
                ph_idx = i
                break
        if ph_idx < 0:
            return  # уже подгружали
        item.takeChild(ph_idx)

        if kind == 'batch':
            bid = data[1]
            bucket = self._batches.get(bid)
            if bucket is None:
                return
            self._build_batch_children(item, bucket)
        else:  # 'codes'
            bucket = data[1]
            self._build_codes_children(item, bucket)

    def _build_batch_children(self, root, bucket):
        """Строит статистика / файлы / диапазон / коды под root-партией.
        Вызывается из _on_item_expanded — на UI потоке, но затрагивает
        только одну партию, не миллионы."""
        t = THEMES[self._theme_name]
        info_c = QColor(t.get('info', '#2E8B57'))
        warn_c = QColor(t.get('warn', '#FFA500'))
        error_c = QColor(t.get('error', '#CD5C5C'))
        muted_c = QColor(t.get('text_muted', '#999999'))

        counters = bucket['counters']
        files = bucket['files']
        codes = bucket['codes']

        # 📊 Статистика
        stats_node = QTreeWidgetItem(root, ["📊 Статистика", ""])
        rows = [
            ("Напечатано", counters['printed'], info_c),
            ("Прочитано", counters['scanned'], info_c),
            ("No read", counters['noread'], warn_c),
            ("Верифицировано", counters['verified'], info_c),
            ("Отбраковано", counters['rejected'], error_c),
            ("Не верифицировано", counters['not_verified'], warn_c),
        ]
        for label, n, color in rows:
            ci = QTreeWidgetItem(stats_node, [label, f"{n:,}"])
            ci.setForeground(0, color if n else muted_c)
            ci.setForeground(1, color if n else muted_c)

        # 📂 Файлы
        if files:
            files_node = QTreeWidgetItem(root, [f"📂 Файлы ({len(files)})", ""])
            for path in sorted(files.keys()):
                cnt = files[path]
                QTreeWidgetItem(files_node,
                                [os.path.basename(path),
                                 f"{cnt:,} событий"])

        # 📅 Временной диапазон
        if bucket.get('first_ts'):
            range_node = QTreeWidgetItem(root, ["📅 Диапазон", ""])
            QTreeWidgetItem(range_node, [
                f"начало: {bucket['first_ts']}",
                os.path.basename(bucket['first_file'])])
            QTreeWidgetItem(range_node, [
                f"конец: {bucket['last_ts']}",
                os.path.basename(bucket['last_file'])])

        # 🔑 Уникальные коды — тоже LAZY: создаём codes_node с placeholder,
        # реальные code-items строятся при раскрытии codes_node. Иначе
        # партия с 5000 кодами заморозит UI на ~1с при первом expand'е.
        if codes:
            codes_node = QTreeWidgetItem(root, [
                f"🔑 Уникальные коды ({len(codes):,})", ""])
            codes_node.setData(0, Qt.ItemDataRole.UserRole,
                               ('codes', bucket))
            codes_placeholder = QTreeWidgetItem(codes_node, ["…", ""])
            codes_placeholder.setData(0, Qt.ItemDataRole.UserRole,
                                      ('codes_placeholder', None))
            # При expand'е codes_node — построим code items.
            # Перехват делаем в том же _on_item_expanded (см. ниже).

    def _build_codes_children(self, codes_node, bucket):
        """Строит code-items под codes_node. Сортировка кодов делается
        здесь, не в основном _populate_tree — на больших партиях экономит
        время первичного открытия."""
        codes = bucket['codes']
        sorted_codes = sorted(codes)
        for c in sorted_codes[:self.MAX_CODES_PER_BATCH]:
            QTreeWidgetItem(codes_node, [c, ""])
        if len(sorted_codes) > self.MAX_CODES_PER_BATCH:
            hidden = len(sorted_codes) - self.MAX_CODES_PER_BATCH
            QTreeWidgetItem(codes_node, [
                f"… ещё {hidden:,} кодов не показано "
                f"(лимит {self.MAX_CODES_PER_BATCH})", ""])

    # ----- Фильтр по дереву -----

    def _apply_filter(self, text):
        """Скрывает root-узлы партий, чьи batch_id и коды не содержат
        введённую подстроку (case-insensitive). Пустой текст — показать всё.

        Дети root'а (Статистика / Файлы / Диапазон / Коды) не фильтруем —
        они подгружают/группируют контент, нет смысла резать."""
        needle = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            root = self._tree.topLevelItem(i)
            if not needle:
                root.setHidden(False)
                continue
            data = root.data(0, Qt.ItemDataRole.UserRole)
            if not data or data[0] != 'batch':
                continue
            bid = data[1]
            bucket = self._batches.get(bid)
            if bucket is None:
                root.setHidden(True)
                continue
            # match если needle в batch_id или в любом из кодов
            bid_str = ('' if bid == NO_BATCH else str(bid)).lower()
            match = needle in bid_str
            if not match:
                # Проверяем коды (без iter по большому set — any() с
                # генератором даст short-circuit на первом совпадении)
                match = any(needle in c.lower() for c in bucket['codes'])
            root.setHidden(not match)

    # ----- Экспорт -----

    def _on_export(self):
        if not self._batches:
            return
        # QFileDialog с фильтром по формату; расширение определяет формат.
        filters = (
            "HTML с раскрывающимися блоками (*.html);;"
            "Excel (*.xlsx);;"
            "CSV — 3 файла, разделитель ; (*.csv);;"
            "JSON (*.json)"
        )
        path, selected = QFileDialog.getSaveFileName(
            self, "Сохранить сводку партий", "group_stats.html", filters)
        if not path:
            return

        # По выбранному фильтру определяем формат и подставляем расширение,
        # если юзер его не дописал.
        if 'html' in selected.lower():
            fmt = 'html'
        elif 'xlsx' in selected.lower() or 'excel' in selected.lower():
            fmt = 'xlsx'
        elif 'csv' in selected.lower():
            fmt = 'csv'
        elif 'json' in selected.lower():
            fmt = 'json'
        else:
            # Fallback по расширению
            ext = os.path.splitext(path)[1].lower()
            fmt = {'.html': 'html', '.xlsx': 'xlsx',
                   '.csv': 'csv', '.json': 'json'}.get(ext, 'html')

        if not os.path.splitext(path)[1]:
            path = path + '.' + fmt

        try:
            if fmt == 'html':
                main, extras = export_html(path, self._batches)
            elif fmt == 'xlsx':
                main, extras = export_xlsx(path, self._batches)
            elif fmt == 'csv':
                main, extras = export_csv(path, self._batches)
            elif fmt == 'json':
                main, extras = export_json(path, self._batches)
            else:
                return
        except RuntimeError as e:
            # openpyxl не установлен и т.п.
            QMessageBox.warning(self, "Экспорт", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))
            return

        # Сообщаем юзеру что сохранили
        msg = f"Сохранено:\n• {main}"
        if extras:
            msg += "\n\nДополнительно:\n"
            msg += "\n".join(f"• {p}" for p in extras)
        QMessageBox.information(self, "Экспорт", msg)

    # ----- Сравнение партии с одним файлом -----

    def _on_tree_context_menu(self, pos):
        """Правый клик по партии → «Сравнить с одним файлом» → подменю с
        файлами где эта партия встречается."""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        # Поднимаемся до root (если кликнули по подноду — статистика и т.п.)
        root = item
        while root.parent() is not None:
            root = root.parent()
        data = root.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != 'batch':
            return
        bid = data[1]
        bucket = self._batches.get(bid)
        if bucket is None or not bucket['files']:
            return

        menu = QMenu(self)
        submenu = menu.addMenu("📊 Сравнить с одним файлом")
        for fp in sorted(bucket['files'].keys()):
            act = submenu.addAction(os.path.basename(fp))
            act.setData(fp)
        chosen = menu.exec(self._tree.mapToGlobal(pos))
        if chosen is None:
            return
        file_path = chosen.data()
        if file_path:
            self._open_compare_dialog(bid, file_path, bucket)

    def _open_compare_dialog(self, bid, file_path, bucket):
        """Сравнение партии в одном файле с её агрегатом по всей группе.
        Данные per-file уже собраны worker'ом на первичном проходе
        (bucket['per_file_counters'][path]) — повторно файл НЕ парсим."""
        per_file = bucket.get('per_file_counters', {}).get(file_path, {})
        # «single» bucket для совместимости с _show_compare_dialog: те же
        # ключи, но counters только этого файла. codes не сравниваем —
        # юзер не запрашивал.
        single = {'counters': per_file if per_file else {}, 'codes': set()}
        self._show_compare_dialog(bid, file_path, single, bucket)

    def _show_compare_dialog(self, bid, file_path, single, group):
        """Показывает таблицу-сравнение single vs group в простом диалоге."""
        from PyQt6.QtWidgets import QDialog as _QDlg, QTableWidget, QTableWidgetItem
        dlg = _QDlg(self)
        dlg.setWindowTitle(f"Сравнение партии {bid if bid != NO_BATCH else '(вне партии)'}")
        dlg.resize(600, 420)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            f"<b>Партия:</b> {bid if bid != NO_BATCH else 'Вне партии'}<br>"
            f"<b>Файл:</b> {os.path.basename(file_path)}"))
        tbl = QTableWidget()
        from core.group_stats_export import _COUNTER_LABELS
        rows = list(_COUNTER_LABELS) + [('__total__', 'Всего событий'),
                                         ('__codes__', 'Уникальных кодов')]
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(3)
        tbl.setHorizontalHeaderLabels(['Категория', 'В этом файле',
                                       'Во всей группе'])
        for i, (key, label) in enumerate(rows):
            tbl.setItem(i, 0, QTableWidgetItem(label))
            if key == '__total__':
                s = sum(single['counters'].values())
                g = sum(group['counters'].values())
            elif key == '__codes__':
                s = len(single.get('codes', set()))
                g = len(group['codes'])
            else:
                s = single['counters'].get(key, 0)
                g = group['counters'].get(key, 0)
            tbl.setItem(i, 1, QTableWidgetItem(f"{s:,}"))
            tbl.setItem(i, 2, QTableWidgetItem(f"{g:,}"))
        tbl.resizeColumnsToContents()
        v.addWidget(tbl)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(dlg.accept)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(btn_close)
        v.addLayout(h)
        dlg.exec()

    # ----- Cleanup -----

    def closeEvent(self, event):
        """Останавливаем worker если ещё работает + освобождаем большой
        _batches dict. Иначе Qt держит диалог (parent=MainWindow) и сотни
        тысяч QTreeWidgetItem'ов + dict с миллионами кодов остаются в RAM
        даже после закрытия окна."""
        if self._worker is not None and self._worker.isRunning():
            # DISCONNECT finished перед wait — иначе worker может эмитнуть
            # finished на уже-удалённый диалог после wait timeout → crash.
            try:
                self._worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._worker.requestInterruption()
            # 2с вместо 500мс: GroupStatsWorker проверяет interruption
            # только между файлами, а текущий файл может быть 5+ ГБ.
            self._worker.wait(2000)
            if self._worker.isRunning():
                # Не дождались — поток продолжит работать в фоне до
                # окончания текущего файла. Никаких сигналов на удалённый
                # диалог не придёт (мы дисконнектнули). Утечка ОЗУ
                # временная — Python соберёт worker когда тот завершится.
                import logging
                logging.getLogger('group_stats_dialog').warning(
                    "Worker не остановился за 2с при закрытии диалога — "
                    "файл слишком большой, прерывание происходит между файлами")
        # Чистим тяжёлые ссылки. self._batches на 336 логах — это 2.7M кодов
        # + counters + per_file_counters → несколько ГБ RAM.
        self._batches = {}
        self._worker = None
        if hasattr(self, '_tree'):
            self._tree.clear()
        # Принудительный GC — Python иногда задерживает освобождение больших
        # dict'ов с миллионами строк, особенно после openpyxl save().
        import gc
        gc.collect()
        super().closeEvent(event)
