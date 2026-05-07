"""
RaG PBO Builder

Graphite UI for building DayZ addon PBOs.

Features:
- Build selected addon folders into PBOs
- If source root contains config.cpp, build source root as one addon
- Independent named Project Source and Build Output path presets
- Optional P3D binarization with DayZ Tools binarize.exe
- Optional config.cpp to config.bin conversion with CfgConvert.exe, including nested config.cpp files
- Optional PBO signing with DSSignFile.exe
- Skip unchanged addons unless Force rebuild is enabled
- Output layout: Addons and Keys folders
- Copies matching .bikey into Keys after signing
- DayZ-focused Preflight v2 checks for config syntax, CfgPatches, CfgMods script modules, prefixes, references with line numbers, excluded assets, RVMATs, P3Ds, case conflicts, texture freshness, path issues, and terrain/WRP map checks, terrain folder/source warnings, 2D map hints, terrain layer checks, terrain size estimates, terrain size breakdowns, smarter source/export warnings, and terrain duplicate checks
- Configurable Preflight checks, compact severity filtering, and report export
- Save settings and build cache
"""

import fnmatch
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_TITLE = "RaG PBO Builder"
APP_VERSION = "0.7.0 Beta"
APP_AUTHOR = "RaG Tyson"
APP_LICENSE_NAME = "Freeware - Proprietary / All Rights Reserved"
APP_LICENSE_TEXT = """RaG PBO Builder License

Copyright (c) 2026 RaG Tyson

Freeware - Proprietary / All Rights Reserved

This software is freeware.
You may use it free of charge for personal and authorized DayZ modding purposes.

All rights reserved.

You may not sell, rent, sublicense, reupload, redistribute, modify, decompile,
reverse engineer, publish, or include this software or its source code in another
project without written permission from the author.

This software is provided "as is", without warranty of any kind, express or implied.

The author is not responsible for damaged files, lost data, invalid PBOs, failed
builds, server issues, broken signatures, leaked keys, or any other damage caused
by the use or misuse of this software.

Important:
Never share your .biprivatekey.
Only distribute the matching .bikey.
"""
APP_ICON_FILE = os.path.join("assets", "HEADONLY_SQUARE_2k.ico")

DEFAULT_TEMP_DIR = str(Path("P:/Temp"))
DEFAULT_PROJECT_ROOT = "P:"
DEFAULT_EXCLUDE_PATTERNS = "*.h,*.hpp,*.png,*.cpp,*.txt,thumbs.db,*.dep,*.bak,*.log,*.pew,source,*.tga,*.bat,*.psd,*.cmd,*.mcr,*.fbx,*.max"

EXCLUDE_DIRS = {".git", ".svn", ".vscode", ".idea", "__pycache__"}
EXCLUDE_FILES = {".gitignore", ".gitattributes", "thumbs.db", "desktop.ini", ".ds_store", "$prefix$", "$pboprefix$", "$prefix$.txt", "$pboprefix$.txt"}
EXCLUDE_EXTENSIONS = {".delete"}

GRAPHITE_BG = "#24262b"
GRAPHITE_HEADER = "#1f2126"
GRAPHITE_CARD = "#2f3238"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_BORDER_SOFT = "#3a3f48"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT = "#a74747"
GRAPHITE_ACCENT_DARK = "#7f3434"
GRAPHITE_ACCENT_HOVER = "#b65353"
GRAPHITE_PREFLIGHT = "#4f5f72"
GRAPHITE_PREFLIGHT_ACTIVE = "#60748b"
GRAPHITE_PREFLIGHT_HOVER = "#6e849d"
GRAPHITE_WARNING = "#d6aa5f"
GRAPHITE_SUCCESS = "#7fb087"
GRAPHITE_SUCCESS_DARK = "#41684a"
GRAPHITE_READY = "#4d657f"
GRAPHITE_BUILDING = "#7f5f3a"
GRAPHITE_ERROR = "#ff7070"
GRAPHITE_ERROR_DARK = "#7f3434"

ZERO = bytes([0])
WIN_SEP = chr(92)
COPY_CHUNK_SIZE = 1024 * 1024
PBO_VERSION_MAGIC = 0x56657273
PBO_STORED_METHOD = 0
TEMP_MARKER_FILE = ".rag_pbo_builder_temp"
BUILDER_TEMP_CHILDREN = {"addons", "preflight", "staging", "binarized", "configs", "_binarize_textures"}

REFERENCE_FILE_EXTENSIONS = (
    "paa", "rvmat", "p3d", "wrp", "wss", "ogg", "cfg", "cpp", "hpp", "h", "emat", "edds", "ptc", "bisurf", "wav", "shp", "dbf", "shx", "prj",
)
REFERENCE_REGEX = re.compile(
    r"[\"']([^\"']+\.(?:" + "|".join(REFERENCE_FILE_EXTENSIONS) + r"))[\"']",
    re.IGNORECASE,
)
RVMAT_TEXTURE_REGEX = re.compile(
    r"\btexture\s*=\s*[\"]?([^\";\r\n]+\.(?:paa|png|tga|psd|rvmat|emat|edds|ptc))[\"]?",
    re.IGNORECASE,
)
P3D_INTERNAL_REFERENCE_REGEX = re.compile(
    rb"([A-Za-z0-9_@#$%&()\-+={}\[\],.;: /\\]+\.(?:paa|rvmat|p3d|wrp|emat|edds|ptc|bisurf|shp|dbf|shx|prj))",
    re.IGNORECASE,
)
PREFLIGHT_TEXT_EXTENSIONS = (".cpp", ".hpp", ".h", ".rvmat", ".cfg", ".c", ".xml", ".json", ".layout", ".imageset")
RISKY_REFERENCE_EXTENSIONS = {".paa", ".rvmat", ".p3d", ".wss", ".ogg", ".wav", ".emat", ".edds", ".ptc", ".bisurf"}
SOURCE_TEXTURE_EXTENSIONS = {".png", ".tga", ".psd"}
SCRIPT_MODULE_FOLDERS = {
    "engineScriptModule": "scripts/1_Core",
    "gamelibScriptModule": "scripts/2_GameLib",
    "gameScriptModule": "scripts/3_Game",
    "worldScriptModule": "scripts/4_World",
    "missionScriptModule": "scripts/5_Mission",
}
SCRIPT_FOLDER_TO_MODULE = {value.lower().replace("/", WIN_SEP): key for key, value in SCRIPT_MODULE_FOLDERS.items()}
TERRAIN_ROAD_SHAPE_EXTENSIONS = {".shp", ".dbf", ".shx", ".prj"}
TERRAIN_WRP_INTERNAL_REFERENCE_REGEX = re.compile(
    rb"([A-Za-z0-9_@#$%&()\-+={}\[\],.;: /\\]+\.(?:paa|rvmat|p3d|wrp|emat|edds|ptc|bisurf|shp|dbf|shx|prj|xml))",
    re.IGNORECASE,
)
TERRAIN_SOURCE_FOLDER_NAMES = {"source", "sources", "terrainbuilder", "terrain_builder", "tb", "export", "exports"}
TERRAIN_SOURCE_EXPORT_EXTENSIONS = {".pew", ".asc", ".xyz", ".tif", ".tiff", ".lbt", ".psd", ".bmp", ".tv4p", ".tv4l", ".raw", ".png", ".tga"}
TERRAIN_ALWAYS_SOURCE_EXPORT_EXTENSIONS = {".pew", ".asc", ".xyz", ".tif", ".tiff", ".lbt", ".tv4p", ".tv4l", ".raw"}
TERRAIN_SOURCE_IMAGE_EXTENSIONS = {".png", ".tga", ".psd", ".bmp"}
TERRAIN_SOURCE_IMAGE_KEYWORDS = {"sat", "satellite", "mask", "height", "heightmap", "normal", "slope", "rough", "spec", "surface"}
TERRAIN_LARGE_SOURCE_FILE_BYTES = 100 * 1024 * 1024
TERRAIN_LAYER_FOLDER_NAMES = {"layers", "data\\layers", "data/layers"}
TERRAIN_SIZE_WARNING_BYTES = 1500 * 1024 * 1024
TERRAIN_SIZE_HIGH_WARNING_BYTES = 3000 * 1024 * 1024
TERRAIN_2D_MAP_REFERENCE_REGEX = re.compile(
    r"\b(?:mapTexture|mapImage|worldMap|satelliteMap|topoMap|textureMap|mapLegend|terrainMap|paperMap|navMap)\b\s*=\s*[\"']([^\"']+\.(?:paa|edds|png|tga))[\"']",
    re.IGNORECASE,
)
SAFE_INTERNAL_BASE_CLASSES = {
    "object", "managed", "pluginbase", "missionbase", "house", "buildingsuper", "itembase",
}
REQUIRED_ADDON_HINTS = {
    "Inventory_Base": "DZ_Data",
    "Clothing_Base": "DZ_Characters",
    "Clothing": "DZ_Characters",
    "CarScript": "DZ_Vehicles_Wheeled",
    "Truck_01_Base": "DZ_Vehicles_Wheeled",
    "Weapon_Base": "DZ_Weapons_Firearms",
    "Rifle_Base": "DZ_Weapons_Firearms",
    "Magazine_Base": "DZ_Weapons_Magazines",
    "Edible_Base": "DZ_Gear_Food",
    "Bottle_Base": "DZ_Gear_Drinks",
    "Container_Base": "DZ_Gear_Containers",
    "TentBase": "DZ_Gear_Camping",
}


class BuildError(Exception):
    pass


class ToolTip:
    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def get_text(self):
        if callable(self.text):
            try:
                return str(self.text())
            except Exception:
                return ""
        return str(self.text or "")

    def schedule(self, event=None):
        self.cancel()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def show(self):
        text = self.get_text()
        if self.window or not text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        self.window.configure(bg=GRAPHITE_BORDER)
        label = tk.Label(self.window, text=text, justify="left", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=8, pady=5, font=("Segoe UI", 9), wraplength=520)
        label.pack(ipadx=1, ipady=1)

    def hide(self, event=None):
        self.cancel()
        if self.window:
            self.window.destroy()
            self.window = None


def add_tooltip(widget, text):
    return ToolTip(widget, text) if text else None


def get_available_logical_threads():
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        try:
            count = process_cpu_count()
            if count and count > 0:
                return count
        except Exception:
            pass
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if callable(sched_getaffinity):
        try:
            return max(1, len(sched_getaffinity(0)))
        except Exception:
            pass
    return os.cpu_count() or 8


def get_default_max_processes():
    return max(1, min(get_available_logical_threads(), 64))


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_app_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    app_dir = (Path(base) if base else Path.home()) / "RaG_PBO_Builder"
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


def get_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def is_safe_window_geometry(value):
    if not value or not isinstance(value, str):
        return False
    match = re.match(r"^(\d+)x(\d+)([+-]\d+[+-]\d+)?$", value.strip())
    return bool(match and int(match.group(1)) >= 800 and int(match.group(2)) >= 600)


def get_initial_dir_from_value(value, fallback=""):
    value = value.strip() if value else ""
    fallback = fallback.strip() if fallback else ""
    for candidate in [value, os.path.dirname(value) if value else "", fallback, os.path.dirname(fallback) if fallback else ""]:
        if candidate and os.path.isdir(candidate):
            return candidate
    return str(Path.home())


def safe_ascii(value, label):
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise BuildError(f"{label} contains non-ASCII characters: {value}")


def get_normalized_path_key(path_value):
    path_value = str(path_value).strip()
    if not path_value:
        return ""
    try:
        return os.path.normcase(os.path.abspath(path_value))
    except Exception:
        return path_value.lower()


def get_default_preset_name_from_path(path_value, fallback_name="Preset"):
    name = os.path.basename(str(path_value).strip().rstrip(WIN_SEP + "/"))
    return name or fallback_name


def normalize_path_presets(value):
    if not isinstance(value, list):
        return []
    result = []
    seen_paths = set()
    seen_names = set()
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
        else:
            path = str(item).strip()
            name = ""
        if not path:
            continue
        path_key = get_normalized_path_key(path)
        if path_key in seen_paths:
            continue
        if not name:
            name = get_default_preset_name_from_path(path)
        base_name = name
        index = 2
        while name.casefold() in seen_names:
            name = f"{base_name} ({index})"
            index += 1
        seen_paths.add(path_key)
        seen_names.add(name.casefold())
        result.append({"name": name, "path": path})
    return result


def parse_exclude_patterns(raw_patterns):
    if not raw_patterns:
        return []
    raw_patterns = raw_patterns.replace(";", ",").replace("\r", "").replace("\n", ",")
    return [item.strip() for item in raw_patterns.split(",") if item.strip()]


def matches_exclude_pattern(name, patterns):
    if not patterns:
        return False
    value = name.lower()
    for pattern in patterns:
        test = pattern.strip().lower()
        if test and (value == test or fnmatch.fnmatch(value, test)):
            return True
    return False


def should_skip_dir(dirname, extra_patterns=None):
    name = dirname.lower()
    return name in EXCLUDE_DIRS or matches_exclude_pattern(name, extra_patterns)


def should_skip_file(filename, extra_patterns=None):
    name = filename.lower()
    if name in {"config.cpp", "config.bin"}:
        return False
    if name in EXCLUDE_FILES or os.path.splitext(name)[1].lower() in EXCLUDE_EXTENSIONS:
        return True
    return matches_exclude_pattern(name, extra_patterns)


def source_file_should_be_staged(filename, extra_patterns=None):
    return filename.lower() == "config.cpp" or not should_skip_file(filename, extra_patterns)


def file_sha1(file_path):
    digest = hashlib.sha1()
    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_sha1_cached_for_build(file_path, build_hash_cache=None):
    if build_hash_cache is None:
        return file_sha1(file_path)
    try:
        stat = os.stat(file_path)
    except OSError:
        return file_sha1(file_path)
    key = os.path.normcase(os.path.abspath(file_path))
    cached = build_hash_cache.get(key)
    if isinstance(cached, dict) and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("sha1"):
        return cached["sha1"]
    digest = file_sha1(file_path)
    build_hash_cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha1": digest}
    return digest


def files_have_same_content(source_file, target_file):
    try:
        with open(source_file, "rb") as src, open(target_file, "rb") as dst:
            while True:
                a = src.read(COPY_CHUNK_SIZE)
                b = dst.read(COPY_CHUNK_SIZE)
                if a != b:
                    return False
                if not a:
                    return True
    except OSError:
        return False


def files_are_same_for_staging(source_file, target_file, content_safe=True):
    if not os.path.isfile(target_file):
        return False
    try:
        source_stat = os.stat(source_file)
        target_stat = os.stat(target_file)
    except OSError:
        return False
    if source_stat.st_size != target_stat.st_size:
        return False
    if content_safe:
        return files_have_same_content(source_file, target_file)
    return source_stat.st_mtime_ns <= target_stat.st_mtime_ns


