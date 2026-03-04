import json
import os

APP_VERSION = "1.0.0"

GITHUB_REPO = "Hunter-James/LogAnalyzerEVOL"

# --- Settings Management ---
SETTINGS_FILE = "settings.json"

def get_settings_path():
    # Determine path relative to the executable or script
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, SETTINGS_FILE)

def load_settings():
    path = get_settings_path()
    defaults = {"theme": "Default", "font_size": 10}
    
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                saved = json.load(f)
                # Merge with defaults to ensure all keys exist
                defaults.update(saved)
                return defaults
        except Exception:
            return defaults
    return defaults

def save_settings(theme_name, font_size):
    path = get_settings_path()
    data = {"theme": theme_name, "font_size": font_size}
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
