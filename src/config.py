import json
import os
import sys

APP_VERSION = "1.5.0"

GITHUB_REPO = "Hunter-James/LogAnalyzerEVOL"

# --- UI Feature Toggles ---
# Какие необязательные элементы UI можно скрыть через Настройки.
# Базовые вещи (поиск, фильтры по уровню, открытие файла) не отключаются.
DEFAULT_UI_FEATURES = {
    "match_case": True,
    "use_regex": True,
    "loggers_filter": True,
    "batches_filter": True,
    "time_range": True,
    "tail_mode": True,
    "save_to_journal": True,
    "group_dupes": True,
    "json_format": True,
    "selection_info": True,
    "scrollbar_markers": True,
    # Режим разработчика: RAM-индикатор в статус-баре + хоткей Ctrl+Shift+M
    # для tracemalloc-снапшота. По умолчанию выключен - обычному
    # пользователю эти цифры только захламляют интерфейс.
    "debug_panel": False,
}

UI_FEATURE_LABELS = {
    "match_case": "Кнопка «Aa» (учитывать регистр поиска)",
    "use_regex": "Кнопка «.*» (использовать regex; иначе поиск буквальный)",
    "loggers_filter": "Кнопка «Компоненты» (фильтр по логгеру)",
    "batches_filter": "Кнопка «Партии» (фильтр по setCurrentBatch / api/close)",
    "time_range": "Поля диапазона времени (Время: с – по)",
    "tail_mode": "Кнопка «Следить» (tail / follow mode)",
    "save_to_journal": "Кнопка «Добавить в журнал»",
    "group_dupes": "Чекбокс «Свернуть дубли» в тулбаре",
    "json_format": "Кнопка «{ } JSON» (форматирование JSON)",
    "selection_info": "Δt и информация о выделении в статус-баре",
    "scrollbar_markers": "Метки ERROR/WARN на скроллбаре",
    "debug_panel": "Режим разработчика (RAM-индикатор в статус-баре + Ctrl+Shift+M снапшот)",
}

# Группировка опций для SettingsDialog. Order-preserving dict (Python 3.7+).
# Каждая категория - QGroupBox в диалоге. Ключи внутри группы - в том
# порядке, в котором будут показаны.
UI_FEATURE_CATEGORIES = {
    "Поиск и фильтры": [
        "match_case",
        "use_regex",
        "loggers_filter",
        "batches_filter",
        "time_range",
    ],
    "Просмотр и журнал": [
        "tail_mode",
        "save_to_journal",
        "group_dupes",
        "json_format",
    ],
    "Индикаторы в статус-баре": [
        "selection_info",
        "scrollbar_markers",
    ],
    "Производительность и отладка": [
        "debug_panel",
    ],
}

# --- Settings Management ---
SETTINGS_FILENAME = "settings.json"


def get_settings_path():
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(application_path, SETTINGS_FILENAME)