def file_fingerprint(file_path, include_content=False, build_hash_cache=None):
    if not file_path or not os.path.isfile(file_path):
        return {"path": file_path or "", "exists": False}
    try:
        stat = os.stat(file_path)
        result = {"path": os.path.abspath(file_path), "exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if include_content:
            result["sha1"] = file_sha1_cached_for_build(file_path, build_hash_cache)
        return result
    except OSError:
        return {"path": file_path or "", "exists": False}


def copy_source_to_staging(source_dir, staging_dir, extra_patterns=None, log=None, content_safe=True):
    os.makedirs(staging_dir, exist_ok=True)
    expected = set()
    copied = updated = unchanged = removed = 0
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not source_file_should_be_staged(file, extra_patterns):
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
            expected.add(rel.replace(os.sep, WIN_SEP).lower())
            target_file = os.path.join(staging_dir, rel)
            if files_are_same_for_staging(source_file, target_file, content_safe):
                unchanged += 1
                continue
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            existed = os.path.isfile(target_file)
            shutil.copy2(source_file, target_file)
            updated += 1 if existed else 0
            copied += 0 if existed else 1
    for root, dirs, files in os.walk(staging_dir, topdown=False):
        for file in files:
            staged_file = os.path.join(root, file)
            rel = os.path.relpath(staged_file, staging_dir).replace(os.sep, WIN_SEP).lower()
            if rel not in expected:
                os.remove(staged_file)
                removed += 1
        if root != staging_dir:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
    if log:
        log(f"Incremental staging: copied={copied}, updated={updated}, unchanged={unchanged}, removed={removed}, content_safe={content_safe}")


def overlay_tree(source_dir, destination_dir):
    if not os.path.isdir(source_dir):
        return
    for root, dirs, files in os.walk(source_dir):
        rel_root = os.path.relpath(root, source_dir)
        target_root = destination_dir if rel_root == "." else os.path.join(destination_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for file in files:
            shutil.copy2(os.path.join(root, file), os.path.join(target_root, file))


def ensure_p3d_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    copied = already_present = skipped = 0
    os.makedirs(staging_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not file.lower().endswith(".p3d"):
                continue
            if should_skip_file(file, extra_patterns):
                skipped += 1
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
            target_file = os.path.join(staging_dir, rel)
            if os.path.isfile(target_file):
                already_present += 1
                continue
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            shutil.copy2(source_file, target_file)
            copied += 1
            log(f"Copied original P3D missing from Binarize output: {rel.replace(os.sep, WIN_SEP)}")
    if copied:
        log(f"Copied {copied} original P3D file(s) that Binarize did not output.")
    else:
        log(f"All non-excluded source P3D files are already present in staging ({already_present} checked).")
    if skipped:
        log(f"Skipped {skipped} excluded P3D file(s) during P3D fallback check.")
    return copied


def ensure_config_cpp_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    copied = skipped_dirs = 0
    os.makedirs(staging_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        before = len(dirs)
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        skipped_dirs += before - len(dirs)
        for file in files:
            if file.lower() != "config.cpp":
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
            target_file = os.path.join(staging_dir, rel)
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            shutil.copy2(source_file, target_file)
            copied += 1
            log(f"Ensured config.cpp in staging: {rel.replace(os.sep, WIN_SEP)}")
    if copied:
        log(f"Ensured {copied} config.cpp file(s) are present in staging.")
    else:
        log("No included config.cpp files found while ensuring configs in staging.")
    if skipped_dirs:
        log(f"Skipped {skipped_dirs} excluded folder(s) while ensuring config.cpp files.")
    return copied


def has_p3d_files(source_dir, extra_patterns=None):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower().endswith(".p3d") and not should_skip_file(file, extra_patterns):
                return True
    return False


def normalize_project_root_arg(project_root):
    return project_root.rstrip(WIN_SEP + "/")


def normalize_working_dir(project_root):
    value = project_root.rstrip(WIN_SEP + "/")
    if len(value) == 2 and value[1] == ":":
        return value + WIN_SEP
    return value


def find_tool(possible):
    for path in possible:
        if Path(path).is_file():
            return str(path)
    return ""


def find_dayz_binarize():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"])


def find_cfgconvert():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe"])


def find_dssignfile():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe", Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe"])


def get_signature_pattern_for_pbo(pbo_path):
    return pbo_path + ".*.bisign"


def find_new_signature_for_pbo(pbo_path):
    signatures = glob.glob(get_signature_pattern_for_pbo(pbo_path))
    if not signatures:
        return ""
    signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return signatures[0]


def remove_old_signatures(pbo_path, log):
    for signature in glob.glob(get_signature_pattern_for_pbo(pbo_path)):
        try:
            os.remove(signature)
            log(f"Removed old signature: {signature}")
        except Exception as e:
            raise BuildError(f"Could not remove old signature: {signature} ({e})")


def wait_for_file_ready(file_path, log, timeout_seconds=10):
    start = time.time()
    last_size = -1
    stable = 0
    log(f"Waiting for file to be ready: {file_path}")
    while time.time() - start < timeout_seconds:
        if os.path.isfile(file_path):
            try:
                size = os.path.getsize(file_path)
                stable = stable + 1 if size > 0 and size == last_size else 0
                if stable >= 2:
                    log(f"File ready: {file_path} ({size} bytes)")
                    return
                last_size = size
            except OSError:
                stable = 0
        time.sleep(0.25)
    raise BuildError(f"File was not ready after {timeout_seconds} seconds: {file_path}")


def get_bikey_for_private_key(private_key):
    if not private_key:
        return ""
    key_path = Path(private_key)
    if key_path.suffix.lower() != ".biprivatekey":
        return ""
    bikey = key_path.with_suffix(".bikey")
    if bikey.is_file():
        return str(bikey)
    matches = list(key_path.parent.glob(key_path.stem + "*.bikey"))
    matches.sort(key=lambda p: p.name.lower())
    return str(matches[0]) if matches else ""


def copy_bikey_to_keys(private_key, output_keys_dir, log):
    bikey = get_bikey_for_private_key(private_key)
    if not bikey:
        log("WARNING: Matching .bikey was not found. Nothing copied to Keys folder.")
        return ""
    os.makedirs(output_keys_dir, exist_ok=True)
    target = os.path.join(output_keys_dir, os.path.basename(bikey))
    if os.path.isfile(target):
        log(f"Bikey already exists. Skipping copy: {target}")
        return ""
    shutil.copy2(bikey, target)
    log(f"Copied bikey -> {target}")
    return target


def run_dssignfile(dssignfile_exe, private_key, pbo_path, log):
    if not dssignfile_exe or not os.path.isfile(dssignfile_exe):
        raise BuildError("DSSignFile.exe not found. Select the DayZ Tools DSSignFile.exe path.")
    if not private_key or not os.path.isfile(private_key):
        raise BuildError("Private key not found. Select your .biprivatekey file.")
    if not private_key.lower().endswith(".biprivatekey"):
        raise BuildError("Selected private key does not end with .biprivatekey.")
    work_dir = get_app_data_dir() / "signing_temp" / f"sign_{os.getpid()}_{time.time_ns()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        work_pbo = work_dir / os.path.basename(pbo_path)
        work_key = work_dir / os.path.basename(private_key)
        shutil.copy2(pbo_path, work_pbo)
        shutil.copy2(private_key, work_key)
        remove_old_signatures(str(work_pbo), log)
        cmd = [dssignfile_exe, work_key.name, work_pbo.name]
        log("")
        log("Signing PBO in isolated temp folder:")
        log(f"  PBO:         {work_pbo.name}")
        log(f"  Key:         {work_key.name}")
        log(f"  Work folder: {work_dir}")
        result = subprocess.run(cmd, cwd=str(work_dir), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
        if result.stdout:
            for line in result.stdout.splitlines():
                log(line)
        signatures = glob.glob(str(work_pbo) + ".*.bisign")
        signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        if result.returncode != 0:
            raise BuildError(f"DSSignFile failed with exit code {result.returncode}: {pbo_path}")
        if not signatures:
            raise BuildError(f"DSSignFile finished but no .bisign was created for: {pbo_path}")
        original_dir = os.path.dirname(os.path.abspath(pbo_path))
        for signature in signatures:
            final_signature = os.path.join(original_dir, os.path.basename(signature))
            shutil.copy2(signature, final_signature)
            log(f"Created signature: {final_signature}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def create_output_work_dir(output_pbo, addon_name):
    output_dir = os.path.dirname(os.path.abspath(output_pbo))
    work_dir = os.path.join(output_dir, "_rag_build_tmp", f"{get_safe_temp_name(addon_name)}_{os.getpid()}_{time.time_ns()}")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def create_publish_backup_dir(final_pbo):
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    name = os.path.splitext(os.path.basename(final_pbo))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(final_dir, "_rag_build_backup", f"{name}_{stamp}_{os.getpid()}_{time.time_ns()}")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def copy_existing_output_artifacts_to_backup(final_pbo, backup_dir, log):
    if os.path.isfile(final_pbo):
        backup_pbo = os.path.join(backup_dir, os.path.basename(final_pbo))
        shutil.copy2(final_pbo, backup_pbo)
        log(f"Backed up existing PBO: {backup_pbo}")
    for signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
        backup_signature = os.path.join(backup_dir, os.path.basename(signature))
        shutil.copy2(signature, backup_signature)
        log(f"Backed up existing signature: {backup_signature}")


def validate_publish_backup(final_pbo, backup_dir, existing_signatures):
    if os.path.isfile(final_pbo) and not os.path.isfile(os.path.join(backup_dir, os.path.basename(final_pbo))):
        raise BuildError("Backup validation failed. Missing backup PBO.")
    for signature in existing_signatures:
        if not os.path.isfile(os.path.join(backup_dir, os.path.basename(signature))):
            raise BuildError(f"Backup validation failed. Missing backup signature: {signature}")


def remove_current_output_artifacts(final_pbo, log):
    if os.path.isfile(final_pbo):
        os.remove(final_pbo)
        log(f"Removed partially published PBO: {final_pbo}")
    for signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
        try:
            os.remove(signature)
            log(f"Removed partially published signature: {signature}")
        except FileNotFoundError:
            pass


def restore_output_artifacts_from_backup(final_pbo, backup_dir, log):
    if not os.path.isdir(backup_dir):
        return
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    log("Attempting to restore previous output artifacts from backup.")
    remove_current_output_artifacts(final_pbo, log)
    backup_pbo = os.path.join(backup_dir, os.path.basename(final_pbo))
    if os.path.isfile(backup_pbo):
        shutil.copy2(backup_pbo, final_pbo)
        log(f"Restored previous PBO: {final_pbo}")
    for backup_signature in glob.glob(os.path.join(backup_dir, os.path.basename(final_pbo) + ".*.bisign")):
        final_signature = os.path.join(final_dir, os.path.basename(backup_signature))
        shutil.copy2(backup_signature, final_signature)
        log(f"Restored previous signature: {final_signature}")


def safe_remove_empty_parent(path_value, stop_at):
    try:
        current = Path(path_value)
        stop = Path(stop_at).resolve(strict=False)
        while current.exists() and current.is_dir():
            if current.resolve(strict=False) == stop or any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except Exception:
        pass


def replace_output_artifacts(temp_pbo, final_pbo, sign_pbos, log):
    if not os.path.isfile(temp_pbo):
        raise BuildError(f"Temporary PBO does not exist and cannot replace output: {temp_pbo}")
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    os.makedirs(final_dir, exist_ok=True)
    temp_signatures = glob.glob(get_signature_pattern_for_pbo(temp_pbo))
    temp_signatures.sort(key=lambda path: os.path.basename(path).lower())
    if sign_pbos and not temp_signatures:
        raise BuildError(f"Signed build expected a .bisign but none was created for: {temp_pbo}")
    backup_dir = create_publish_backup_dir(final_pbo)
    backup_root = os.path.dirname(backup_dir)
    prepared = []
    publish_started = False
    publish_id = f"{os.getpid()}_{time.time_ns()}"
    try:
        log("Preparing output publish set.")
        existing_signatures = glob.glob(get_signature_pattern_for_pbo(final_pbo))
        existing_signatures.sort(key=lambda path: os.path.basename(path).lower())
        copy_existing_output_artifacts_to_backup(final_pbo, backup_dir, log)
        validate_publish_backup(final_pbo, backup_dir, existing_signatures)
        for temp_signature in temp_signatures:
            final_signature = os.path.join(final_dir, os.path.basename(temp_signature))
            prepared_signature = final_signature + f".new_{publish_id}"
            shutil.copy2(temp_signature, prepared_signature)
            prepared.append((prepared_signature, final_signature))
            log(f"Prepared signature for publish: {prepared_signature}")
        log("Publishing output artifacts after successful build validation.")
        publish_started = True
        os.replace(temp_pbo, final_pbo)
        log(f"Output PBO updated: {final_pbo}")
        new_names = {os.path.basename(final_signature) for _, final_signature in prepared}
        for prepared_signature, final_signature in prepared:
            os.replace(prepared_signature, final_signature)
            log(f"Output signature updated: {final_signature}")
        for old_signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
            if os.path.basename(old_signature) not in new_names:
                os.remove(old_signature)
                log(f"Removed stale signature: {old_signature}")
        shutil.rmtree(backup_dir, ignore_errors=True)
        safe_remove_empty_parent(backup_root, final_dir)
        log("Output publish set completed successfully.")
    except Exception as e:
        log(f"ERROR: Output publish failed: {e}")
        for prepared_signature, _ in prepared:
            if os.path.isfile(prepared_signature):
                try:
                    os.remove(prepared_signature)
                except Exception:
                    pass
        if publish_started:
            try:
                restore_output_artifacts_from_backup(final_pbo, backup_dir, log)
            except Exception as restore_error:
                log(f"ERROR: Could not restore previous output from backup: {restore_error}")
        else:
            log("Publish had not started yet. Existing output was left untouched.")
            shutil.rmtree(backup_dir, ignore_errors=True)
            safe_remove_empty_parent(backup_root, final_dir)
        raise BuildError(f"Output publish failed. Existing output was left untouched or restored if needed. Details: {e}")


def cleanup_output_work_dir(work_dir, log=None):
    if not work_dir:
        return
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        parent = os.path.dirname(work_dir)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except Exception as e:
        if log:
            log(f"WARNING: Could not clean output work folder: {work_dir} ({e})")


def resolve_for_safety(path_value):
    return Path(path_value).expanduser().resolve(strict=False)


def paths_overlap(path_a, path_b):
    if not path_a or not path_b:
        return False
    try:
        a = resolve_for_safety(path_a)
        b = resolve_for_safety(path_b)
        if a == b:
            return True
        try:
            a.relative_to(b)
            return True
        except ValueError:
            pass
        try:
            b.relative_to(a)
            return True
        except ValueError:
            return False
    except Exception:
        return False


def get_dangerous_temp_root_reason(temp_root, source_root="", output_root=""):
    if not temp_root:
        return "Temp dir is empty."
    try:
        root_path = resolve_for_safety(temp_root)
    except Exception as e:
        return f"Could not resolve temp dir: {e}"
    root_text = str(root_path)
    if len(root_text) < 5:
        return f"Temp dir path is too short: {root_text}"
    if root_path.parent == root_path:
        return f"Temp dir points to a filesystem root: {root_text}"
    drive, tail = os.path.splitdrive(root_text)
    if drive and tail in {"\\", "/"}:
        return f"Temp dir points to a drive root: {root_text}"
    important = [Path.home(), Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads"]
    for env_name in ["ProgramFiles", "ProgramFiles(x86)", "SystemRoot", "WINDIR", "LOCALAPPDATA", "APPDATA"]:
        value = os.environ.get(env_name)
        if value:
            important.append(Path(value))
    for item in important:
        try:
            if root_path == resolve_for_safety(item):
                return f"Temp dir points to an important folder: {root_text}"
        except Exception:
            pass
    risky = {"steam", "steamapps", "common", "dayz tools", "dayz", "program files", "program files (x86)", "windows"}
    if {part.lower() for part in root_path.parts}.intersection(risky):
        return f"Temp dir appears to be inside an important game/system folder: {root_text}"
    if source_root and paths_overlap(root_path, source_root):
        return "Temp dir overlaps with the selected Project Source."
    if output_root and paths_overlap(root_path, output_root):
        return "Temp dir overlaps with the selected Build Output."
    return ""


def ensure_builder_temp_root(temp_root, log=None, source_root="", output_root=""):
    reason = get_dangerous_temp_root_reason(temp_root, source_root, output_root)
    if reason:
        raise BuildError(f"Unsafe temp dir. {reason}")
    root_path = resolve_for_safety(temp_root)
    root_path.mkdir(parents=True, exist_ok=True)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.exists():
        marker.write_text("RaG PBO Builder temp folder marker.\nThis file allows the builder to safely clean only known builder temp folders.\n", encoding="utf-8")
        if log:
            log(f"Created temp marker: {marker}")
    return root_path


def clear_temp_folder(temp_root, log, source_root="", output_root=""):
    root_path = ensure_builder_temp_root(temp_root, None, source_root, output_root)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.is_file():
        raise BuildError("Temp marker file is missing. Refusing cleanup for safety: " + str(marker))
    log(f"Safe temp cleanup: {root_path}")
    log("Only known RaG PBO Builder temp folders will be removed.")
    removed = 0
    for child_name in sorted(BUILDER_TEMP_CHILDREN):
        child = root_path / child_name
        if not child.exists():
            continue
        resolved = resolve_for_safety(child)
        try:
            resolved.relative_to(root_path)
        except ValueError:
            raise BuildError(f"Refusing to delete path outside temp root: {resolved}")
        if resolved == root_path:
            raise BuildError(f"Refusing to delete temp root itself: {resolved}")
        was_dir = child.is_dir()
        if was_dir:
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
        log(f"Removed temp {'folder' if was_dir else 'file'}: {child}")
    if removed == 0:
        log("No known builder temp folders found to remove.")
    log("Safe temp cleanup finished.")


def clear_full_temp_folder(temp_root, log, source_root="", output_root=""):
    root_path = ensure_builder_temp_root(temp_root, None, source_root, output_root)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.is_file():
        raise BuildError("Temp marker file is missing. Refusing full cleanup for safety: " + str(marker))
    log(f"Full temp cleanup: {root_path}")
    log("All files and folders inside the temp root will be removed, except the builder marker file.")
    removed = 0
    for item in root_path.iterdir():
        if item.name == TEMP_MARKER_FILE:
            continue
        resolved = resolve_for_safety(item)
        try:
            resolved.relative_to(root_path)
        except ValueError:
            raise BuildError(f"Refusing to delete path outside temp root: {resolved}")
        if resolved == root_path:
            raise BuildError(f"Refusing to delete temp root itself: {resolved}")
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
        log(f"Removed temp item: {item}")
    if removed == 0:
        log("Full temp cleanup found nothing to remove.")
    log("Full temp cleanup finished.")


def create_temp_exclude_file(temp_root, raw_patterns, log):
    if parse_exclude_patterns(raw_patterns):
        log("Using exclude patterns internally only. No generated exclude.lst will be created.")
    return ""


def pack_pbo(source_dir, output_path, prefix, log, extra_patterns=None):
    source_dir = os.path.normpath(source_dir)
    output_path = os.path.normpath(output_path)
    if not os.path.isdir(source_dir):
        raise BuildError(f"Source is not a directory: {source_dir}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in filenames:
            if should_skip_file(fname, extra_patterns):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, source_dir).replace(os.sep, WIN_SEP)
            files.append((rel, full, os.path.getsize(full)))
    files.sort(key=lambda item: item[0].lower())
    header = bytearray()
    header.extend(ZERO)
    header.extend(struct.pack("<I", PBO_VERSION_MAGIC))
    header.extend(struct.pack("<IIII", 0, 0, 0, 0))
    if prefix:
        header.extend(b"prefix")
        header.extend(ZERO)
        header.extend(safe_ascii(prefix, "PBO prefix"))
        header.extend(ZERO)
    header.extend(ZERO)
    for rel, full, size in files:
        header.extend(safe_ascii(rel, "File path"))
        header.extend(ZERO)
        header.extend(struct.pack("<IIIII", 0, size, 0, 0, size))
    header.extend(ZERO)
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))
    temp_output = output_path + ".tmp"
    sha = hashlib.sha1()
    total = 0
    try:
        with open(temp_output, "wb") as out:
            out.write(header)
            sha.update(header)
            total += len(header)
            for rel, full, size in files:
                with open(full, "rb") as file:
                    while True:
                        chunk = file.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        sha.update(chunk)
                        total += len(chunk)
            out.write(ZERO)
            out.write(sha.digest())
            total += 21
        os.replace(temp_output, output_path)
    except Exception:
        if os.path.isfile(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass
        raise
    log(f"Packed {len(files):4d} files / {total:,} bytes -> {output_path}")


class PboArchiveEntry:
    def __init__(self, name, packing_method, original_size, reserved, timestamp, data_size, offset=0):
        self.name = name
        self.packing_method = packing_method
        self.original_size = original_size
        self.reserved = reserved
        self.timestamp = timestamp
        self.data_size = data_size
        self.offset = offset


def read_pbo_cstring(file, max_length=8192):
    data = bytearray()

    while len(data) <= max_length:
        chunk = file.read(1)

        if not chunk:
            raise BuildError("Unexpected end of file while reading PBO header string.")

        if chunk == ZERO:
            return data.decode("utf-8", errors="replace")

        data.extend(chunk)

    raise BuildError("PBO header string is too long or corrupt.")


def read_pbo_header_fields(file):
    raw = file.read(20)

    if len(raw) != 20:
        raise BuildError("Unexpected end of file while reading PBO header fields.")

    return struct.unpack("<IIIII", raw)


def read_pbo_properties(file):
    properties = {}

    while True:
        key = read_pbo_cstring(file, 1024)

        if not key:
            break

        properties[key] = read_pbo_cstring(file, 8192)

    return properties


def get_pbo_method_label(packing_method):
    if packing_method == PBO_STORED_METHOD:
        return "stored"

    try:
        raw_label = struct.pack("<I", packing_method).decode("ascii", errors="ignore").strip()
    except Exception:
        raw_label = ""

    if raw_label and all(32 <= ord(char) <= 126 for char in raw_label):
        return f"{raw_label} / 0x{packing_method:08X}"

    return f"0x{packing_method:08X}"


def format_pbo_timestamp(timestamp):
    if not timestamp:
        return ""

    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def read_pbo_archive(pbo_path):
    if not pbo_path or not os.path.isfile(pbo_path):
        raise BuildError(f"PBO file does not exist: {pbo_path}")

    file_size = os.path.getsize(pbo_path)
    entries = []
    properties = {}

    with open(pbo_path, "rb") as file:
        first_name = read_pbo_cstring(file)
        pending_name = None

        if first_name:
            pending_name = first_name
        else:
            fields = read_pbo_header_fields(file)

            if fields[0] == PBO_VERSION_MAGIC:
                properties = read_pbo_properties(file)
            elif all(value == 0 for value in fields):
                return {
                    "path": pbo_path,
                    "size": file_size,
                    "properties": properties,
                    "entries": entries,
                    "data_start": file.tell(),
                    "payload_end": file.tell(),
                    "footer_size": max(0, file_size - file.tell()),
                }
            else:
                raise BuildError(f"Unsupported or corrupt PBO header marker: 0x{fields[0]:08X}")

        while True:
            name = pending_name if pending_name is not None else read_pbo_cstring(file)
            pending_name = None
            packing_method, original_size, reserved, timestamp, data_size = read_pbo_header_fields(file)

            if not name:
                if all(value == 0 for value in [packing_method, original_size, reserved, timestamp, data_size]):
                    data_start = file.tell()
                    break

                if packing_method == PBO_VERSION_MAGIC:
                    properties.update(read_pbo_properties(file))
                    continue

                raise BuildError(f"Unsupported or corrupt PBO header entry: 0x{packing_method:08X}")

            entries.append(PboArchiveEntry(name, packing_method, original_size, reserved, timestamp, data_size))

        offset = data_start

        for entry in entries:
            entry.offset = offset
            offset += entry.data_size

        if offset > file_size:
            raise BuildError("PBO header file sizes exceed archive length. The PBO may be corrupt.")

    return {
        "path": pbo_path,
        "size": file_size,
        "properties": properties,
        "entries": entries,
        "data_start": data_start,
        "payload_end": offset,
        "footer_size": max(0, file_size - offset),
    }


def get_safe_pbo_extract_path(output_dir, entry_name):
    if not output_dir:
        raise BuildError("Extract output folder is empty.")

    if not entry_name or "\x00" in entry_name:
        raise BuildError("PBO entry has an invalid empty or NUL-containing filename.")

    raw = entry_name.replace("/", WIN_SEP)

    if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
        raise BuildError(f"Refusing to extract absolute PBO path: {entry_name}")

    parts = []

    for part in re.split(r"[\\/]+", raw):
        if not part or part == ".":
            continue

        if part == ".." or ":" in part:
            raise BuildError(f"Refusing unsafe PBO path: {entry_name}")

        parts.append(part)

    if not parts:
        raise BuildError(f"Refusing empty PBO path after normalization: {entry_name}")

    root = Path(output_dir).resolve(strict=False)
    target = root.joinpath(*parts).resolve(strict=False)

    try:
        target.relative_to(root)
    except ValueError:
        raise BuildError(f"Refusing to extract outside output folder: {entry_name}")

    return target


def extract_pbo_files(pbo_path, output_dir, selected_names=None, log=None):
    archive = read_pbo_archive(pbo_path)
    selected = set(selected_names or [])
    should_filter = bool(selected)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    extracted = 0
    total_bytes = 0

    with open(pbo_path, "rb") as source:
        for entry in archive["entries"]:
            if should_filter and entry.name not in selected:
                continue

            if entry.packing_method != PBO_STORED_METHOD:
                raise BuildError(f"Cannot extract compressed or unsupported PBO entry: {entry.name} ({get_pbo_method_label(entry.packing_method)})")

            target = get_safe_pbo_extract_path(output_dir, entry.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.seek(entry.offset)
            remaining = entry.data_size

            with open(target, "wb") as out:
                while remaining > 0:
                    chunk = source.read(min(COPY_CHUNK_SIZE, remaining))

                    if not chunk:
                        raise BuildError(f"Unexpected end of PBO data while extracting: {entry.name}")

                    out.write(chunk)
                    remaining -= len(chunk)

            extracted += 1
            total_bytes += entry.data_size

            if log:
                log(f"Extracted: {entry.name}")

    if extracted == 0:
        raise BuildError("No PBO entries were selected for extraction.")

    return {
        "files": extracted,
        "bytes": total_bytes,
        "output_dir": str(output_root),
    }


def get_safe_temp_name(name):
    safe = name.strip() if name else "addon"
    safe = safe.replace("/", "_").replace(WIN_SEP, "_").replace(":", "_")
    return safe or "addon"


def get_addon_temp_root(temp_root, addon_name):
    return os.path.join(temp_root, "addons", get_safe_temp_name(addon_name))


def get_pbo_base_name(folder_name, pbo_name, selected_count):
    clean = pbo_name.strip() if pbo_name else ""
    if clean and selected_count == 1:
        return clean.replace(".pbo", "").replace("/", "_").replace(WIN_SEP, "_")
    return folder_name


def read_pbo_prefix_file(source_dir):
    names = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
    try:
        entries = os.listdir(source_dir)
    except OSError:
        return ""
    for entry in entries:
        if entry.lower() not in names:
            continue
        path = os.path.join(source_dir, entry)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as file:
                for line in file:
                    prefix = line.strip().strip('"').strip("'")
                    if prefix:
                        return prefix.replace("/", WIN_SEP).strip(WIN_SEP + "/")
        except OSError:
            return ""
    return ""


def get_pbo_prefix(pbo_base_name, source_dir=None):
    file_prefix = read_pbo_prefix_file(source_dir) if source_dir else ""
    return file_prefix or pbo_base_name


def detect_addon_targets(source_root, output_addons_dir):
    if not os.path.isdir(source_root):
        return []
    source_root = os.path.normpath(source_root)
    if os.path.isfile(os.path.join(source_root, "config.cpp")):
        return [(os.path.basename(source_root) or "addon", source_root)]
    result = []
    output_addons_abs = os.path.abspath(output_addons_dir) if output_addons_dir else ""
    for name in os.listdir(source_root):
        full = os.path.join(source_root, name)
        if not os.path.isdir(full) or should_skip_dir(name) or name.lower() in {"output", "addons", "keys"}:
            continue
        try:
            full_abs = os.path.abspath(full)
            if output_addons_abs and (full_abs == output_addons_abs or output_addons_abs.startswith(full_abs + os.sep)):
                continue
        except Exception:
            pass
        result.append((name, full))
    result.sort(key=lambda item: item[0].lower())
    return result


def compute_addon_state_hash(source_dir, prefix, settings, extra_patterns=None, build_hash_cache=None):
    digest = hashlib.sha1()
    tracked = {
        "prefix": prefix,
        "pbo_name": settings.get("pbo_name", ""),
        "use_binarize": bool(settings["use_binarize"]),
        "convert_config": bool(settings["convert_config"]),
        "sign_pbos": bool(settings["sign_pbos"]),
        "project_root": settings["project_root"],
        "exclude_patterns": settings["exclude_patterns"],
        "max_processes": settings["max_processes"],
        "binarize_exe": file_fingerprint(settings.get("binarize_exe", ""), True, build_hash_cache),
        "cfgconvert_exe": file_fingerprint(settings.get("cfgconvert_exe", ""), True, build_hash_cache),
        "dssignfile_exe": file_fingerprint(settings.get("dssignfile_exe", ""), True, build_hash_cache),
    }
    private_key = settings.get("private_key", "")
    if settings.get("sign_pbos") and os.path.isfile(private_key):
        tracked["private_key"] = file_fingerprint(private_key, True, build_hash_cache)
    digest.update(json.dumps(tracked, sort_keys=True).encode("utf-8"))
    for root, dirs, filenames in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in sorted(filenames, key=lambda value: value.lower()):
            if should_skip_file(fname, extra_patterns):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, source_dir).replace(os.sep, WIN_SEP).lower()
            try:
                stat = os.stat(full)
            except OSError:
                continue
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(file_sha1_cached_for_build(full, build_hash_cache).encode("ascii"))
    return digest.hexdigest()


def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class PreflightResult:
    def __init__(self):
        self.errors = 0
        self.warnings = 0
        self.info = 0
        self.checked_files = 0
        self.checked_references = 0
        self.checked_configs = 0
        self.checked_script_modules = 0
        self.checked_prefixes = 0
        self.checked_paths = 0
        self.checked_terrain = 0
        self.events = []
        self.report_txt = ""
        self.report_json = ""

    def add_event(self, severity, message):
        self.events.append({
            "severity": severity,
            "message": message,
        })

    def error(self, log, message):
        self.errors += 1
        self.add_event("ERROR", message)
        log("ERROR: " + message)

    def warning(self, log, message):
        self.warnings += 1
        self.add_event("WARNING", message)
        log("WARNING: " + message)

    def note(self, log, message):
        self.info += 1
        self.add_event("INFO", message)
        log("INFO: " + message)


def strip_cpp_comments(content):
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"//.*?$", "", content, flags=re.MULTILINE)
    return content


def find_matching_brace(content, open_index):
    depth = 0
    in_string = ""
    escaped = False

    for index in range(open_index, len(content)):
        char = content[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            continue

        if char in {'"', "'"}:
            in_string = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1


def find_class_body(content, class_name):
    pattern = re.compile(r"\bclass\s+" + re.escape(class_name) + r"\b[^;{]*\{", re.IGNORECASE)
    match = pattern.search(content)

    if not match:
        return ""

    open_index = content.find("{", match.start())
    close_index = find_matching_brace(content, open_index)

    if close_index < 0:
        return ""

    return content[open_index + 1:close_index]


def iter_class_blocks(content):
    pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{", re.IGNORECASE)
    position = 0

    while True:
        match = pattern.search(content, position)

        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_brace(content, open_index)

        if close_index < 0:
            position = match.end()
            continue

        yield match.group(1), match.group(2) or "", content[open_index + 1:close_index], match.start(), close_index + 1
        position = close_index + 1


def iter_top_level_class_blocks(content):
    position = 0
    pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{", re.IGNORECASE)

    while True:
        match = pattern.search(content, position)

        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_brace(content, open_index)

        if close_index < 0:
            position = match.end()
            continue

        yield match.group(1), match.group(2) or "", content[open_index + 1:close_index]
        position = close_index + 1


def parse_array_values(content, array_name):
    pattern = re.compile(r"\b" + re.escape(array_name) + r"\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;", re.IGNORECASE | re.DOTALL)
    match = pattern.search(content)

    if not match:
        return None

    values = []

    for item in match.group(1).split(","):
        item = item.strip().strip('"').strip("'")

        if item:
            values.append(item)

    return values


def strip_dayz_resource_guid_prefix(value):
    # DayZ .layout and some GUI/resource files can prefix asset paths with a
    # Workbench/resource GUID, for example:
    #   {03C79F5D93FF384F}RaG_Config/Data/LoadingScreens/1.edds
    # The GUID is not part of the actual packed PBO path and must be ignored
    # during reference resolution, exclude checks, and missing-file checks.
    value = str(value).strip()

    match = re.match(r"^\{[0-9A-Fa-f]{8,32}\}(.+)$", value)

    if match:
        return match.group(1).strip()

    return value


def normalize_reference_path(reference):
    value = str(reference).strip().strip('"').strip("'")
    value = strip_dayz_resource_guid_prefix(value)
    value = value.replace("/", WIN_SEP)

    while value.startswith(WIN_SEP):
        value = value[1:]

    return value


def normalize_rel_path_key(path_value):
    return normalize_reference_path(path_value).lower()


def is_path_inside(child, parent):
    try:
        Path(child).resolve(strict=False).relative_to(Path(parent).resolve(strict=False))
        return True
    except Exception:
        return False


def path_would_be_excluded(relative_path, extra_patterns=None):
    parts = [part for part in normalize_reference_path(relative_path).split(WIN_SEP) if part]

    if not parts:
        return False

    for directory in parts[:-1]:
        if should_skip_dir(directory, extra_patterns):
            return True

    return should_skip_file(parts[-1], extra_patterns)


def resolve_reference_path(reference, addon_source_dir, project_root):
    ref = normalize_reference_path(reference)

    if not ref:
        return "", "missing"

    ref_os = ref.replace(WIN_SEP, os.sep)
    candidates = []

    if os.path.isabs(ref_os):
        candidates.append(ref_os)

    addon_source_dir = os.path.normpath(addon_source_dir)
    addon_parent = os.path.dirname(addon_source_dir)

    candidates.append(os.path.join(addon_source_dir, ref_os))
    candidates.append(os.path.join(addon_parent, ref_os))

    parts = [part for part in ref.split(WIN_SEP) if part]
    addon_folder = os.path.basename(os.path.normpath(addon_source_dir))
    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
    prefix_first = explicit_prefix.split(WIN_SEP)[0] if explicit_prefix else ""

    if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
        candidates.append(os.path.join(addon_source_dir, *parts[1:]))

    if project_root:
        candidates.append(os.path.join(normalize_working_dir(project_root), ref_os))

    seen = set()

    for candidate in candidates:
        candidate = os.path.normpath(candidate)
        key = os.path.normcase(os.path.abspath(candidate))

        if key in seen:
            continue

        seen.add(key)

        if os.path.isfile(candidate):
            return candidate, "ok"

    return candidates[0] if candidates else ref_os, "missing"


def get_line_number_from_index(content, index):
    if content is None or index is None:
        return 0

    try:
        return content.count(chr(10), 0, max(0, index)) + 1
    except Exception:
        return 0


def format_source_location(source_file, addon_source_dir, line_number=0):
    if source_file:
        try:
            rel_file = os.path.relpath(source_file, addon_source_dir).replace(os.sep, WIN_SEP)
        except Exception:
            rel_file = str(source_file)
    else:
        rel_file = "<unknown>"

    if line_number and line_number > 0:
        return f"{rel_file}: line {line_number}"

    return rel_file


def report_reference_status(reference, source_file, addon_source_dir, project_root, extra_patterns, result, log, severity="error", context="referenced file", line_number=0):
    ref = normalize_reference_path(reference)

    if not ref:
        return

    source_location = format_source_location(source_file, addon_source_dir, line_number)
    resolved, status = resolve_reference_path(ref, addon_source_dir, project_root)

    result.checked_references += 1

    if status == "missing":
        message = f"Missing {context} in {source_location}: {ref}"
        if severity == "warning":
            result.warning(log, message)
        else:
            result.error(log, message)
        return

    if is_path_inside(resolved, addon_source_dir):
        rel_resolved = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)

        if path_would_be_excluded(rel_resolved, extra_patterns):
            result.error(log, f"Referenced file exists but is excluded from the packed PBO in {source_location}: {ref} -> {rel_resolved}")


def collect_config_cpp_files(source_dir, extra_patterns=None):
    configs = []

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if file.lower() == "config.cpp":
                configs.append(os.path.join(root, file))

    configs.sort(key=lambda path: os.path.relpath(path, source_dir).lower())
    return configs


def collect_pbo_prefix_files(source_dir):
    prefix_names = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
    matches = []

    try:
        entries = os.listdir(source_dir)
    except OSError:
        return matches

    for entry in entries:
        if entry.lower() in prefix_names:
            full = os.path.join(source_dir, entry)
            if os.path.isfile(full):
                matches.append(full)

    matches.sort(key=lambda value: os.path.basename(value).lower())
    return matches


def read_raw_prefix_file(prefix_file):
    try:
        with open(prefix_file, "r", encoding="utf-8-sig", errors="ignore") as file:
            for line in file:
                value = line.strip().strip('"').strip("'")
                if value:
                    return value
    except OSError:
        return ""

    return ""


def preflight_check_prefix(addon_name, addon_source_dir, result, log):
    result.checked_prefixes += 1
    prefix_files = collect_pbo_prefix_files(addon_source_dir)

    if len(prefix_files) > 1:
        names = ", ".join(os.path.basename(path) for path in prefix_files)
        result.warning(log, f"Multiple PBO prefix files found in {addon_name}: {names}")

    if not prefix_files:
        result.note(log, f"No PBO prefix file found in {addon_name}. The PBO Name/folder name will be used as prefix.")
        return

    raw_prefix = read_raw_prefix_file(prefix_files[0])

    if not raw_prefix:
        result.warning(log, f"PBO prefix file is empty: {prefix_files[0]}")
        return

    if raw_prefix.startswith("P:" + WIN_SEP) or raw_prefix.startswith("P:/"):
        result.warning(log, f"PBO prefix starts with P: in {addon_name}: {raw_prefix}")

    if raw_prefix.startswith(WIN_SEP) or raw_prefix.startswith("/"):
        result.warning(log, f"PBO prefix has a leading slash in {addon_name}: {raw_prefix}")

    if raw_prefix.endswith(WIN_SEP) or raw_prefix.endswith("/"):
        result.warning(log, f"PBO prefix has a trailing slash in {addon_name}: {raw_prefix}")

    if "/" in raw_prefix:
        result.warning(log, f"PBO prefix uses forward slashes in {addon_name}. Backslashes are recommended: {raw_prefix}")

    normalized_prefix = raw_prefix.replace("/", WIN_SEP).strip(WIN_SEP)
    last_prefix_part = normalized_prefix.split(WIN_SEP)[-1]
    folder_name = os.path.basename(os.path.normpath(addon_source_dir))
    prefix_norm = re.sub(r"[^a-z0-9]", "", last_prefix_part.lower())
    folder_norm = re.sub(r"[^a-z0-9]", "", folder_name.lower())

    if prefix_norm and folder_norm and prefix_norm not in folder_norm and not folder_norm.endswith(prefix_norm):
        result.warning(log, f"PBO prefix seems unrelated to the addon folder in {addon_name}: prefix '{raw_prefix}', folder '{folder_name}'")

    result.note(log, f"Detected PBO prefix for {addon_name}: {normalized_prefix}")


def preflight_check_config_cpp(config_cpp, cfgconvert_exe, temp_root, addon_name, result, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        result.warning(log, "CfgConvert.exe is not configured. Skipping config.cpp syntax check.")
        return

    check_dir = os.path.join(temp_root, "preflight", get_safe_temp_name(addon_name))
    os.makedirs(check_dir, exist_ok=True)
    output_bin = os.path.join(check_dir, os.path.basename(config_cpp) + ".bin")

    if os.path.isfile(output_bin):
        os.remove(output_bin)

    cmd = [cfgconvert_exe, "-bin", "-dst", output_bin, config_cpp]
    completed = subprocess.run(
        cmd,
        cwd=os.path.dirname(config_cpp),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=get_subprocess_creationflags(),
        startupinfo=get_hidden_startupinfo(),
    )

    if completed.returncode != 0 or not os.path.isfile(output_bin):
        result.error(log, f"Config syntax check failed: {config_cpp}")

        for line in (completed.stdout or "No CfgConvert output.").splitlines():
            log("  " + line)
    else:
        log(f"Config syntax OK: {config_cpp}")


def get_external_base_classes(content):
    clean = strip_cpp_comments(content)
    classes = []
    class_names = set()

    for class_name, base_name, _, _, _ in iter_class_blocks(clean):
        classes.append((class_name, base_name))
        class_names.add(class_name)

    external_bases = []

    for class_name, base_name in classes:
        if not base_name:
            continue

        if base_name in class_names:
            continue

        if base_name.lower() in SAFE_INTERNAL_BASE_CLASSES:
            continue

        external_bases.append((class_name, base_name))

    return external_bases


def get_required_addon_hints_for_bases(external_bases):
    hints = set()

    for _, base_name in external_bases:
        for base_hint, required_addon in REQUIRED_ADDON_HINTS.items():
            if base_name == base_hint or base_name.endswith(base_hint) or base_hint.lower() in base_name.lower():
                hints.add(required_addon)

    return sorted(hints)


def preflight_check_cfgpatches(config_cpp, addon_source_dir, result, log, enable_required_addons_hints=True):
    try:
        content = Path(config_cpp).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        result.warning(log, f"Could not read config.cpp for CfgPatches check: {config_cpp} ({e})")
        return

    result.checked_configs += 1
    rel_config = os.path.relpath(config_cpp, addon_source_dir).replace(os.sep, WIN_SEP)
    clean = strip_cpp_comments(content)
    cfgpatches_body = find_class_body(clean, "CfgPatches")

    if not cfgpatches_body:
        result.error(log, f"config.cpp has no CfgPatches class: {rel_config}")
        return

    patch_classes = list(iter_top_level_class_blocks(cfgpatches_body))

    if not patch_classes:
        result.error(log, f"CfgPatches exists but contains no addon patch class: {rel_config}")
        return

    external_bases = get_external_base_classes(content) if enable_required_addons_hints else []
    required_hints = get_required_addon_hints_for_bases(external_bases) if enable_required_addons_hints else []

    for patch_name, _, patch_body in patch_classes:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", patch_name):
            result.warning(log, f"CfgPatches class name contains unsafe characters in {rel_config}: {patch_name}")

        required_addons = parse_array_values(patch_body, "requiredAddons")

        if required_addons is None:
            result.warning(log, f"requiredAddons[] is missing in CfgPatches class {patch_name} ({rel_config})")

            if external_bases:
                sample = ", ".join([f"{child}: {base}" for child, base in external_bases[:5]])
                result.warning(log, f"{patch_name} inherits from external-looking classes but has no requiredAddons[] entry: {sample}")

            continue

        if not required_addons:
            result.note(log, f"requiredAddons[] is empty in CfgPatches class {patch_name} ({rel_config}). This can be valid, but verify load order manually.")

            if external_bases:
                sample = ", ".join([f"{child}: {base}" for child, base in external_bases[:5]])
                result.warning(log, f"requiredAddons[] is empty, but {rel_config} inherits from external-looking classes: {sample}")

        if required_hints:
            missing_hints = [hint for hint in required_hints if hint not in required_addons]

            if missing_hints:
                result.note(log, f"Possible requiredAddons[] hints for {patch_name}: {', '.join(missing_hints)}")


def resolve_script_module_path(path_value, addon_source_dir, project_root, prefix=""):
    raw = normalize_reference_path(path_value).rstrip(WIN_SEP)

    if not raw:
        return "", False

    candidates = []
    addon_folder = os.path.basename(os.path.normpath(addon_source_dir))
    prefix_first = normalize_reference_path(prefix).split(WIN_SEP)[0] if prefix else ""

    candidates.append(os.path.join(addon_source_dir, raw))

    parts = [part for part in raw.split(WIN_SEP) if part]

    if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
        candidates.append(os.path.join(addon_source_dir, *parts[1:]))

    if project_root:
        candidates.append(os.path.join(normalize_working_dir(project_root), raw))

    seen = set()

    for candidate in candidates:
        candidate = os.path.normpath(candidate)
        key = os.path.normcase(os.path.abspath(candidate))

        if key in seen:
            continue

        seen.add(key)

        if os.path.isdir(candidate) or os.path.isfile(candidate):
            return candidate, True

    return candidates[0] if candidates else raw, False


def resolve_config_include_path(include_value, config_cpp, addon_source_dir="", project_root=""):
    raw = normalize_reference_path(include_value).strip(WIN_SEP)

    if not raw:
        return ""

    include_os = raw.replace(WIN_SEP, os.sep)
    config_dir = Path(config_cpp).parent
    candidates = [config_dir / include_os]

    if addon_source_dir:
        addon_source_dir = os.path.normpath(addon_source_dir)
        addon_parent = os.path.dirname(addon_source_dir)
        addon_folder = os.path.basename(addon_source_dir)
        explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
        prefix_first = explicit_prefix.split(WIN_SEP)[0] if explicit_prefix else ""
        parts = [part for part in raw.split(WIN_SEP) if part]

        candidates.append(Path(addon_source_dir) / include_os)
        candidates.append(Path(addon_parent) / include_os)

        if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
            candidates.append(Path(addon_source_dir).joinpath(*parts[1:]))

    if project_root:
        candidates.append(Path(normalize_working_dir(project_root)) / include_os)

    seen = set()

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate

        key = os.path.normcase(str(resolved))

        if key in seen:
            continue

        seen.add(key)

        if resolved.is_file():
            return str(resolved)

    return ""


def read_config_with_local_includes(config_cpp, seen=None, addon_source_dir="", project_root=""):
    if seen is None:
        seen = set()

    try:
        path = Path(config_cpp).resolve(strict=False)
    except Exception:
        path = Path(config_cpp)

    key = os.path.normcase(str(path))

    if key in seen:
        return ""

    seen.add(key)

    try:
        content = Path(config_cpp).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.IGNORECASE | re.MULTILINE)

    def replace_include(match):
        include_path = resolve_config_include_path(match.group(1).strip(), config_cpp, addon_source_dir, project_root)

        if include_path:
            return read_config_with_local_includes(include_path, seen, addon_source_dir, project_root)

        return match.group(0)

    return include_pattern.sub(replace_include, content)


def config_file_mentions_class(config_cpp, class_name, addon_source_dir="", project_root=""):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        return False

    clean = strip_cpp_comments(content)
    pattern = re.compile(r"\bclass\s+" + re.escape(class_name) + r"\b", re.IGNORECASE)

    return bool(pattern.search(clean))


def config_file_has_class(config_cpp, class_name, addon_source_dir="", project_root=""):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        return False

    clean = strip_cpp_comments(content)

    if find_class_body(clean, class_name):
        return True

    return config_file_mentions_class(config_cpp, class_name, addon_source_dir, project_root)


def find_config_cpp_with_class(config_files, class_name, addon_source_dir="", project_root=""):
    for config_cpp in config_files:
        if config_file_has_class(config_cpp, class_name, addon_source_dir, project_root):
            return config_cpp

    return ""


def preflight_check_cfgmods(config_cpp, addon_name, addon_source_dir, project_root, result, log):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        result.warning(log, f"Could not read config.cpp for CfgMods check: {config_cpp}")
        return

    clean = strip_cpp_comments(content)
    cfgmods_body = find_class_body(clean, "CfgMods")
    cfgmods_is_declared = bool(re.search(r"\bclass\s+CfgMods\b", clean, re.IGNORECASE))
    script_folders = []

    for folder in SCRIPT_MODULE_FOLDERS.values():
        folder_path = os.path.join(addon_source_dir, *folder.split("/"))

        if os.path.isdir(folder_path):
            script_folders.append((folder, folder_path))

    if not cfgmods_body:
        if cfgmods_is_declared:
            result.note(log, f"CfgMods class was found in addon configs, but the body could not be parsed for script module path checks: {addon_name}")
        elif script_folders:
            result.warning(log, f"Script folders exist but no CfgMods class was found in addon configs: {addon_name}")
        return

    if "class defs" not in cfgmods_body.lower():
        result.warning(log, f"CfgMods exists but has no class defs section: {addon_name}")

    prefix = get_pbo_prefix(addon_name, addon_source_dir)
    referenced_paths = []
    missing_module_folder_keys = set()

    for module_name, expected_folder in SCRIPT_MODULE_FOLDERS.items():
        module_body = find_class_body(cfgmods_body, module_name)

        if not module_body:
            expected_path = os.path.join(addon_source_dir, *expected_folder.split("/"))
            if os.path.isdir(expected_path):
                result.warning(log, f"{expected_folder} exists but no {module_name} files[] entry was found in CfgMods: {addon_name}")
                missing_module_folder_keys.add(os.path.normcase(os.path.abspath(expected_path)))
            continue

        result.checked_script_modules += 1
        files = parse_array_values(module_body, "files")

        if files is None:
            result.warning(log, f"{module_name} exists but has no files[] path: {addon_name}")
            continue

        if not files:
            result.warning(log, f"{module_name} files[] is empty: {addon_name}")
            continue

        for file_path in files:
            resolved, exists = resolve_script_module_path(file_path, addon_source_dir, project_root, prefix)
            referenced_paths.append(os.path.normcase(os.path.abspath(resolved)))

            if not exists:
                result.warning(log, f"{module_name} files[] path does not exist: {file_path}")

    for folder, folder_path in script_folders:
        folder_key = os.path.normcase(os.path.abspath(folder_path))
        if folder_key in missing_module_folder_keys:
            continue

        is_referenced = False

        for referenced in referenced_paths:
            if referenced == folder_key or folder_key.startswith(referenced + os.sep) or referenced.startswith(folder_key + os.sep):
                is_referenced = True
                break

        if not is_referenced:
            module_name = SCRIPT_FOLDER_TO_MODULE.get(folder.lower().replace("/", WIN_SEP), "script module")
            result.warning(log, f"{folder} exists but is not referenced by {module_name} files[] in CfgMods: {addon_name}")


def preflight_scan_references(file_path, addon_source_dir, project_root, extra_patterns, result, log):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
            content = file.read()
    except Exception as e:
        result.warning(log, f"Could not read file for reference scan: {file_path} ({e})")
        return

    result.checked_files += 1
    seen = set()
    ext = os.path.splitext(file_path)[1].lower()

    for match in REFERENCE_REGEX.finditer(content):
        ref = normalize_reference_path(match.group(1).strip())
        ref_ext = os.path.splitext(ref)[1].lower()

        # Terrain-specific config references are handled by the WRP/terrain checks
        # so users do not get duplicate errors for worldName and road shape paths.
        if ext == ".cpp" and ref_ext in {".wrp", ".shp", ".dbf", ".shx", ".prj"}:
            continue

        key = ref.lower()
        line_number = get_line_number_from_index(content, match.start(1))

        if key in seen:
            continue

        seen.add(key)
        report_reference_status(ref, file_path, addon_source_dir, project_root, extra_patterns, result, log, "error", "referenced file", line_number)

    if ext == ".rvmat":
        preflight_scan_rvmat_textures(file_path, content, addon_source_dir, project_root, extra_patterns, result, log, seen)


def preflight_scan_rvmat_textures(file_path, content, addon_source_dir, project_root, extra_patterns, result, log, seen=None):
    seen = seen if seen is not None else set()
    rel_file = os.path.relpath(file_path, addon_source_dir).replace(os.sep, WIN_SEP)

    for match in RVMAT_TEXTURE_REGEX.finditer(content):
        ref = normalize_reference_path(match.group(1).strip())
        key = ref.lower()
        line_number = get_line_number_from_index(content, match.start(1))
        source_location = format_source_location(file_path, addon_source_dir, line_number)

        if key in seen:
            continue

        seen.add(key)
        ext = os.path.splitext(ref)[1].lower()

        if ext in SOURCE_TEXTURE_EXTENSIONS:
            result.warning(log, f"RVMAT references a source texture format instead of .paa in {source_location}: {ref}")

        report_reference_status(ref, file_path, addon_source_dir, project_root, extra_patterns, result, log, "error", "RVMAT texture", line_number)


def preflight_scan_p3d_internal_references(p3d_file, addon_source_dir, project_root, extra_patterns, result, log):
    rel_file = os.path.relpath(p3d_file, addon_source_dir).replace(os.sep, WIN_SEP)

    try:
        with open(p3d_file, "rb") as file:
            data = file.read()
    except Exception as e:
        result.warning(log, f"Could not read P3D for internal reference scan: {rel_file} ({e})")
        return

    result.checked_files += 1
    seen = set()
    found = 0

    for match in P3D_INTERNAL_REFERENCE_REGEX.finditer(data):
        ref = normalize_reference_path(match.group(1).decode("ascii", errors="ignore").strip())
        key = ref.lower()

        if not ref or key in seen or len(ref) < 5:
            continue

        seen.add(key)
        found += 1
        report_reference_status(ref, p3d_file, addon_source_dir, project_root, extra_patterns, result, log, "warning", "internal P3D reference")

    if found:
        log(f"P3D internal scan checked {found} reference(s): {rel_file}")


def preflight_scan_case_conflicts(addon_source_dir, extra_patterns, result, log):
    seen = {}

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)
            rel = os.path.relpath(full, addon_source_dir).replace(os.sep, WIN_SEP)
            key = rel.lower()

            if key in seen and seen[key] != rel:
                result.warning(log, f"Case-only path conflict detected: {seen[key]} <-> {rel}")
            else:
                seen[key] = rel


def preflight_scan_texture_freshness(addon_source_dir, extra_patterns, result, log):
    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]
        file_map = {file.lower(): file for file in files}

        for file in files:
            ext = os.path.splitext(file)[1].lower()

            if ext not in SOURCE_TEXTURE_EXTENSIONS:
                continue

            source_texture = os.path.join(root, file)
            paa_name = os.path.splitext(file)[0] + ".paa"
            paa_file = file_map.get(paa_name.lower())
            rel_source = os.path.relpath(source_texture, addon_source_dir).replace(os.sep, WIN_SEP)

            if should_skip_file(file, extra_patterns):
                continue

            if not paa_file:
                result.warning(log, f"Source texture exists without matching .paa: {rel_source}")
                continue

            paa_path = os.path.join(root, paa_file)

            try:
                if os.path.getmtime(source_texture) > os.path.getmtime(paa_path):
                    rel_paa = os.path.relpath(paa_path, addon_source_dir).replace(os.sep, WIN_SEP)
                    result.warning(log, f"Source texture is newer than .paa: {rel_source} -> {rel_paa}")
            except OSError:
                pass


def preflight_scan_invalid_paths(addon_source_dir, extra_patterns, result, log):
    invalid_chars = set('<>"|?*')

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for name in list(dirs) + list(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, addon_source_dir).replace(os.sep, WIN_SEP)
            result.checked_paths += 1

            if any(ord(char) < 32 for char in name):
                result.warning(log, f"Path contains control characters: {rel}")

            if any(char in invalid_chars for char in name):
                result.warning(log, f"Path contains Windows-invalid characters: {rel}")

            if name != name.strip():
                result.warning(log, f"Path has leading/trailing whitespace: {rel}")

            try:
                rel.encode("ascii")
            except UnicodeEncodeError:
                result.warning(log, f"Path contains non-ASCII characters: {rel}")

            if len(os.path.abspath(full)) > 240:
                result.warning(log, f"Path is very long and may cause tool issues: {rel}")


def verify_pack_source_before_packing(original_source_dir, pack_source, convert_config, log, extra_patterns=None):
    if not os.path.isdir(pack_source):
        raise BuildError(f"Pack source does not exist before verification: {pack_source}")

    if not convert_config:
        return

    original_configs = collect_config_cpp_files(original_source_dir, extra_patterns)

    if not original_configs:
        return

    remaining_config_cpp = []
    config_bin_count = 0

    for root, dirs, files in os.walk(pack_source):
        for file in files:
            lower = file.lower()
            if lower == "config.cpp":
                remaining_config_cpp.append(os.path.join(root, file))
            elif lower == "config.bin":
                config_bin_count += 1

    if remaining_config_cpp:
        rel = os.path.relpath(remaining_config_cpp[0], pack_source).replace(os.sep, WIN_SEP)
        raise BuildError(f"Post-conversion verification failed. config.cpp is still in pack source: {rel}")

    if config_bin_count == 0:
        raise BuildError("Post-conversion verification failed. Source had config.cpp but no config.bin exists in pack source.")

    log(f"Post-conversion verification OK: config.bin files found={config_bin_count}, config.cpp packed=0")


def read_packed_pbo_prefix(pbo_path):
    try:
        with open(pbo_path, "rb") as file:
            data = file.read(65536)
    except OSError:
        return ""

    marker = b"prefix\x00"
    index = data.find(marker)

    if index < 0:
        return ""

    start = index + len(marker)
    end = data.find(b"\x00", start)

    if end < 0:
        return ""

    return data[start:end].decode("ascii", errors="ignore")


def verify_packed_pbo(pbo_path, expected_prefix, log):
    if not os.path.isfile(pbo_path):
        raise BuildError(f"Post-pack verification failed. PBO does not exist: {pbo_path}")

    size = os.path.getsize(pbo_path)

    if size <= 0:
        raise BuildError(f"Post-pack verification failed. PBO is empty: {pbo_path}")

    packed_prefix = read_packed_pbo_prefix(pbo_path)

    if expected_prefix and packed_prefix and packed_prefix != expected_prefix:
        raise BuildError(f"Post-pack verification failed. PBO prefix mismatch. Expected '{expected_prefix}', got '{packed_prefix}'.")

    if expected_prefix and not packed_prefix:
        log("WARNING: Post-pack verification could not read the PBO prefix from the header.")
    else:
        log(f"Post-pack verification OK: size={size:,} bytes, prefix={packed_prefix or '<none>'}")


def verify_published_output(pbo_path, sign_pbos, log):
    if not os.path.isfile(pbo_path):
        raise BuildError(f"Published output verification failed. PBO is missing: {pbo_path}")

    if sign_pbos and not find_new_signature_for_pbo(pbo_path):
        raise BuildError(f"Published output verification failed. Signature is missing for: {pbo_path}")

    log("Published output verification OK.")


def parse_tool_output_summary(tool_name, lines):
    summary = {
        "errors": 0,
        "warnings": 0,
        "missing": 0,
        "model": 0,
        "texture": 0,
    }

    for line in lines:
        lower = line.lower()

        if "error" in lower or "cannot" in lower or "failed" in lower or "bad version" in lower:
            summary["errors"] += 1

        if "warning" in lower or "unsupported" in lower:
            summary["warnings"] += 1

        if "missing" in lower or "cannot open" in lower or "cannot load" in lower:
            summary["missing"] += 1

        if "model" in lower or "model.cfg" in lower or "skeleton" in lower:
            summary["model"] += 1

        if "texture" in lower or ".paa" in lower or ".rvmat" in lower:
            summary["texture"] += 1

    return summary


def log_tool_output_summary(tool_name, lines, log):
    summary = parse_tool_output_summary(tool_name, lines)
    log("")
    log(f"{tool_name} output summary:")
    log(f"  Errors / critical lines: {summary['errors']}")
    log(f"  Warnings:                {summary['warnings']}")
    log(f"  Missing references:      {summary['missing']}")
    log(f"  Model-related lines:     {summary['model']}")
    log(f"  Texture/material lines:  {summary['texture']}")
    log("")
    return summary



def collect_wrp_files(addon_source_dir, extra_patterns=None):
    wrp_files = []

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if file.lower().endswith(".wrp") and not should_skip_file(file, extra_patterns):
                wrp_files.append(os.path.join(root, file))

    wrp_files.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return wrp_files


def collect_navmesh_files(addon_source_dir, extra_patterns=None):
    navmesh_root = os.path.join(addon_source_dir, "navmesh")
    navmesh_files = []

    if not os.path.isdir(navmesh_root):
        return navmesh_root, navmesh_files

    # Do not filter here. The preflight caller needs to see excluded navmesh files too,
    # otherwise it cannot warn that navmesh data exists but will not be packed.
    for root, dirs, files in os.walk(navmesh_root):
        for file in files:
            navmesh_files.append(os.path.join(root, file))

    navmesh_files.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return navmesh_root, navmesh_files


def get_explicit_pbo_prefix(addon_source_dir):
    prefix_files = collect_pbo_prefix_files(addon_source_dir)

    if not prefix_files:
        return ""

    raw_prefix = read_raw_prefix_file(prefix_files[0])
    return normalize_reference_path(raw_prefix).strip(WIN_SEP)


def get_detected_pbo_prefix_for_preflight(addon_source_dir):
    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)

    if explicit_prefix:
        return explicit_prefix

    folder_name = os.path.basename(os.path.normpath(addon_source_dir)) or "addon"
    return normalize_reference_path(folder_name).strip(WIN_SEP)


def iter_config_file_contents(config_files, addon_source_dir="", project_root="", include_resolved=False):
    seen = set()
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.IGNORECASE | re.MULTILINE)

    def visit(config_cpp):
        try:
            path = Path(config_cpp).resolve(strict=False)
        except Exception:
            path = Path(config_cpp)

        key = os.path.normcase(str(path))

        if key in seen:
            return

        seen.add(key)

        try:
            content = Path(config_cpp).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""

        yield config_cpp, content

        if not include_resolved or not content:
            return

        for match in include_pattern.finditer(content):
            include_path = resolve_config_include_path(match.group(1).strip(), config_cpp, addon_source_dir, project_root)

            if include_path:
                yield from visit(include_path)

    for config_cpp in config_files:
        yield from visit(config_cpp)


def find_worldname_references(config_files, addon_source_dir="", project_root=""):
    pattern = re.compile(r"\bworldName\s*=\s*[\"']([^\"']+\.wrp)[\"']\s*;", re.IGNORECASE)
    results = []

    for config_cpp, content in iter_config_file_contents(config_files, addon_source_dir, project_root, True):
        if not content:
            continue

        for match in pattern.finditer(content):
            results.append((config_cpp, match.group(1).strip(), get_line_number_from_index(content, match.start(1))))

    return results


def find_terrain_shape_references(config_files, addon_source_dir="", project_root=""):
    # Terrain configs commonly use newRoadsShape = "...roads.shp";
    # The broader regex catches explicit quoted shape references as well.
    shape_regex = re.compile(r"[\"']([^\"']+\.(?:shp|dbf|shx|prj))[\"']", re.IGNORECASE)
    results = []
    seen = set()

    for config_cpp, content in iter_config_file_contents(config_files, addon_source_dir, project_root, True):
        if not content:
            continue

        for match in shape_regex.finditer(content):
            ref = normalize_reference_path(match.group(1).strip())
            key = (os.path.normcase(os.path.abspath(config_cpp)), ref.lower())

            if key in seen:
                continue

            seen.add(key)
            results.append((config_cpp, ref, get_line_number_from_index(content, match.start(1))))

    return results


def check_shape_sidecars(shape_path, addon_source_dir, result, log):
    if not shape_path or not os.path.isfile(shape_path):
        return

    ext = os.path.splitext(shape_path)[1].lower()

    if ext != ".shp":
        return

    base = os.path.splitext(shape_path)[0]

    for sidecar_ext in [".dbf", ".shx"]:
        sidecar = base + sidecar_ext

        if not os.path.isfile(sidecar):
            try:
                rel_shape = os.path.relpath(shape_path, addon_source_dir).replace(os.sep, WIN_SEP)
            except Exception:
                rel_shape = shape_path
            result.warning(log, f"Road shape sidecar is missing for {rel_shape}: {os.path.basename(sidecar)}")


def config_contains_class(config_files, class_name, addon_source_dir="", project_root=""):
    for config_cpp in config_files:
        if config_file_has_class(config_cpp, class_name, addon_source_dir, project_root):
            return True

    return False


def format_byte_size(size):
    try:
        size = float(size)
    except Exception:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0

    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1

    if index == 0:
        return f"{int(size)} {units[index]}"

    return f"{size:.2f} {units[index]}"


def estimate_packed_source_size(source_dir, extra_patterns=None):
    total_size = 0
    total_files = 0

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)

            try:
                total_size += os.path.getsize(full)
                total_files += 1
            except OSError:
                continue

    return total_size, total_files


