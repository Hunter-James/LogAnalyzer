from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton
from PyQt6.QtCore import Qt

from config import APP_VERSION, THEMES


# HTML без <style> - стиль строится динамически из текущей темы (см. _build_help_css)
HELP_BODY = r"""
<h1>Log Analyzer — справка</h1>
<p>Краткое руководство по функционалу. Версия приложения: {version}.</p>

<h2>Открытие файлов</h2>
<ul>
  <li><b>Open File</b> — кнопка в тулбаре, открывает диалог выбора файла. Можно выбрать несколько сразу.</li>
  <li><b>Drag &amp; Drop</b> — перетащите файл (или несколько) прямо в окно. Если активна правая панель, файл попадёт туда.</li>
  <li>Поддерживаются <code>.log</code> и <code>.txt</code> (других форматов фильтр не предлагает, но открыть можно).</li>
  <li>Открытые файлы и сессия восстанавливаются при следующем запуске.</li>
</ul>

<h2>Вкладки и сплит-режим</h2>
<ul>
  <li>Каждый файл открывается в отдельной вкладке.</li>
  <li>Вкладку можно <b>перетащить мышью</b> между левой и правой группой — получится сплит-вид с двумя файлами рядом для сравнения.</li>
  <li><kbd>Ctrl</kbd> + клик по вкладке — множественное выделение. <kbd>Shift</kbd> + клик — выделить диапазон.</li>
  <li>Правый клик по вкладке — контекстное меню (закрыть, закрыть остальные, перенести в другую панель и т.д.).</li>
</ul>

<h2>Фильтры по уровню</h2>
<ul>
  <li>Чекбоксы <b>INFO / DEBUG / WARN / ERROR</b> в тулбаре — глобальные, применяются ко всем открытым файлам сразу.</li>
  <li>Записи без явного уровня (продолжения многострочных сообщений, стек-трейсы) показываются всегда — иначе терялся бы контекст ошибок.</li>
</ul>

<h2>Свернуть дубли</h2>
<ul>
  <li>Чекбокс <b>Свернуть дубли</b> в тулбаре схлопывает <b>подряд идущие</b> записи с одинаковым уровнем и сообщением в одну строку с префиксом <code>[×N]</code>.</li>
  <li>Полезно когда одна и та же ошибка повторяется десятками раз подряд — вместо шума получается одна строка с указанием количества.</li>
  <li>В журнал поиска при этом всё равно попадают <b>все</b> совпадения, а не только лидеры групп.</li>
</ul>

<h2>Поиск</h2>
<ul>
  <li>Поле <b>Search</b> в каждой вкладке — локальный поиск по содержимому именно этого файла.</li>
  <li>Поддерживается обычный текст и <b>регулярные выражения</b>. Если ввод не парсится как regex, автоматически используется обычный поиск подстроки.</li>
  <li>Кнопка <b>Aa</b> рядом с полем — переключатель <b>Match case</b> (учитывать регистр), как в Notepad++. По умолчанию выключен — поиск регистронезависимый. <b>Активное состояние выделено синим фоном.</b></li>
  <li>Результаты фильтруют список — показываются только строки с совпадением.</li>
  <li>В нижнем окне <b>Выделение</b> совпадения подсвечиваются жёлтым; вид автоматически прокручивается к первому совпадению — удобно при длинных строках с агрегационными кодами.</li>
</ul>

<h2>Регулярные выражения (regex)</h2>
<p>Используется движок Python <code>re</code> — синтаксис PCRE-совместимый, как в Notepad++ (Boost.Regex).
Базовые конструкции работают <b>точь-в-точь как в Notepad++</b>:</p>
<ul>
  <li><code>.</code> — любой символ кроме перевода строки</li>
  <li><code>\d \w \s</code> — цифра / буквенно-цифровой символ / пробел</li>
  <li><code>\D \W \S</code> — отрицания</li>
  <li><code>[abc]</code>, <code>[^abc]</code>, <code>[a-z]</code> — классы символов</li>
  <li><code>*</code>, <code>+</code>, <code>?</code>, <code>{n}</code>, <code>{n,m}</code>, <code>*?</code> (нежадный) — квантификаторы</li>
  <li><code>a|b</code> — альтернатива</li>
  <li><code>(...)</code> — группа, <code>(?:...)</code> — неперехватывающая</li>
  <li><code>^</code>, <code>$</code> — начало/конец строки, <code>\b</code> — граница слова</li>
  <li><code>(?=...)</code>, <code>(?!...)</code>, <code>(?&lt;=...)</code>, <code>(?&lt;!...)</code> — lookahead/lookbehind</li>
  <li><code>\1</code>, <code>\2</code> — обратные ссылки на группы</li>
  <li><code>(?i)</code> внутри паттерна — встроенный модификатор регистра</li>
</ul>
<p><b>Чем отличается от Notepad++:</b> Boost.Regex поддерживает несколько фич, которых нет в Python <code>re</code>:</p>
<ul>
  <li><code>\K</code> — «забыть» уже сматченный префикс — <b>не работает</b></li>
  <li>Рекурсия <code>(?R)</code>, <code>(?0)</code> — <b>не работает</b></li>
  <li><code>\h</code> (горизонтальный пробел), <code>\R</code> (любой перевод строки) — <b>не работают</b></li>
  <li>Именованные группы пишутся как <code>(?P&lt;name&gt;...)</code> вместо <code>(?&lt;name&gt;...)</code> — но в Notepad++ обычно не нужны.</li>
</ul>
<p class="tip">Для 99% типовых задач (вытащить ID, найти ошибку по шаблону, отфильтровать по таймстампу) синтаксис идентичен.</p>

<h3>Примеры regex для логов:</h3>
<ul>
  <li><code>10:20:\d{2}\.\d{3}</code> — все строки минуты 10:20</li>
  <li><code>Ошибка ПЛК: .*переполнен</code> — конкретная ошибка с произвольным текстом между</li>
  <li><code>0104610051753297215\w+</code> — агрегационные коды, начинающиеся с этого префикса</li>
  <li><code>(ERROR|WARN).*PLC</code> — ERROR или WARN, упоминающий PLC</li>
  <li><code>\b\d{14}\b</code> — целое число ровно из 14 цифр (партии, GTIN)</li>
  <li><code>POST /api/(login|generate-batch)</code> — конкретные HTTP-эндпоинты</li>
</ul>

<h2>Навигация по результатам</h2>
<ul>
  <li><kbd>F3</kbd> — перейти к следующей строке отфильтрованного вида.</li>
  <li><kbd>Shift</kbd>+<kbd>F3</kbd> — к предыдущей.</li>
  <li>Навигация закольцована: после последней строки переход в начало.</li>
</ul>

<h2>Журнал поиска</h2>
<ul>
  <li>Кнопка <b>Добавить в журнал</b> сохраняет текущий поиск (со всеми совпадениями) во вкладку <b>Поиск</b> внизу.</li>
  <li><b>Двойной клик</b> по строке в журнале — переход к ней в основном списке.</li>
  <li>Правый клик — копировать выделенное / очистить весь журнал.</li>
  <li>Журнал ограничен последними <b>50 поисками</b> и <b>1000 совпадениями</b> на поиск (для производительности при больших логах).</li>
</ul>

<h2>Фильтр по компонентам</h2>
<ul>
  <li>Кнопка <b>Компоненты</b> рядом с поиском — открывает меню со всеми логгерами текущего файла (PLCService, DataService, HikariPool и т.п.).</li>
  <li>Чекбоксы оставляют видимыми только записи от выбранных компонентов. Меню не закрывается при клике, можно отметить сразу несколько.</li>
  <li>Кнопка <b>Все / Ни одного</b> сверху меню переключает все разом.</li>
  <li>Метка кнопки показывает счётчик: <code>Компоненты: 3/8</code> — выбрано 3 из 8.</li>
</ul>

<h2>Фильтр по диапазону времени</h2>
<ul>
  <li>Поля <b>Время: с … – по …</b> в тулбаре вьювера принимают форматы:
    <ul>
      <li><code>ЧЧ:ММ</code> — например, <code>10:00</code></li>
      <li><code>ЧЧ:ММ:СС</code> — например, <code>10:00:30</code></li>
      <li><code>ЧЧ:ММ:СС.ммм</code> — например, <code>10:00:30.500</code></li>
    </ul>
  </li>
  <li>В поле <b>с</b> отсутствующие части заполняются нулями.</li>
  <li>В поле <b>по</b> — девятками: ввод <code>10:00</code> в "по" покрывает всю минуту <code>10:00:00 – 10:00:59.999</code>.</li>
  <li>Невалидный ввод подсвечивается красноватым фоном и игнорируется (фильтр не применяется).</li>
</ul>

<h2>Метки на скроллбаре</h2>
<ul>
  <li>На вертикальном скроллбаре списка — <span style="color:#cd5c5c;">красные риски</span> в позициях ERROR и <span style="color:#ffa500;">жёлтые</span> в позициях WARN.</li>
  <li>Сразу видно, в какой части файла кучкуются проблемы — без необходимости скроллить весь файл.</li>
</ul>

<h2>Настройки и тема</h2>
<ul>
  <li>Кнопка <b>Settings</b> — смена темы (Default, Minimalist Black/White, Windows 95, Hacker) и размера шрифта.</li>
  <li>Сессия (открытые файлы, тема, размер шрифта) сохраняется между запусками в <code>settings.json</code>.</li>
</ul>

<h2>Масштабирование и копирование</h2>
<ul>
  <li><kbd>Ctrl</kbd> + колесо мыши над списком или окном "Выделение" — меняет размер шрифта.</li>
  <li><kbd>Ctrl</kbd>+<kbd>C</kbd> копирует выделенные строки из активного окна (список / детали / журнал поиска).</li>
</ul>

<p class="tip">Подсказка: путь к открытому файлу всегда виден в заголовке окна.</p>
""".replace("{version}", APP_VERSION)