def load_settings():
    path = get_settings_path()
    defaults = {
        "theme": "Default",
        "font_size": 10,
        "files_left": [],
        "files_right": [],
        "ui_features": dict(DEFAULT_UI_FEATURES),
        # Конфигурация двух групп табов: имена и цвета (раунд 1).
        # В следующих раундах будет произвольное число групп - тогда
        # эта структура расширится в groups: [...].
        "group_configs": [
            {"name": "Группа 1", "color": "#E53935"},
            {"name": "Группа 2", "color": "#43A047"},
        ],
    }

    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                saved = json.load(f)
                defaults.update(saved)
        except Exception:
            return defaults

    # Мерджим ui_features с дефолтами - если в settings.json нет нового флага,
    # подставляем True (включено), чтобы не скрывать новые фичи у старых пользователей.
    user_features = defaults.get("ui_features", {}) or {}
    merged_features = dict(DEFAULT_UI_FEATURES)
    merged_features.update({k: bool(v)
                           for k, v in user_features.items() if k in DEFAULT_UI_FEATURES})
    defaults["ui_features"] = merged_features

    # Нормализуем group_configs: произвольная длина (>=2), каждая запись
    # {name, color, collapsed}.
    raw_groups = defaults.get("group_configs") or []
    fallback = [
        {"name": "Группа 1", "color": "#E53935"},
        {"name": "Группа 2", "color": "#43A047"},
    ]
    normalized = []
    target_len = max(2, len(raw_groups))
    for i in range(target_len):
        src = (raw_groups[i] if i < len(raw_groups)
               and isinstance(raw_groups[i], dict) else {})
        if i < 2:
            name_def = fallback[i]["name"]
            color_def = fallback[i]["color"]
        else:
            name_def = f"Группа {i + 1}"
            color_def = "#1E88E5"
        normalized.append({
            "name": str(src.get("name") or name_def),
            "color": str(src.get("color") or color_def),
            "collapsed": bool(src.get("collapsed", False)),
        })
    defaults["group_configs"] = normalized

    # Режим расположения групп: 'splitter' (рядом) или 'stack' (видна
    # одна, остальные через клик по плашке). Дефолт — stack, чтобы
    # при первом запуске не грузить много вкладок параллельно.
    mode = str(defaults.get("group_layout_mode") or "stack").lower()
    if mode not in ("splitter", "stack"):
        mode = "stack"
    defaults["group_layout_mode"] = mode

    # «Запоминать разделение экрана между запусками». False = при старте
    # всегда восстанавливаемся в stack (безопаснее, lazy-load по одной
    # вкладке). True = берём group_layout_mode из settings как есть. По
    # умолчанию False, иначе юзер случайно перетащил таб в splitter, потом
    # закрыл приложение, потом удивляется почему все 4 группы открываются
    # параллельно при следующем запуске.
    defaults["remember_split_layout"] = bool(defaults.get("remember_split_layout", False))

    # Режим открытия файла: False = классический (полный парсинг сразу,
    # все фичи доступны через ~1-2с), True = быстрый Two-stage (text-view
    # с маркерами ERROR/WARN за <1с, полный анализ доезжает в фоне).
    # Default False — поведение как до фичи fast-open.
    defaults["fast_open_mode"] = bool(defaults.get("fast_open_mode", False))

    # Движок быстрого просмотра (Stage 1 в fast_open_mode):
    #   'list'    — QListView + модель сырых строк. Открытие мгновенное
    #               даже на миллионах строк. Выделение/копирование целыми
    #               строками, без Ctrl+F и текстового выделения фрагментов.
    #   'full'    — QPlainTextEdit со всем текстом. Полноценный редактор
    #               (выделение фрагментов, Ctrl+F), но на 1M+ строк
    #               открытие занимает несколько секунд.
    #   'limited' — QPlainTextEdit с первыми N строками. Компромисс:
    #               быстро + текстовый редактор, но виден не весь файл.
    engine = str(defaults.get("fast_view_engine") or "list").lower()
    if engine not in ("list", "full", "limited"):
        engine = "list"
    defaults["fast_view_engine"] = engine

    # archived_groups больше не поддерживается (архивирование убрано в
    # пользу единой операции «Скрыть»). Чтобы не падать на старых settings.json -
    # просто игнорируем ключ если он там есть.
    defaults.pop("archived_groups", None)

    return defaults


def save_settings(data):
    """
    Saves a dictionary of settings to the JSON file.
    Expected keys: theme, font_size, files_left, files_right
    """
    path = get_settings_path()
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Failed to save settings: {e}")


# --- Theme Definitions ---
# Каждая тема дополнительно содержит "json_palette" - цвета для:
#   - gutter_bg / gutter_glyph / gutter_glyph_collapsed (полоса свёрток в "Выделение")
#   - json_key/string/number/keyword/bracket (подсветка JSON-токенов)
#   - tree_meta (служебные узлы дерева: "(контекст строки)", "...")
# Тёмные темы используют VS Code Dark+, светлые - VS Code Light+.
_DARK_JSON_PALETTE = {
    "gutter_bg": "#1E1E1E",
    "gutter_glyph": "#777777",
    "gutter_glyph_collapsed": "#D4D4D4",
    "json_key": "#9CDCFE",
    "json_string": "#CE9178",
    "json_number": "#B5CEA8",
    "json_keyword": "#569CD6",
    "json_bracket": "#FFD700",
    "tree_meta": "#888888",
}
_LIGHT_JSON_PALETTE = {
    "gutter_bg": "#ECECEC",
    "gutter_glyph": "#666666",
    "gutter_glyph_collapsed": "#1F1F1F",
    "json_key": "#0451A5",
    "json_string": "#A31515",
    "json_number": "#098658",
    "json_keyword": "#0000FF",
    "json_bracket": "#444444",
    "tree_meta": "#5E5E5E",
}