def collect_packed_size_by_top_folder(source_dir, extra_patterns=None):
    breakdown = {}
    root_files_key = "<root files>"

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        rel_root = os.path.relpath(root, source_dir)
        rel_parts = [] if rel_root == "." else list(Path(rel_root).parts)
        top_key = rel_parts[0] if rel_parts else root_files_key

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)

            try:
                file_size = os.path.getsize(full)
            except OSError:
                continue

            entry = breakdown.setdefault(top_key, {"size": 0, "files": 0})
            entry["size"] += file_size
            entry["files"] += 1

    result = []

    for folder_name, info in breakdown.items():
        result.append((folder_name, info["size"], info["files"]))

    result.sort(key=lambda item: item[1], reverse=True)
    return result


def is_terrain_source_like_path(file_path, addon_source_dir):
    try:
        rel_path = os.path.relpath(file_path, addon_source_dir)
    except Exception:
        rel_path = file_path

    rel_parts = [part.lower() for part in Path(rel_path).parts]
    file_name = os.path.basename(file_path).lower()
    file_stem = os.path.splitext(file_name)[0].lower()
    ext = os.path.splitext(file_name)[1].lower()

    if any(part in TERRAIN_SOURCE_FOLDER_NAMES for part in rel_parts[:-1]):
        return True, "inside terrain source/export folder"

    if ext in TERRAIN_ALWAYS_SOURCE_EXPORT_EXTENSIONS:
        return True, "terrain source/export file type"

    if ext in TERRAIN_SOURCE_IMAGE_EXTENSIONS:
        for keyword in TERRAIN_SOURCE_IMAGE_KEYWORDS:
            if keyword in file_stem:
                return True, "terrain source image name"

    return False, ""


