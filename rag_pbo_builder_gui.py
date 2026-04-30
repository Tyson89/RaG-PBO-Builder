"""
RaG PBO Builder

Graphite UI for building DayZ addon PBOs.

Features:
- Build selected addon folders into PBOs
- If source root contains config.cpp, build source root as one addon
- Optional P3D binarization with DayZ Tools binarize.exe
- Optional config.cpp to config.bin conversion with CfgConvert.exe, including nested config.cpp files
- Optional PBO signing with DSSignFile.exe
- Skip unchanged addons unless Force rebuild is enabled
- Force rebuild only refreshes temp folders for selected addons
- Preserves already-binarized P3D files if Binarize does not output them
- Output layout: Addons and Keys folders
- Copies matching .bikey into Keys after signing
- Preflight checks for config syntax, missing references, path casing, and readable internal P3D references
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
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "RaG PBO Builder"
APP_VERSION = "0.1.0 Beta"
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
APP_ICON_FILE = "HEADONLY_SQUARE_2k.ico"

EXCLUDE_DIRS = {".git", ".svn", ".vscode", ".idea", "__pycache__"}
EXCLUDE_FILES = {".gitignore", ".gitattributes", "thumbs.db", "desktop.ini", ".ds_store", "$prefix$"}
EXCLUDE_EXTENSIONS = {".delete"}

DEFAULT_TEMP_DIR = str(Path("P:/Temp"))
DEFAULT_PROJECT_ROOT = "P:"
DEFAULT_EXCLUDE_PATTERNS = "*.h,*.hpp,*.png,*.cpp,*.txt,thumbs.db,*.dep,*.bak,*.log,*.pew,source,*.tga,*.bat,*.psd,*.cmd,*.mcr,*.fbx,*.max"

GRAPHITE_BG = "#24262b"
GRAPHITE_CARD = "#2f3238"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT = "#a74747"
GRAPHITE_ACCENT_DARK = "#7f3434"
GRAPHITE_ERROR = "#ff7070"

ZERO = bytes([0])
WIN_SEP = chr(92)
COPY_CHUNK_SIZE = 1024 * 1024


class BuildError(Exception):
    pass


class ToolTip:
    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self.schedule)
        widget.bind("<Leave>", self.hide)
        widget.bind("<ButtonPress>", self.hide)

    def schedule(self, event=None):
        self.cancel()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self):
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        self.window.configure(bg=GRAPHITE_BORDER)
        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            wraplength=440,
        )
        label.pack(ipadx=1, ipady=1)

    def hide(self, event=None):
        self.cancel()
        if self.window:
            self.window.destroy()
            self.window = None


def add_tooltip(widget, text):
    if text:
        ToolTip(widget, text)


def get_initial_dir_from_value(value, fallback=""):
    value = value.strip() if value else ""
    fallback = fallback.strip() if fallback else ""
    if value:
        if os.path.isdir(value):
            return value
        parent = os.path.dirname(value)
        if parent and os.path.isdir(parent):
            return parent
    if fallback:
        if os.path.isdir(fallback):
            return fallback
        parent = os.path.dirname(fallback)
        if parent and os.path.isdir(parent):
            return parent
    return str(Path.home())


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_app_data_dir():
    local_appdata = os.environ.get("LOCALAPPDATA")
    base_dir = Path(local_appdata) if local_appdata else Path.home()
    app_dir = base_dir / "RaG_PBO_Builder"
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return get_logs_dir() / f"build_{stamp}.log"


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
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def safe_ascii(value, label):
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise BuildError(f"{label} contains non-ASCII characters: {value}")


def matches_exclude_pattern(name, patterns):
    if not patterns:
        return False
    value = name.lower()
    for pattern in patterns:
        test = pattern.strip().lower()
        if not test:
            continue
        if value == test:
            return True
        if fnmatch.fnmatch(value, test):
            return True
    return False


def should_skip_dir(dirname, extra_patterns=None):
    name = dirname.lower()
    if name in EXCLUDE_DIRS:
        return True
    if matches_exclude_pattern(name, extra_patterns):
        return True
    return False


def should_skip_file(filename, extra_patterns=None):
    name = filename.lower()
    # Never exclude config files by pattern. config.cpp must be converted; config.bin must be packed.
    if name in {"config.cpp", "config.bin"}:
        return False
    if name in EXCLUDE_FILES:
        return True
    if os.path.splitext(name)[1].lower() in EXCLUDE_EXTENSIONS:
        return True
    if matches_exclude_pattern(name, extra_patterns):
        return True
    return False


def parse_exclude_patterns(raw_patterns):
    if not raw_patterns:
        return []
    normalized = raw_patterns.replace(";", ",")
    normalized = normalized.replace(chr(13), "")
    normalized = normalized.replace(chr(10), ",")
    result = []
    for item in normalized.split(","):
        pattern = item.strip()
        if pattern:
            result.append(pattern)
    return result


def create_temp_exclude_file(temp_root, raw_patterns, log):
    # Do not generate an exclude.lst file. Exclude patterns are used internally by the Python builder only.
    patterns = parse_exclude_patterns(raw_patterns)
    if patterns:
        log("Using exclude patterns internally only. No generated exclude.lst will be created.")
    return ""


def has_p3d_files(source_dir, extra_patterns=None):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower().endswith(".p3d"):
                return True
    return False


def source_file_should_be_staged(filename, extra_patterns=None):
    # config.cpp must always be copied so CfgConvert can turn it into config.bin.
    if filename.lower() == "config.cpp":
        return True

    return not should_skip_file(filename, extra_patterns)


def files_are_same_for_staging(source_file, target_file):
    if not os.path.isfile(target_file):
        return False

    try:
        source_stat = os.stat(source_file)
        target_stat = os.stat(target_file)
    except OSError:
        return False

    # Size mismatch always means we need to update.
    if source_stat.st_size != target_stat.st_size:
        return False

    # If source is newer, update staging.
    # If target is newer or same age and same size, keep it.
    if source_stat.st_mtime_ns > target_stat.st_mtime_ns:
        return False

    return True


def copy_source_to_staging(source_dir, staging_dir, extra_patterns=None, log=None):
    source_dir = os.path.normpath(source_dir)
    staging_dir = os.path.normpath(staging_dir)

    os.makedirs(staging_dir, exist_ok=True)

    expected_rel_paths = set()
    copied = 0
    updated = 0
    unchanged = 0
    removed = 0

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]

        for file in files:
            if not source_file_should_be_staged(file, extra_patterns):
                continue

            source_file = os.path.join(root, file)
            rel_path = os.path.relpath(source_file, source_dir)
            rel_key = rel_path.replace(os.sep, WIN_SEP).lower()
            expected_rel_paths.add(rel_key)

            target_file = os.path.join(staging_dir, rel_path)

            if files_are_same_for_staging(source_file, target_file):
                unchanged += 1
                continue

            os.makedirs(os.path.dirname(target_file), exist_ok=True)

            existed = os.path.isfile(target_file)
            shutil.copy2(source_file, target_file)

            if existed:
                updated += 1
            else:
                copied += 1

    # Remove files from staging that no longer exist in source or are now excluded.
    # This also removes stale generated config.bin files, which are recreated later by CfgConvert.
    for root, dirs, files in os.walk(staging_dir, topdown=False):
        for file in files:
            staged_file = os.path.join(root, file)
            rel_path = os.path.relpath(staged_file, staging_dir)
            rel_key = rel_path.replace(os.sep, WIN_SEP).lower()

            if rel_key not in expected_rel_paths:
                os.remove(staged_file)
                removed += 1

        # Clean up empty folders, but keep the staging root itself.
        if root != staging_dir:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass

    if log:
        log(
            "Incremental staging: "
            f"copied={copied}, updated={updated}, unchanged={unchanged}, removed={removed}"
        )

def ensure_p3d_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    if not os.path.isdir(source_dir):
        log(f"WARNING: Source folder does not exist while ensuring P3Ds: {source_dir}")
        return
    if not os.path.isdir(staging_dir):
        os.makedirs(staging_dir, exist_ok=True)

    copied = 0
    already_present = 0
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not file.lower().endswith(".p3d"):
                continue
            source_p3d = os.path.join(root, file)
            rel_p3d = os.path.relpath(source_p3d, source_dir)
            target_p3d = os.path.join(staging_dir, rel_p3d)
            if os.path.isfile(target_p3d):
                already_present += 1
                continue
            os.makedirs(os.path.dirname(target_p3d), exist_ok=True)
            shutil.copy2(source_p3d, target_p3d)
            copied += 1
            rel_log = rel_p3d.replace(os.sep, WIN_SEP)
            log(f"Copied original P3D missing from Binarize output: {rel_log}")

    if copied:
        log(f"Copied {copied} original P3D file(s) that Binarize did not output.")
    else:
        log(f"All source P3D files are already present in staging ({already_present} checked).")


def ensure_config_cpp_files_in_staging(source_dir, staging_dir, log):
    if not os.path.isdir(source_dir):
        log(f"WARNING: Source folder does not exist while ensuring configs: {source_dir}")
        return
    if not os.path.isdir(staging_dir):
        os.makedirs(staging_dir, exist_ok=True)

    copied = 0
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for file in files:
            if file.lower() != "config.cpp":
                continue
            source_config = os.path.join(root, file)
            rel_config = os.path.relpath(source_config, source_dir)
            target_config = os.path.join(staging_dir, rel_config)
            os.makedirs(os.path.dirname(target_config), exist_ok=True)
            shutil.copy2(source_config, target_config)
            copied += 1
            rel_log = rel_config.replace(os.sep, WIN_SEP)
            log(f"Ensured config.cpp in staging: {rel_log}")

    if copied:
        log(f"Ensured {copied} config.cpp file(s) are present in staging.")
    else:
        log("No config.cpp files found while ensuring configs in staging.")


def overlay_tree(source_dir, destination_dir):
    if not os.path.isdir(source_dir):
        return
    for root, dirs, files in os.walk(source_dir):
        rel_root = os.path.relpath(root, source_dir)
        target_root = destination_dir if rel_root == "." else os.path.join(destination_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for file in files:
            source_file = os.path.join(root, file)
            target_file = os.path.join(target_root, file)
            shutil.copy2(source_file, target_file)


def normalize_project_root_arg(project_root):
    return project_root.rstrip(WIN_SEP + "/")


def normalize_working_dir(project_root):
    value = project_root.rstrip(WIN_SEP + "/")
    if len(value) == 2 and value[1] == ":":
        return value + WIN_SEP
    return value



def read_steam_registry_paths():
    paths = []

    if os.name != "nt":
        return paths

    try:
        import winreg
    except Exception:
        return paths

    registry_locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamExe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]

    for root_key, sub_key, value_name in registry_locations:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                value, value_type = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue

        if not value:
            continue

        path = Path(str(value).replace("/", os.sep))

        # SteamExe points to steam.exe, SteamPath / InstallPath point to the Steam folder.
        if path.suffix.lower() == ".exe":
            path = path.parent

        if path.is_dir():
            paths.append(path)

    return paths


def parse_steam_vdf_quoted_values(line):
    # Supports basic Steam VDF lines like:
    # "path" "D:\\SteamLibrary"
    # "1"    "D:\\SteamLibrary"
    return re.findall(r'"([^"]*)"', line)


def read_steam_library_paths():
    steam_roots = []

    # Environment variable support for users with custom setups.
    for env_name in ["STEAM_DIR", "STEAM_PATH"]:
        env_value = os.environ.get(env_name)
        if env_value and Path(env_value).is_dir():
            steam_roots.append(Path(env_value))

    steam_roots.extend(read_steam_registry_paths())

    # Last-resort common Steam locations. These are only checked if they exist.
    steam_roots.extend([
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam",
    ])

    library_paths = []
    seen = set()

    def add_library(path):
        normalized = Path(str(path).replace("\\\\", "\\")).expanduser()
        try:
            normalized = normalized.resolve()
        except Exception:
            normalized = Path(os.path.normpath(str(normalized)))

        key = str(normalized).lower()
        if key not in seen and normalized.is_dir():
            seen.add(key)
            library_paths.append(normalized)

    for steam_root in steam_roots:
        add_library(steam_root)

        libraryfolders = steam_root / "steamapps" / "libraryfolders.vdf"
        if not libraryfolders.is_file():
            continue

        try:
            lines = libraryfolders.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for line in lines:
            values = parse_steam_vdf_quoted_values(line)
            if len(values) < 2:
                continue

            key = values[0].lower()
            value = values[1]

            # New format:
            # "path" "D:\\SteamLibrary"
            if key == "path":
                add_library(value)
                continue

            # Old format:
            # "1" "D:\\SteamLibrary"
            if key.isdigit() and value:
                add_library(value)

    return library_paths


def get_steam_common_paths():
    common_paths = []

    for library_path in read_steam_library_paths():
        common_path = library_path / "steamapps" / "common"
        if common_path.is_dir():
            common_paths.append(common_path)

    return common_paths


def read_steam_app_install_dir_from_manifest(library_path, appid):
    manifest = library_path / "steamapps" / f"appmanifest_{appid}.acf"

    if not manifest.is_file():
        return ""

    try:
        content = manifest.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    match = re.search(r'"installdir"\s+"([^"]+)"', content, re.IGNORECASE)
    if not match:
        return ""

    install_dir = match.group(1).strip()
    if not install_dir:
        return ""

    app_path = library_path / "steamapps" / "common" / install_dir
    return str(app_path) if app_path.is_dir() else ""


def find_steam_app_install_dir(appid, fallback_folder_names=None):
    fallback_folder_names = fallback_folder_names or []

    for library_path in read_steam_library_paths():
        app_path = read_steam_app_install_dir_from_manifest(library_path, appid)
        if app_path:
            return app_path

    for common_path in get_steam_common_paths():
        for folder_name in fallback_folder_names:
            candidate = common_path / folder_name
            if candidate.is_dir():
                return str(candidate)

    return ""


def find_dayz_tools_root():
    # DayZ Tools is normally installed as:
    # <SteamLibrary>\steamapps\common\DayZ Tools
    # Steam libraryfolders.vdf support allows installs on D:, E:, external drives, etc.
    return find_steam_app_install_dir("223350", ["DayZ Tools"])


def find_dayz_install_dir():
    # Useful for future features or diagnostics. DayZ game appid is 221100.
    return find_steam_app_install_dir("221100", ["DayZ"])


def find_exe_in_dayz_tools(relative_paths):
    dayz_tools_root = find_dayz_tools_root()

    if dayz_tools_root:
        for relative_path in relative_paths:
            candidate = Path(dayz_tools_root) / relative_path
            if candidate.is_file():
                return str(candidate)

    # Fallback: scan all Steam common folders directly in case the manifest is missing.
    for common_path in get_steam_common_paths():
        tools_root = common_path / "DayZ Tools"
        if not tools_root.is_dir():
            continue

        for relative_path in relative_paths:
            candidate = tools_root / relative_path
            if candidate.is_file():
                return str(candidate)

    return ""


def find_dayz_binarize():
    found = find_exe_in_dayz_tools([
        Path("Bin") / "Binarize" / "binarize.exe",
    ])

    if found:
        return found

    possible_paths = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe",
        Path("C:/Program Files (x86)/Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"),
        Path("C:/Program Files/Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"),
    ]

    for path in possible_paths:
        if path.is_file():
            return str(path)

    return ""


def find_cfgconvert():
    found = find_exe_in_dayz_tools([
        Path("Bin") / "CfgConvert" / "CfgConvert.exe",
    ])

    if found:
        return found

    possible_paths = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe",
        Path("C:/Program Files (x86)/Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe"),
        Path("C:/Program Files/Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe"),
    ]

    for path in possible_paths:
        if path.is_file():
            return str(path)

    return ""


def find_dssignfile():
    found = find_exe_in_dayz_tools([
        Path("Bin") / "DSUtils" / "DSSignFile.exe",
        Path("Bin") / "DSSignFile" / "DSSignFile.exe",
    ])

    if found:
        return found

    possible_paths = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe",
        Path("C:/Program Files (x86)/Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe"),
        Path("C:/Program Files/Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe"),
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe",
    ]

    for path in possible_paths:
        if path.is_file():
            return str(path)

    return ""


def get_signature_pattern_for_pbo(pbo_path):
    return pbo_path + ".*.bisign"


def find_new_signature_for_pbo(pbo_path):
    signatures = glob.glob(get_signature_pattern_for_pbo(pbo_path))
    if not signatures:
        return ""
    signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return signatures[0]


def remove_old_signatures(pbo_path, log):
    old_signatures = glob.glob(get_signature_pattern_for_pbo(pbo_path))
    for signature in old_signatures:
        try:
            os.remove(signature)
            log(f"Removed old signature: {signature}")
        except Exception as e:
            raise BuildError(f"Could not remove old signature: {signature} ({e})")


def clean_output_for_pbo(pbo_path, log):
    if os.path.isfile(pbo_path):
        os.remove(pbo_path)
        log(f"Removed old PBO: {pbo_path}")
    remove_old_signatures(pbo_path, log)


def wait_for_file_ready(file_path, log, timeout_seconds=10):
    start_time = time.time()
    last_size = -1
    stable_hits = 0
    log(f"Waiting for file to be ready: {file_path}")
    while time.time() - start_time < timeout_seconds:
        if os.path.isfile(file_path):
            try:
                current_size = os.path.getsize(file_path)
                if current_size > 0 and current_size == last_size:
                    stable_hits += 1
                else:
                    stable_hits = 0
                if stable_hits >= 2:
                    log(f"File ready: {file_path} ({current_size} bytes)")
                    return
                last_size = current_size
            except OSError:
                stable_hits = 0
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
    if matches:
        matches.sort(key=lambda path: path.name.lower())
        return str(matches[0])
    return ""


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
    if not os.path.isfile(pbo_path):
        raise BuildError(f"PBO does not exist and cannot be signed: {pbo_path}")

    remove_old_signatures(pbo_path, log)
    original_pbo_dir = os.path.dirname(os.path.abspath(pbo_path))
    pbo_name = os.path.basename(pbo_path)
    key_name = os.path.basename(private_key)
    signing_root = get_app_data_dir() / "signing_temp"
    if signing_root.exists():
        shutil.rmtree(signing_root)
    signing_root.mkdir(parents=True, exist_ok=True)
    work_pbo = signing_root / pbo_name
    work_key = signing_root / key_name
    shutil.copy2(pbo_path, work_pbo)
    shutil.copy2(private_key, work_key)
    cmd = [dssignfile_exe, key_name, pbo_name]

    log("")
    log("Signing PBO in clean temp folder:")
    log(f"  Original PBO: {pbo_path}")
    log(f"  Work folder:  {signing_root}")
    log(f"  Tool:         {dssignfile_exe}")
    log("")

    result = subprocess.run(
        cmd,
        cwd=str(signing_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=get_subprocess_creationflags(),
        startupinfo=get_hidden_startupinfo(),
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            log(line)
    else:
        log("DSSignFile returned no output.")

    work_signatures = glob.glob(str(work_pbo) + ".*.bisign")
    work_signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    if result.returncode != 0:
        raise BuildError(f"DSSignFile failed with exit code {result.returncode}: {pbo_path}")
    if not work_signatures:
        raise BuildError(f"DSSignFile finished but no .bisign was created for: {pbo_path}")
    work_signature = work_signatures[0]
    final_signature = os.path.join(original_pbo_dir, os.path.basename(work_signature))
    shutil.copy2(work_signature, final_signature)
    if not os.path.isfile(final_signature):
        raise BuildError(f"Could not copy signature back to output folder: {final_signature}")
    log(f"Created signature: {final_signature}")


def run_dayz_binarize(source_dir, binarized_output_dir, binarize_exe, project_root, temp_dir, max_processes, exclude_file, log):
    if os.path.exists(binarized_output_dir):
        shutil.rmtree(binarized_output_dir)
    os.makedirs(binarized_output_dir, exist_ok=True)

    project_root_arg = normalize_project_root_arg(project_root)
    working_dir = normalize_working_dir(project_root)
    binpath = str(Path(binarize_exe).parent)
    source_name = os.path.basename(os.path.normpath(source_dir)) or "addon"
    texture_temp_dir = os.path.join(temp_dir, "addons", get_safe_temp_name(source_name), "textures")
    if os.path.isdir(texture_temp_dir):
        shutil.rmtree(texture_temp_dir)
    os.makedirs(texture_temp_dir, exist_ok=True)

    cmd = [
        binarize_exe,
        "-targetBonesInterval=56",
        f"-maxProcesses={max_processes}",
        "-always",
        "-silent",
        f"-addon={project_root_arg}",
        f"-textures={texture_temp_dir}",
        f"-binpath={binpath}",
    ]
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

    result = subprocess.run(
        cmd,
        cwd=working_dir if os.path.isdir(working_dir) else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=get_subprocess_creationflags(),
        startupinfo=get_hidden_startupinfo(),
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            log(line)
    if result.returncode != 0:
        raise BuildError(f"Binarize failed with exit code {result.returncode}: {source_dir}")


def run_cfgconvert_to_bin(staging_dir, cfgconvert_exe, log):
    if not os.path.isdir(staging_dir):
        raise BuildError(f"Staging folder does not exist: {staging_dir}")
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise BuildError("CfgConvert.exe not found. Select the DayZ Tools CfgConvert.exe path.")

    config_files = []
    for root, dirs, files in os.walk(staging_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for file in files:
            if file.lower() == "config.cpp":
                config_files.append(os.path.join(root, file))
    if not config_files:
        log("No config.cpp found. Skipping CPP to BIN.")
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
        result = subprocess.run(
            cmd,
            cwd=config_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=get_subprocess_creationflags(),
            startupinfo=get_hidden_startupinfo(),
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                log(line)
        if result.returncode != 0 or not os.path.isfile(config_bin):
            raise BuildError(f"CfgConvert failed with exit code {result.returncode}: {config_cpp}")
        os.remove(config_cpp)
        log(f"Removed source config.cpp from staging: {rel_config}")


def clear_temp_folder(temp_root, log):
    temp_root = os.path.normpath(temp_root)
    if not temp_root:
        raise BuildError("Temp dir is empty. Refusing to clear it.")
    root_path = Path(temp_root)
    if len(str(root_path)) < 5:
        raise BuildError(f"Temp dir path is too short. Refusing to clear it: {temp_root}")
    if root_path.exists():
        log(f"Clearing temp folder: {temp_root}")
        for item in root_path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        root_path.mkdir(parents=True, exist_ok=True)
        log(f"Created temp folder: {temp_root}")


def pack_pbo(source_dir, output_path, prefix, log, extra_patterns=None):
    source_dir = os.path.normpath(source_dir)
    output_path = os.path.normpath(output_path)
    if not os.path.isdir(source_dir):
        raise BuildError(f"Source is not a directory: {source_dir}")
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    files = []
    for root, dirs, filenames in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in filenames:
            if should_skip_file(fname, extra_patterns):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, source_dir).replace(os.sep, WIN_SEP)
            size = os.path.getsize(full)
            files.append((rel, full, size))
    files.sort(key=lambda x: x[0].lower())

    header = bytearray()
    header.extend(ZERO)
    header.extend(struct.pack("<I", 0x56657273))
    header.extend(struct.pack("<I", 0))
    header.extend(struct.pack("<I", 0))
    header.extend(struct.pack("<I", 0))
    header.extend(struct.pack("<I", 0))
    if prefix:
        header.extend(b"prefix")
        header.extend(ZERO)
        header.extend(safe_ascii(prefix, "PBO prefix"))
        header.extend(ZERO)
    header.extend(ZERO)
    for rel, full, size in files:
        header.extend(safe_ascii(rel, "File path"))
        header.extend(ZERO)
        header.extend(struct.pack("<I", 0))
        header.extend(struct.pack("<I", size))
        header.extend(struct.pack("<I", 0))
        header.extend(struct.pack("<I", 0))
        header.extend(struct.pack("<I", size))
    header.extend(ZERO)
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))

    temp_output_path = output_path + ".tmp"
    sha = hashlib.sha1()
    total_bytes = 0
    try:
        with open(temp_output_path, "wb") as out:
            out.write(header)
            sha.update(header)
            total_bytes += len(header)
            for rel, full, size in files:
                with open(full, "rb") as f:
                    while True:
                        chunk = f.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        sha.update(chunk)
                        total_bytes += len(chunk)
            digest = sha.digest()
            out.write(ZERO)
            out.write(digest)
            total_bytes += 1 + len(digest)
        os.replace(temp_output_path, output_path)
    except Exception:
        if os.path.isfile(temp_output_path):
            try:
                os.remove(temp_output_path)
            except Exception:
                pass
        raise
    log(f"Packed {len(files):4d} files / {total_bytes:,} bytes -> {output_path}")


def get_safe_temp_name(name):
    safe = name.strip() if name else "addon"
    safe = safe.replace("/", "_").replace(WIN_SEP, "_").replace(":", "_")
    return safe or "addon"


def get_addon_temp_root(temp_root, addon_name):
    return os.path.join(temp_root, "addons", get_safe_temp_name(addon_name))


def get_pbo_base_name(folder_name, pbo_name, selected_count):
    clean_name = pbo_name.strip() if pbo_name else ""
    if clean_name and selected_count == 1:
        clean_name = clean_name.replace(".pbo", "")
        clean_name = clean_name.replace("/", "_").replace(WIN_SEP, "_")
        return clean_name
    return folder_name


def get_pbo_prefix(pbo_base_name):
    return pbo_base_name


def get_single_addon_target(source_root):
    normalized_root = os.path.normpath(source_root)
    folder_name = os.path.basename(normalized_root)
    if not folder_name:
        folder_name = "addon"
    return [(folder_name, normalized_root)]


def collect_subfolders(source_root, output_addons_dir):
    source_root = os.path.normpath(source_root)
    output_addons_dir = os.path.normpath(output_addons_dir)
    result = []
    for name in os.listdir(source_root):
        full = os.path.join(source_root, name)
        if not os.path.isdir(full):
            continue
        if should_skip_dir(name):
            continue
        if name.lower() in {"output", "addons", "keys"}:
            continue
        try:
            full_abs = os.path.abspath(full)
            output_abs = os.path.abspath(output_addons_dir)
            if full_abs == output_abs or output_abs.startswith(full_abs + os.sep):
                continue
        except Exception:
            pass
        result.append((name, full))
    result.sort(key=lambda x: x[0].lower())
    return result


def detect_addon_targets(source_root, output_addons_dir):
    if not os.path.isdir(source_root):
        return []
    root_config_cpp = os.path.isfile(os.path.join(source_root, "config.cpp"))
    if root_config_cpp:
        return get_single_addon_target(source_root)
    return collect_subfolders(source_root, output_addons_dir)


def compute_addon_state_hash(source_dir, prefix, settings, extra_patterns=None):
    digest = hashlib.sha1()
    tracked_settings = {
        "prefix": prefix,
        "pbo_name": settings.get("pbo_name", ""),
        "use_binarize": bool(settings["use_binarize"]),
        "convert_config": bool(settings["convert_config"]),
        "sign_pbos": bool(settings["sign_pbos"]),
        "project_root": settings["project_root"],
        "exclude_patterns": settings["exclude_patterns"],
        "max_processes": settings["max_processes"],
    }
    private_key = settings.get("private_key", "")
    if settings.get("sign_pbos") and os.path.isfile(private_key):
        try:
            tracked_settings["private_key"] = private_key
            tracked_settings["private_key_size"] = os.path.getsize(private_key)
            tracked_settings["private_key_mtime_ns"] = os.stat(private_key).st_mtime_ns
        except OSError:
            pass
    digest.update(json.dumps(tracked_settings, sort_keys=True).encode("utf-8"))
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
    return digest.hexdigest()


def format_duration(seconds):
    seconds = int(seconds)
    minutes = seconds // 60
    remaining = seconds % 60
    return f"{minutes:02d}:{remaining:02d}"


REFERENCE_EXTENSIONS = (
    ".paa", ".rvmat", ".p3d", ".wss", ".ogg", ".cfg", ".cpp", ".hpp", ".h", ".emat", ".edds", ".ptc",
)
PREFLIGHT_TEXT_EXTENSIONS = (
    ".cpp", ".hpp", ".h", ".rvmat", ".cfg", ".c", ".xml", ".json",
)
REFERENCE_REGEX = re.compile(
    r"[\"']([^\"']+\.(?:paa|rvmat|p3d|wss|ogg|cfg|cpp|hpp|h|emat|edds|ptc))[\"']",
    re.IGNORECASE,
)
P3D_INTERNAL_REFERENCE_REGEX = re.compile(
    rb"([A-Za-z0-9_@#$%&()\-+={}\[\],.;: /\\]+\.(?:paa|rvmat|p3d|emat|edds|ptc))",
    re.IGNORECASE,
)


class PreflightResult:
    def __init__(self):
        self.errors = 0
        self.warnings = 0
        self.checked_files = 0
        self.checked_references = 0

    def error(self, log, message):
        self.errors += 1
        log("ERROR: " + message)

    def warning(self, log, message):
        self.warnings += 1
        log("WARNING: " + message)


def normalize_reference_path(reference):
    value = reference.strip().strip('"').strip("'")
    value = value.replace("/", WIN_SEP)
    while value.startswith(WIN_SEP):
        value = value[1:]
    return value


def find_case_mismatch(path):
    normalized = os.path.normpath(path)
    drive, rest = os.path.splitdrive(normalized)
    if not rest:
        return ""
    parts = [part for part in rest.replace("/", WIN_SEP).split(WIN_SEP) if part]
    current = drive + WIN_SEP if drive else (WIN_SEP if normalized.startswith(WIN_SEP) else "")
    for part in parts:
        parent = current if current else "."
        try:
            entries = os.listdir(parent)
        except Exception:
            return ""
        exact = part in entries
        lower_match = ""
        if not exact:
            part_lower = part.lower()
            for entry in entries:
                if entry.lower() == part_lower:
                    lower_match = entry
                    break
        if lower_match:
            return f"expected '{lower_match}', referenced '{part}' in {normalized}"
        current = os.path.join(current, part) if current else part
    return ""


def resolve_reference_path(reference, addon_source_dir, project_root):
    ref = normalize_reference_path(reference)
    if not ref:
        return "", "missing"
    candidates = []
    if os.path.isabs(ref):
        candidates.append(ref)
    addon_source_dir = os.path.normpath(addon_source_dir)
    addon_parent = os.path.dirname(addon_source_dir)
    candidates.append(os.path.join(addon_source_dir, ref))
    candidates.append(os.path.join(addon_parent, ref))
    if project_root:
        project_root_normalized = normalize_working_dir(project_root)
        candidates.append(os.path.join(project_root_normalized, ref))
    seen = set()
    for candidate in candidates:
        candidate = os.path.normpath(candidate)
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(candidate):
            mismatch = find_case_mismatch(candidate)
            if mismatch:
                return candidate, "case_mismatch:" + mismatch
            return candidate, "ok"
    return candidates[0] if candidates else ref, "missing"


def iter_preflight_text_files(source_dir):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in PREFLIGHT_TEXT_EXTENSIONS:
                yield os.path.join(root, file)


def iter_p3d_files(source_dir):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for file in files:
            if file.lower().endswith(".p3d"):
                yield os.path.join(root, file)


def collect_config_cpp_files(source_dir):
    configs = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for file in files:
            if file.lower() == "config.cpp":
                configs.append(os.path.join(root, file))
    configs.sort(key=lambda path: os.path.relpath(path, source_dir).lower())
    return configs


def preflight_check_config_cpp(config_cpp, cfgconvert_exe, temp_root, addon_name, result, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        result.warning(log, "CfgConvert.exe is not configured. Skipping config.cpp syntax check.")
        return
    rel_name = os.path.basename(config_cpp)
    safe_addon = get_safe_temp_name(addon_name)
    check_dir = os.path.join(temp_root, "preflight", safe_addon)
    os.makedirs(check_dir, exist_ok=True)
    output_bin = os.path.join(check_dir, rel_name + ".bin")
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
        output = completed.stdout.strip() if completed.stdout else "No CfgConvert output."
        result.error(log, f"Config syntax check failed: {config_cpp}")
        for line in output.splitlines():
            log("  " + line)
        return
    log(f"Config syntax OK: {config_cpp}")


def preflight_scan_references(file_path, addon_source_dir, project_root, result, log):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
            content = file.read()
    except Exception as e:
        result.warning(log, f"Could not read file for reference scan: {file_path} ({e})")
        return
    result.checked_files += 1
    rel_file = os.path.relpath(file_path, addon_source_dir).replace(os.sep, WIN_SEP)
    seen_refs = set()
    for match in REFERENCE_REGEX.finditer(content):
        reference = match.group(1).strip()
        normalized_ref = normalize_reference_path(reference)
        ref_key = normalized_ref.lower()
        if ref_key in seen_refs:
            continue
        seen_refs.add(ref_key)
        result.checked_references += 1
        resolved, status = resolve_reference_path(normalized_ref, addon_source_dir, project_root)
        if status == "missing":
            result.error(log, f"Missing referenced file in {rel_file}: {normalized_ref}")
        elif status.startswith("case_mismatch:"):
            detail = status.split(":", 1)[1]
            result.warning(log, f"Path casing mismatch in {rel_file}: {normalized_ref} ({detail})")


def preflight_scan_p3d_internal_references(p3d_file, addon_source_dir, project_root, result, log):
    rel_file = os.path.relpath(p3d_file, addon_source_dir).replace(os.sep, WIN_SEP)
    try:
        with open(p3d_file, "rb") as file:
            data = file.read()
    except Exception as e:
        result.warning(log, f"Could not read P3D for internal reference scan: {rel_file} ({e})")
        return
    result.checked_files += 1
    seen_refs = set()
    found_refs = 0
    for match in P3D_INTERNAL_REFERENCE_REGEX.finditer(data):
        raw_reference = match.group(1)
        try:
            reference = raw_reference.decode("ascii", errors="ignore").strip()
        except Exception:
            continue
        normalized_ref = normalize_reference_path(reference)
        ref_key = normalized_ref.lower()
        if not normalized_ref or ref_key in seen_refs:
            continue
        if len(normalized_ref) < 5:
            continue
        seen_refs.add(ref_key)
        found_refs += 1
        result.checked_references += 1
        resolved, status = resolve_reference_path(normalized_ref, addon_source_dir, project_root)
        if status == "missing":
            result.warning(log, f"Missing internal P3D reference in {rel_file}: {normalized_ref}")
        elif status.startswith("case_mismatch:"):
            detail = status.split(":", 1)[1]
            result.warning(log, f"Internal P3D path casing mismatch in {rel_file}: {normalized_ref} ({detail})")
    if found_refs:
        log(f"P3D internal scan checked {found_refs} reference(s): {rel_file}")
    else:
        log(f"P3D internal scan found no readable references: {rel_file}")


def run_preflight_for_targets(settings, targets, log, progress_callback=None):
    start_time = time.time()
    result = PreflightResult()
    cfgconvert_exe = settings.get("cfgconvert_exe", "")
    temp_root = settings.get("temp_dir", DEFAULT_TEMP_DIR)
    project_root = settings.get("project_root", DEFAULT_PROJECT_ROOT)
    log("")
    log("=" * 80)
    log("Preflight Check")
    log("=" * 80)
    for index, (addon_name, addon_source_dir) in enumerate(targets, start=1):
        if progress_callback:
            progress_callback(index - 1, len(targets))
        log("")
        log(f"Checking addon {index}/{len(targets)}: {addon_name}")
        config_files = collect_config_cpp_files(addon_source_dir)
        if config_files:
            log(f"Found {len(config_files)} config.cpp file(s).")
            for config_cpp in config_files:
                preflight_check_config_cpp(config_cpp, cfgconvert_exe, temp_root, addon_name, result, log)
        else:
            result.warning(log, f"No config.cpp found in addon source: {addon_source_dir}")
        for text_file in iter_preflight_text_files(addon_source_dir):
            preflight_scan_references(text_file, addon_source_dir, project_root, result, log)
        for p3d_file in iter_p3d_files(addon_source_dir):
            preflight_scan_p3d_internal_references(p3d_file, addon_source_dir, project_root, result, log)
    if progress_callback:
        progress_callback(len(targets), len(targets))
    elapsed = time.time() - start_time
    log("")
    log("=" * 80)
    log("Preflight summary")
    log("=" * 80)
    log(f"Addons:             {len(targets)}")
    log(f"Scanned files:      {result.checked_files}")
    log(f"Checked references: {result.checked_references}")
    log(f"Errors:             {result.errors}")
    log(f"Warnings:           {result.warnings}")
    log(f"Time:               {format_duration(elapsed)}")
    log("=" * 80)
    return result


def build_all(settings, log, progress_callback):
    start_time = time.time()
    source_root = os.path.normpath(settings["source_root"])
    output_root_dir = os.path.normpath(settings["output_root_dir"])
    output_addons_dir = os.path.join(output_root_dir, "Addons")
    output_keys_dir = os.path.join(output_root_dir, "Keys")
    temp_root = os.path.normpath(settings["temp_dir"])
    if not os.path.isdir(source_root):
        raise BuildError(f"Source root is not a directory: {source_root}")
    os.makedirs(output_addons_dir, exist_ok=True)
    os.makedirs(output_keys_dir, exist_ok=True)
    os.makedirs(temp_root, exist_ok=True)

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

    log(f"Output root:   {output_root_dir}")
    log(f"Output Addons: {output_addons_dir}")
    log(f"Output Keys:   {output_keys_dir}")
    if force_rebuild:
        log(f"Force rebuild enabled. Only selected addon temp folders will be refreshed: {temp_root}")
    else:
        log(f"Force rebuild disabled. Keeping existing temp folder contents: {temp_root}")

    if use_binarize:
        if not binarize_exe or not os.path.isfile(binarize_exe):
            raise BuildError("binarize.exe not found. Select the DayZ Tools binarize.exe path.")
        log(f"Using binarize.exe: {binarize_exe}")
        exclude_file = create_temp_exclude_file(temp_root, exclude_patterns, log)
        if exclude_file:
            log(f"Using generated exclude file: {exclude_file}")
        else:
            log("No exclude file will be passed to Binarize.")

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
        log(f"Using private key: {private_key}")

    all_targets = detect_addon_targets(source_root, output_addons_dir)
    targets = [(name, path) for name, path in all_targets if name in selected_addons] if selected_addons else []
    if not targets:
        raise BuildError("No addon targets selected.")
    log(f"Found {len(all_targets)} addon target(s). Selected {len(targets)} for build.")

    if preflight_before_build:
        log("Preflight before build enabled. Running checks before packing.")
        preflight_result = run_preflight_for_targets(settings, targets, log, progress_callback)
        if preflight_result.errors > 0:
            raise BuildError(f"Preflight failed with {preflight_result.errors} error(s). Build aborted.")
        if preflight_result.warnings > 0:
            log(f"Preflight completed with {preflight_result.warnings} warning(s). Continuing build.")
        else:
            log("Preflight completed without errors or warnings. Continuing build.")

    if force_rebuild:
        log("Force rebuild enabled. Cache will be ignored for selected addons.")

    cache = load_build_cache()
    cache_key_root = os.path.abspath(source_root).lower()
    source_cache = cache.setdefault(cache_key_root, {})
    summary = {
        "built": 0,
        "skipped": 0,
        "signed": 0,
        "failed": 0,
        "keys_copied": 0,
        "targets": len(targets),
        "log_file": settings.get("log_file", ""),
    }
    build_jobs = []

    for index, (folder_name, folder_path) in enumerate(targets, start=1):
        progress_callback(index - 1, len(targets))
        log("")
        log("=" * 80)
        log(f"Preparing addon {index}/{len(targets)}: {folder_name}")
        log("=" * 80)
        pbo_base_name = get_pbo_base_name(folder_name, pbo_name, len(targets))
        output_pbo = os.path.join(output_addons_dir, pbo_base_name + ".pbo")
        prefix = get_pbo_prefix(pbo_base_name)
        state_hash = compute_addon_state_hash(folder_path, prefix, settings, exclude_pattern_list)
        cache_entry = source_cache.get(folder_name, {})
        signature_exists = bool(find_new_signature_for_pbo(output_pbo))
        can_skip = (
            not force_rebuild
            and cache_entry.get("hash") == state_hash
            and os.path.isfile(output_pbo)
            and (not sign_pbos or signature_exists)
        )
        if can_skip:
            log(f"Skipping {folder_name} - no changes detected.")
            summary["skipped"] += 1
            continue

        clean_output_for_pbo(output_pbo, log)
        addon_temp_root = get_addon_temp_root(temp_root, folder_name)
        if force_rebuild:
            for temp_subfolder in ["staging", "binarized", "textures", "configs"]:
                selected_temp_path = os.path.join(addon_temp_root, temp_subfolder)
                if os.path.isdir(selected_temp_path):
                    shutil.rmtree(selected_temp_path)
                    log(f"Force rebuild: removed selected addon temp folder only: {selected_temp_path}")

        pack_source = folder_path
        folder_has_p3d = use_binarize and has_p3d_files(folder_path, exclude_pattern_list)
        needs_staging = convert_config or folder_has_p3d
        staging_dir = ""
        binarized_dir = ""
        if needs_staging:
            staging_dir = os.path.join(addon_temp_root, "staging")
            log("Copying source to staging folder...")
            copy_source_to_staging(folder_path, staging_dir, exclude_pattern_list, log)
            ensure_config_cpp_files_in_staging(folder_path, staging_dir, log)
            pack_source = staging_dir
        if folder_has_p3d:
            binarized_dir = os.path.join(addon_temp_root, "binarized")
        elif use_binarize:
            log("No P3D files found. Skipping P3D binarize for this addon.")
        build_jobs.append({
            "folder_name": folder_name,
            "folder_path": folder_path,
            "output_pbo": output_pbo,
            "prefix": prefix,
            "pack_source": pack_source,
            "folder_has_p3d": folder_has_p3d,
            "staging_dir": staging_dir,
            "binarized_dir": binarized_dir,
            "state_hash": state_hash,
        })

    for build_index, job in enumerate(build_jobs, start=1):
        progress_callback(build_index - 1, len(build_jobs))
        folder_name = job["folder_name"]
        log("")
        log("=" * 80)
        log(f"Packing addon {build_index}/{len(build_jobs)}: {folder_name}")
        log("=" * 80)
        try:
            if use_binarize and job["folder_has_p3d"]:
                run_dayz_binarize(
                    source_dir=job["folder_path"],
                    binarized_output_dir=job["binarized_dir"],
                    binarize_exe=binarize_exe,
                    project_root=project_root,
                    temp_dir=temp_root,
                    max_processes=max_processes,
                    exclude_file=exclude_file,
                    log=log,
                )
                log("Overlaying binarized files onto staging folder...")
                overlay_tree(job["binarized_dir"], job["staging_dir"])
                ensure_p3d_files_in_staging(job["folder_path"], job["staging_dir"], log, exclude_pattern_list)

            if convert_config:
                ensure_config_cpp_files_in_staging(job["folder_path"], job["pack_source"], log)
                run_cfgconvert_to_bin(job["pack_source"], cfgconvert_exe, log)

            log(f"PBO name:   {os.path.basename(job['output_pbo'])}")
            log(f"PBO prefix: {job['prefix']}")
            pack_pbo(job["pack_source"], job["output_pbo"], job["prefix"], log, exclude_pattern_list)
            summary["built"] += 1
            if sign_pbos:
                wait_for_file_ready(job["output_pbo"], log)
                run_dssignfile(dssignfile_exe, private_key, job["output_pbo"], log)
                summary["signed"] += 1
                copied_key = copy_bikey_to_keys(private_key, output_keys_dir, log)
                if copied_key:
                    summary["keys_copied"] += 1
            source_cache[folder_name] = {
                "hash": job["state_hash"],
                "pbo": job["output_pbo"],
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            save_build_cache(cache)
        except Exception:
            summary["failed"] += 1
            raise

    progress_callback(len(targets), len(targets))
    save_build_cache(cache)
    elapsed = time.time() - start_time
    summary["elapsed"] = elapsed
    log("")
    log("=" * 80)
    log("Build summary")
    log("=" * 80)
    log(f"Targets:     {summary['targets']}")
    log(f"Built:       {summary['built']}")
    log(f"Skipped:     {summary['skipped']}")
    log(f"Signed:      {summary['signed']}")
    log(f"Keys copied: {summary['keys_copied']}")
    log(f"Failed:      {summary['failed']}")
    log(f"Time:        {format_duration(elapsed)}")
    if summary.get("log_file"):
        log(f"Log:         {summary['log_file']}")
    log("=" * 80)
    log("")
    log("Build finished.")
    return summary


class RaGPboBuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.set_window_icon()
        self.geometry("1080x900")
        self.minsize(960, 830)
        self._apply_graphite_theme()

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.is_building = False
        self.current_log_file = None
        self.current_log_path = ""
        self.current_addon_targets = []
        self.status_var = tk.StringVar(value="Status: Idle")
        self.saved_settings = load_saved_settings()

        saved_pbo_name = self.saved_settings.get("pbo_name", self.saved_settings.get("prefix_root", ""))
        saved_output_root = self.saved_settings.get("output_root", self.saved_settings.get("output_addons", ""))
        self.source_root_var = tk.StringVar(value=self.saved_settings.get("source_root", ""))
        self.output_root_var = tk.StringVar(value=saved_output_root)
        self.pbo_name_var = tk.StringVar(value=saved_pbo_name)
        self.use_binarize_var = tk.BooleanVar(value=self.saved_settings.get("use_binarize", True))
        self.convert_config_var = tk.BooleanVar(value=self.saved_settings.get("convert_config", True))
        self.sign_pbos_var = tk.BooleanVar(value=self.saved_settings.get("sign_pbos", True))
        self.force_rebuild_var = tk.BooleanVar(value=self.saved_settings.get("force_rebuild", False))
        self.preflight_before_build_var = tk.BooleanVar(value=self.saved_settings.get("preflight_before_build", False))
        self.max_processes_var = tk.IntVar(value=self.saved_settings.get("max_processes", 16))
        self.binarize_exe_var = tk.StringVar(value=self.saved_settings.get("binarize_exe", find_dayz_binarize()))
        self.cfgconvert_exe_var = tk.StringVar(value=self.saved_settings.get("cfgconvert_exe", find_cfgconvert()))
        self.dssignfile_exe_var = tk.StringVar(value=self.saved_settings.get("dssignfile_exe", find_dssignfile()))
        self.private_key_var = tk.StringVar(value=self.saved_settings.get("private_key", ""))
        self.project_root_var = tk.StringVar(value=self.saved_settings.get("project_root", DEFAULT_PROJECT_ROOT))
        self.temp_dir_var = tk.StringVar(value=self.saved_settings.get("temp_dir", DEFAULT_TEMP_DIR))
        self.exclude_patterns_var = tk.StringVar(value=self.saved_settings.get("exclude_patterns", DEFAULT_EXCLUDE_PATTERNS))

        self._build_ui()
        self.refresh_addon_list(select_saved=True)
        self._poll_log_queue()

    def set_window_icon(self):
        icon_path = resource_path(APP_ICON_FILE)
        if not os.path.isfile(icon_path):
            return
        try:
            self.iconbitmap(icon_path)
        except Exception:
            try:
                icon_image = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, icon_image)
            except Exception:
                pass

    def _apply_graphite_theme(self):
        self.configure(bg=GRAPHITE_BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT, fieldbackground=GRAPHITE_FIELD, font=("Segoe UI", 10))
        style.configure("TFrame", background=GRAPHITE_BG)
        style.configure("TLabelframe", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BG, lightcolor=GRAPHITE_CARD, darkcolor=GRAPHITE_BG, relief="flat", padding=18)
        style.configure("TLabelframe.Label", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT)
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=5)
        style.configure("TButton", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_CARD_SOFT, lightcolor=GRAPHITE_CARD_SOFT, darkcolor=GRAPHITE_CARD_SOFT, focusthickness=0, focuscolor=GRAPHITE_CARD_SOFT, relief="flat", padding=(12, 8))
        style.configure("TEntry", fieldbackground=GRAPHITE_FIELD, background=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, insertcolor=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, lightcolor=GRAPHITE_FIELD, darkcolor=GRAPHITE_FIELD, relief="flat", padding=7)
        style.configure("TSpinbox", fieldbackground=GRAPHITE_FIELD, background=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, insertcolor=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, lightcolor=GRAPHITE_FIELD, darkcolor=GRAPHITE_FIELD, relief="flat", padding=6)
        style.configure("Horizontal.TProgressbar", background=GRAPHITE_ACCENT, troughcolor=GRAPHITE_CARD, bordercolor=GRAPHITE_CARD, lightcolor=GRAPHITE_ACCENT, darkcolor=GRAPHITE_ACCENT_DARK)
        style.configure("Vertical.TScrollbar", background=GRAPHITE_CARD_SOFT, troughcolor=GRAPHITE_BG, bordercolor=GRAPHITE_BG, arrowcolor=GRAPHITE_MUTED, relief="flat")
        style.map("TButton", background=[("active", GRAPHITE_BORDER), ("pressed", GRAPHITE_ACCENT_DARK)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.map("TEntry", fieldbackground=[("readonly", GRAPHITE_FIELD), ("disabled", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.map("TSpinbox", fieldbackground=[("readonly", GRAPHITE_FIELD), ("disabled", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED)])

    def _build_ui(self):
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        title = ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 19, "bold"))
        title.pack(side="left")
        subtitle = ttk.Label(header, text="Build selected DayZ addons into Addons and Keys output folders", foreground=GRAPHITE_MUTED)
        subtitle.pack(side="left", padx=(14, 0), pady=(8, 0))
        self.about_button = tk.Button(
            header,
            text="About",
            command=self.open_about_window,
            bg=GRAPHITE_CARD_SOFT,
            fg=GRAPHITE_TEXT,
            activebackground=GRAPHITE_BORDER,
            activeforeground=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            font=("Segoe UI", 10),
            cursor="hand2",
        )
        self.about_button.pack(side="right")
        add_tooltip(self.about_button, "Show version, author, and safety information.")

        self.licence_button = tk.Button(
            header,
            text="Licence",
            command=self.open_licence_window,
            bg=GRAPHITE_CARD_SOFT,
            fg=GRAPHITE_TEXT,
            activebackground=GRAPHITE_BORDER,
            activeforeground=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            font=("Segoe UI", 10),
            cursor="hand2",
        )
        self.licence_button.pack(side="right", padx=(0, 8))
        add_tooltip(self.licence_button, "Show licence terms and warranty disclaimer.")

        settings = ttk.LabelFrame(outer, text="Build settings", padding=18)
        settings.pack(fill="x", pady=(0, 14))
        self._add_folder_row(settings, 0, "Source root", self.source_root_var, self.choose_source_root, "Folder containing addon folders. If this folder itself contains config.cpp, it will be built as one addon.")
        self._add_folder_row(settings, 1, "Output root", self.output_root_var, self.choose_output_root, "Root output folder. The builder creates Addons and Keys inside this folder automatically.")
        label = ttk.Label(settings, text="PBO name")
        label.grid(row=2, column=0, sticky="w", pady=5)
        add_tooltip(label, "Optional PBO filename override. Only used when exactly one addon is selected.")
        entry = ttk.Entry(settings, textvariable=self.pbo_name_var)
        entry.grid(row=2, column=1, sticky="ew", pady=5, padx=(8, 8))
        add_tooltip(entry, "Leave empty to use the selected addon folder name. Only applies to single-addon builds.")
        hint = ttk.Label(settings, text="Optional, single-addon builds only", foreground=GRAPHITE_MUTED)
        hint.grid(row=2, column=2, sticky="w", pady=5)
        add_tooltip(hint, "For multi-addon builds, each PBO always uses its addon folder name.")
        settings.columnconfigure(1, weight=1)

        options_frame = ttk.LabelFrame(outer, text="Build options", padding=18)
        options_frame.pack(fill="x", pady=(0, 14))
        self._add_checkbutton(options_frame, "Binarize P3D", self.use_binarize_var, 0, 0, "Run DayZ Tools binarize.exe before packing addons that contain P3D files.")
        self._add_checkbutton(options_frame, "CPP to BIN", self.convert_config_var, 0, 1, "Convert root and nested config.cpp files to config.bin in staging before packing.")
        self._add_checkbutton(options_frame, "Sign PBOs", self.sign_pbos_var, 0, 2, "Sign built PBOs with DSSignFile.exe and your .biprivatekey.")
        self._add_checkbutton(options_frame, "Force rebuild", self.force_rebuild_var, 0, 3, "Ignore the build cache and rebuild all selected addons.")
        self._add_checkbutton(options_frame, "Preflight before build", self.preflight_before_build_var, 0, 4, "Run syntax and path checks before building. Errors stop the build; warnings only get logged.")
        max_frame = ttk.Frame(options_frame)
        max_frame.grid(row=0, column=5, sticky="w", pady=(0, 8), padx=(8, 0))
        label = ttk.Label(max_frame, text="Max processes")
        label.pack(side="left")
        add_tooltip(label, "Passed to binarize.exe as maxProcesses.")
        spinbox = ttk.Spinbox(max_frame, from_=1, to=64, textvariable=self.max_processes_var, width=8)
        spinbox.pack(side="left", padx=(8, 0))
        add_tooltip(spinbox, "How many worker processes Binarize may use.")
        options_frame.columnconfigure(6, weight=1)

        addons_frame = ttk.LabelFrame(outer, text="Addon selection", padding=18)
        addons_frame.pack(fill="both", expand=True, pady=(0, 14))
        addons_frame.columnconfigure(0, weight=1)
        addons_frame.rowconfigure(0, weight=1)
        self.addon_listbox = tk.Listbox(
            addons_frame,
            selectmode="extended",
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            selectbackground="#6f2f2f",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=GRAPHITE_BORDER,
            highlightcolor=GRAPHITE_ACCENT,
            font=("Consolas", 10),
            height=6,
            exportselection=False,
        )
        self.addon_listbox.grid(row=0, column=0, sticky="nsew")
        add_tooltip(self.addon_listbox, "Select which addons to build. Hold Ctrl or Shift to select multiple entries.")
        addon_scrollbar = ttk.Scrollbar(addons_frame, command=self.addon_listbox.yview)
        addon_scrollbar.grid(row=0, column=1, sticky="ns")
        self.addon_listbox.configure(yscrollcommand=addon_scrollbar.set)
        self.addon_listbox.bind("<<ListboxSelect>>", lambda event: self.save_path_settings())
        addon_buttons = ttk.Frame(addons_frame)
        addon_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        refresh_button = ttk.Button(addon_buttons, text="Refresh addons", command=self.refresh_addon_list)
        refresh_button.pack(side="left")
        add_tooltip(refresh_button, "Refresh the addon list from the selected Source root.")
        select_all_button = ttk.Button(addon_buttons, text="Select all", command=self.select_all_addons)
        select_all_button.pack(side="left", padx=(8, 0))
        add_tooltip(select_all_button, "Select all detected addons for the next build.")
        select_none_button = ttk.Button(addon_buttons, text="Select none", command=self.select_no_addons)
        select_none_button.pack(side="left", padx=(8, 0))
        add_tooltip(select_none_button, "Clear the addon selection.")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        primary_actions = ttk.Frame(actions)
        primary_actions.pack(fill="x")
        secondary_actions = ttk.Frame(actions)
        secondary_actions.pack(fill="x", pady=(8, 0))
        self.build_button = self._make_action_button(primary_actions, "Build PBOs", self.start_build, primary=True, tooltip="Build the currently selected addon(s).")
        self.preflight_button = self._make_action_button(primary_actions, "Preflight", self.start_preflight, tooltip="Check selected addon(s) for config syntax errors and missing referenced files before packing.")
        self.options_button = self._make_action_button(primary_actions, "Options", self.open_options_window, tooltip="Open tool paths, temp folder, project root, private key, and exclude pattern settings.")
        self.status_label = ttk.Label(primary_actions, textvariable=self.status_var, foreground=GRAPHITE_MUTED, width=32)
        self.status_label.pack(side="left", padx=(14, 0))
        add_tooltip(self.status_label, "Current builder status.")
        self.progress = ttk.Progressbar(primary_actions, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.clear_button = self._make_action_button(secondary_actions, "Clear log", self.clear_log, tooltip="Clear the visible log window only. Saved log files are not deleted.")
        self.clear_temp_button = self._make_action_button(secondary_actions, "Clear temp", self.clear_temp_from_ui, tooltip="Manually clear the full selected temp folder after confirmation.")
        self.open_output_button = self._make_action_button(secondary_actions, "Open output", self.open_output_folder, tooltip="Open the selected output root folder in Windows Explorer.")
        self.open_logs_button = self._make_action_button(secondary_actions, "Open logs", self.open_logs_folder, tooltip="Open the folder containing saved build logs.")
        self.latest_log_button = self._make_action_button(secondary_actions, "Latest log", self.open_latest_log, tooltip="Open the newest saved build log file.")
        self.clear_cache_button = self._make_action_button(secondary_actions, "Clear cache", self.clear_build_cache_from_ui, tooltip="Clear build-cache entries only for the selected source root and selected addon(s).")

        log_frame = ttk.LabelFrame(outer, text="Log", padding=18)
        log_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.log_text = tk.Text(log_frame, wrap="word", height=24, font=("Consolas", 9), bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT)
        self.log_text.pack(side="left", fill="both", expand=True)
        add_tooltip(self.log_text, "Build output, Binarize output, signing output, warnings, and errors.")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.version_footer = tk.Label(
            self,
            text=f"v{APP_VERSION}",
            bg=GRAPHITE_BG,
            fg=GRAPHITE_MUTED,
            font=("Segoe UI", 9),
        )
        self.version_footer.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)
        add_tooltip(self.version_footer, "Version information.")

    def _add_checkbutton(self, parent, text, variable, row, column, tooltip):
        def refresh_toggle():
            if variable.get():
                checkbox.configure(
                    text="✓ " + text,
                    bg=GRAPHITE_CARD_SOFT,
                    fg=GRAPHITE_TEXT,
                    activebackground=GRAPHITE_BORDER,
                    activeforeground=GRAPHITE_TEXT,
                )
            else:
                checkbox.configure(
                    text="  " + text,
                    bg=GRAPHITE_FIELD,
                    fg=GRAPHITE_MUTED,
                    activebackground=GRAPHITE_CARD_SOFT,
                    activeforeground=GRAPHITE_TEXT,
                )

        def on_toggle():
            refresh_toggle()
            self.save_path_settings()

        checkbox = tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=on_toggle,
            indicatoron=False,
            selectcolor=GRAPHITE_CARD_SOFT,
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=7,
            font=("Segoe UI", 10),
            cursor="hand2",
            anchor="center",
        )
        checkbox.grid(row=row, column=column, sticky="w", pady=(0, 8), padx=(0, 10))
        refresh_toggle()
        add_tooltip(checkbox, tooltip)
        return checkbox

    def _make_action_button(self, parent, text, command, primary=False, tooltip=""):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=GRAPHITE_ACCENT_DARK if primary else GRAPHITE_CARD_SOFT,
            fg="#ffffff" if primary else GRAPHITE_TEXT,
            activebackground=GRAPHITE_ACCENT if primary else GRAPHITE_BORDER,
            activeforeground="#ffffff" if primary else GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=8,
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            cursor="hand2",
        )
        button.pack(side="left", padx=(0 if primary else 8, 0))
        add_tooltip(button, tooltip)
        return button

    def _add_folder_row(self, parent, row, label, variable, command, tooltip=""):
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", pady=5)
        add_tooltip(label_widget, tooltip)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(8, 8))
        add_tooltip(entry, tooltip)
        button = ttk.Button(parent, text="Browse", command=command)
        button.grid(row=row, column=2, sticky="e", pady=5)
        add_tooltip(button, tooltip)

    def _add_file_row(self, parent, row, label, variable, command, tooltip=""):
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", pady=5)
        add_tooltip(label_widget, tooltip)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(8, 8))
        add_tooltip(entry, tooltip)
        button = ttk.Button(parent, text="Browse", command=command)
        button.grid(row=row, column=2, sticky="e", pady=5)
        add_tooltip(button, tooltip)

    def open_licence_window(self):
        licence = tk.Toplevel(self)
        licence.title("Licence")
        licence.geometry("720x560")
        licence.minsize(600, 420)
        licence.configure(bg=GRAPHITE_BG)
        licence.transient(self)
        licence.grab_set()

        container = ttk.Frame(licence, padding=18)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text="Licence", font=("Segoe UI", 20, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(container, text=APP_LICENSE_NAME, foreground=GRAPHITE_MUTED)
        subtitle.pack(anchor="w", pady=(6, 14))

        text = tk.Text(
            container,
            wrap="word",
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_ACCENT_DARK,
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=GRAPHITE_BORDER,
            highlightcolor=GRAPHITE_ACCENT,
            font=("Segoe UI", 10),
        )
        text.pack(side="left", fill="both", expand=True, pady=(0, 12))
        text.insert("1.0", APP_LICENSE_TEXT)
        text.configure(state="disabled")

        scrollbar = ttk.Scrollbar(container, command=text.yview)
        scrollbar.pack(side="right", fill="y", pady=(0, 12))
        text.configure(yscrollcommand=scrollbar.set)

        close_button = tk.Button(
            container,
            text="Close",
            command=licence.destroy,
            bg=GRAPHITE_CARD_SOFT,
            fg=GRAPHITE_TEXT,
            activebackground=GRAPHITE_BORDER,
            activeforeground=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=8,
            font=("Segoe UI", 10),
            cursor="hand2",
        )
        close_button.pack(anchor="e")

    def open_about_window(self):
        about = tk.Toplevel(self)
        about.title("About")
        about.geometry("520x360")
        about.minsize(480, 320)
        about.configure(bg=GRAPHITE_BG)
        about.transient(self)
        about.grab_set()
        container = ttk.Frame(about, padding=18)
        container.pack(fill="both", expand=True)
        title = ttk.Label(container, text=APP_TITLE, font=("Segoe UI", 20, "bold"))
        title.pack(anchor="w")
        version = ttk.Label(container, text=f"Version: {APP_VERSION}", foreground=GRAPHITE_MUTED)
        version.pack(anchor="w", pady=(6, 0))
        author = ttk.Label(container, text=f"Author: {APP_AUTHOR}", foreground=GRAPHITE_MUTED)
        author.pack(anchor="w", pady=(2, 14))
        info_text = (
            "DayZ PBO build helper for packing, binarizing, signing, validating, and preparing addon output folders."
            + chr(10) + chr(10)
            + f"Licence: {APP_LICENSE_NAME}" + chr(10)
            + "Copyright © 2026 RaG Tyson" + chr(10) + chr(10)
            + "Important:" + chr(10)
            + "- Never share your .biprivatekey." + chr(10)
            + "- Only distribute the matching .bikey." + chr(10)
            + "- Always check generated PBOs before release." + chr(10) + chr(10)
            + "This tool is provided as-is without warranty."
        )
        text = tk.Text(container, height=9, wrap="word", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        text.pack(fill="both", expand=True, pady=(0, 12))
        text.insert("1.0", info_text)
        text.configure(state="disabled")
        close_button = tk.Button(container, text="Close", command=about.destroy, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2")
        close_button.pack(anchor="e")

    def open_options_window(self):
        options = tk.Toplevel(self)
        options.title("Options")
        options.geometry("900x540")
        options.minsize(760, 480)
        options.configure(bg=GRAPHITE_BG)
        options.transient(self)
        options.grab_set()
        container = ttk.Frame(options, padding=16)
        container.pack(fill="both", expand=True)
        title = ttk.Label(container, text="Options", font=("Segoe UI", 17, "bold"))
        title.pack(anchor="w", pady=(0, 12))
        options_frame = ttk.LabelFrame(container, text="Tool paths and build settings", padding=14)
        options_frame.pack(fill="both", expand=True)
        options_frame.columnconfigure(1, weight=1)
        self._add_file_row(options_frame, 0, "binarize.exe", self.binarize_exe_var, self.choose_binarize_exe, "Path to DayZ Tools binarize.exe.")
        self._add_file_row(options_frame, 1, "CfgConvert.exe", self.cfgconvert_exe_var, self.choose_cfgconvert_exe, "Path to DayZ Tools CfgConvert.exe.")
        self._add_file_row(options_frame, 2, "DSSignFile.exe", self.dssignfile_exe_var, self.choose_dssignfile_exe, "Path to DayZ Tools DSSignFile.exe.")
        self._add_file_row(options_frame, 3, "Private key", self.private_key_var, self.choose_private_key, "Your .biprivatekey. Never distribute this file.")
        self._add_folder_row(options_frame, 4, "Project root", self.project_root_var, self.choose_project_root, "Usually P: or your DayZ project drive root.")
        self._add_folder_row(options_frame, 5, "Temp dir", self.temp_dir_var, self.choose_temp_dir, "Temporary staging folder. Manual Clear temp deletes this folder.")
        ttk.Label(options_frame, text="Exclude patterns").grid(row=6, column=0, sticky="nw", pady=5)
        exclude_entry = tk.Text(options_frame, height=5, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        exclude_entry.grid(row=6, column=1, columnspan=2, sticky="nsew", pady=5, padx=(8, 0))
        exclude_entry.insert("1.0", self.exclude_patterns_var.get())
        add_tooltip(exclude_entry, "Comma, semicolon, or newline separated exclude patterns. Used internally by the builder.")
        options_frame.rowconfigure(6, weight=1)
        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(12, 0))

        def save_and_close():
            self.exclude_patterns_var.set(exclude_entry.get("1.0", "end").strip())
            self.save_path_settings()
            options.destroy()

        save_button = tk.Button(buttons, text="Save", command=save_and_close, bg=GRAPHITE_ACCENT_DARK, fg="#ffffff", activebackground=GRAPHITE_ACCENT, activeforeground="#ffffff", relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        save_button.pack(side="right")
        cancel_button = tk.Button(buttons, text="Cancel", command=options.destroy, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2")
        cancel_button.pack(side="right", padx=(0, 8))

    def get_selected_addon_names(self):
        selected = []
        for index in self.addon_listbox.curselection():
            selected.append(self.addon_listbox.get(index))
        return selected

    def refresh_addon_list(self, select_saved=False):
        source_root = self.source_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        output_addons_dir = os.path.join(output_root, "Addons") if output_root else ""
        previous_selection = set(self.get_selected_addon_names())
        saved_selection = set(self.saved_settings.get("selected_addons", [])) if select_saved else set()
        self.addon_listbox.delete(0, "end")
        self.current_addon_targets = []
        if not source_root or not os.path.isdir(source_root):
            return
        self.current_addon_targets = detect_addon_targets(source_root, output_addons_dir)
        for name, path in self.current_addon_targets:
            self.addon_listbox.insert("end", name)
        names = [name for name, path in self.current_addon_targets]
        if saved_selection:
            selection = saved_selection
        elif previous_selection:
            selection = previous_selection
        else:
            selection = set(names)
        for index, name in enumerate(names):
            if name in selection:
                self.addon_listbox.selection_set(index)
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
            max_processes = 16
        data = {
            "source_root": self.source_root_var.get().strip(),
            "output_root": self.output_root_var.get().strip(),
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
            "selected_addons": self.get_selected_addon_names() if hasattr(self, "addon_listbox") else [],
        }
        save_saved_settings(data)

    def choose_source_root(self):
        path = filedialog.askdirectory(title="Select source root", initialdir=get_initial_dir_from_value(self.source_root_var.get(), self.output_root_var.get()))
        if path:
            self.source_root_var.set(path)
            self.refresh_addon_list()
            self.save_path_settings()

    def choose_output_root(self):
        path = filedialog.askdirectory(title="Select output root folder", initialdir=get_initial_dir_from_value(self.output_root_var.get(), self.source_root_var.get()))
        if path:
            self.output_root_var.set(path)
            self.refresh_addon_list()
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
            raise BuildError("Select a source root folder.")
        if not os.path.isdir(source_root):
            raise BuildError(f"Source root does not exist: {source_root}")
        selected_addons = self.get_selected_addon_names()
        if not selected_addons:
            raise BuildError("Select at least one addon to check.")
        selected_set = set(selected_addons)
        targets = [(name, path) for name, path in self.current_addon_targets if name in selected_set]
        if not targets:
            raise BuildError("No selected addon targets found.")
        settings = {
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "project_root": self.project_root_var.get().strip() or DEFAULT_PROJECT_ROOT,
            "temp_dir": self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR,
        }
        self.save_path_settings()
        return settings, targets

    def start_preflight(self):
        if self.is_building:
            return
        try:
            settings, targets = self.validate_preflight_settings()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.current_log_path = str(create_build_log_path())
        if self.current_log_path:
            Path(self.current_log_path).parent.mkdir(parents=True, exist_ok=True)
            self.current_log_file = open(self.current_log_path, "w", encoding="utf-8")
        self.is_building = True
        self.build_button.configure(state="disabled")
        self.preflight_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.status_var.set("Status: Preflight running...")
        self.log("Starting preflight check...")
        if self.current_log_path:
            self.log(f"Log file: {self.current_log_path}")
        self.worker_thread = threading.Thread(target=self._preflight_worker, args=(settings, targets), daemon=True)
        self.worker_thread.start()

    def _preflight_worker(self, settings, targets):
        try:
            result = run_preflight_for_targets(settings, targets, self.thread_log, self.thread_progress)
            self.log_queue.put(("preflight_done", (result.errors, result.warnings)))
        except Exception as e:
            self.log_queue.put(("error", str(e)))

    def validate_settings(self):
        self.refresh_addon_list()
        source_root = self.source_root_var.get().strip()
        output_root = self.output_root_var.get().strip()
        if not source_root:
            raise BuildError("Select a source root folder.")
        if not os.path.isdir(source_root):
            raise BuildError(f"Source root does not exist: {source_root}")
        if not output_root:
            raise BuildError("Select an output root folder.")
        selected_addons = self.get_selected_addon_names()
        if not selected_addons:
            raise BuildError("Select at least one addon to build.")
        if self.pbo_name_var.get().strip() and len(selected_addons) > 1:
            raise BuildError("PBO name override can only be used when exactly one addon is selected.")
        if self.use_binarize_var.get():
            binarize_exe = self.binarize_exe_var.get().strip()
            if not binarize_exe:
                raise BuildError("Select binarize.exe or disable P3D binarize.")
            if not os.path.isfile(binarize_exe):
                raise BuildError(f"binarize.exe does not exist: {binarize_exe}")
        if self.convert_config_var.get():
            cfgconvert_exe = self.cfgconvert_exe_var.get().strip()
            if not cfgconvert_exe:
                raise BuildError("Select CfgConvert.exe or disable CPP to BIN.")
            if not os.path.isfile(cfgconvert_exe):
                raise BuildError(f"CfgConvert.exe does not exist: {cfgconvert_exe}")
        if self.sign_pbos_var.get():
            dssignfile_exe = self.dssignfile_exe_var.get().strip()
            private_key = self.private_key_var.get().strip()
            if not dssignfile_exe:
                raise BuildError("Select DSSignFile.exe or disable Sign PBOs.")
            if not os.path.isfile(dssignfile_exe):
                raise BuildError(f"DSSignFile.exe does not exist: {dssignfile_exe}")
            if not private_key:
                raise BuildError("Select a .biprivatekey file or disable Sign PBOs.")
            if not os.path.isfile(private_key):
                raise BuildError(f"Private key does not exist: {private_key}")
        try:
            max_processes = int(self.max_processes_var.get())
        except Exception:
            max_processes = 16
        if max_processes < 1:
            max_processes = 1
        log_path = str(create_build_log_path())
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
            "selected_addons": selected_addons,
            "log_file": log_path,
        }
        self.save_path_settings()
        return settings

    def start_build(self):
        if self.is_building:
            return
        try:
            settings = self.validate_settings()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.current_log_path = settings.get("log_file", "")
        if self.current_log_path:
            Path(self.current_log_path).parent.mkdir(parents=True, exist_ok=True)
            self.current_log_file = open(self.current_log_path, "w", encoding="utf-8")
        self.is_building = True
        self.build_button.configure(state="disabled")
        self.preflight_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.status_var.set("Status: Build running...")
        self.log("Starting build...")
        if self.current_log_path:
            self.log(f"Log file: {self.current_log_path}")
        self.worker_thread = threading.Thread(target=self._build_worker, args=(settings,), daemon=True)
        self.worker_thread.start()

    def _build_worker(self, settings):
        try:
            build_all(settings, self.thread_log, self.thread_progress)
            self.log_queue.put(("done", None))
        except Exception as e:
            self.log_queue.put(("error", str(e)))

    def thread_log(self, message):
        self.log_queue.put(("log", message))

    def thread_progress(self, current, total):
        self.log_queue.put(("progress", (current, total)))

    def _poll_log_queue(self):
        try:
            while True:
                item_type, payload = self.log_queue.get_nowait()
                if item_type == "log":
                    self.log(payload)
                elif item_type == "progress":
                    current, total = payload
                    maximum = max(total, 1)
                    self.progress.configure(maximum=maximum, value=current)
                    self.status_var.set(f"Status: Working... {current}/{maximum}")
                elif item_type == "done":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self.status_var.set("Status: Build finished")
                    self.close_current_log_file()
                    messagebox.showinfo(APP_TITLE, "Build finished.")
                elif item_type == "preflight_done":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self.status_var.set("Status: Preflight finished")
                    self.close_current_log_file()
                    errors, warnings = payload
                    if errors:
                        messagebox.showerror(APP_TITLE, f"Preflight finished with {errors} error(s) and {warnings} warning(s).")
                    elif warnings:
                        messagebox.showwarning(APP_TITLE, f"Preflight finished with {warnings} warning(s).")
                    else:
                        messagebox.showinfo(APP_TITLE, "Preflight finished without errors or warnings.")
                elif item_type == "error":
                    self.is_building = False
                    self.build_button.configure(state="normal")
                    self.preflight_button.configure(state="normal")
                    self.log("")
                    self.log(f"ERROR: {payload}")
                    self.status_var.set("Status: Error")
                    self.close_current_log_file()
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def log(self, message):
        line = str(message)
        self.log_text.insert("end", line + chr(10))
        self.log_text.see("end")
        self.update_idletasks()
        try:
            print(line, flush=True)
        except Exception:
            pass
        if self.current_log_file:
            try:
                self.current_log_file.write(line + chr(10))
                self.current_log_file.flush()
            except Exception:
                pass

    def close_current_log_file(self):
        if self.current_log_file:
            try:
                self.current_log_file.close()
            except Exception:
                pass
            self.current_log_file = None

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def clear_temp_from_ui(self):
        if self.is_building:
            messagebox.showwarning(APP_TITLE, "Cannot clear temp folder while a build is running.")
            return
        temp_dir = self.temp_dir_var.get().strip() or DEFAULT_TEMP_DIR
        if not temp_dir:
            messagebox.showerror(APP_TITLE, "Temp dir is empty.")
            return
        confirm_message = "Clear this temp folder?" + chr(10) + chr(10) + temp_dir
        confirm = messagebox.askyesno(APP_TITLE, confirm_message)
        if not confirm:
            return
        try:
            clear_temp_folder(temp_dir, self.log)
            messagebox.showinfo(APP_TITLE, "Temp folder cleared.")
        except Exception as e:
            self.log("")
            self.log(f"ERROR: {e}")
            messagebox.showerror(APP_TITLE, str(e))

    def open_output_folder(self):
        output_root = self.output_root_var.get().strip()
        if not output_root:
            messagebox.showerror(APP_TITLE, "Output root folder is empty.")
            return
        if not os.path.isdir(output_root):
            messagebox.showerror(APP_TITLE, f"Output root folder does not exist: {output_root}")
            return
        try:
            if os.name == "nt":
                os.startfile(output_root)
            else:
                subprocess.Popen(["xdg-open", output_root])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def open_logs_folder(self):
        logs_dir = get_logs_dir()
        try:
            if os.name == "nt":
                os.startfile(str(logs_dir))
            else:
                subprocess.Popen(["xdg-open", str(logs_dir)])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def open_latest_log(self):
        logs_dir = get_logs_dir()
        log_files = list(logs_dir.glob("build_*.log"))
        if not log_files:
            messagebox.showinfo(APP_TITLE, "No build logs found yet.")
            return
        latest_log = max(log_files, key=lambda path: path.stat().st_mtime)
        try:
            if os.name == "nt":
                os.startfile(str(latest_log))
            else:
                subprocess.Popen(["xdg-open", str(latest_log)])
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def clear_build_cache_from_ui(self):
        if self.is_building:
            messagebox.showwarning(APP_TITLE, "Cannot clear build cache while a build is running.")
            return
        source_root = self.source_root_var.get().strip()
        selected_addons = self.get_selected_addon_names()
        if not source_root:
            messagebox.showerror(APP_TITLE, "Source root is empty.")
            return
        if not os.path.isdir(source_root):
            messagebox.showerror(APP_TITLE, f"Source root does not exist: {source_root}")
            return
        if not selected_addons:
            messagebox.showerror(APP_TITLE, "Select at least one addon whose cache should be cleared.")
            return
        cache = load_build_cache()
        cache_key_root = os.path.abspath(source_root).lower()
        source_cache = cache.get(cache_key_root, {})
        if not source_cache:
            self.log(f"No build cache found for source root: {source_root}")
            messagebox.showinfo(APP_TITLE, "No build cache found for the selected source root.")
            return
        selected_text = chr(10).join([f"- {name}" for name in selected_addons])
        confirm_message = "Clear build cache for the selected addon(s)?" + chr(10) + chr(10) + "Source root: " + source_root + chr(10) + chr(10) + selected_text
        confirm = messagebox.askyesno(APP_TITLE, confirm_message)
        if not confirm:
            return
        cleared = 0
        for addon_name in selected_addons:
            if addon_name in source_cache:
                del source_cache[addon_name]
                cleared += 1
                self.log(f"Cleared build cache for addon: {addon_name}")
            else:
                self.log(f"No cache entry for addon: {addon_name}")
        if source_cache:
            cache[cache_key_root] = source_cache
        elif cache_key_root in cache:
            del cache[cache_key_root]
        save_build_cache(cache)
        messagebox.showinfo(APP_TITLE, f"Cleared {cleared} cache entrie(s).")


if __name__ == "__main__":
    app = RaGPboBuilderApp()
    app.mainloop()
