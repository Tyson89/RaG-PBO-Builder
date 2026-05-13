import json
import os
import sys
from pathlib import Path

INSPECTOR_SETTINGS_DIR = "RaG_PBO_Inspector"

def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_initial_dir_from_value(value, fallback=""):
    value = value.strip() if value else ""
    fallback = fallback.strip() if fallback else ""

    for candidate in [value, os.path.dirname(value) if value else "", fallback, os.path.dirname(fallback) if fallback else ""]:
        if candidate and os.path.isdir(candidate):
            return candidate

    return str(Path.home())


def get_app_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    app_dir = (Path(base) if base else Path.home()) / INSPECTOR_SETTINGS_DIR
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_settings_file_path():
    return get_app_data_dir() / "settings.json"


def load_settings():
    path = get_settings_file_path()

    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data):
    path = get_settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def find_tool(possible):
    for path in possible:
        if Path(path).is_file():
            return str(path)
    return ""


def find_cfgconvert():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe"])