THEMES = {
    "Default": {
        "layout": "top",
        "bg_main": "#353535",
        "bg_panel": "#353535",
        "border": "#252525",
        "text_main": "#FFFFFF",
        "text_muted": "#D4D4D4",
        "accent": "#2A82DA",
        "selection": "#264F78",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#2E8B57", "debug": "#4682B4", "warn": "#FFA500", "error": "#CD5C5C",
        "json_palette": _DARK_JSON_PALETTE,
    },
    "Minimalist Black": {
        "layout": "top",
        "bg_main": "#1E1E1E",
        "bg_panel": "#252526",
        "border": "#3E3E42",
        "text_main": "#CCCCCC",
        "text_muted": "#858585",
        "accent": "#007ACC",
        "selection": "#37373D",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#4EC9B0", "debug": "#569CD6", "warn": "#DCDCAA", "error": "#F44747",
        "json_palette": _DARK_JSON_PALETTE,
    },
    "Minimalist White": {
        "layout": "top",
        "bg_main": "#FFFFFF",
        "bg_panel": "#F3F3F3",
        "border": "#E0E0E0",
        "text_main": "#333333",
        "text_muted": "#666666",
        "accent": "#0078D7",
        "selection": "#E8E8E8",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#008000", "debug": "#0000FF", "warn": "#FFA500", "error": "#FF0000",
        "json_palette": _LIGHT_JSON_PALETTE,
    },
    "Windows 95": {
        "layout": "top",
        "bg_main": "#C0C0C0",
        "bg_panel": "#C0C0C0",
        "border": "#808080",
        "text_main": "#000000",
        "text_muted": "#404040",
        "accent": "#000080",
        "selection": "#FFFFFF",
        "font_family": "MS Sans Serif",
        "mono_font": "Courier New",
        "info": "#008000", "debug": "#000080", "warn": "#808000", "error": "#800000",
        # Та же светлая палитра, но gutter под цвет панели Win95
        "json_palette": {
            **_LIGHT_JSON_PALETTE,
            "gutter_bg": "#A0A0A0",
            "gutter_glyph": "#000000",
            "gutter_glyph_collapsed": "#000000",
        },
    },
    "Hacker": {
        "layout": "side",
        "bg_main": "#0A0A0A",
        "bg_panel": "#111111",
        "border": "#444444",
        "text_main": "#E0E0E0",
        "text_muted": "#666666",
        "accent": "#FFFFFF",
        "selection": "#333333",
        "font_family": "Consolas",
        "mono_font": "Consolas",
        "info": "#00FF00", "debug": "#00FFFF", "warn": "#FFFF00", "error": "#FF0000",
        # Зелёная CRT-эстетика - гнём палитру под неё
        "json_palette": {
            "gutter_bg": "#0A0A0A",
            "gutter_glyph": "#008800",
            "gutter_glyph_collapsed": "#00FF00",
            "json_key": "#00FF88",
            "json_string": "#88FF00",
            "json_number": "#FFFF00",
            "json_keyword": "#00FFFF",
            "json_bracket": "#FFFFFF",
            "tree_meta": "#666666",
        },
    },

    # ----- Windows XP (Luna) -----
    # Бежевый фон Windows Classic + ярко-синий акцент Luna; шрифт Tahoma как в XP.
    "Windows XP": {
        "layout": "top",
        "bg_main": "#ECE9D8",
        "bg_panel": "#FFFFFF",
        "border": "#ACA899",
        "text_main": "#000000",
        "text_muted": "#5A5A5A",
        "accent": "#0054E3",
        "selection": "#316AC5",
        "font_family": "Tahoma",
        "mono_font": "Courier New",
        "info": "#006400", "debug": "#0054E3", "warn": "#B8860B", "error": "#A52A2A",
        "json_palette": {
            **_LIGHT_JSON_PALETTE,
            "gutter_bg": "#D6D3C0",
            "gutter_glyph": "#0054E3",
            "gutter_glyph_collapsed": "#000000",
        },
    },

    # ----- macOS Big Sur (Light) -----
    # Современный светлый Apple-стиль: молочно-белый фон, мягкие границы,
    # системный синий iOS-вариант. SF Pro / Helvetica Neue / fallback.
    "macOS Big Sur Light": {
        "layout": "top",
        "bg_main": "#FAFAFA",
        "bg_panel": "#F5F5F7",
        "border": "#D1D1D6",
        "text_main": "#1D1D1F",
        "text_muted": "#86868B",
        "accent": "#0071E3",
        "selection": "#DEEAF7",
        "font_family": "Helvetica Neue",
        "mono_font": "Menlo",
        "info": "#1F8B4C", "debug": "#0071E3", "warn": "#B45309", "error": "#D32F2F",
        "json_palette": dict(_LIGHT_JSON_PALETTE),
    },

    # ----- macOS Big Sur (Dark) -----
    # Тёмная вариация Big Sur: приглушённый чёрный, мягкие панели, ярче accent.
    "macOS Big Sur Dark": {
        "layout": "top",
        "bg_main": "#1E1E1E",
        "bg_panel": "#2A2A2C",
        "border": "#3A3A3C",
        "text_main": "#F5F5F7",
        "text_muted": "#98989D",
        "accent": "#0A84FF",
        "selection": "#284B82",
        "font_family": "Helvetica Neue",
        "mono_font": "Menlo",
        "info": "#30D158", "debug": "#0A84FF", "warn": "#FF9F0A", "error": "#FF453A",
        "json_palette": dict(_DARK_JSON_PALETTE),
    },

    # ----- Frutiger Aero (2007 Vista/Web 2.0 эстетика) -----
    # Голубое небо, белые облака, глянцевые поверхности эпохи Vista/iPhone OS 1.
    # Светлый бирюзово-голубой фон, ярко-аквамарин акцент, белые "глянцевые" панели.
    "Frutiger Aero": {
        "layout": "top",
        "bg_main": "#D7EEF9",
        "bg_panel": "#FFFFFF",
        "border": "#9EC9E5",
        "text_main": "#0A2A3F",
        "text_muted": "#406680",
        "accent": "#1FA8D8",
        "selection": "#A8DCF0",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#1A8E3D", "debug": "#1FA8D8", "warn": "#E08A00", "error": "#D14B4B",
        "json_palette": {
            **_LIGHT_JSON_PALETTE,
            "json_key": "#0E5E8A",
            "json_string": "#A04000",
            "json_number": "#1A8E3D",
            "json_keyword": "#1FA8D8",
            "gutter_bg": "#BFE0F0",
            "gutter_glyph": "#0E5E8A",
            "gutter_glyph_collapsed": "#0A2A3F",
        },
    },

    # ----- Y2K (2000s neon-silver) -----
    # Тёмный графит со стальным отливом, неоновый розово-голубой акцент,
    # ассоциация с Winamp / WMP / интерфейсами рубежа тысячелетий.
    "Y2K": {
        "layout": "top",
        "bg_main": "#1A1A23",
        "bg_panel": "#252533",
        "border": "#7B7BA8",
        "text_main": "#E6E6F0",
        "text_muted": "#9E9EBF",
        "accent": "#FF3FA4",
        "selection": "#3D2D55",
        "font_family": "Verdana",
        "mono_font": "Consolas",
        "info": "#56F39A", "debug": "#5CD2FF", "warn": "#FFD700", "error": "#FF3F6E",
        "json_palette": {
            "gutter_bg": "#15151E",
            "gutter_glyph": "#FF3FA4",
            "gutter_glyph_collapsed": "#5CD2FF",
            "json_key": "#5CD2FF",
            "json_string": "#FFB6E1",
            "json_number": "#56F39A",
            "json_keyword": "#FF3FA4",
            "json_bracket": "#C0C0C0",
            "tree_meta": "#7B7BA8",
        },
    },

    # ----- Tokyo Night (популярная тема VS Code) -----
    # Глубокий ночной синий с иссиня-фиолетовыми и циан-акцентами.
    "Tokyo Night": {
        "layout": "top",
        "bg_main": "#1A1B26",
        "bg_panel": "#24283B",
        "border": "#3B4261",
        "text_main": "#C0CAF5",
        "text_muted": "#787C99",
        "accent": "#7AA2F7",
        "selection": "#283457",
        "font_family": "Segoe UI",
        "mono_font": "JetBrains Mono",
        "info": "#9ECE6A", "debug": "#7AA2F7", "warn": "#E0AF68", "error": "#F7768E",
        "json_palette": {
            "gutter_bg": "#16161E",
            "gutter_glyph": "#565F89",
            "gutter_glyph_collapsed": "#C0CAF5",
            "json_key": "#7AA2F7",
            "json_string": "#9ECE6A",
            "json_number": "#FF9E64",
            "json_keyword": "#BB9AF7",
            "json_bracket": "#E0AF68",
            "tree_meta": "#565F89",
        },
    },

    # ----- Dracula (классическая фиолетово-зелёная тема) -----
    "Dracula": {
        "layout": "top",
        "bg_main": "#282A36",
        "bg_panel": "#21222C",
        "border": "#44475A",
        "text_main": "#F8F8F2",
        "text_muted": "#6272A4",
        "accent": "#BD93F9",
        "selection": "#44475A",
        "font_family": "Segoe UI",
        "mono_font": "Fira Code",
        "info": "#50FA7B", "debug": "#8BE9FD", "warn": "#F1FA8C", "error": "#FF5555",
        "json_palette": {
            "gutter_bg": "#1E1F29",
            "gutter_glyph": "#6272A4",
            "gutter_glyph_collapsed": "#F8F8F2",
            "json_key": "#8BE9FD",
            "json_string": "#F1FA8C",
            "json_number": "#BD93F9",
            "json_keyword": "#FF79C6",
            "json_bracket": "#FFB86C",
            "tree_meta": "#6272A4",
        },
    },

    # ----- Nord (минималистичный арктический набор) -----
    "Nord": {
        "layout": "top",
        "bg_main": "#2E3440",
        "bg_panel": "#3B4252",
        "border": "#434C5E",
        "text_main": "#ECEFF4",
        "text_muted": "#81A1C1",
        "accent": "#88C0D0",
        "selection": "#434C5E",
        "font_family": "Segoe UI",
        "mono_font": "Fira Code",
        "info": "#A3BE8C", "debug": "#88C0D0", "warn": "#EBCB8B", "error": "#BF616A",
        "json_palette": {
            "gutter_bg": "#2A2F3A",
            "gutter_glyph": "#4C566A",
            "gutter_glyph_collapsed": "#ECEFF4",
            "json_key": "#8FBCBB",
            "json_string": "#A3BE8C",
            "json_number": "#B48EAD",
            "json_keyword": "#81A1C1",
            "json_bracket": "#D8DEE9",
            "tree_meta": "#4C566A",
        },
    },

    # ----- Gruvbox Dark (ретро sepia/ochre) -----
    "Gruvbox Dark": {
        "layout": "top",
        "bg_main": "#282828",
        "bg_panel": "#32302F",
        "border": "#504945",
        "text_main": "#EBDBB2",
        "text_muted": "#A89984",
        "accent": "#FABD2F",
        "selection": "#3C3836",
        "font_family": "Segoe UI",
        "mono_font": "Hack",
        "info": "#B8BB26", "debug": "#83A598", "warn": "#FABD2F", "error": "#FB4934",
        "json_palette": {
            "gutter_bg": "#1D2021",
            "gutter_glyph": "#7C6F64",
            "gutter_glyph_collapsed": "#EBDBB2",
            "json_key": "#83A598",
            "json_string": "#B8BB26",
            "json_number": "#D3869B",
            "json_keyword": "#FE8019",
            "json_bracket": "#FABD2F",
            "tree_meta": "#928374",
        },
    },

    # ----- Synthwave '84 (неоновая закатная эстетика) -----
    "Synthwave '84": {
        "layout": "top",
        "bg_main": "#241B2F",
        "bg_panel": "#2A2139",
        "border": "#495495",
        "text_main": "#F92AAD",
        "text_muted": "#8B95C9",
        "accent": "#03EDF9",
        "selection": "#373350",
        "font_family": "Segoe UI",
        "mono_font": "JetBrains Mono",
        "info": "#72F1B8", "debug": "#03EDF9", "warn": "#FEDE5D", "error": "#FE4450",
        "json_palette": {
            "gutter_bg": "#1B1426",
            "gutter_glyph": "#495495",
            "gutter_glyph_collapsed": "#03EDF9",
            "json_key": "#03EDF9",
            "json_string": "#FF8B39",
            "json_number": "#F97E72",
            "json_keyword": "#F92AAD",
            "json_bracket": "#FEDE5D",
            "tree_meta": "#8B95C9",
        },
    },

    # ----- Catppuccin Mocha (пастельная тёмная) -----
    "Catppuccin Mocha": {
        "layout": "top",
        "bg_main": "#1E1E2E",
        "bg_panel": "#181825",
        "border": "#313244",
        "text_main": "#CDD6F4",
        "text_muted": "#7F849C",
        "accent": "#CBA6F7",
        "selection": "#313244",
        "font_family": "Segoe UI",
        "mono_font": "JetBrains Mono",
        "info": "#A6E3A1", "debug": "#89B4FA", "warn": "#F9E2AF", "error": "#F38BA8",
        "json_palette": {
            "gutter_bg": "#11111B",
            "gutter_glyph": "#585B70",
            "gutter_glyph_collapsed": "#CDD6F4",
            "json_key": "#89B4FA",
            "json_string": "#A6E3A1",
            "json_number": "#FAB387",
            "json_keyword": "#CBA6F7",
            "json_bracket": "#F9E2AF",
            "tree_meta": "#6C7086",
        },
    },
}