def find_terrain_source_roots(addon_source_dir):
    roots = []

    for root, dirs, files in os.walk(addon_source_dir):
        rel_root = os.path.relpath(root, addon_source_dir)
        depth = 0 if rel_root == "." else len(Path(rel_root).parts)

        # Keep this shallow. Source/export folders deeper in normal asset folders are less likely
        # to be full Terrain Builder source roots and can create noisy warnings.
        if depth > 2:
            dirs[:] = []
            continue

        for directory in dirs:
            if directory.lower() in TERRAIN_SOURCE_FOLDER_NAMES:
                roots.append(os.path.join(root, directory))

    roots.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return roots


def collect_terrain_source_export_files(addon_source_dir, max_examples=20):
    matches = []
    total = 0
    total_size = 0

    for root, dirs, files in os.walk(addon_source_dir):
        for file in files:
            full = os.path.join(root, file)
            is_source_like, reason = is_terrain_source_like_path(full, addon_source_dir)

            if not is_source_like:
                continue

            try:
                file_size = os.path.getsize(full)
            except OSError:
                file_size = 0

            total += 1
            total_size += file_size
            matches.append((full, file_size, reason))

    matches.sort(key=lambda item: (item[1], os.path.relpath(item[0], addon_source_dir).lower()), reverse=True)

    if max_examples and len(matches) > max_examples:
        return matches[:max_examples], total, total_size

    return matches, total, total_size


def find_terrain_layer_dirs(addon_source_dir):
    candidates = [
        os.path.join(addon_source_dir, "data", "layers"),
        os.path.join(addon_source_dir, "layers"),
    ]
    result = []
    seen = set()

    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(candidate):
            result.append(candidate)

    return result


def collect_rvmat_files(folder):
    rvmats = []

    if not os.path.isdir(folder):
        return rvmats

    for root, dirs, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".rvmat"):
                rvmats.append(os.path.join(root, file))

    rvmats.sort(key=lambda path: os.path.relpath(path, folder).lower())
    return rvmats


def preflight_check_terrain_structure(addon_name, addon_source_dir, wrp_files, extra_patterns, result, log):
    data_dir = os.path.join(addon_source_dir, "data")
    world_dir = os.path.join(addon_source_dir, "world")

    result.note(log, "Terrain-style folder layout detected. CE/server mission files are not validated by RaG PBO Builder.")

    if not os.path.isdir(data_dir):
        result.warning(log, f"Terrain/WRP addon has no data folder. This can be valid, but most terrain projects keep runtime textures/materials under data: {addon_name}")

    if not os.path.isdir(world_dir):
        result.warning(log, f"Terrain/WRP addon has no world folder. This can be valid, but most terrain projects keep the WRP and terrain config under world: {addon_name}")

    for wrp_file in wrp_files:
        rel_wrp = os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP)
        first_part = rel_wrp.split(WIN_SEP)[0].lower() if rel_wrp else ""

        if first_part not in {"world", "data"}:
            result.warning(log, f"WRP is outside a typical terrain world/data folder: {rel_wrp}")

    for source_root in find_terrain_source_roots(addon_source_dir):
        rel_source = os.path.relpath(source_root, addon_source_dir).replace(os.sep, WIN_SEP)

        if not path_would_be_excluded(rel_source, extra_patterns):
            result.warning(log, f"Terrain source/export folder is not excluded and may be packed: {rel_source}")

    source_examples, source_total, source_total_size = collect_terrain_source_export_files(addon_source_dir)
    shown = 0
    unexcluded_total = 0
    unexcluded_size = 0

    for source_file, file_size, reason in source_examples:
        rel_source_file = os.path.relpath(source_file, addon_source_dir).replace(os.sep, WIN_SEP)

        if path_would_be_excluded(rel_source_file, extra_patterns):
            continue

        unexcluded_total += 1
        unexcluded_size += file_size
        shown += 1

        if file_size >= TERRAIN_LARGE_SOURCE_FILE_BYTES:
            result.warning(log, f"Large terrain source/export file may be packed ({format_byte_size(file_size)}, {reason}). Check exclude patterns: {rel_source_file}")
        else:
            result.warning(log, f"Terrain source/export file may be packed ({format_byte_size(file_size)}, {reason}). Check exclude patterns: {rel_source_file}")

    if source_total > len(source_examples):
        result.note(log, f"Additional terrain source/export-looking files were found but not listed individually: {source_total - len(source_examples)}")

    if unexcluded_total > shown:
        result.note(log, f"Additional unexcluded terrain source/export-looking files were found but not listed individually: {unexcluded_total - shown}")

    if unexcluded_size > 0 and unexcluded_total > 1:
        result.warning(log, f"Unexcluded terrain source/export-looking files may add {format_byte_size(unexcluded_size)} to the packed PBO: {addon_name}")


def preflight_check_terrain_layers(addon_name, addon_source_dir, project_root, extra_patterns, result, log):
    layer_dirs = find_terrain_layer_dirs(addon_source_dir)
    detected_prefix = get_detected_pbo_prefix_for_preflight(addon_source_dir)

    if not layer_dirs:
        result.note(log, f"Terrain layers folder was not found under data\\layers or layers. This can be valid depending on your terrain workflow: {addon_name}")
        return

    for layer_dir in layer_dirs:
        rel_layer_dir = os.path.relpath(layer_dir, addon_source_dir).replace(os.sep, WIN_SEP)
        rvmat_files = collect_rvmat_files(layer_dir)

        if not rvmat_files:
            result.warning(log, f"Terrain layers folder contains no .rvmat files: {rel_layer_dir}")
            continue

        result.note(log, f"Terrain layers folder detected with {len(rvmat_files)} .rvmat file(s): {rel_layer_dir}")

        for rvmat_file in rvmat_files:
            try:
                content = Path(rvmat_file).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                result.warning(log, f"Could not read terrain layer RVMAT: {rvmat_file} ({e})")
                continue

            rel_rvmat = os.path.relpath(rvmat_file, addon_source_dir).replace(os.sep, WIN_SEP)

            for match in RVMAT_TEXTURE_REGEX.finditer(content):
                ref = normalize_reference_path(match.group(1).strip())
                ref_lower = ref.lower()
                line_number = get_line_number_from_index(content, match.start(1))

                if detected_prefix and not ref_lower.startswith(detected_prefix.lower() + WIN_SEP) and not ref_lower.startswith("dz" + WIN_SEP):
                    source_location = format_source_location(rvmat_file, addon_source_dir, line_number)
                    result.warning(log, f"Terrain layer RVMAT references a texture outside the detected prefix and outside DZ in {source_location}: {ref}")

                # Missing/excluded texture checks are intentionally delegated to the normal
                # RVMAT scan later to avoid duplicate terrain-specific errors.


def find_terrain_2d_map_references(config_files):
    results = []
    seen = set()

    for config_cpp, content in iter_config_file_contents(config_files):
        if not content:
            continue

        for match in TERRAIN_2D_MAP_REFERENCE_REGEX.finditer(content):
            ref = normalize_reference_path(match.group(1).strip())
            key = (os.path.normcase(os.path.abspath(config_cpp)), ref.lower())

            if key in seen:
                continue

            seen.add(key)
            results.append((config_cpp, ref, get_line_number_from_index(content, match.start(1))))

    return results


def preflight_check_terrain_2d_map_config(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log):
    if not config_files:
        return

    map_refs = find_terrain_2d_map_references(config_files)

    if not map_refs:
        result.warning(log, f"Terrain/WRP addon has no obvious 2D map image config reference. This can be valid if the map item/UI is handled elsewhere: {addon_name}")
        return

    result.note(log, f"Detected {len(map_refs)} possible 2D map image reference(s): {addon_name}")

    for config_cpp, map_ref, line_number in map_refs:
        # The normal reference scanner also checks these files. This terrain-specific pass
        # keeps the message contextual and warning-only to avoid blocking unusual map setups.
        resolved, status = resolve_reference_path(map_ref, addon_source_dir, project_root)
        source_location = format_source_location(config_cpp, addon_source_dir, line_number)

        if status == "missing":
            result.warning(log, f"Missing possible 2D map image reference in {source_location}: {map_ref}")
            continue

        if is_path_inside(resolved, addon_source_dir):
            rel_resolved = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)

            if path_would_be_excluded(rel_resolved, extra_patterns):
                result.warning(log, f"Possible 2D map image exists but is excluded from the packed PBO in {source_location}: {map_ref} -> {rel_resolved}")


def preflight_check_terrain_size(addon_name, addon_source_dir, extra_patterns, result, log):
    total_size, total_files = estimate_packed_source_size(addon_source_dir, extra_patterns)
    result.note(log, f"Estimated packed terrain source size before PBO overhead: {format_byte_size(total_size)} / {total_files} file(s)")

    breakdown = collect_packed_size_by_top_folder(addon_source_dir, extra_patterns)

    if breakdown:
        result.note(log, "Terrain size breakdown by top-level folder/file group:")
        shown = 0
        other_size = 0
        other_files = 0

        for folder_name, folder_size, folder_files in breakdown:
            if shown < 8:
                marker = ""

                if folder_name.lower() in TERRAIN_SOURCE_FOLDER_NAMES:
                    marker = " WARNING: source/export folder is being packed"

                result.note(log, f"  {folder_name}: {format_byte_size(folder_size)} / {folder_files} file(s){marker}")
                shown += 1
            else:
                other_size += folder_size
                other_files += folder_files

        if other_files:
            result.note(log, f"  <other>: {format_byte_size(other_size)} / {other_files} file(s)")

        for folder_name, folder_size, folder_files in breakdown:
            if folder_name.lower() in TERRAIN_SOURCE_FOLDER_NAMES and folder_size > 0:
                result.warning(log, f"Terrain source/export top-level folder is included in estimated packed files: {folder_name} ({format_byte_size(folder_size)} / {folder_files} file(s))")

    if total_size >= TERRAIN_SIZE_HIGH_WARNING_BYTES:
        result.warning(log, f"Terrain addon is very large ({format_byte_size(total_size)}). Check that source/export folders are excluded: {addon_name}")
    elif total_size >= TERRAIN_SIZE_WARNING_BYTES:
        result.warning(log, f"Terrain addon is large ({format_byte_size(total_size)}). Check that only runtime files are being packed: {addon_name}")


def preflight_check_terrain_wrp(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log, checks):
    wrp_files = collect_wrp_files(addon_source_dir, extra_patterns)

    if not wrp_files:
        return

    result.checked_terrain += 1
    result.note(log, f"Terrain/WRP addon detected: {addon_name}")
    result.note(log, "Terrain PBO detected. Server mission/world selection setup is outside the PBO and is not validated here.")

    wrp_rel_paths = [os.path.relpath(path, addon_source_dir).replace(os.sep, WIN_SEP) for path in wrp_files]

    if len(wrp_files) > 1:
        result.warning(log, f"Multiple WRP files found in terrain addon {addon_name}: {', '.join(wrp_rel_paths)}")
    else:
        result.note(log, f"Detected WRP: {wrp_rel_paths[0]}")

    if checks.get("terrain_structure", True):
        preflight_check_terrain_structure(addon_name, addon_source_dir, wrp_files, extra_patterns, result, log)
    else:
        result.note(log, "Terrain folder/source structure check disabled.")

    if checks.get("terrain_size", True):
        preflight_check_terrain_size(addon_name, addon_source_dir, extra_patterns, result, log)
    else:
        result.note(log, "Terrain size/source warning check disabled.")

    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
    detected_prefix = get_detected_pbo_prefix_for_preflight(addon_source_dir)

    if checks.get("terrain_cfgworlds", True):
        if not explicit_prefix:
            result.warning(log, f"Terrain/WRP addon has no explicit PBO prefix file. For maps, a $PBOPREFIX$ file is strongly recommended: {addon_name}")

        if not config_files:
            result.error(log, f"WRP found but no config.cpp exists in terrain addon: {addon_name}")
        else:
            has_cfgworlds = config_contains_class(config_files, "CfgWorlds", addon_source_dir, project_root)
            has_cfgworldlist = config_contains_class(config_files, "CfgWorldList", addon_source_dir, project_root) or config_contains_class(config_files, "CfgWorldsList", addon_source_dir, project_root)

            if not has_cfgworlds:
                result.error(log, f"WRP found but no CfgWorlds class found in addon configs: {addon_name}")

            if not has_cfgworldlist:
                result.warning(log, f"WRP found but no CfgWorldList class found in addon configs: {addon_name}")

            worldname_refs = find_worldname_references(config_files, addon_source_dir, project_root)

            if not worldname_refs:
                result.warning(log, f"WRP found but no worldName .wrp path was found in addon configs: {addon_name}")
            else:
                if len(wrp_files) > 1 and len(worldname_refs) == 1:
                    result.warning(log, f"Multiple WRP files were found, but only one worldName entry was detected. Remove old/test WRP files or verify the intended terrain WRP: {addon_name}")

                if len(worldname_refs) > 1:
                    result.warning(log, f"Multiple worldName .wrp entries were detected in terrain configs. Verify only the intended terrain world is active: {addon_name}")

                detected_wrp_keys = {os.path.normcase(os.path.abspath(path)) for path in wrp_files}
                resolved_worldname_keys = set()

                for config_cpp, world_ref, line_number in worldname_refs:
                    source_location = format_source_location(config_cpp, addon_source_dir, line_number)
                    normalized_world_ref = normalize_reference_path(world_ref)
                    report_reference_status(normalized_world_ref, config_cpp, addon_source_dir, project_root, extra_patterns, result, log, "error", "worldName WRP", line_number)
                    resolved, status = resolve_reference_path(normalized_world_ref, addon_source_dir, project_root)

                    if status == "ok":
                        resolved_key = os.path.normcase(os.path.abspath(resolved))
                        resolved_worldname_keys.add(resolved_key)

                        if resolved_key not in detected_wrp_keys:
                            try:
                                resolved_rel = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)
                            except Exception:
                                resolved_rel = resolved
                            result.warning(log, f"worldName in {source_location} points to a WRP that differs from detected addon WRP files: {resolved_rel}")

                    if detected_prefix and not normalized_world_ref.lower().startswith(detected_prefix.lower() + WIN_SEP):
                        result.warning(log, f"worldName path does not start with detected PBO prefix in {source_location}: prefix '{detected_prefix}', worldName '{normalized_world_ref}'")

                unused_wrp_paths = []

                for wrp_file in wrp_files:
                    wrp_key = os.path.normcase(os.path.abspath(wrp_file))

                    if wrp_key not in resolved_worldname_keys:
                        unused_wrp_paths.append(os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP))

                if resolved_worldname_keys and unused_wrp_paths:
                    result.warning(log, f"WRP file(s) are present but not referenced by worldName. Check for stale terrain exports: {', '.join(unused_wrp_paths)}")

        if checks.get("terrain_2d_map", False):
            preflight_check_terrain_2d_map_config(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log)
        else:
            result.note(log, "2D map image config check disabled.")

    if checks.get("terrain_layers", True):
        preflight_check_terrain_layers(addon_name, addon_source_dir, project_root, extra_patterns, result, log)
    else:
        result.note(log, "Terrain layer/RVMAT check disabled.")

    if checks.get("terrain_navmesh", False):
        navmesh_root, navmesh_files = collect_navmesh_files(addon_source_dir, extra_patterns)

        if not os.path.isdir(navmesh_root):
            result.warning(log, f"Terrain/WRP addon has no navmesh folder. This can be valid for early tests, but released maps usually need navmesh data: {addon_name}")
        else:
            result.note(log, f"Navmesh folder detected: {os.path.relpath(navmesh_root, addon_source_dir).replace(os.sep, WIN_SEP)}")

            if not navmesh_files:
                result.warning(log, f"Navmesh folder exists but contains no files: {addon_name}")

            excluded_navmesh_count = 0
            packed_navmesh_count = 0

            for navmesh_file in navmesh_files:
                rel_navmesh = os.path.relpath(navmesh_file, addon_source_dir).replace(os.sep, WIN_SEP)

                if path_would_be_excluded(rel_navmesh, extra_patterns):
                    excluded_navmesh_count += 1
                    result.warning(log, f"Navmesh file exists but is excluded from the packed PBO: {rel_navmesh}")
                else:
                    packed_navmesh_count += 1

            if navmesh_files and packed_navmesh_count == 0:
                result.warning(log, f"Navmesh folder contains files, but all navmesh files appear to be excluded from the packed PBO: {addon_name}")

    if checks.get("terrain_road_shapes", True):
        shape_refs = find_terrain_shape_references(config_files, addon_source_dir, project_root)

        if shape_refs:
            for config_cpp, shape_ref, line_number in shape_refs:
                report_reference_status(shape_ref, config_cpp, addon_source_dir, project_root, extra_patterns, result, log, "error", "terrain road/shape reference", line_number)
                resolved, status = resolve_reference_path(shape_ref, addon_source_dir, project_root)

                if status == "ok":
                    check_shape_sidecars(resolved, addon_source_dir, result, log)
        else:
            result.note(log, f"No terrain road/shape references found in addon configs: {addon_name}")

    if checks.get("wrp_internal", False):
        for wrp_file in wrp_files:
            preflight_scan_wrp_internal_references(wrp_file, addon_source_dir, project_root, extra_patterns, result, log)
    else:
        result.note(log, "WRP internal reference scan disabled.")


