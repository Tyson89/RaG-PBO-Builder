import json
import os
import sys
from datetime import datetime
from pathlib import Path


APP_DATA_DIR_NAME = "RaG_PBO_Builder"


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_app_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    app_dir = (Path(base) if base else Path.home()) / APP_DATA_DIR_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_settings_file_path():
    return get_app_data_dir() / "settings.json"


def get_cache_file_path():
    return get_app_data_dir() / "cache.json"


def get_logs_dir():
    logs_dir = get_app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def create_build_log_path():
    return get_logs_dir() / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def load_json_file(path):
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def load_saved_settings():
    return load_json_file(get_settings_file_path())


def save_saved_settings(data):
    save_json_file(get_settings_file_path(), data)


def load_build_cache():
    return load_json_file(get_cache_file_path())


def save_build_cache(data):
    save_json_file(get_cache_file_path(), data)