def _adjust_color(hex_color, delta):
    """Делает цвет светлее (+) или темнее (-) на delta (0-255)."""
    h = hex_color.lstrip('#')
    rgb = [int(h[i:i+2], 16) for i in (0, 2, 4)]
    rgb = [max(0, min(255, c + delta)) for c in rgb]
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def _is_light_bg(hex_color):
    """True если фоновый цвет светлый (по человеческой формуле luminance)."""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 128


def _build_help_html(theme_name):
    """Собирает финальный HTML с CSS под текущую тему - чтобы текст и блоки code
    были читаемы на любом фоне (тёмном/светлом/Windows 95/Hacker)."""
    t = THEMES.get(theme_name, THEMES["Default"])
    is_light = _is_light_bg(t['bg_main'])

    # Фон code/kbd должен контрастировать с основным фоном:
    # на светлой теме - чуть темнее, на тёмной - чуть светлее.
    code_bg = _adjust_color(t['bg_main'], -25 if is_light else +30)
    code_border = _adjust_color(t['bg_main'], -50 if is_light else +60)

    style = f"""
    <style>
        body {{
            color: {t['text_main']};
            background-color: {t['bg_main']};
            font-family: '{t['font_family']}', sans-serif;
        }}
        h1 {{ color: {t['accent']}; }}
        h2 {{
            color: {t['accent']};
            margin-top: 18px;
            border-bottom: 1px solid {t['border']};
            padding-bottom: 3px;
        }}
        h3 {{ color: {t['accent']}; margin-top: 12px; }}
        p, li {{ color: {t['text_main']}; }}
        code, kbd {{
            background-color: {code_bg};
            color: {t['text_main']};
            padding: 1px 5px;
            border-radius: 3px;
            font-family: '{t['mono_font']}', Consolas, monospace;
        }}
        kbd {{ border: 1px solid {code_border}; }}
        li {{ margin: 4px 0; }}
        .tip {{ color: {t['text_muted']}; font-size: 11px; }}
        a {{ color: {t['accent']}; }}
    </style>
    """
    return style + HELP_BODY


class HelpDialog(QDialog):
    """Модальное окно со справкой по функционалу приложения.
    Принимает текущую тему, чтобы корректно выглядеть и на светлых, и на тёмных схемах."""

    def __init__(self, theme_name="Default", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справка — Log Analyzer")
        self.resize(820, 720)

        t = THEMES.get(theme_name, THEMES["Default"])

        # Стилизуем сам диалог и кнопку Закрыть под тему - иначе на белой/Windows 95
        # темах окно остаётся системно-серым и контрастирует с основным окном.
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t['bg_main']};
                color: {t['text_main']};
            }}
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
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {t['bg_main']};
                color: {t['text_main']};
                border: none;
                padding: 6px 12px;
            }}
        """)
        self.browser.setHtml(_build_help_html(theme_name))
        layout.addWidget(self.browser, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(10, 8, 10, 10)
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