def preflight_scan_wrp_internal_references(wrp_file, addon_source_dir, project_root, extra_patterns, result, log):
    rel_file = os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP)

    try:
        with open(wrp_file, "rb") as file:
            data = file.read()
    except Exception as e:
        result.warning(log, f"Could not read WRP for internal reference scan: {rel_file} ({e})")
        return

    result.checked_files += 1
    seen = set()
    found = 0

    for match in TERRAIN_WRP_INTERNAL_REFERENCE_REGEX.finditer(data):
        try:
            ref = normalize_reference_path(match.group(1).decode("ascii", errors="ignore").strip())
        except Exception:
            continue

        key = ref.lower()

        if not ref or key in seen or len(ref) < 5:
            continue

        seen.add(key)
        found += 1
        report_reference_status(ref, wrp_file, addon_source_dir, project_root, extra_patterns, result, log, "warning", "possible internal WRP reference")

    if found:
        log(f"WRP internal scan checked {found} possible reference(s): {rel_file}")

def get_preflight_check_settings(settings):
    return {
        "required_addons_hints": bool(settings.get("preflight_check_required_addons_hints", True)),
        "texture_freshness": bool(settings.get("preflight_check_texture_freshness", True)),
        "risky_paths": bool(settings.get("preflight_check_risky_paths", True)),
        "case_conflicts": bool(settings.get("preflight_check_case_conflicts", True)),
        "p3d_internal": bool(settings.get("preflight_check_p3d_internal", True)),
        "terrain_cfgworlds": bool(settings.get("preflight_check_terrain_cfgworlds", True)),
        "terrain_navmesh": bool(settings.get("preflight_check_terrain_navmesh", False)),
        "terrain_road_shapes": bool(settings.get("preflight_check_terrain_road_shapes", True)),
        "terrain_structure": bool(settings.get("preflight_check_terrain_structure", True)),
        "terrain_layers": bool(settings.get("preflight_check_terrain_layers", True)),
        "terrain_2d_map": bool(settings.get("preflight_check_terrain_2d_map", False)),
        "terrain_size": bool(settings.get("preflight_check_terrain_size", True)),
        "wrp_internal": bool(settings.get("preflight_check_wrp_internal", False)),
    }


def get_preflight_report_paths(log_file):
    if not log_file:
        return "", ""

    base = Path(log_file)
    return str(base.with_name(base.stem + "_preflight_report.txt")), str(base.with_name(base.stem + "_preflight_report.json"))


