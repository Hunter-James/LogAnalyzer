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
    merged_features.update({k: bool(v) for k, v in user_features.items() if k in DEFAULT_UI_FEATURES})
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
        "info": "#2E8B57", "debug": "#4682B4", "warn": "#FFA500", "error": "#CD5C5C"
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
        "info": "#4EC9B0", "debug": "#569CD6", "warn": "#DCDCAA", "error": "#F44747"
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
        "info": "#008000", "debug": "#0000FF", "warn": "#FFA500", "error": "#FF0000"
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
        "info": "#008000", "debug": "#000080", "warn": "#808000", "error": "#800000"
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
        "info": "#00FF00", "debug": "#00FFFF", "warn": "#FFFF00", "error": "#FF0000"
    }
}
