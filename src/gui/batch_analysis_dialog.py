from html import escape
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton

from config import THEMES
from core.models import BATCH_EVENT_RULES


def _adjust_color(hex_color, delta):
    h = hex_color.lstrip('#')
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    rgb = [max(0, min(255, c + delta)) for c in rgb]
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def _is_light_bg(hex_color):
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 128


def _format_duration_ms(ms):
    if ms <= 0:
        return "—"
    s = ms // 1000
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h}ч {m}мин {sec}с"
    if m:
        return f"{m}мин {sec}с"
    return f"{sec}.{int((ms % 1000) // 100)}с"


def _format_delta_short(ms):
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:.0f} ms"
    if ms < 60_000:
        return f"{ms / 1000:.2f} с"
    if ms < 3_600_000:
        return f"{ms / 60_000:.2f} мин"
    return f"{ms / 3_600_000:.2f} ч"


def _build_analysis_html(analysis, theme_name):
    """Рендерит HTML с метриками анализа партии под текущую тему."""
    t = THEMES.get(theme_name, THEMES["Default"])
    is_light = _is_light_bg(t['bg_main'])
    panel_bg = _adjust_color(t['bg_main'], -15 if is_light else +20)
    border = _adjust_color(t['bg_main'], -40 if is_light else +50)

    bid = analysis['batch_id']
    title = "Партия " + escape(bid) if bid else "Вне партии"

    css = f"""
    <style>
        body {{ color: {t['text_main']}; background: {t['bg_main']};
                font-family: '{t['font_family']}', sans-serif; }}
        h1 {{ color: {t['accent']}; margin: 0 0 6px 0; }}
        h2 {{ color: {t['accent']}; margin: 16px 0 4px 0;
              border-bottom: 1px solid {border}; padding-bottom: 2px; }}
        table {{ border-collapse: collapse; margin: 4px 0; width: 100%; }}
        td {{ padding: 3px 8px; vertical-align: top; }}
        td.k {{ color: {t['text_muted']}; width: 60%; }}
        td.v {{ color: {t['text_main']}; text-align: right; font-family: '{t['mono_font']}', monospace; }}
        td.bar {{ width: 30%; }}
        .lvl-info {{ color: {t['info']}; }}
        .lvl-debug {{ color: {t['debug']}; }}
        .lvl-warn {{ color: {t['warn']}; }}
        .lvl-error {{ color: {t['error']}; }}
        .panel {{ background: {panel_bg}; border: 1px solid {border};
                  border-radius: 3px; padding: 8px 12px; margin: 4px 0; }}
        .err-row td {{ font-family: '{t['mono_font']}', monospace; padding: 2px 8px; }}
        .err-count {{ color: {t['error']}; font-weight: bold; }}
        .muted {{ color: {t['text_muted']}; }}
    </style>
    """

    parts = [css]
    parts.append(f"<h1>Анализ: {title}</h1>")
    parts.append(f"<div class='muted'>Время: <b>{escape(analysis['first_ts'] or '—')} → "
                 f"{escape(analysis['last_ts'] or '—')}</b></div>")

    # --- Общее ---
    parts.append("<h2>Общее</h2>")
    parts.append("<table>")
    parts.append(f"<tr><td class='k'>Длительность партии</td>"
                 f"<td class='v'>{_format_duration_ms(analysis['duration_ms'])}</td></tr>")
    parts.append(f"<tr><td class='k'>Всего записей в партии</td>"
                 f"<td class='v'>{analysis['total']:,}</td></tr>")
    parts.append(f"<tr><td class='k'>Записей в час (среднее)</td>"
                 f"<td class='v'>{analysis['per_hour']:,}</td></tr>")
    parts.append("</table>")

    # --- Уровни ---
    levels = analysis['levels']
    total_lvl = sum(levels.values()) or 1
    parts.append("<h2>Распределение по уровням</h2><table>")
    for lvl_name, css_class in [
        ('INFO', 'lvl-info'), ('DEBUG', 'lvl-debug'),
        ('WARN', 'lvl-warn'), ('ERROR', 'lvl-error'),
    ]:
        c = levels.get(lvl_name, 0)
        pct = c / total_lvl * 100
        parts.append(
            f"<tr><td class='k'><span class='{css_class}'>{lvl_name}</span></td>"
            f"<td class='v'>{c:,} ({pct:.1f}%)</td></tr>"
        )
    if levels.get('UNKNOWN'):
        parts.append(f"<tr><td class='k'>UNKNOWN (продолжения)</td>"
                     f"<td class='v'>{levels['UNKNOWN']:,}</td></tr>")
    parts.append("</table>")

    # --- События по группам ---
    events = analysis['events']
    labels = analysis['event_labels']

    def render_event_group(title_html, keys):
        present = [(k, events.get(k, 0)) for k in keys if events.get(k, 0) > 0]
        if not present:
            return ""
        out = [f"<h2>{title_html}</h2><table>"]
        for k, c in present:
            out.append(f"<tr><td class='k'>{escape(labels.get(k, k))}</td>"
                       f"<td class='v'>{c:,}</td></tr>")
        out.append("</table>")
        return "".join(out)

    # --- Баланс кодов: SGTIN (продукт) ---
    sgtin_scanned = analysis['sgtin_scanned_unique']
    if sgtin_scanned > 0 or analysis['sgtin_dup_in_groups'] > 0:
        parts.append("<h2>Серийные коды (SGTIN — единицы продукта)</h2>")
        parts.append("<table>")
        parts.append(f"<tr><td class='k'>Уникальных кодов отсканировано</td>"
                     f"<td class='v'>{sgtin_scanned:,}</td></tr>")
        parts.append(f"<tr><td class='k'>Всего срабатываний сканера (HIKROBOT)</td>"
                     f"<td class='v'>{analysis['sgtin_scan_events']:,}</td></tr>")
        if analysis['sgtin_repeated_scans']:
            parts.append(f"<tr><td class='k'>Кодов отсканировано <b>повторно</b> "
                         f"(больше 1 раза)</td>"
                         f"<td class='v lvl-warn'>{analysis['sgtin_repeated_scans']:,}</td></tr>")
        if analysis['sgtin_dup_in_groups']:
            parts.append(f"<tr><td class='k'>Дублей: код «уже находится в одной из групп»</td>"
                         f"<td class='v lvl-error'>{analysis['sgtin_dup_in_groups']:,}</td></tr>")
        if analysis['sgtin_dup_in_current']:
            parts.append(f"<tr><td class='k'>Дублей: код «уже добавлен в текущую группу»</td>"
                         f"<td class='v lvl-error'>{analysis['sgtin_dup_in_current']:,}</td></tr>")
        if analysis['sgtin_not_found']:
            parts.append(f"<tr><td class='k'>Кодов «не найден в базе»</td>"
                         f"<td class='v lvl-error'>{analysis['sgtin_not_found']:,}</td></tr>")
        parts.append("</table>")

        if analysis['sgtin_most_repeated']:
            parts.append("<div class='panel muted'>Чаще всего пере-сканированные коды:<br>")
            for code, n in analysis['sgtin_most_repeated']:
                parts.append(f"&nbsp;&nbsp;• <code>{escape(code[:40])}</code> "
                             f"— <b>{n}×</b><br>")
            parts.append("</div>")

    # --- Баланс кодов: групповые (упаковки) ---
    grp_recv = analysis['group_codes_received']
    grp_print = analysis['group_codes_printed']
    if grp_recv > 0 or grp_print > 0:
        parts.append("<h2>Групповые коды (агрегации — наклейки на коробах)</h2>")
        parts.append("<table>")
        parts.append(f"<tr><td class='k'>Получено код-агрегата (сгенерировано для групп)</td>"
                     f"<td class='v'>{grp_recv:,}</td></tr>")
        parts.append(f"<tr><td class='k'>Отправлено в принтер (PrintService.sendData)</td>"
                     f"<td class='v'>{grp_print:,}</td></tr>")
        if analysis['group_codes_lost']:
            parts.append(f"<tr><td class='k'>⚠ Получено, но <b>не</b> ушло в принтер</td>"
                         f"<td class='v lvl-error'>{analysis['group_codes_lost']:,}</td></tr>")
        elif grp_recv == grp_print and grp_recv > 0:
            parts.append(f"<tr><td class='k' colspan='2' "
                         f"style='color:{t['info']};'>✓ Все агрегаты ушли на печать</td></tr>")
        parts.append("</table>")

    parts.append(render_event_group(
        "Печать кодов (события)",
        ['print_request', 'print_sent', 'print_sato', 'print_data']
    ))
    agg_html = render_event_group(
        "Агрегация (события)",
        ['agg_attempted', 'agg_finished', 'agg_finish_response', 'agg_cleared']
    )
    if agg_html and analysis['agg_efficiency_pct'] is not None:
        # Дополним эффективностью
        eff = analysis['agg_efficiency_pct']
        eff_color = t['info'] if eff >= 95 else (t['warn'] if eff >= 50 else t['error'])
        agg_html = agg_html.replace(
            "</table>",
            f"<tr><td class='k'><b>Эффективность агрегации</b></td>"
            f"<td class='v' style='color:{eff_color};'><b>{eff}%</b></td></tr></table>"
        )
    parts.append(agg_html)
    parts.append(render_event_group(
        "Сканирование (события)",
        ['scan_hikrobot', 'scan_image']
    ))
    parts.append(render_event_group(
        "Обмен с Л2 / Сериализация / HTTP",
        ['exchange_sgtin', 'serialization', 'http_request']
    ))
    # --- Все ошибки в одной секции (event-rules вида err_*) ---
    err_keys = [k for k, _, _ in BATCH_EVENT_RULES if k.startswith('err_')]
    parts.append(render_event_group("Проблемы и ошибки (счётчики)", err_keys))

    # --- Ритм работы ---
    parts.append("<h2>Ритм работы</h2><table>")
    parts.append(f"<tr><td class='k'>Среднее Δt между сканированиями (HIKROBOT)</td>"
                 f"<td class='v'>{_format_delta_short(analysis['avg_scan_delta_ms'])}</td></tr>")
    parts.append(f"<tr><td class='k'>Длинных пауз (&gt; 5 минут между записями)</td>"
                 f"<td class='v'>{len(analysis['pauses'])}</td></tr>")
    parts.append("</table>")

    if analysis['pauses']:
        parts.append("<div class='panel muted'>Самые длинные паузы (возможные простои оператора):<br>")
        for start_ms, end_ms, diff_ms in analysis['pauses']:
            # ms -> HH:MM:SS.mmm
            def to_hms(ms):
                s = ms // 1000
                return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}.{ms % 1000:03d}"
            parts.append(f"&nbsp;&nbsp;• {to_hms(start_ms)} → {to_hms(end_ms)} "
                         f"({_format_delta_short(diff_ms)})<br>")
        parts.append("</div>")

    # --- Топ активных минут ---
    if analysis['top_minutes']:
        parts.append("<h2>Самые активные минуты (пики активности)</h2><table>")
        max_count = max(c for _, c in analysis['top_minutes'])
        for minute, c in analysis['top_minutes']:
            bar_w = int(200 * c / max_count) if max_count else 0
            bar_html = (f"<div style='background:{t['accent']};height:8px;"
                        f"width:{bar_w}px;border-radius:2px;'></div>")
            parts.append(
                f"<tr><td class='k'><b>{escape(minute)}</b></td>"
                f"<td class='v'>{c:,} строк</td>"
                f"<td class='bar'>{bar_html}</td></tr>"
            )
        parts.append("</table>")

    # --- Топ компонентов ---
    if analysis['top_loggers']:
        parts.append("<h2>Самые активные компоненты</h2><table>")
        max_c = max(c for _, c in analysis['top_loggers'])
        for lg, c in analysis['top_loggers']:
            bar_w = int(200 * c / max_c) if max_c else 0
            bar_html = (f"<div style='background:{t['accent']};height:8px;"
                        f"width:{bar_w}px;border-radius:2px;'></div>")
            parts.append(
                f"<tr><td class='k'><b>{escape(lg)}</b></td>"
                f"<td class='v'>{c:,}</td>"
                f"<td class='bar'>{bar_html}</td></tr>"
            )
        parts.append("</table>")

    # --- Распределение ERROR по часам (мини-гистограмма) ---
    eph = analysis.get('errors_per_hour') or []
    if eph:
        parts.append("<h2>Распределение ERROR по часам</h2><table>")
        max_e = max(c for _, c in eph)
        for hour, c in eph:
            bar_w = int(200 * c / max_e) if max_e else 0
            bar_html = (f"<div style='background:{t['error']};height:8px;"
                        f"width:{bar_w}px;border-radius:2px;'></div>")
            parts.append(
                f"<tr><td class='k'><b>{escape(hour)}:00</b></td>"
                f"<td class='v lvl-error'>{c:,}</td>"
                f"<td class='bar'>{bar_html}</td></tr>"
            )
        parts.append("</table>")

    # --- Категории ошибок (нормализованные, сгруппированные по смыслу) ---
    if analysis['top_error_categories']:
        parts.append("<h2>Категории ошибок (похожие сгруппированы)</h2>")
        parts.append("<div class='muted' style='font-size:11px;margin-bottom:4px;'>"
                     "Переменные части (коды, числа, UUID, ID групп) заменены на "
                     "<code>&lt;CODE&gt;</code>/<code>&lt;N&gt;</code>/<code>&lt;UUID&gt;</code>/"
                     "<code>&lt;GROUP&gt;</code> чтобы похожие ошибки попали в одну строку."
                     "</div><table>")
        for msg, c in analysis['top_error_categories']:
            parts.append(
                f"<tr class='err-row'>"
                f"<td class='err-count'>{c}×</td>"
                f"<td>{escape(msg)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<div class='panel muted'>Ошибок (ERROR) в этой партии нет 🎉</div>")

    return "".join(parts)


class BatchAnalysisDialog(QDialog):
    """Модальное окно с подробной аналитикой по партии."""

    def __init__(self, analysis, theme_name="Default", parent=None):
        super().__init__(parent)
        bid = analysis['batch_id']
        title = f"Анализ партии {bid}" if bid else "Анализ: вне партии"
        self.setWindowTitle(title)
        self.resize(820, 720)

        t = THEMES.get(theme_name, THEMES["Default"])
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QPushButton {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                padding: 6px 14px;
                border-radius: 3px;
            }}
            QPushButton:hover {{ background-color: {t['selection']}; }}
            QPushButton:default {{ border: 1px solid {t['accent']}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {t['bg_main']};
                color: {t['text_main']};
                border: none;
                padding: 8px 14px;
            }}
        """)
        self.browser.setHtml(_build_analysis_html(analysis, theme_name))
        layout.addWidget(self.browser, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(10, 8, 10, 10)
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
