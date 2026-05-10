import json
import os
import sys

APP_VERSION = "1.0.0"

GITHUB_REPO = "Hunter-James/LogAnalyzerEVOL"

# --- UI Feature Toggles ---
# Какие необязательные элементы UI можно скрыть через Настройки.
# Базовые вещи (поиск, фильтры по уровню, открытие файла) не отключаются.
DEFAULT_UI_FEATURES = {
    "match_case": True,
    "loggers_filter": True,
    "batches_filter": True,
    "time_range": True,
    "tail_mode": True,
    "save_to_journal": True,
    "group_dupes": True,
    "json_format": True,
    "selection_info": True,
    "scrollbar_markers": True,
}

UI_FEATURE_LABELS = {
    "match_case": "Кнопка «Aa» (учитывать регистр поиска)",
    "loggers_filter": "Кнопка «Компоненты» (фильтр по логгеру)",
    "batches_filter": "Кнопка «Партии» (фильтр по setCurrentBatch / api/close)",
    "time_range": "Поля диапазона времени (Время: с – по)",
    "tail_mode": "Кнопка «Следить» (tail / follow mode)",
    "save_to_journal": "Кнопка «Добавить в журнал»",
    "group_dupes": "Чекбокс «Свернуть дубли» в тулбаре",
    "json_format": "Кнопка «{ } JSON» (форматирование JSON)",
    "selection_info": "Δt и информация о выделении в статус-баре",
    "scrollbar_markers": "Метки ERROR/WARN на скроллбаре",
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

    # ----- macOS Aqua (классический OS X 10.x до Yosemite) -----
    # Светло-серый фон, голубоватая selection, шрифт Lucida Grande (на Windows
    # подменится на системный sans-serif - это OK).
    "macOS Aqua": {
        "layout": "top",
        "bg_main": "#ECECEC",
        "bg_panel": "#F5F5F5",
        "border": "#C8C8C8",
        "text_main": "#000000",
        "text_muted": "#5A5A5A",
        "accent": "#1D72E8",
        "selection": "#B5D7FE",
        "font_family": "Lucida Grande",
        "mono_font": "Menlo",
        "info": "#008000", "debug": "#1D72E8", "warn": "#A06000", "error": "#C00000",
        "json_palette": dict(_LIGHT_JSON_PALETTE),
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

    # ----- Liquid Glass (Light) -----
    # Apple Liquid Glass: тонкие холодные оттенки, очень светлый "стеклянный"
    # фон, мягкая голубая selection, тонкая полупрозрачная граница.
    # Полноценный blur-эффект Qt StyleSheet не даёт - имитируем через
    # цветовую схему: близкие к белому панели на чуть голубоватом фоне.
    "Liquid Glass Light": {
        "layout": "top",
        "bg_main": "#F5F8FC",
        "bg_panel": "#FFFFFF",
        "border": "#E0E5EB",
        "text_main": "#1D1D1F",
        "text_muted": "#6E6E73",
        "accent": "#007AFF",
        "selection": "#D6E6FF",
        "font_family": "Helvetica Neue",
        "mono_font": "SF Mono",
        "info": "#28A745", "debug": "#007AFF", "warn": "#F59E0B", "error": "#DC3545",
        "json_palette": dict(_LIGHT_JSON_PALETTE),
    },

    # ----- Liquid Glass (Dark) -----
    # Тёмная версия Liquid Glass: приглушённый антрацит со стеклянным отливом,
    # яркий iOS-blue акцент, мягкие сине-серые границы.
    "Liquid Glass Dark": {
        "layout": "top",
        "bg_main": "#1C1C1E",
        "bg_panel": "#2C2C2E",
        "border": "#3A3A3C",
        "text_main": "#F5F5F7",
        "text_muted": "#98989D",
        "accent": "#0A84FF",
        "selection": "#2D5BA8",
        "font_family": "Helvetica Neue",
        "mono_font": "SF Mono",
        "info": "#30D158", "debug": "#0A84FF", "warn": "#FF9F0A", "error": "#FF453A",
        "json_palette": dict(_DARK_JSON_PALETTE),
    },
}