def export_preflight_report(settings, targets, result, elapsed, log):
    txt_path, json_path = get_preflight_report_paths(settings.get("log_file", ""))

    if not txt_path or not json_path:
        return

    enabled_checks = get_preflight_check_settings(settings)
    report_data = {
        "app": APP_TITLE,
        "version": APP_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "targets": [
            {
                "name": name,
                "path": path,
            }
            for name, path in targets
        ],
        "enabled_checks": enabled_checks,
        "summary": {
            "addons": len(targets),
            "checked_files": result.checked_files,
            "checked_references": result.checked_references,
            "checked_configs": result.checked_configs,
            "script_modules": result.checked_script_modules,
            "checked_paths": result.checked_paths,
            "checked_terrain": result.checked_terrain,
            "errors": result.errors,
            "warnings": result.warnings,
            "info": result.info,
            "time": format_duration(elapsed),
        },
        "events": result.events,
    }

    lines = []
    lines.append(f"{APP_TITLE} Preflight Report")
    lines.append(f"Version: {APP_VERSION}")
    lines.append(f"Created: {report_data['created']}")
    lines.append("")
    lines.append("Targets:")
    for target in report_data["targets"]:
        lines.append(f"- {target['name']}: {target['path']}")
    lines.append("")
    lines.append("Enabled checks:")
    for check_name, enabled in enabled_checks.items():
        lines.append(f"- {check_name}: {'enabled' if enabled else 'disabled'}")
    lines.append("")
    lines.append("Summary:")
    for key, value in report_data["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("Issues / notes:")
    if result.events:
        for event in result.events:
            lines.append(f"[{event['severity']}] {event['message']}")
    else:
        lines.append("No errors, warnings, or info notes were recorded.")
    lines.append("")

    try:
        Path(txt_path).write_text(chr(10).join(lines), encoding="utf-8")
        Path(json_path).write_text(json.dumps(report_data, indent=4), encoding="utf-8")
        result.report_txt = txt_path
        result.report_json = json_path
        log(f"Preflight report saved: {txt_path}")
        log(f"Preflight report JSON saved: {json_path}")
    except Exception as e:
        result.warning(log, f"Could not export preflight report: {e}")


def run_preflight_for_targets(settings, targets, log, progress_callback=None):
    start = time.time()
    result = PreflightResult()
    project_root = settings.get("project_root", DEFAULT_PROJECT_ROOT)
    extra_patterns = parse_exclude_patterns(settings.get("exclude_patterns", ""))
    preflight_checks = get_preflight_check_settings(settings)

    log("")
    log("=" * 80)
    log("DayZ Preflight Check")
    log("=" * 80)

    for index, (addon_name, addon_source_dir) in enumerate(targets, start=1):
        if progress_callback:
            progress_callback(index - 1, len(targets))

        log("")
        log(f"Checking addon {index}/{len(targets)}: {addon_name}")

        preflight_check_prefix(addon_name, addon_source_dir, result, log)

        if preflight_checks["case_conflicts"]:
            preflight_scan_case_conflicts(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Case-only path conflict check disabled.")

        if preflight_checks["risky_paths"]:
            preflight_scan_invalid_paths(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Risky filename/path check disabled.")

        if preflight_checks["texture_freshness"]:
            preflight_scan_texture_freshness(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Texture freshness check disabled.")

        configs = collect_config_cpp_files(addon_source_dir, extra_patterns)
        root_config_cpp = os.path.normcase(os.path.abspath(os.path.join(addon_source_dir, "config.cpp")))

        if configs:
            log(f"Found {len(configs)} config.cpp file(s).")

            for config_cpp in configs:
                preflight_check_config_cpp(config_cpp, settings.get("cfgconvert_exe", ""), settings.get("temp_dir", DEFAULT_TEMP_DIR), addon_name, result, log)
                preflight_check_cfgpatches(config_cpp, addon_source_dir, result, log, preflight_checks["required_addons_hints"])

            # DayZ only needs one CfgMods class for script module registration.
            # Some projects keep it in the root config.cpp, some include it from another config.cpp,
            # and others keep it in a nested scripts\config.cpp.
            # Do not warn per nested config; validate the config that actually contains or includes CfgMods,
            # or warn once if none exists anywhere in the addon configs.
            cfgmods_config_cpp = find_config_cpp_with_class(configs, "CfgMods", addon_source_dir, project_root)
            cfgmods_check_cpp = cfgmods_config_cpp or os.path.join(addon_source_dir, "config.cpp")

            if not os.path.isfile(cfgmods_check_cpp):
                cfgmods_check_cpp = configs[0]

            preflight_check_cfgmods(cfgmods_check_cpp, addon_name, addon_source_dir, project_root, result, log)
        else:
            result.warning(log, f"No config.cpp found in addon source: {addon_source_dir}")

        preflight_check_terrain_wrp(
            addon_name,
            addon_source_dir,
            configs,
            project_root,
            extra_patterns,
            result,
            log,
            preflight_checks,
        )

        if not preflight_checks["p3d_internal"]:
            result.note(log, "P3D internal reference scan disabled.")

        for root, dirs, files in os.walk(addon_source_dir):
            dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

            for file in files:
                full = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()

                if ext in PREFLIGHT_TEXT_EXTENSIONS:
                    preflight_scan_references(full, addon_source_dir, project_root, extra_patterns, result, log)
                elif ext == ".p3d" and preflight_checks["p3d_internal"]:
                    preflight_scan_p3d_internal_references(full, addon_source_dir, project_root, extra_patterns, result, log)

    if progress_callback:
        progress_callback(len(targets), len(targets))

    elapsed = time.time() - start
    log("")
    log("=" * 80)
    log("Preflight summary")
    log("=" * 80)
    log(f"Addons:             {len(targets)}")
    log(f"Scanned files:      {result.checked_files}")
    log(f"Checked references: {result.checked_references}")
    log(f"Checked configs:    {result.checked_configs}")
    log(f"Script modules:     {result.checked_script_modules}")
    log(f"Checked paths:      {result.checked_paths}")
    log(f"Terrain checks:     {result.checked_terrain}")
    log(f"Errors:             {result.errors}")
    log(f"Warnings:           {result.warnings}")
    log(f"Info:               {result.info}")
    log(f"Time:               {format_duration(elapsed)}")
    log("=" * 80)

    export_preflight_report(settings, targets, result, elapsed, log)

    return result


def run_dayz_binarize(source_dir, binarized_output_dir, binarize_exe, project_root, temp_dir, max_processes, exclude_file, log, addon_name=""):
    if os.path.exists(binarized_output_dir):
        shutil.rmtree(binarized_output_dir)
    os.makedirs(binarized_output_dir, exist_ok=True)
    project_root_arg = normalize_project_root_arg(project_root)
    working_dir = normalize_working_dir(project_root)
    binpath = str(Path(binarize_exe).parent)
    source_name = addon_name or os.path.basename(os.path.normpath(source_dir)) or "addon"
    texture_temp_dir = os.path.join(temp_dir, "addons", get_safe_temp_name(source_name), "textures")
    if os.path.isdir(texture_temp_dir):
        shutil.rmtree(texture_temp_dir)
    os.makedirs(texture_temp_dir, exist_ok=True)
    cmd = [binarize_exe, "-targetBonesInterval=56", f"-maxProcesses={max_processes}", "-always", "-silent", f"-addon={project_root_arg}", f"-textures={texture_temp_dir}", f"-binpath={binpath}"]
    if exclude_file:
        cmd.append(f"-exclude={exclude_file}")
    cmd.extend([source_dir, binarized_output_dir])
    log("")
    log("Binarizing P3D files:")
    log(f"  Source:       {source_dir}")
    log(f"  Output:       {binarized_output_dir}")
    log(f"  Project root: {project_root_arg}")
    log(f"  Texture temp: {texture_temp_dir}")
    log("")
    result = subprocess.run(cmd, cwd=working_dir if os.path.isdir(working_dir) else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
    output_lines = result.stdout.splitlines() if result.stdout else []
    if output_lines:
        for line in output_lines:
            log(line)
    else:
        log("Binarize returned no output.")
    log_tool_output_summary("Binarize", output_lines, log)
    if result.returncode != 0:
        raise BuildError(f"Binarize failed with exit code {result.returncode}: {source_dir}")
    return parse_tool_output_summary("Binarize", output_lines)

def run_cfgconvert_to_bin(staging_dir, cfgconvert_exe, log, extra_patterns=None):
    if not os.path.isdir(staging_dir):
        raise BuildError(f"Staging folder does not exist: {staging_dir}")
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise BuildError("CfgConvert.exe not found. Select the DayZ Tools CfgConvert.exe path.")
    config_files = []
    for root, dirs, files in os.walk(staging_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower() == "config.cpp":
                config_files.append(os.path.join(root, file))
    if not config_files:
        log("No included config.cpp found. Skipping CPP to BIN.")
        return
    config_files.sort(key=lambda path: os.path.relpath(path, staging_dir).lower())
    log("")
    log(f"Converting {len(config_files)} config.cpp file(s) to config.bin:")
    for config_cpp in config_files:
        config_dir = os.path.dirname(config_cpp)
        config_bin = os.path.join(config_dir, "config.bin")
        rel_config = os.path.relpath(config_cpp, staging_dir).replace(os.sep, WIN_SEP)
        rel_bin = os.path.relpath(config_bin, staging_dir).replace(os.sep, WIN_SEP)
        if os.path.isfile(config_bin):
            os.remove(config_bin)
        cmd = [cfgconvert_exe, "-bin", "-dst", config_bin, config_cpp]
        log("")
        log(f"Converting: {rel_config} -> {rel_bin}")
        result = subprocess.run(cmd, cwd=config_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
        if result.stdout:
            for line in result.stdout.splitlines():
                log(line)
        if result.returncode != 0 or not os.path.isfile(config_bin):
            raise BuildError(f"CfgConvert failed with exit code {result.returncode}: {config_cpp}")
        os.remove(config_cpp)
        log(f"Removed source config.cpp from staging: {rel_config}")


def build_all(settings, log, progress_callback):
    start = time.time()
    source_root = os.path.normpath(settings["source_root"])
    output_root = os.path.normpath(settings["output_root_dir"])
    output_addons_dir = os.path.join(output_root, "Addons")
    output_keys_dir = os.path.join(output_root, "Keys")
    temp_root = os.path.normpath(settings["temp_dir"])
    if not os.path.isdir(source_root):
        raise BuildError(f"Project Source is not a directory: {source_root}")
    os.makedirs(output_addons_dir, exist_ok=True)
    os.makedirs(output_keys_dir, exist_ok=True)
    ensure_builder_temp_root(temp_root, log, source_root, output_root)

    use_binarize = settings["use_binarize"]
    convert_config = settings["convert_config"]
    sign_pbos = settings["sign_pbos"]
    binarize_exe = settings["binarize_exe"]
    cfgconvert_exe = settings["cfgconvert_exe"]
    dssignfile_exe = settings["dssignfile_exe"]
    private_key = settings["private_key"]
    exclude_patterns = settings["exclude_patterns"]
    exclude_pattern_list = parse_exclude_patterns(exclude_patterns)
    project_root = settings["project_root"]
    pbo_name = settings["pbo_name"]
    max_processes = settings["max_processes"]
    selected_addons = set(settings.get("selected_addons", []))
    force_rebuild = bool(settings.get("force_rebuild", False))
    preflight_before_build = bool(settings.get("preflight_before_build", False))
    exclude_file = ""

    log(f"Build Output:   {output_root}")
    log(f"Output Addons: {output_addons_dir}")
    log(f"Output Keys:   {output_keys_dir}")
    log(f"Force rebuild {'enabled' if force_rebuild else 'disabled'}. Temp: {temp_root}")
    log("Content-safe checks enabled internally. File contents are hashed for cache/staging checks.")
    log("Using per-build SHA1 cache for repeated file fingerprints. Source hashes are not persisted across runs.")
    log(f"Detected total logical CPU threads: {os.cpu_count() or 'unknown'}")
    log(f"Detected available logical threads: {get_available_logical_threads()}")
    log(f"Configured Binarize max processes: {max_processes}")

    if use_binarize:
        if not binarize_exe or not os.path.isfile(binarize_exe):
            raise BuildError("binarize.exe not found. Select the DayZ Tools binarize.exe path.")
        log(f"Using binarize.exe: {binarize_exe}")
        exclude_file = create_temp_exclude_file(temp_root, exclude_patterns, log)
        if not exclude_file:
            log("No exclude file will be passed to Binarize. Binarize uses the filtered staging folder instead.")
    if convert_config:
        if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
            raise BuildError("CfgConvert.exe not found. Select the DayZ Tools CfgConvert.exe path.")
        log(f"Using CfgConvert.exe: {cfgconvert_exe}")
    if sign_pbos:
        if not dssignfile_exe or not os.path.isfile(dssignfile_exe):
            raise BuildError("DSSignFile.exe not found. Select the DayZ Tools DSSignFile.exe path.")
        if not private_key or not os.path.isfile(private_key):
            raise BuildError("Private key not found. Select your .biprivatekey file.")
        log(f"Using DSSignFile.exe: {dssignfile_exe}")
        log(f"Using private key: {os.path.basename(private_key)}")

    all_targets = detect_addon_targets(source_root, output_addons_dir)
    targets = [(name, path) for name, path in all_targets if name in selected_addons] if selected_addons else []
    if not targets:
        raise BuildError("No addon targets selected.")
    log(f"Found {len(all_targets)} addon target(s). Selected {len(targets)} for build.")

    if preflight_before_build:
        log("Preflight before build enabled. Running checks before packing.")
        preflight = run_preflight_for_targets(settings, targets, log, progress_callback)
        if preflight.errors > 0:
            raise BuildError(f"Preflight failed with {preflight.errors} error(s). Build aborted.")
        log(f"Preflight completed with {preflight.warnings} warning(s). Continuing build." if preflight.warnings else "Preflight completed without errors or warnings. Continuing build.")

    cache = load_build_cache()
    build_hash_cache = {}
    cache_key_root = os.path.abspath(source_root).lower()
    source_cache = cache.setdefault(cache_key_root, {})
    summary = {"built": 0, "skipped": 0, "signed": 0, "failed": 0, "keys_copied": 0, "p3d_fallbacks": 0, "targets": len(targets), "log_file": settings.get("log_file", "")}
    jobs = []

    if force_rebuild:
        log("Force rebuild enabled. Cache will be ignored for selected addons.")

    for index, (folder_name, folder_path) in enumerate(targets, start=1):
        progress_callback(index - 1, len(targets))
        log("")
        log("=" * 80)
        log(f"Preparing addon {index}/{len(targets)}: {folder_name}")
        log("=" * 80)
        pbo_base_name = get_pbo_base_name(folder_name, pbo_name, len(targets))
        output_pbo = os.path.join(output_addons_dir, pbo_base_name + ".pbo")
        prefix = get_pbo_prefix(pbo_base_name, folder_path)
        state_hash = compute_addon_state_hash(folder_path, prefix, settings, exclude_pattern_list, build_hash_cache)
        can_skip = (not force_rebuild and source_cache.get(folder_name, {}).get("hash") == state_hash and os.path.isfile(output_pbo) and (not sign_pbos or find_new_signature_for_pbo(output_pbo)))
        if can_skip:
            log(f"Skipping {folder_name} - no changes detected.")
            summary["skipped"] += 1
            continue
        addon_temp_root = get_addon_temp_root(temp_root, folder_name)
        if force_rebuild:
            for subfolder in ["staging", "binarized", "textures", "configs"]:
                path = os.path.join(addon_temp_root, subfolder)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    log(f"Force rebuild: removed selected addon temp folder only: {path}")
        folder_has_p3d = use_binarize and has_p3d_files(folder_path, exclude_pattern_list)
        needs_staging = convert_config or folder_has_p3d
        pack_source = folder_path
        staging_dir = ""
        binarized_dir = ""
        if needs_staging:
            staging_dir = os.path.join(addon_temp_root, "staging")
            log("Copying source to staging folder...")
            copy_source_to_staging(folder_path, staging_dir, exclude_pattern_list, log, True)
            pack_source = staging_dir
        if folder_has_p3d:
            binarized_dir = os.path.join(addon_temp_root, "binarized")
        elif use_binarize:
            log("No P3D files found. Skipping P3D binarize for this addon.")
        output_work_dir = create_output_work_dir(output_pbo, folder_name)
        jobs.append({"folder_name": folder_name, "folder_path": folder_path, "output_pbo": output_pbo, "temp_output_pbo": os.path.join(output_work_dir, os.path.basename(output_pbo)), "output_work_dir": output_work_dir, "prefix": prefix, "pack_source": pack_source, "folder_has_p3d": folder_has_p3d, "staging_dir": staging_dir, "binarized_dir": binarized_dir, "binarize_source": staging_dir if folder_has_p3d and staging_dir else folder_path, "state_hash": state_hash})

    for build_index, job in enumerate(jobs, start=1):
        progress_callback(build_index - 1, len(jobs))
        log("")
        log("=" * 80)
        log(f"Packing addon {build_index}/{len(jobs)}: {job['folder_name']}")
        log("=" * 80)
        try:
            if use_binarize and job["folder_has_p3d"]:
                log("Running Binarize against filtered staging folder...")
                run_dayz_binarize(job["binarize_source"], job["binarized_dir"], binarize_exe, project_root, temp_root, max_processes, exclude_file, log, job["folder_name"])
                log("Overlaying binarized files onto staging folder...")
                overlay_tree(job["binarized_dir"], job["staging_dir"])
                fallback_count = ensure_p3d_files_in_staging(job["folder_path"], job["staging_dir"], log, exclude_pattern_list)
                summary["p3d_fallbacks"] += fallback_count
            if convert_config:
                ensure_config_cpp_files_in_staging(job["folder_path"], job["pack_source"], log, exclude_pattern_list)
                run_cfgconvert_to_bin(job["pack_source"], cfgconvert_exe, log, exclude_pattern_list)
            verify_pack_source_before_packing(job["folder_path"], job["pack_source"], convert_config, log, exclude_pattern_list)
            log(f"PBO Name:   {os.path.basename(job['output_pbo'])}")
            log(f"PBO prefix: {job['prefix']}")
            pack_pbo(job["pack_source"], job["temp_output_pbo"], job["prefix"], log, exclude_pattern_list)
            verify_packed_pbo(job["temp_output_pbo"], job["prefix"], log)
            if sign_pbos:
                wait_for_file_ready(job["temp_output_pbo"], log)
                run_dssignfile(dssignfile_exe, private_key, job["temp_output_pbo"], log)
                summary["signed"] += 1
            replace_output_artifacts(job["temp_output_pbo"], job["output_pbo"], sign_pbos, log)
            verify_published_output(job["output_pbo"], sign_pbos, log)
            cleanup_output_work_dir(job["output_work_dir"], log)
            summary["built"] += 1
            if sign_pbos:
                if copy_bikey_to_keys(private_key, output_keys_dir, log):
                    summary["keys_copied"] += 1
            source_cache[job["folder_name"]] = {"hash": job["state_hash"], "pbo": job["output_pbo"], "updated": datetime.now().isoformat(timespec="seconds")}
            save_build_cache(cache)
        except Exception:
            summary["failed"] += 1
            raise

    progress_callback(len(targets), len(targets))
    save_build_cache(cache)
    elapsed = time.time() - start
    log("")
    log("=" * 80)
    log("Build summary")
    log("=" * 80)
    log(f"Targets:       {summary['targets']}")
    log(f"Built:         {summary['built']}")
    log(f"Skipped:       {summary['skipped']}")
    log(f"Signed:        {summary['signed']}")
    log(f"Keys copied:   {summary['keys_copied']}")
    log(f"P3D fallbacks: {summary['p3d_fallbacks']}")
    log(f"Failed:        {summary['failed']}")
    log(f"Time:          {format_duration(elapsed)}")
    if settings.get("log_file"):
        log(f"Log:         {settings.get('log_file')}")
    log("=" * 80)
    log("")
    log("Build finished.")
    return summary


class PboInspectorWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.archive = None
        self.entries = []
        self.pbo_path_var = tk.StringVar(value="")
        self.output_dir_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="No PBO loaded")

        self.title("PBO Inspector / Extractor")
        self.geometry("980x720")
        self.minsize(820, 620)
        self.configure(bg=GRAPHITE_BG)
        self.transient(parent)
        self._apply_tree_style()
        self._build_ui()

    def _apply_tree_style(self):
        style = ttk.Style(self)
        style.configure(
            "Pbo.Treeview",
            background=GRAPHITE_FIELD,
            fieldbackground=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            rowheight=24,
        )
        style.configure(
            "Pbo.Treeview.Heading",
            background=GRAPHITE_CARD_SOFT,
            foreground=GRAPHITE_TEXT,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Pbo.Treeview", background=[("selected", GRAPHITE_ACCENT_DARK)], foreground=[("selected", "#ffffff")])

    def _make_button(self, parent, text, command, primary=False):
        if primary:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_ACCENT_DARK, "#ffffff", GRAPHITE_ACCENT, GRAPHITE_ACCENT_HOVER, "bold"
        else:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_CARD_SOFT, GRAPHITE_TEXT, GRAPHITE_BORDER, GRAPHITE_BORDER, "normal"

        button = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active_bg, activeforeground="#ffffff" if fg == "#ffffff" else GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=12, pady=7, font=("Segoe UI", 9, weight), cursor="hand2")
        button.pack(side="left", padx=(0, 8))
        self.parent._attach_button_hover(button, bg, hover_bg, active_bg)
        return button

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="PBO Inspector / Extractor", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 10))

        path_frame = ttk.LabelFrame(container, text="Archive", padding=12)
        path_frame.pack(fill="x", pady=(0, 10))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="PBO file", style="FieldName.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        pbo_entry = ttk.Entry(path_frame, textvariable=self.pbo_path_var)
        pbo_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(path_frame, text="Browse", command=self.choose_pbo).grid(row=0, column=2, sticky="e", pady=4)

        ttk.Label(path_frame, text="Extract to", style="FieldName.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        output_entry = ttk.Entry(path_frame, textvariable=self.output_dir_var)
        output_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(path_frame, text="Browse", command=self.choose_output_dir).grid(row=1, column=2, sticky="e", pady=4)

        action_frame = ttk.Frame(container)
        action_frame.pack(fill="x", pady=(0, 10))
        self.inspect_button = self._make_button(action_frame, "Inspect", self.inspect_pbo, primary=True)
        self.extract_selected_button = self._make_button(action_frame, "Extract selected", self.extract_selected)
        self.extract_all_button = self._make_button(action_frame, "Extract all", self.extract_all)
        self.open_output_button = self._make_button(action_frame, "Open output", self.open_output_folder)
        ttk.Label(action_frame, textvariable=self.summary_var, foreground=GRAPHITE_MUTED).pack(side="left", padx=(6, 0))

        content_frame = ttk.LabelFrame(container, text="Contents", padding=10)
        content_frame.pack(fill="both", expand=True, pady=(0, 10))
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)

        columns = ("path", "size", "method", "timestamp")
        self.tree = ttk.Treeview(content_frame, columns=columns, show="headings", selectmode="extended", style="Pbo.Treeview")
        self.tree.heading("path", text="Path")
        self.tree.heading("size", text="Size")
        self.tree.heading("method", text="Method")
        self.tree.heading("timestamp", text="Timestamp")
        self.tree.column("path", width=520, anchor="w")
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("method", width=120, anchor="w")
        self.tree.column("timestamp", width=150, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        tree_scroll = ttk.Scrollbar(content_frame, command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        log_frame = ttk.LabelFrame(container, text="Inspector log", padding=10)
        log_frame.pack(fill="x")
        self.log_text = tk.Text(log_frame, height=6, wrap="word", bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def log(self, message):
        self.log_text.insert("end", str(message) + chr(10))
        self.log_text.see("end")

    def get_default_output_dir(self):
        pbo_path = self.pbo_path_var.get().strip()

        if not pbo_path:
            return ""

        pbo = Path(pbo_path)
        return str(pbo.with_name(pbo.stem + "_extracted"))

    def choose_pbo(self):
        path = filedialog.askopenfilename(title="Select PBO file", initialdir=get_initial_dir_from_value(self.pbo_path_var.get(), self.parent.output_root_var.get()), filetypes=[("PBO files", "*.pbo"), ("All files", "*.*")], parent=self)

        if not path:
            return

        self.pbo_path_var.set(path)

        if not self.output_dir_var.get().strip():
            self.output_dir_var.set(self.get_default_output_dir())

        self.inspect_pbo()

    def choose_output_dir(self):
        path = filedialog.askdirectory(title="Select extract output folder", initialdir=get_initial_dir_from_value(self.output_dir_var.get(), self.get_default_output_dir()), parent=self)

        if path:
            self.output_dir_var.set(path)

    def inspect_pbo(self):
        pbo_path = self.pbo_path_var.get().strip()

        try:
            archive = read_pbo_archive(pbo_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self)
            return

        self.archive = archive
        self.entries = list(archive["entries"])
        self.tree.delete(*self.tree.get_children())

        total_bytes = sum(entry.data_size for entry in self.entries)
        unsupported = 0

        for index, entry in enumerate(self.entries):
            if entry.packing_method != PBO_STORED_METHOD:
                unsupported += 1

            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(entry.name, format_byte_size(entry.data_size), get_pbo_method_label(entry.packing_method), format_pbo_timestamp(entry.timestamp)),
            )

        prefix = archive["properties"].get("prefix", "")
        footer = archive.get("footer_size", 0)
        unsupported_text = f", unsupported entries: {unsupported}" if unsupported else ""
        self.summary_var.set(f"{len(self.entries)} file(s), {format_byte_size(total_bytes)}, prefix: {prefix or '<none>'}, footer: {format_byte_size(footer)}{unsupported_text}")
        self.log(f"Loaded: {pbo_path}")
        self.log(f"Files: {len(self.entries)}, payload: {format_byte_size(total_bytes)}, prefix: {prefix or '<none>'}")

        if unsupported:
            self.log(f"WARNING: {unsupported} compressed or unsupported entry/entries can be listed but not extracted.")

    def confirm_output_folder(self, output_dir):
        if os.path.isdir(output_dir):
            try:
                has_contents = any(Path(output_dir).iterdir())
            except Exception:
                has_contents = False

            if has_contents:
                return messagebox.askyesno(APP_TITLE, "Extract into a non-empty folder?\n\nExisting files with matching names will be overwritten.\n\n" + output_dir, parent=self)

        return True

    def extract_selected(self):
        selected_items = self.tree.selection()

        if not selected_items:
            messagebox.showerror(APP_TITLE, "Select at least one PBO entry to extract.", parent=self)
            return

        selected_names = [self.entries[int(item)].name for item in selected_items]
        self.extract_entries(selected_names)

    def extract_all(self):
        self.extract_entries(None)

    def extract_entries(self, selected_names):
        pbo_path = self.pbo_path_var.get().strip()
        output_dir = self.output_dir_var.get().strip() or self.get_default_output_dir()

        if output_dir:
            self.output_dir_var.set(output_dir)

        archive_path = self.archive.get("path", "") if self.archive else ""
        archive_matches_path = bool(archive_path and pbo_path and os.path.normcase(os.path.abspath(archive_path)) == os.path.normcase(os.path.abspath(pbo_path)))

        if not self.archive or not archive_matches_path:
            self.inspect_pbo()

            if not self.archive:
                return

        if not output_dir:
            messagebox.showerror(APP_TITLE, "Select an extract output folder.", parent=self)
            return

        if not self.confirm_output_folder(output_dir):
            return

        try:
            result = extract_pbo_files(pbo_path, output_dir, selected_names, self.log)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self)
            return

        self.log(f"Extracted {result['files']} file(s) / {format_byte_size(result['bytes'])} -> {result['output_dir']}")
        messagebox.showinfo(APP_TITLE, f"Extracted {result['files']} file(s).", parent=self)

    def open_output_folder(self):
        output_dir = self.output_dir_var.get().strip() or self.get_default_output_dir()

        if not output_dir:
            messagebox.showerror(APP_TITLE, "Extract output folder is empty.", parent=self)
            return

        if not os.path.isdir(output_dir):
            messagebox.showerror(APP_TITLE, f"Extract output folder does not exist: {output_dir}", parent=self)
            return

        try:
            if os.name == "nt":
                os.startfile(output_dir)
            else:
                subprocess.Popen(["xdg-open", output_dir])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self)


class RaGPboBuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.saved_settings = load_saved_settings()
        self.title(APP_TITLE)
        self.set_window_icon()
        saved_geometry = self.saved_settings.get("window_geometry", "")
        self.geometry(saved_geometry if is_safe_window_geometry(saved_geometry) else "1180x900")
        self.minsize(1120, 830)
        self._apply_graphite_theme()

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.is_building = False
        self.current_log_file = None
        self.current_log_path = ""
        self.current_addon_targets = []
        self.geometry_save_after_id = None
        self.status_var = tk.StringVar(value="Idle")

        self.source_root_presets = normalize_path_presets(self.saved_settings.get("source_root_presets", []))
        self.output_root_presets = normalize_path_presets(self.saved_settings.get("output_root_presets", []))
        self.source_root_var = tk.StringVar(value=self.saved_settings.get("source_root", ""))
        self.output_root_var = tk.StringVar(value=self.saved_settings.get("output_root", self.saved_settings.get("output_addons", "")))
        self.source_root_preset_var = tk.StringVar(value="")
        self.output_root_preset_var = tk.StringVar(value="")
        self.pbo_name_var = tk.StringVar(value=self.saved_settings.get("pbo_name", self.saved_settings.get("prefix_root", "")))
        self.use_binarize_var = tk.BooleanVar(value=self.saved_settings.get("use_binarize", True))
        self.convert_config_var = tk.BooleanVar(value=self.saved_settings.get("convert_config", True))
        self.sign_pbos_var = tk.BooleanVar(value=self.saved_settings.get("sign_pbos", True))
        self.force_rebuild_var = tk.BooleanVar(value=self.saved_settings.get("force_rebuild", False))
        self.preflight_before_build_var = tk.BooleanVar(value=self.saved_settings.get("preflight_before_build", False))
        self.max_processes_var = tk.IntVar(value=self.saved_settings.get("max_processes", get_default_max_processes()))
        self.binarize_exe_var = tk.StringVar(value=self.saved_settings.get("binarize_exe", find_dayz_binarize()))
        self.cfgconvert_exe_var = tk.StringVar(value=self.saved_settings.get("cfgconvert_exe", find_cfgconvert()))
        self.dssignfile_exe_var = tk.StringVar(value=self.saved_settings.get("dssignfile_exe", find_dssignfile()))
        self.private_key_var = tk.StringVar(value=self.saved_settings.get("private_key", ""))
        self.project_root_var = tk.StringVar(value=self.saved_settings.get("project_root", DEFAULT_PROJECT_ROOT))
        self.temp_dir_var = tk.StringVar(value=self.saved_settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self.exclude_patterns_var = tk.StringVar(value=self.saved_settings.get("exclude_patterns", DEFAULT_EXCLUDE_PATTERNS))
        self.log_filter_var = tk.StringVar(value=self.saved_settings.get("log_filter", "All"))
        self.preflight_check_required_addons_hints_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_required_addons_hints", True))
        self.preflight_check_texture_freshness_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_texture_freshness", True))
        self.preflight_check_risky_paths_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_risky_paths", True))
        self.preflight_check_case_conflicts_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_case_conflicts", True))
        self.preflight_check_p3d_internal_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_p3d_internal", True))
        self.preflight_check_terrain_cfgworlds_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_cfgworlds", True))
        self.preflight_check_terrain_navmesh_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_navmesh", False))
        self.preflight_check_terrain_road_shapes_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_road_shapes", True))
        self.preflight_check_terrain_structure_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_structure", True))
        self.preflight_check_terrain_layers_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_layers", True))
        self.preflight_check_terrain_2d_map_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_2d_map", False))
        self.preflight_check_terrain_size_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_terrain_size", True))
        self.preflight_check_wrp_internal_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_wrp_internal", False))
        self.log_history = []
        self.current_error_count = 0
        self.current_warning_count = 0
        self.current_info_count = 0

        self._build_ui()
        self.update_path_preset_dropdowns()
        self.set_status("Idle", "ready")
        self.refresh_addon_list(select_saved=True)
        self._poll_log_queue()
        self.bind("<Configure>", self.on_window_configure)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def set_window_icon(self):
        icon_path = resource_path(APP_ICON_FILE)
        if not os.path.isfile(icon_path):
            return
        try:
            self.iconbitmap(icon_path)
        except Exception:
            try:
                image = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, image)
            except Exception:
                pass

    def _apply_graphite_theme(self):
        self.configure(bg=GRAPHITE_BG)

        # Keep ttk drop-down listboxes dark as well. Without this, Windows can draw
        # combobox popups/readonly fields with a white system theme background.
        self.option_add("*TCombobox*Listbox.background", GRAPHITE_FIELD)
        self.option_add("*TCombobox*Listbox.foreground", GRAPHITE_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", GRAPHITE_ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=GRAPHITE_BG,
            foreground=GRAPHITE_TEXT,
            fieldbackground=GRAPHITE_FIELD,
            font=("Segoe UI", 10),
        )
        style.configure("TFrame", background=GRAPHITE_BG)
        style.configure("Card.TFrame", background=GRAPHITE_CARD)
        style.configure("FieldName.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10))
        style.configure("FieldMuted.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_MUTED, font=("Segoe UI", 10))
        style.configure(
            "TLabelframe",
            background=GRAPHITE_CARD,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER_SOFT,
            lightcolor=GRAPHITE_CARD,
            darkcolor=GRAPHITE_CARD,
            relief="flat",
            padding=18,
        )
        style.configure(
            "TLabelframe.Label",
            background=GRAPHITE_CARD,
            foreground=GRAPHITE_TEXT,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT)
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=4)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)])
        style.configure(
            "TButton",
            background=GRAPHITE_CARD_SOFT,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_CARD_SOFT,
            lightcolor=GRAPHITE_CARD_SOFT,
            darkcolor=GRAPHITE_CARD_SOFT,
            focuscolor=GRAPHITE_CARD_SOFT,
            focusthickness=0,
            relief="flat",
            padding=(12, 8),
        )
        style.configure(
            "TEntry",
            fieldbackground=GRAPHITE_FIELD,
            background=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            insertcolor=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            lightcolor=GRAPHITE_FIELD,
            darkcolor=GRAPHITE_FIELD,
            focuscolor=GRAPHITE_FIELD,
            focusthickness=0,
            relief="flat",
            padding=7,
        )
        style.configure(
            "TSpinbox",
            fieldbackground=GRAPHITE_FIELD,
            background=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            insertcolor=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            lightcolor=GRAPHITE_FIELD,
            darkcolor=GRAPHITE_FIELD,
            focuscolor=GRAPHITE_FIELD,
            focusthickness=0,
            relief="flat",
            padding=6,
        )
        style.configure(
            "TCombobox",
            fieldbackground=GRAPHITE_FIELD,
            background=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_FIELD,
            selectforeground=GRAPHITE_TEXT,
            arrowcolor=GRAPHITE_MUTED,
            bordercolor=GRAPHITE_BORDER,
            lightcolor=GRAPHITE_FIELD,
            darkcolor=GRAPHITE_FIELD,
            focuscolor=GRAPHITE_FIELD,
            focusthickness=0,
            relief="flat",
            padding=5,
        )
        style.configure("Horizontal.TProgressbar", background=GRAPHITE_ACCENT, troughcolor=GRAPHITE_CARD, bordercolor=GRAPHITE_CARD)
        style.configure("Vertical.TScrollbar", background=GRAPHITE_CARD_SOFT, troughcolor=GRAPHITE_BG, arrowcolor=GRAPHITE_MUTED, relief="flat")
        style.map("TButton", background=[("active", GRAPHITE_BORDER), ("pressed", GRAPHITE_ACCENT_DARK)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.map(
            "TEntry",
            bordercolor=[("focus", GRAPHITE_ACCENT), ("!focus", GRAPHITE_BORDER)],
            lightcolor=[("focus", GRAPHITE_FIELD), ("!focus", GRAPHITE_FIELD)],
            darkcolor=[("focus", GRAPHITE_FIELD), ("!focus", GRAPHITE_FIELD)],
            fieldbackground=[("disabled", GRAPHITE_CARD), ("readonly", GRAPHITE_FIELD), ("!disabled", GRAPHITE_FIELD)],
            foreground=[("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)],
        )
        style.map(
            "TSpinbox",
            bordercolor=[("focus", GRAPHITE_ACCENT), ("!focus", GRAPHITE_BORDER)],
            fieldbackground=[("disabled", GRAPHITE_CARD), ("readonly", GRAPHITE_FIELD), ("!disabled", GRAPHITE_FIELD)],
            foreground=[("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", GRAPHITE_FIELD), ("disabled", GRAPHITE_CARD), ("!disabled", GRAPHITE_FIELD)],
            background=[("readonly", GRAPHITE_FIELD), ("active", GRAPHITE_CARD_SOFT), ("!disabled", GRAPHITE_FIELD)],
            foreground=[("readonly", GRAPHITE_TEXT), ("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)],
            selectbackground=[("readonly", GRAPHITE_FIELD), ("!disabled", GRAPHITE_FIELD)],
            selectforeground=[("readonly", GRAPHITE_TEXT), ("!disabled", GRAPHITE_TEXT)],
            bordercolor=[("focus", GRAPHITE_ACCENT), ("!focus", GRAPHITE_BORDER)],
            arrowcolor=[("active", GRAPHITE_TEXT), ("!disabled", GRAPHITE_MUTED)],
        )

    def _build_ui(self):
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=GRAPHITE_HEADER, bd=0, highlightthickness=0)
        header.pack(fill="x", pady=(0, 10), ipady=5)
        left = tk.Frame(header, bg=GRAPHITE_HEADER)
        left.pack(side="left", fill="x", expand=True, padx=(14, 8))
        tk.Label(left, text=APP_TITLE, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="Build selected DayZ addons into Addons and Keys output folders", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        right = tk.Frame(header, bg=GRAPHITE_HEADER)
        right.pack(side="right", padx=(8, 14))
        self.about_button = self._make_header_button(right, "About", self.open_about_window)
        self.licence_button = self._make_header_button(right, "Licence", self.open_licence_window)
        self.inspector_button = self._make_header_button(right, "Inspector", self.open_pbo_inspector_window)
        self.options_button = self._make_header_button(right, "Options", self.open_options_window)

        settings = ttk.LabelFrame(outer, text="Build settings", padding=10)
        settings.pack(fill="x", pady=(0, 10))
        self.source_root_preset_combo = self._add_preset_folder_row(settings, 0, "Project Source", self.source_root_var, self.choose_source_root, "Folder containing your addon project. If this folder itself contains config.cpp, it will be built as one addon.", self.open_source_root_folder, self.source_root_preset_var, self.apply_source_root_preset, self.save_source_root_preset, self.delete_source_root_preset, self.get_source_root_preset_tooltip)
        self.output_root_preset_combo = self._add_preset_folder_row(settings, 1, "Build Output", self.output_root_var, self.choose_output_root, "Build output folder. The builder creates Addons and Keys inside this folder automatically.", self.open_output_folder, self.output_root_preset_var, self.apply_output_root_preset, self.save_output_root_preset, self.delete_output_root_preset, self.get_output_root_preset_tooltip)
        ttk.Label(settings, text="PBO Name", style="FieldName.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        pbo_entry = ttk.Entry(settings, textvariable=self.pbo_name_var)
        pbo_entry.grid(row=2, column=1, sticky="ew", pady=3, padx=(8, 8))
        add_tooltip(pbo_entry, "Optional PBO filename override. Only used when exactly one addon is selected.")
        pbo_hint = ttk.Label(settings, text="Only used when one addon is selected", style="FieldMuted.TLabel")
        pbo_hint.grid(row=2, column=2, sticky="w", pady=3, padx=(8, 0))
        add_tooltip(pbo_hint, "Only used when exactly one addon is selected. Multi-addon builds always use each addon folder name.")
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(2, minsize=165)
        settings.columnconfigure(3, minsize=455)

        options = ttk.LabelFrame(outer, text="Build options", padding=12)
        options.pack(fill="x", pady=(0, 10))
        for col, size in [(0, 125), (1, 150), (2, 150), (3, 150)]:
            options.columnconfigure(col, minsize=size)
        options.columnconfigure(4, weight=1)
        ttk.Label(options, text="Pipeline", style="FieldMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5), padx=(0, 14))
        self._add_checkbutton(options, "Binarize P3D", self.use_binarize_var, 0, 1, "Run DayZ Tools binarize.exe before packing addons that contain P3D files.")
        self._add_checkbutton(options, "CPP to BIN", self.convert_config_var, 0, 2, "Convert root and nested config.cpp files to config.bin in staging before packing.")
        self._add_checkbutton(options, "Sign PBOs", self.sign_pbos_var, 0, 3, "Sign built PBOs with DSSignFile.exe and your .biprivatekey.")
        ttk.Label(options, text="Safety", style="FieldMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 5), padx=(0, 14))
        self._add_checkbutton(options, "Force rebuild", self.force_rebuild_var, 1, 1, "Ignore the build cache, refresh selected addon temp folders, and rebuild all selected addons.")
        self._add_checkbutton(options, "Preflight before build", self.preflight_before_build_var, 1, 2, "Run syntax and path checks before building. Errors stop the build; warnings only get logged.", columnspan=2)
        ttk.Label(options, text="Performance", style="FieldMuted.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 2), padx=(0, 14))
        max_frame = ttk.Frame(options, style="Card.TFrame")
        max_frame.grid(row=2, column=1, columnspan=3, sticky="w")
        workers_label = ttk.Label(max_frame, text="Binarize workers", style="FieldName.TLabel")
        workers_label.pack(side="left")
        spinbox = ttk.Spinbox(max_frame, from_=1, to=64, textvariable=self.max_processes_var, width=8)
        spinbox.pack(side="left", padx=(8, 0))
        worker_tooltip = "How many worker processes Binarize may use. The default is assigned automatically according to the available logical threads of the running system."
        add_tooltip(workers_label, worker_tooltip)
        add_tooltip(spinbox, worker_tooltip)

        addons = ttk.LabelFrame(outer, text="Addon selection", padding=12)
        addons.pack(fill="both", expand=True, pady=(0, 10))
        addons.columnconfigure(0, weight=1)
        addons.rowconfigure(0, weight=1)
        self.addon_listbox = tk.Listbox(addons, selectmode="extended", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, selectbackground="#6f2f2f", selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Consolas", 10), height=4, exportselection=False)
        self.addon_listbox.grid(row=0, column=0, sticky="nsew")
        self.addon_listbox.bind("<<ListboxSelect>>", lambda event: self.save_path_settings())
        scrollbar = ttk.Scrollbar(addons, command=self.addon_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.addon_listbox.configure(yscrollcommand=scrollbar.set)
        addon_buttons = ttk.Frame(addons)
        addon_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(addon_buttons, text="Refresh addons", command=self.refresh_addon_list).pack(side="left")
        ttk.Button(addon_buttons, text="Select all", command=self.select_all_addons).pack(side="left", padx=(8, 0))
        ttk.Button(addon_buttons, text="Select none", command=self.select_no_addons).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(6, 0))
        primary = ttk.Frame(actions)
        primary.pack(fill="x")
        secondary = ttk.Frame(actions)
        secondary.pack(fill="x", pady=(4, 0))
        self.build_button = self._make_action_button(primary, "Build PBOs", self.start_build, primary=True, large=True, tooltip="Build the currently selected addon(s).")
        self.preflight_button = self._make_action_button(primary, "Preflight", self.start_preflight, variant="preflight", large=True, tooltip="Check selected addon(s) before packing.")
        self.status_badge = tk.Label(primary, text="Ready", bg=GRAPHITE_READY, fg="#ffffff", relief="flat", borderwidth=0, padx=10, pady=5, font=("Segoe UI", 9, "bold"))
        self.status_badge.pack(side="left", padx=(14, 6))
        self.status_label = ttk.Label(primary, textvariable=self.status_var, foreground=GRAPHITE_MUTED, width=20)
        self.status_label.pack(side="left", padx=(0, 4))
        self.progress = ttk.Progressbar(primary, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.clear_button = self._make_action_button(secondary, "Clear log", self.clear_log)
        self.clear_temp_button = self._make_action_button(secondary, "Clear build temp", self.clear_temp_from_ui)
        self.clear_full_temp_button = self._make_action_button(secondary, "Clear full temp", self.clear_full_temp_from_ui, tooltip="Deletes all contents inside the selected temp root after confirmation and safety checks.")
        self.clear_cache_button = self._make_action_button(secondary, "Clear build cache", self.clear_build_cache_from_ui)
        self.open_logs_button = self._make_action_button(secondary, "Open logs", self.open_logs_folder)
        self.latest_log_button = self._make_action_button(secondary, "Latest log", self.open_latest_log)

        filter_frame = ttk.Frame(secondary, style="Card.TFrame")
        filter_frame.pack(side="right")
        ttk.Label(filter_frame, text="Log filter", style="FieldMuted.TLabel").pack(side="left", padx=(0, 6))
        self.log_filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.log_filter_var,
            state="readonly",
            values=["All", "Hide INFO", "Warnings + Errors", "Errors Only"],
            width=15,
        )
        self.log_filter_combo.pack(side="left")
        self.log_filter_combo.bind("<<ComboboxSelected>>", self.on_log_filter_changed)
        add_tooltip(self.log_filter_combo, "Filter the visible log output. Saved log files still contain all lines.")

        log_frame = ttk.LabelFrame(outer, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=42, font=("Consolas", 9), bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.configure_log_tags()
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.version_footer = tk.Label(self, text=f"v{APP_VERSION}", bg=GRAPHITE_BG, fg=GRAPHITE_MUTED, font=("Segoe UI", 9))
        self.version_footer.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)

    def _make_header_button(self, parent, text, command):
        button = tk.Button(parent, text=text, command=command, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=12, pady=6, font=("Segoe UI", 9), cursor="hand2")
        button.pack(side="right", padx=(0, 8) if text != "About" else 0)
        self._attach_button_hover(button, GRAPHITE_CARD_SOFT, GRAPHITE_BORDER, GRAPHITE_BORDER)
        return button

    def _attach_button_hover(self, button, normal_bg, hover_bg, pressed_bg=None):
        pressed_bg = pressed_bg or hover_bg
        def on_enter(event=None):
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover_bg, activebackground=pressed_bg)
        def on_leave(event=None):
            button.configure(bg=normal_bg, activebackground=pressed_bg)
        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")

    def _make_action_button(self, parent, text, command, primary=False, tooltip="", variant="", large=False):
        if primary:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_ACCENT_DARK, "#ffffff", GRAPHITE_ACCENT, GRAPHITE_ACCENT_HOVER, "bold"
        elif variant == "preflight":
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_PREFLIGHT, "#ffffff", GRAPHITE_PREFLIGHT_ACTIVE, GRAPHITE_PREFLIGHT_HOVER, "bold"
        else:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_CARD_SOFT, GRAPHITE_TEXT, GRAPHITE_BORDER, GRAPHITE_BORDER, "normal"
        button = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active_bg, activeforeground="#ffffff" if fg == "#ffffff" else GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14 if large else 9, pady=8 if large else 5, font=("Segoe UI", 10 if large else 9, weight), cursor="hand2")
        button.pack(side="left", padx=(0 if primary else 8, 0))
        self._attach_button_hover(button, bg, hover_bg, active_bg)
        add_tooltip(button, tooltip)
        return button

    def _add_checkbutton(self, parent, text, variable, row, column, tooltip, columnspan=1):
        def refresh():
            if variable.get():
                checkbox.configure(text="✓ " + text, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT)
            else:
                checkbox.configure(text="  " + text, bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, activebackground=GRAPHITE_CARD_SOFT, activeforeground=GRAPHITE_TEXT)
        def on_toggle():
            refresh()
            self.save_path_settings()
        width = 22 if columnspan > 1 else max(14, min(len(text) + 3, 22))
        checkbox = tk.Checkbutton(parent, text=text, variable=variable, command=on_toggle, indicatoron=False, selectcolor=GRAPHITE_CARD_SOFT, relief="flat", borderwidth=0, padx=12, pady=7, font=("Segoe UI", 10), cursor="hand2", anchor="w", justify="left", width=width)
        checkbox.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(0, 6), padx=(0, 8))
        refresh()
        add_tooltip(checkbox, tooltip)
        return checkbox

    def _add_preset_folder_row(self, parent, row, label, variable, browse_command, tooltip, open_command, preset_variable, preset_selected_command, save_command, delete_command, preset_tooltip):
        label_widget = ttk.Label(parent, text=label, style="FieldName.TLabel")
        label_widget.grid(row=row, column=0, sticky="w", pady=3)
        add_tooltip(label_widget, tooltip)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 8))
        add_tooltip(entry, tooltip)
        action_frame = ttk.Frame(parent, width=165, style="Card.TFrame")
        action_frame.grid(row=row, column=2, sticky="e", pady=3)
        action_frame.grid_propagate(False)
        browse = ttk.Button(action_frame, text="Browse", command=browse_command, width=9)
        browse.pack(side="left")
        open_button = ttk.Button(action_frame, text="Open", command=open_command, width=7)
        open_button.pack(side="left", padx=(6, 0))
        preset_frame = ttk.Frame(parent, width=455, style="Card.TFrame")
        preset_frame.grid(row=row, column=3, sticky="e", pady=3)
        preset_frame.grid_propagate(False)
        ttk.Label(preset_frame, text="Preset", style="FieldMuted.TLabel").pack(side="left", padx=(0, 6))
        combo = ttk.Combobox(preset_frame, textvariable=preset_variable, state="readonly", values=[], width=26)
        combo.pack(side="left", fill="x", expand=True)
        add_tooltip(combo, preset_tooltip)
        combo.bind("<<ComboboxSelected>>", preset_selected_command)
        save = ttk.Button(preset_frame, text="Save preset", command=save_command, width=12)
        save.pack(side="left", padx=(6, 0))
        delete = ttk.Button(preset_frame, text="Delete", command=delete_command, width=7)
        delete.pack(side="left", padx=(6, 0))
        return combo

    def _add_folder_row(self, parent, row, label, variable, command, tooltip=""):
        ttk.Label(parent, text=label, style="FieldName.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(8, 8))
        add_tooltip(entry, tooltip)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="e", pady=5)

    def _add_file_row(self, parent, row, label, variable, command, tooltip=""):
        ttk.Label(parent, text=label, style="FieldName.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(8, 8))
        add_tooltip(entry, tooltip)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="e", pady=5)

    def set_status(self, text, state="ready"):
        self.status_var.set(text)
        if not hasattr(self, "status_badge"):
            return
        states = {"ready": ("Ready", GRAPHITE_READY), "building": ("Building", GRAPHITE_BUILDING), "preflight": ("Preflight", GRAPHITE_PREFLIGHT), "success": ("Done", GRAPHITE_SUCCESS_DARK), "error": ("Error", GRAPHITE_ERROR_DARK)}
        label, bg = states.get(state, states["ready"])
        self.status_badge.configure(text=label, bg=bg)

    def get_path_preset_names(self, presets):
        return [item["name"] for item in normalize_path_presets(presets) if item.get("name")]

    def find_preset_by_name(self, presets, name):
        name_key = str(name).strip().casefold()
        if not name_key:
            return None
        for preset in normalize_path_presets(presets):
            if preset.get("name", "").casefold() == name_key:
                return preset
        return None

    def find_preset_by_path(self, presets, path):
        key = get_normalized_path_key(path)
        if not key:
            return None
        for preset in normalize_path_presets(presets):
            if get_normalized_path_key(preset.get("path", "")) == key:
                return preset
        return None

    def get_matching_preset_name(self, presets, path):
        preset = self.find_preset_by_path(presets, path)
        return preset.get("name", "") if preset else ""

    def get_path_preset_tooltip(self, presets, preset_name, label):
        preset = self.find_preset_by_name(presets, preset_name)
        if not preset:
            return f"{label} preset\n\nSelect a saved named preset."
        return f"{label} preset\n\nName: {preset.get('name', '')}\nPath: {preset.get('path', '')}"

    def get_source_root_preset_tooltip(self):
        return self.get_path_preset_tooltip(self.source_root_presets, self.source_root_preset_var.get(), "Project Source")

    def get_output_root_preset_tooltip(self):
        return self.get_path_preset_tooltip(self.output_root_presets, self.output_root_preset_var.get(), "Build Output")

    def update_path_preset_dropdowns(self):
        if hasattr(self, "source_root_preset_combo"):
            self.source_root_presets = normalize_path_presets(self.source_root_presets)
            names = self.get_path_preset_names(self.source_root_presets)
            self.source_root_preset_combo.configure(values=names)
            match = self.get_matching_preset_name(self.source_root_presets, self.source_root_var.get().strip())
            self.source_root_preset_var.set(match if match else (self.source_root_preset_var.get() if self.source_root_preset_var.get() in names else ""))
        if hasattr(self, "output_root_preset_combo"):
            self.output_root_presets = normalize_path_presets(self.output_root_presets)
            names = self.get_path_preset_names(self.output_root_presets)
            self.output_root_preset_combo.configure(values=names)
            match = self.get_matching_preset_name(self.output_root_presets, self.output_root_var.get().strip())
            self.output_root_preset_var.set(match if match else (self.output_root_preset_var.get() if self.output_root_preset_var.get() in names else ""))

    def apply_source_root_preset(self, event=None):
        preset = self.find_preset_by_name(self.source_root_presets, self.source_root_preset_var.get())
        if preset:
            self.source_root_var.set(preset.get("path", ""))
            self.refresh_addon_list(select_all_default=True)
            self.save_path_settings()

    def apply_output_root_preset(self, event=None):
        preset = self.find_preset_by_name(self.output_root_presets, self.output_root_preset_var.get())
        if preset:
            self.output_root_var.set(preset.get("path", ""))
            self.refresh_addon_list(select_all_default=True)
            self.save_path_settings()

    def save_path_preset(self, path_var, list_name, preset_var, label):
        path = path_var.get().strip()
        if not path:
            messagebox.showerror(APP_TITLE, f"{label} path is empty.")
            return
        presets = normalize_path_presets(getattr(self, list_name, []))
        existing_by_path = self.find_preset_by_path(presets, path)
        default_name = existing_by_path.get("name", "") if existing_by_path else get_default_preset_name_from_path(path, label)
        name = simpledialog.askstring(APP_TITLE, f"Preset name for {label}:", initialvalue=default_name, parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror(APP_TITLE, "Preset name cannot be empty.")
            return
        existing_by_name = self.find_preset_by_name(presets, name)
        path_key = get_normalized_path_key(path)
        if existing_by_name and get_normalized_path_key(existing_by_name.get("path", "")) != path_key:
            if not messagebox.askyesno(APP_TITLE, f"A {label} preset named '{name}' already exists.\n\nReplace its path with the current path?\n\n{path}"):
                return
        new_presets = []
        replaced = False
        for preset in presets:
            same_name = preset.get("name", "").casefold() == name.casefold()
            same_path = get_normalized_path_key(preset.get("path", "")) == path_key
            if same_name or same_path:
                if not replaced:
                    new_presets.append({"name": name, "path": path})
                    replaced = True
            else:
                new_presets.append(preset)
        if not replaced:
            new_presets.append({"name": name, "path": path})
        setattr(self, list_name, normalize_path_presets(new_presets))
        preset_var.set(name)
        self.update_path_preset_dropdowns()
        self.save_path_settings()
        self.log(f"Saved {label} preset: {name} -> {path}")

    def delete_path_preset(self, path_var, list_name, preset_var, label):
        presets = normalize_path_presets(getattr(self, list_name, []))
        name = preset_var.get().strip() or self.get_matching_preset_name(presets, path_var.get().strip())
        preset = self.find_preset_by_name(presets, name)
        if not preset:
            messagebox.showerror(APP_TITLE, f"Select a {label} preset to delete.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Delete this {label} preset?\n\nName: {preset['name']}\nPath: {preset['path']}"):
            return
        setattr(self, list_name, [p for p in presets if p.get("name", "").casefold() != preset["name"].casefold()])
        preset_var.set("")
        self.update_path_preset_dropdowns()
        self.save_path_settings()
        self.log(f"Deleted {label} preset: {preset['name']} -> {preset['path']}")

    def save_source_root_preset(self):
        self.save_path_preset(self.source_root_var, "source_root_presets", self.source_root_preset_var, "Project Source")

    def delete_source_root_preset(self):
        self.delete_path_preset(self.source_root_var, "source_root_presets", self.source_root_preset_var, "Project Source")

    def save_output_root_preset(self):
        self.save_path_preset(self.output_root_var, "output_root_presets", self.output_root_preset_var, "Build Output")

    def delete_output_root_preset(self):
        self.delete_path_preset(self.output_root_var, "output_root_presets", self.output_root_preset_var, "Build Output")

    def open_pbo_inspector_window(self):
        PboInspectorWindow(self)

    def open_licence_window(self):
        window = tk.Toplevel(self)
        window.title("Licence")
        window.geometry("720x560")
        window.minsize(600, 420)
        window.configure(bg=GRAPHITE_BG)
        window.transient(self)
        window.grab_set()
        container = ttk.Frame(window, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Licence", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(container, text=APP_LICENSE_NAME, foreground=GRAPHITE_MUTED).pack(anchor="w", pady=(6, 14))
        text = tk.Text(container, wrap="word", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        text.pack(side="left", fill="both", expand=True, pady=(0, 12))
        text.insert("1.0", APP_LICENSE_TEXT)
        text.configure(state="disabled")
        scrollbar = ttk.Scrollbar(container, command=text.yview)
        scrollbar.pack(side="right", fill="y", pady=(0, 12))
        text.configure(yscrollcommand=scrollbar.set)
        tk.Button(container, text="Close", command=window.destroy, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2").pack(anchor="e")

    def open_about_window(self):
        window = tk.Toplevel(self)
        window.title("About")
        window.geometry("520x360")
        window.minsize(480, 320)
        window.configure(bg=GRAPHITE_BG)
        window.transient(self)
        window.grab_set()
        container = ttk.Frame(window, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=APP_TITLE, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(container, text=f"Version: {APP_VERSION}", foreground=GRAPHITE_MUTED).pack(anchor="w", pady=(6, 0))
        ttk.Label(container, text=f"Author: {APP_AUTHOR}", foreground=GRAPHITE_MUTED).pack(anchor="w", pady=(2, 14))
        info = (
            "DayZ PBO build helper for packing, binarizing, signing, validating, and preparing addon output folders.\n\n"
            f"Licence: {APP_LICENSE_NAME}\n"
            "Copyright © 2026 RaG Tyson\n\n"
            "Important:\n"
            "- Never share your .biprivatekey.\n"
            "- Only distribute the matching .bikey.\n"
            "- Always check generated PBOs before release.\n\n"
            "This tool is provided as-is without warranty."
        )
        text = tk.Text(container, height=9, wrap="word", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        text.pack(fill="both", expand=True, pady=(0, 12))
        text.insert("1.0", info)
        text.configure(state="disabled")
        tk.Button(container, text="Close", command=window.destroy, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2").pack(anchor="e")

    def open_options_window(self):
        window = tk.Toplevel(self)
        window.title("Options")
        window.geometry("940x900")
        window.minsize(820, 840)
        window.configure(bg=GRAPHITE_BG)
        window.transient(self)
        window.grab_set()
        container = ttk.Frame(window, padding=16)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Options", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 12))
        frame = ttk.LabelFrame(container, text="Tool paths and build settings", padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        self._add_file_row(frame, 0, "binarize.exe", self.binarize_exe_var, self.choose_binarize_exe, "Path to DayZ Tools binarize.exe.")
        self._add_file_row(frame, 1, "CfgConvert.exe", self.cfgconvert_exe_var, self.choose_cfgconvert_exe, "Path to DayZ Tools CfgConvert.exe.")
        self._add_file_row(frame, 2, "DSSignFile.exe", self.dssignfile_exe_var, self.choose_dssignfile_exe, "Path to DayZ Tools DSSignFile.exe.")
        self._add_file_row(frame, 3, "Private key", self.private_key_var, self.choose_private_key, "Your .biprivatekey. Never distribute this file.")
        self._add_folder_row(frame, 4, "Project root", self.project_root_var, self.choose_project_root, "Usually P: or your DayZ project drive root.")
        self._add_folder_row(frame, 5, "Temp dir", self.temp_dir_var, self.choose_temp_dir, "Temporary staging folder.")
        ttk.Label(frame, text="Exclude patterns").grid(row=6, column=0, sticky="nw", pady=5)
        exclude_entry = tk.Text(frame, height=5, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        exclude_entry.grid(row=6, column=1, columnspan=2, sticky="nsew", pady=5, padx=(8, 0))
        exclude_entry.insert("1.0", self.exclude_patterns_var.get())
        frame.rowconfigure(6, weight=1)

        preflight_frame = ttk.LabelFrame(container, text="Preflight checks", padding=14)
        preflight_frame.pack(fill="x", pady=(12, 0))
        for col, size in [(0, 175), (1, 175), (2, 175)]:
            preflight_frame.columnconfigure(col, minsize=size)
        preflight_frame.columnconfigure(3, weight=1)

        self._add_checkbutton(
            preflight_frame,
            "requiredAddons hints",
            self.preflight_check_required_addons_hints_var,
            0,
            0,
            "Suggest possible requiredAddons[] dependencies based on inherited base classes.",
        )
        self._add_checkbutton(
            preflight_frame,
            "Texture freshness",
            self.preflight_check_texture_freshness_var,
            0,
            1,
            "Warn if source texture files are newer than matching .paa files or missing .paa output.",
        )
        self._add_checkbutton(
            preflight_frame,
            "Risky path names",
            self.preflight_check_risky_paths_var,
            0,
            2,
            "Warn about non-ASCII, very long, or otherwise risky filenames and paths.",
        )
        self._add_checkbutton(
            preflight_frame,
            "Case conflicts",
            self.preflight_check_case_conflicts_var,
            1,
            0,
            "Warn about files that differ only by letter casing.",
        )
        self._add_checkbutton(
            preflight_frame,
            "P3D internal scan",
            self.preflight_check_p3d_internal_var,
            1,
            1,
            "Best-effort scan for readable internal P3D references.",
        )


        terrain_frame = ttk.LabelFrame(container, text="Terrain / WRP checks", padding=14)
        terrain_frame.pack(fill="x", pady=(12, 0))
        for col, size in [(0, 185), (1, 185), (2, 185)]:
            terrain_frame.columnconfigure(col, minsize=size)
        terrain_frame.columnconfigure(3, weight=1)

        self._add_checkbutton(
            terrain_frame,
            "WRP / CfgWorlds",
            self.preflight_check_terrain_cfgworlds_var,
            0,
            0,
            "When a .wrp is detected, check CfgWorlds, CfgWorldList, worldName, prefix consistency, and terrain layer hints.",
        )
        self._add_checkbutton(
            terrain_frame,
            "Road shapes",
            self.preflight_check_terrain_road_shapes_var,
            0,
            1,
            "Check explicit terrain road/shape references such as .shp and required .dbf/.shx sidecar files.",
        )
        self._add_checkbutton(
            terrain_frame,
            "Navmesh",
            self.preflight_check_terrain_navmesh_var,
            0,
            2,
            "Warn about missing or excluded navmesh data for WRP terrain addons. Disabled by default because early test maps may not ship navmesh.",
        )
        self._add_checkbutton(
            terrain_frame,
            "WRP internal scan",
            self.preflight_check_wrp_internal_var,
            1,
            0,
            "Best-effort binary scan for readable WRP references. Disabled by default because WRP scans can be noisy.",
        )
        self._add_checkbutton(
            terrain_frame,
            "Terrain structure",
            self.preflight_check_terrain_structure_var,
            1,
            1,
            "Warn about unusual terrain folder layout and source/export folders that may be packed.",
        )
        self._add_checkbutton(
            terrain_frame,
            "Terrain layers",
            self.preflight_check_terrain_layers_var,
            1,
            2,
            "Check terrain layer folders and layer RVMAT references for suspicious paths.",
        )
        self._add_checkbutton(
            terrain_frame,
            "2D map config",
            self.preflight_check_terrain_2d_map_var,
            2,
            0,
            "Optional warning-only check for possible 2D map image references in terrain configs. Disabled by default because map UI setups vary.",
        )
        self._add_checkbutton(
            terrain_frame,
            "Size/source warn",
            self.preflight_check_terrain_size_var,
            2,
            1,
            "Estimate terrain addon size and warn when source/export data may be making the PBO too large.",
        )

        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(12, 0))
        def save_and_close():
            self.exclude_patterns_var.set(exclude_entry.get("1.0", "end").strip())
            self.save_path_settings()
            window.destroy()
        tk.Button(buttons, text="Save", command=save_and_close, bg=GRAPHITE_ACCENT_DARK, fg="#ffffff", activebackground=GRAPHITE_ACCENT, activeforeground="#ffffff", relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2").pack(side="right")
        tk.Button(buttons, text="Cancel", command=window.destroy, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2").pack(side="right", padx=(0, 8))

    def get_selected_addon_names(self):
        return [self.addon_listbox.get(index) for index in self.addon_listbox.curselection()]

    def refresh_addon_list(self, select_saved=False, select_all_default=False):
        source_root = self.source_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        output_addons_dir = os.path.join(output_root, "Addons") if output_root else ""
        previous = set(self.get_selected_addon_names()) if hasattr(self, "addon_listbox") else set()
        saved = set(self.saved_settings.get("selected_addons", [])) if select_saved else set()
        self.addon_listbox.delete(0, "end")
        self.current_addon_targets = []
        if not source_root or not os.path.isdir(source_root):
            self.update_path_preset_dropdowns()
            return
        self.current_addon_targets = detect_addon_targets(source_root, output_addons_dir)
        for name, _ in self.current_addon_targets:
            self.addon_listbox.insert("end", name)
        names = [name for name, _ in self.current_addon_targets]
        if select_all_default:
            selection = set(names)
        else:
            selection = saved or previous or set(names)

        for index, name in enumerate(names):
            if name in selection:
                self.addon_listbox.selection_set(index)
        self.update_path_preset_dropdowns()
        self.save_path_settings()

    def select_all_addons(self):
        self.addon_listbox.selection_set(0, "end")
        self.save_path_settings()

    def select_no_addons(self):
        self.addon_listbox.selection_clear(0, "end")
        self.save_path_settings()

    def save_path_settings(self):
        try:
            max_processes = int(self.max_processes_var.get())
        except Exception:
            max_processes = get_default_max_processes()
        data = {
            "source_root": self.source_root_var.get().strip(),
            "output_root": self.output_root_var.get().strip(),
            "source_root_presets": normalize_path_presets(self.source_root_presets),
            "output_root_presets": normalize_path_presets(self.output_root_presets),
            "pbo_name": self.pbo_name_var.get().strip(),
            "use_binarize": bool(self.use_binarize_var.get()),
            "convert_config": bool(self.convert_config_var.get()),
            "sign_pbos": bool(self.sign_pbos_var.get()),
            "force_rebuild": bool(self.force_rebuild_var.get()),
            "preflight_before_build": bool(self.preflight_before_build_var.get()),
            "max_processes": max_processes,
            "binarize_exe": self.binarize_exe_var.get().strip(),
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "dssignfile_exe": self.dssignfile_exe_var.get().strip(),
            "private_key": self.private_key_var.get().strip(),
            "project_root": self.project_root_var.get().strip(),
            "temp_dir": self.temp_dir_var.get().strip(),
            "exclude_patterns": self.exclude_patterns_var.get().strip(),
            "log_filter": self.log_filter_var.get().strip() if hasattr(self, "log_filter_var") else "All",
            "preflight_check_required_addons_hints": bool(self.preflight_check_required_addons_hints_var.get()) if hasattr(self, "preflight_check_required_addons_hints_var") else True,
            "preflight_check_texture_freshness": bool(self.preflight_check_texture_freshness_var.get()) if hasattr(self, "preflight_check_texture_freshness_var") else True,
            "preflight_check_risky_paths": bool(self.preflight_check_risky_paths_var.get()) if hasattr(self, "preflight_check_risky_paths_var") else True,
            "preflight_check_case_conflicts": bool(self.preflight_check_case_conflicts_var.get()) if hasattr(self, "preflight_check_case_conflicts_var") else True,
            "preflight_check_p3d_internal": bool(self.preflight_check_p3d_internal_var.get()) if hasattr(self, "preflight_check_p3d_internal_var") else True,
            "preflight_check_terrain_cfgworlds": bool(self.preflight_check_terrain_cfgworlds_var.get()) if hasattr(self, "preflight_check_terrain_cfgworlds_var") else True,
            "preflight_check_terrain_navmesh": bool(self.preflight_check_terrain_navmesh_var.get()) if hasattr(self, "preflight_check_terrain_navmesh_var") else False,
            "preflight_check_terrain_road_shapes": bool(self.preflight_check_terrain_road_shapes_var.get()) if hasattr(self, "preflight_check_terrain_road_shapes_var") else True,
            "preflight_check_terrain_structure": bool(self.preflight_check_terrain_structure_var.get()) if hasattr(self, "preflight_check_terrain_structure_var") else True,
            "preflight_check_terrain_layers": bool(self.preflight_check_terrain_layers_var.get()) if hasattr(self, "preflight_check_terrain_layers_var") else True,
            "preflight_check_terrain_2d_map": bool(self.preflight_check_terrain_2d_map_var.get()) if hasattr(self, "preflight_check_terrain_2d_map_var") else False,
            "preflight_check_terrain_size": bool(self.preflight_check_terrain_size_var.get()) if hasattr(self, "preflight_check_terrain_size_var") else True,
            "preflight_check_wrp_internal": bool(self.preflight_check_wrp_internal_var.get()) if hasattr(self, "preflight_check_wrp_internal_var") else False,
            "selected_addons": self.get_selected_addon_names() if hasattr(self, "addon_listbox") else [],
            "window_geometry": self.geometry() if is_safe_window_geometry(self.geometry()) else self.saved_settings.get("window_geometry", ""),
        }
        self.saved_settings = data
        save_saved_settings(data)

    def choose_source_root(self):
        path = filedialog.askdirectory(title="Select Project Source", initialdir=get_initial_dir_from_value(self.source_root_var.get(), self.output_root_var.get()))
        if path:
            self.source_root_var.set(path)
            self.refresh_addon_list(select_all_default=True)
            self.save_path_settings()

    def choose_output_root(self):
        path = filedialog.askdirectory(title="Select Build Output folder", initialdir=get_initial_dir_from_value(self.output_root_var.get(), self.source_root_var.get()))
        if path:
            self.output_root_var.set(path)
            self.refresh_addon_list(select_all_default=True)
            self.save_path_settings()

    def choose_project_root(self):
        path = filedialog.askdirectory(title="Select project root, usually P:", initialdir=get_initial_dir_from_value(self.project_root_var.get(), self.source_root_var.get()))
        if path:
            if len(path) == 3 and path[1] == ":" and path.endswith(WIN_SEP):
                path = path[:2]
            self.project_root_var.set(path)
            self.save_path_settings()

    def choose_temp_dir(self):
        path = filedialog.askdirectory(title="Select temporary build directory", initialdir=get_initial_dir_from_value(self.temp_dir_var.get(), self.source_root_var.get()))
        if path:
            self.temp_dir_var.set(path)
            self.save_path_settings()

    def choose_binarize_exe(self):
        path = filedialog.askopenfilename(title="Select binarize.exe", initialdir=get_initial_dir_from_value(self.binarize_exe_var.get(), self.project_root_var.get()), filetypes=[("binarize.exe", "binarize.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.binarize_exe_var.set(path)
            self.save_path_settings()

    def choose_cfgconvert_exe(self):
        path = filedialog.askopenfilename(title="Select CfgConvert.exe", initialdir=get_initial_dir_from_value(self.cfgconvert_exe_var.get(), self.project_root_var.get()), filetypes=[("CfgConvert.exe", "CfgConvert.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.cfgconvert_exe_var.set(path)
            self.save_path_settings()

    def choose_dssignfile_exe(self):
        path = filedialog.askopenfilename(title="Select DSSignFile.exe", initialdir=get_initial_dir_from_value(self.dssignfile_exe_var.get(), self.project_root_var.get()), filetypes=[("DSSignFile.exe", "DSSignFile.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.dssignfile_exe_var.set(path)
            self.save_path_settings()

    def choose_private_key(self):
        path = filedialog.askopenfilename(title="Select private key", initialdir=get_initial_dir_from_value(self.private_key_var.get(), self.output_root_var.get()), filetypes=[("BI private key", "*.biprivatekey"), ("All files", "*.*")])
        if path:
            self.private_key_var.set(path)
            self.save_path_settings()

    def validate_preflight_settings(self):
        self.refresh_addon_list()
        source_root = self.source_root_var.get().strip()
        if not source_root:
            raise BuildError("Select a Project Source folder.")
        if not os.path.isdir(source_root):
            raise BuildError(f"Project Source does not exist: {source_root}")
        selected = self.get_selected_addon_names()
        if not selected:
            raise BuildError("Select at least one addon to check.")
        selected_set = set(selected)
        targets = [(name, path) for name, path in self.current_addon_targets if name in selected_set]
        if not targets:
            raise BuildError("No selected addon targets found.")
        settings = {
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "project_root": self.project_root_var.get().strip() or DEFAULT_PROJECT_ROOT,
            "temp_dir": self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR,
            "exclude_patterns": self.exclude_patterns_var.get().strip(),
            "preflight_check_required_addons_hints": bool(self.preflight_check_required_addons_hints_var.get()),
            "preflight_check_texture_freshness": bool(self.preflight_check_texture_freshness_var.get()),
            "preflight_check_risky_paths": bool(self.preflight_check_risky_paths_var.get()),
            "preflight_check_case_conflicts": bool(self.preflight_check_case_conflicts_var.get()),
            "preflight_check_p3d_internal": bool(self.preflight_check_p3d_internal_var.get()),
            "preflight_check_terrain_cfgworlds": bool(self.preflight_check_terrain_cfgworlds_var.get()),
            "preflight_check_terrain_navmesh": bool(self.preflight_check_terrain_navmesh_var.get()),
            "preflight_check_terrain_road_shapes": bool(self.preflight_check_terrain_road_shapes_var.get()),
            "preflight_check_terrain_structure": bool(self.preflight_check_terrain_structure_var.get()),
            "preflight_check_terrain_layers": bool(self.preflight_check_terrain_layers_var.get()),
            "preflight_check_terrain_2d_map": bool(self.preflight_check_terrain_2d_map_var.get()),
            "preflight_check_terrain_size": bool(self.preflight_check_terrain_size_var.get()),
            "preflight_check_wrp_internal": bool(self.preflight_check_wrp_internal_var.get()),
        }
        self.save_path_settings()
        return settings, targets

    def validate_settings(self):
        self.refresh_addon_list()
        source_root = self.source_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        if not source_root:
            raise BuildError("Select a Project Source folder.")
        if not os.path.isdir(source_root):
            raise BuildError(f"Project Source does not exist: {source_root}")
        if not output_root:
            raise BuildError("Select a Build Output folder.")
        selected = self.get_selected_addon_names()
        if not selected:
            raise BuildError("Select at least one addon to build.")
        if self.pbo_name_var.get().strip() and len(selected) > 1:
            raise BuildError("PBO Name override can only be used when exactly one addon is selected.")
        if self.use_binarize_var.get():
            path = self.binarize_exe_var.get().strip()
            if not path:
                raise BuildError("Select binarize.exe or disable P3D binarize.")
            if not os.path.isfile(path):
                raise BuildError(f"binarize.exe does not exist: {path}")
        if self.convert_config_var.get():
            path = self.cfgconvert_exe_var.get().strip()
            if not path:
                raise BuildError("Select CfgConvert.exe or disable CPP to BIN.")
            if not os.path.isfile(path):
                raise BuildError(f"CfgConvert.exe does not exist: {path}")
        if self.sign_pbos_var.get():
            sign = self.dssignfile_exe_var.get().strip()
            key = self.private_key_var.get().strip()
            if not sign:
                raise BuildError("Select DSSignFile.exe or disable Sign PBOs.")
            if not os.path.isfile(sign):
                raise BuildError(f"DSSignFile.exe does not exist: {sign}")
            if not key:
                raise BuildError("Select a .biprivatekey file or disable Sign PBOs.")
            if not os.path.isfile(key):
                raise BuildError(f"Private key does not exist: {key}")
        try:
            max_processes = int(self.max_processes_var.get())
        except Exception:
            max_processes = get_default_max_processes()
        max_processes = max(1, max_processes)
        settings = {
            "source_root": source_root,
            "output_root_dir": output_root,
            "pbo_name": self.pbo_name_var.get().strip(),
            "use_binarize": bool(self.use_binarize_var.get()),
            "convert_config": bool(self.convert_config_var.get()),
            "sign_pbos": bool(self.sign_pbos_var.get()),
            "force_rebuild": bool(self.force_rebuild_var.get()),
            "preflight_before_build": bool(self.preflight_before_build_var.get()),
            "binarize_exe": self.binarize_exe_var.get().strip(),
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "dssignfile_exe": self.dssignfile_exe_var.get().strip(),
            "private_key": self.private_key_var.get().strip(),
            "project_root": self.project_root_var.get().strip() or DEFAULT_PROJECT_ROOT,
            "temp_dir": self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR,
            "exclude_patterns": self.exclude_patterns_var.get().strip(),
            "max_processes": max_processes,
            "selected_addons": selected,
            "log_file": str(create_build_log_path()),
            "preflight_check_required_addons_hints": bool(self.preflight_check_required_addons_hints_var.get()),
            "preflight_check_texture_freshness": bool(self.preflight_check_texture_freshness_var.get()),
            "preflight_check_risky_paths": bool(self.preflight_check_risky_paths_var.get()),
            "preflight_check_case_conflicts": bool(self.preflight_check_case_conflicts_var.get()),
            "preflight_check_p3d_internal": bool(self.preflight_check_p3d_internal_var.get()),
            "preflight_check_terrain_cfgworlds": bool(self.preflight_check_terrain_cfgworlds_var.get()),
            "preflight_check_terrain_navmesh": bool(self.preflight_check_terrain_navmesh_var.get()),
            "preflight_check_terrain_road_shapes": bool(self.preflight_check_terrain_road_shapes_var.get()),
            "preflight_check_terrain_structure": bool(self.preflight_check_terrain_structure_var.get()),
            "preflight_check_terrain_layers": bool(self.preflight_check_terrain_layers_var.get()),
            "preflight_check_terrain_2d_map": bool(self.preflight_check_terrain_2d_map_var.get()),
            "preflight_check_terrain_size": bool(self.preflight_check_terrain_size_var.get()),
            "preflight_check_wrp_internal": bool(self.preflight_check_wrp_internal_var.get()),
        }
        self.save_path_settings()
        return settings

    def start_preflight(self):
        if self.is_building:
            return
        try:
            settings, targets = self.validate_preflight_settings()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.current_log_path = str(create_build_log_path())
        settings["log_file"] = self.current_log_path
        Path(self.current_log_path).parent.mkdir(parents=True, exist_ok=True)
        self.current_log_file = open(self.current_log_path, "w", encoding="utf-8")
        self.reset_run_counters("Preflight running...")
        self.is_building = True
        self.build_button.configure(state="disabled")
        self.preflight_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.set_status("Preflight running...", "preflight")
        self.log("Starting preflight check...")
        self.log(f"Log file: {self.current_log_path}")
        self.worker_thread = threading.Thread(target=self._preflight_worker, args=(settings, targets), daemon=True)
        self.worker_thread.start()

    def _preflight_worker(self, settings, targets):
        try:
            result = run_preflight_for_targets(settings, targets, self.thread_log, self.thread_progress)
            self.log_queue.put(("preflight_done", result))
        except Exception as e:
            self.log_queue.put(("error", str(e)))

    def start_build(self):
        if self.is_building:
            return
        try:
            settings = self.validate_settings()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.current_log_path = settings.get("log_file", "")
        Path(self.current_log_path).parent.mkdir(parents=True, exist_ok=True)
        self.current_log_file = open(self.current_log_path, "w", encoding="utf-8")
        self.reset_run_counters("Build running...")
        self.is_building = True
        self.build_button.configure(state="disabled")
        self.preflight_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.set_status("Build running...", "building")
        self.log("Starting build...")
        self.log(f"Log file: {self.current_log_path}")
        self.worker_thread = threading.Thread(target=self._build_worker, args=(settings,), daemon=True)
        self.worker_thread.start()

    def _build_worker(self, settings):
        try:
            summary = build_all(settings, self.thread_log, self.thread_progress)
            self.log_queue.put(("done", summary))
        except Exception as e:
            self.log_queue.put(("error", str(e)))

    def thread_log(self, message):
        self.log_queue.put(("log", message))

    def thread_progress(self, current, total):
        self.log_queue.put(("progress", (current, total)))

    def reset_run_counters(self, summary_text="Running..."):
        self.current_error_count = 0
        self.current_warning_count = 0
        self.current_info_count = 0

    def line_passes_log_filter(self, line):
        mode = self.log_filter_var.get().strip() if hasattr(self, "log_filter_var") else "All"
        tag = self.get_log_tag(line)

        if mode == "Hide INFO":
            return tag != "log_info"

        if mode == "Warnings + Errors":
            return tag in {"log_warning", "log_error"}

        if mode == "Errors Only":
            return tag == "log_error"

        return True

    def on_log_filter_changed(self, event=None):
        self.render_log_history()
        self.save_path_settings()

    def render_log_history(self):
        if not hasattr(self, "log_text"):
            return

        self.log_text.delete("1.0", "end")

        for line in self.log_history:
            if not self.line_passes_log_filter(line):
                continue
            tag = self.get_log_tag(line)
            self.log_text.insert("end", line + chr(10), tag if tag else None)

        self.log_text.see("end")

    def configure_log_tags(self):
        self.log_text.tag_configure("log_error", foreground=GRAPHITE_ERROR)
        self.log_text.tag_configure("log_warning", foreground=GRAPHITE_WARNING)
        self.log_text.tag_configure("log_success", foreground=GRAPHITE_SUCCESS)
        self.log_text.tag_configure("log_section", foreground=GRAPHITE_MUTED)
        self.log_text.tag_configure("log_tool", foreground=GRAPHITE_PREFLIGHT_ACTIVE)
        self.log_text.tag_configure("log_info", foreground=GRAPHITE_MUTED)

    def get_log_tag(self, line):
        text = line.strip()
        upper = text.upper()
        if not text:
            return ""
        if upper.startswith("ERROR") or " ERROR:" in upper:
            return "log_error"
        if upper.startswith("WARNING") or " WARNING:" in upper:
            return "log_warning"
        if upper.startswith("INFO") or " INFO:" in upper:
            return "log_info"
        if "BUILD FINISHED" in upper or "COMPLETED SUCCESSFULLY" in upper or upper.endswith(" OK") or upper.endswith(": OK"):
            return "log_success"
        if text.startswith("=" * 8):
            return "log_section"
        if "Binarize" in text or "CfgConvert" in text or "DSSignFile" in text or "Preflight" in text:
            return "log_tool"
        return ""

    def _poll_log_queue(self):
        batch = []
        def flush():
            if batch:
                self.log_many(batch)
                batch.clear()
        try:
            while True:
                item_type, payload = self.log_queue.get_nowait()
                if item_type == "log":
                    batch.append(payload)
                    continue
                flush()
                if item_type == "progress":
                    current, total = payload
                    maximum = max(total, 1)
                    self.progress.configure(maximum=maximum, value=current)
                    self.set_status(f"Working... {current}/{maximum}", "building")
                elif item_type == "done":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self.set_status("Build finished", "success")
                    self.close_current_log_file()
                    messagebox.showinfo(APP_TITLE, "Build finished.")
                elif item_type == "preflight_done":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self.set_status("Preflight finished", "success")
                    self.close_current_log_file()
                    result = payload
                    if result.errors:
                        messagebox.showerror(APP_TITLE, f"Preflight finished with {result.errors} error(s) and {result.warnings} warning(s).")
                    elif result.warnings:
                        messagebox.showwarning(APP_TITLE, f"Preflight finished with {result.warnings} warning(s).")
                    else:
                        messagebox.showinfo(APP_TITLE, "Preflight finished without errors or warnings.")
                elif item_type == "error":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.log("")
                    self.log(f"ERROR: {payload}")
                    self.set_status("Error", "error")
                    self.close_current_log_file()
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            flush()
        self.after(100, self._poll_log_queue)

    def log(self, message):
        self.log_many([message])

    def log_many(self, messages):
        lines = [str(item) for item in messages]

        for line in lines:
            tag = self.get_log_tag(line)
            self.log_history.append(line)

            if tag == "log_error":
                self.current_error_count += 1
            elif tag == "log_warning":
                self.current_warning_count += 1
            elif tag == "log_info":
                self.current_info_count += 1

            if self.line_passes_log_filter(line):
                self.log_text.insert("end", line + chr(10), tag if tag else None)

        self.log_text.see("end")
        try:
            for line in lines:
                print(line, flush=True)
        except Exception:
            pass
        if self.current_log_file:
            try:
                self.current_log_file.write(chr(10).join(lines) + chr(10))
                self.current_log_file.flush()
            except Exception:
                pass
        self.update_idletasks()

    def on_window_configure(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self.state() == "zoomed":
            return
        if self.geometry_save_after_id:
            try:
                self.after_cancel(self.geometry_save_after_id)
            except Exception:
                pass
        self.geometry_save_after_id = self.after(700, self.save_window_geometry)

    def save_window_geometry(self):
        self.geometry_save_after_id = None
        geometry = self.geometry()
        if is_safe_window_geometry(geometry):
            self.saved_settings["window_geometry"] = geometry
            save_saved_settings(self.saved_settings)

    def on_close(self):
        try:
            self.save_window_geometry()
            self.save_path_settings()
        except Exception:
            pass
        self.close_current_log_file()
        self.destroy()

    def close_current_log_file(self):
        if self.current_log_file:
            try:
                self.current_log_file.close()
            except Exception:
                pass
            self.current_log_file = None

    def clear_log(self):
        self.log_history.clear()
        self.log_text.delete("1.0", "end")
        self.current_error_count = 0
        self.current_warning_count = 0
        self.current_info_count = 0

    def clear_temp_from_ui(self):
        if self.is_building:
            messagebox.showwarning(APP_TITLE, "Cannot clear temp folder while a build is running.")
            return
        temp_dir = self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR
        confirm = messagebox.askyesno(APP_TITLE, "Safely clear RaG PBO Builder temp data?\n\nTemp root:\n" + temp_dir + "\n\nOnly known builder temp folders will be removed.")
        if not confirm:
            return
        try:
            clear_temp_folder(temp_dir, self.log, self.source_root_var.get().strip(), self.output_root_var.get().strip())
            messagebox.showinfo(APP_TITLE, "Builder temp data cleared.")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror(APP_TITLE, str(e))

    def clear_full_temp_from_ui(self):
        if self.is_building:
            messagebox.showwarning(APP_TITLE, "Cannot clear full temp while a build is running.")
            return
        temp_dir = self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR
        confirm = messagebox.askyesno(APP_TITLE, "Clear ALL selected temp folder contents?\n\nTemp root:\n" + temp_dir + "\n\nThis removes every file and folder inside the temp root except the marker file.")
        if not confirm:
            return
        try:
            clear_full_temp_folder(temp_dir, self.log, self.source_root_var.get().strip(), self.output_root_var.get().strip())
            messagebox.showinfo(APP_TITLE, "All temp folder contents cleared.")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror(APP_TITLE, str(e))

    def open_folder_in_explorer(self, folder_path, empty_message, missing_message):
        folder_path = folder_path.strip() if folder_path else ""
        if not folder_path:
            messagebox.showerror(APP_TITLE, empty_message)
            return
        if not os.path.isdir(folder_path):
            messagebox.showerror(APP_TITLE, missing_message.format(folder_path=folder_path))
            return
        try:
            if os.name == "nt":
                os.startfile(folder_path)
            else:
                subprocess.Popen(["xdg-open", folder_path])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def open_source_root_folder(self):
        self.open_folder_in_explorer(self.source_root_var.get().strip(), "Project Source folder is empty.", "Project Source folder does not exist: {folder_path}")

    def open_output_folder(self):
        self.open_folder_in_explorer(self.output_root_var.get().strip(), "Build Output folder is empty.", "Build Output folder does not exist: {folder_path}")

    def open_logs_folder(self):
        self.open_folder_in_explorer(str(get_logs_dir()), "Logs folder is empty.", "Logs folder does not exist: {folder_path}")

    def open_latest_log(self):
        logs = list(get_logs_dir().glob("build_*.log"))
        if not logs:
            messagebox.showinfo(APP_TITLE, "No build logs found yet.")
            return
        latest = max(logs, key=lambda path: path.stat().st_mtime)
        try:
            if os.name == "nt":
                os.startfile(str(latest))
            else:
                subprocess.Popen(["xdg-open", str(latest)])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def clear_build_cache_from_ui(self):
        if self.is_building:
            messagebox.showwarning(APP_TITLE, "Cannot clear build cache while a build is running.")
            return
        source_root = self.source_root_var.get().strip()
        selected = self.get_selected_addon_names()
        if not source_root or not os.path.isdir(source_root):
            messagebox.showerror(APP_TITLE, "Project Source is empty or does not exist.")
            return
        if not selected:
            messagebox.showerror(APP_TITLE, "Select at least one addon whose cache should be cleared.")
            return
        cache = load_build_cache()
        key = os.path.abspath(source_root).lower()
        source_cache = cache.get(key, {})
        if not source_cache:
            messagebox.showinfo(APP_TITLE, "No build cache found for the selected source root.")
            return
        if not messagebox.askyesno(APP_TITLE, "Clear build cache for the selected addon(s)?\n\n" + "\n".join("- " + name for name in selected)):
            return
        cleared = 0
        for name in selected:
            if name in source_cache:
                del source_cache[name]
                cleared += 1
                self.log(f"Cleared build cache for addon: {name}")
        if source_cache:
            cache[key] = source_cache
        elif key in cache:
            del cache[key]
        save_build_cache(cache)
        messagebox.showinfo(APP_TITLE, f"Cleared {cleared} cache entry/entries.")


if __name__ == "__main__":
    app = RaGPboBuilderApp()
    app.mainloop()
