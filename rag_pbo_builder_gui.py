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

import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from rag_build_pipeline import (
    build_all,
    clear_full_temp_folder,
    clear_temp_folder,
    detect_addon_targets,
    find_cfgconvert,
    find_dayz_binarize,
    find_dssignfile,
    find_imagetopaa,
    get_default_max_processes,
)
from rag_builder_common import (
    BuildError,
    WIN_SEP,
    parse_exclude_patterns,
)
from rag_builder_storage import (
    create_build_log_path,
    get_logs_dir,
    load_build_cache,
    load_saved_settings,
    resource_path,
    save_build_cache,
    save_saved_settings,
)
from rag_preflight import run_preflight_for_targets
from rag_version import APP_VERSION

APP_TITLE = "RaG PBO Builder"
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
        self.update_paa_from_sources_var = tk.BooleanVar(value=self.saved_settings.get("update_paa_from_sources", False))
        self.sign_pbos_var = tk.BooleanVar(value=self.saved_settings.get("sign_pbos", True))
        self.force_rebuild_var = tk.BooleanVar(value=self.saved_settings.get("force_rebuild", False))
        self.preflight_before_build_var = tk.BooleanVar(value=self.saved_settings.get("preflight_before_build", False))
        self.max_processes_var = tk.IntVar(value=self.saved_settings.get("max_processes", get_default_max_processes()))
        self.binarize_exe_var = tk.StringVar(value=self.saved_settings.get("binarize_exe", find_dayz_binarize()))
        self.cfgconvert_exe_var = tk.StringVar(value=self.saved_settings.get("cfgconvert_exe", find_cfgconvert()))
        self.imagetopaa_exe_var = tk.StringVar(value=self.saved_settings.get("imagetopaa_exe", find_imagetopaa()))
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
        self.preflight_check_script_checks_var = tk.BooleanVar(value=self.saved_settings.get("preflight_check_script_checks", True))
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
        self._add_checkbutton(options, "Binarize P3D", self.use_binarize_var, 0, 1, "Run DayZ Tools binarize.exe before packing addons that contain P3D files. Already-binarized (ODOL) P3Ds are skipped and packed as-is to avoid double binarization.")
        self._add_checkbutton(options, "CPP to BIN", self.convert_config_var, 0, 2, "Convert root and nested config.cpp files to config.bin in staging before packing.")
        self._add_checkbutton(options, "Sign PBOs", self.sign_pbos_var, 0, 3, "Sign built PBOs with DSSignFile.exe and your .biprivatekey.")
        self._add_checkbutton(options, "Update PAA", self.update_paa_from_sources_var, 0, 4, "Use ImageToPAA.exe to update missing or stale staged .paa files from newer .png/.tga source textures. Source files are not overwritten.")
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
        window.geometry("940x720")
        window.minsize(820, 560)
        window.configure(bg=GRAPHITE_BG)
        window.transient(self)
        window.grab_set()
        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Options", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 12))

        scroll_shell = ttk.Frame(outer)
        scroll_shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_shell, bg=GRAPHITE_BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(scroll_shell, orient="vertical", command=canvas.yview)
        container = ttk.Frame(canvas)
        container_id = canvas.create_window((0, 0), window=container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_scroll_width(event):
            canvas.itemconfigure(container_id, width=event.width)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def close_options_window():
            window.unbind_all("<MouseWheel>")
            window.destroy()

        container.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_scroll_width)
        canvas.bind("<Enter>", lambda event: window.bind_all("<MouseWheel>", on_mousewheel))
        canvas.bind("<Leave>", lambda event: window.unbind_all("<MouseWheel>"))
        window.protocol("WM_DELETE_WINDOW", close_options_window)

        frame = ttk.LabelFrame(container, text="Tool paths and build settings", padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        self._add_file_row(frame, 0, "binarize.exe", self.binarize_exe_var, self.choose_binarize_exe, "Path to DayZ Tools binarize.exe.")
        self._add_file_row(frame, 1, "CfgConvert.exe", self.cfgconvert_exe_var, self.choose_cfgconvert_exe, "Path to DayZ Tools CfgConvert.exe.")
        self._add_file_row(frame, 2, "ImageToPAA.exe", self.imagetopaa_exe_var, self.choose_imagetopaa_exe, "Path to DayZ Tools ImageToPAA.exe.")
        self._add_file_row(frame, 3, "DSSignFile.exe", self.dssignfile_exe_var, self.choose_dssignfile_exe, "Path to DayZ Tools DSSignFile.exe.")
        self._add_file_row(frame, 4, "Private key", self.private_key_var, self.choose_private_key, "Your .biprivatekey. Never distribute this file.")
        self._add_folder_row(frame, 5, "Project root", self.project_root_var, self.choose_project_root, "Usually P: or your DayZ project drive root.")
        self._add_folder_row(frame, 6, "Temp dir", self.temp_dir_var, self.choose_temp_dir, "Temporary staging folder.")
        ttk.Label(frame, text="Exclude patterns").grid(row=7, column=0, sticky="nw", pady=5)
        exclude_entry = tk.Text(frame, height=5, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Segoe UI", 10))
        exclude_entry.grid(row=7, column=1, columnspan=2, sticky="nsew", pady=5, padx=(8, 0))
        exclude_entry.insert("1.0", self.exclude_patterns_var.get())
        frame.rowconfigure(7, weight=1)

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
        self._add_checkbutton(
            preflight_frame,
            "Script checks",
            self.preflight_check_script_checks_var,
            1,
            2,
            "Warn about bad modded class inheritance, duplicate script classes, missing super.SetActions(), and obvious script syntax issues.",
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

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        def save_and_close():
            self.exclude_patterns_var.set(exclude_entry.get("1.0", "end").strip())
            self.save_path_settings()
            close_options_window()
        tk.Button(buttons, text="Save", command=save_and_close, bg=GRAPHITE_ACCENT_DARK, fg="#ffffff", activebackground=GRAPHITE_ACCENT, activeforeground="#ffffff", relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2").pack(side="right")
        tk.Button(buttons, text="Cancel", command=close_options_window, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=14, pady=8, font=("Segoe UI", 10), cursor="hand2").pack(side="right", padx=(0, 8))

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
        exclude_pattern_list = parse_exclude_patterns(self.exclude_patterns_var.get())
        self.current_addon_targets = detect_addon_targets(source_root, output_addons_dir, exclude_pattern_list)
        for name, _ in self.current_addon_targets:
            self.addon_listbox.insert("end", name)
        names = [name for name, _ in self.current_addon_targets]
        available = set(names)
        if select_all_default:
            selection = available
        else:
            requested_selection = saved or previous
            selection = (requested_selection & available) if requested_selection else available
            if not selection:
                selection = available

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
            "update_paa_from_sources": bool(self.update_paa_from_sources_var.get()),
            "sign_pbos": bool(self.sign_pbos_var.get()),
            "force_rebuild": bool(self.force_rebuild_var.get()),
            "preflight_before_build": bool(self.preflight_before_build_var.get()),
            "max_processes": max_processes,
            "binarize_exe": self.binarize_exe_var.get().strip(),
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "imagetopaa_exe": self.imagetopaa_exe_var.get().strip(),
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
            "preflight_check_script_checks": bool(self.preflight_check_script_checks_var.get()) if hasattr(self, "preflight_check_script_checks_var") else True,
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

    def choose_imagetopaa_exe(self):
        path = filedialog.askopenfilename(title="Select ImageToPAA.exe", initialdir=get_initial_dir_from_value(self.imagetopaa_exe_var.get(), self.project_root_var.get()), filetypes=[("ImageToPAA.exe", "ImageToPAA.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.imagetopaa_exe_var.set(path)
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
            "preflight_check_script_checks": bool(self.preflight_check_script_checks_var.get()),
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
        if self.update_paa_from_sources_var.get():
            path = self.imagetopaa_exe_var.get().strip()
            if not path:
                raise BuildError("Select ImageToPAA.exe or disable Update PAA.")
            if not os.path.isfile(path):
                raise BuildError(f"ImageToPAA.exe does not exist: {path}")
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
            "update_paa_from_sources": bool(self.update_paa_from_sources_var.get()),
            "sign_pbos": bool(self.sign_pbos_var.get()),
            "force_rebuild": bool(self.force_rebuild_var.get()),
            "preflight_before_build": bool(self.preflight_before_build_var.get()),
            "binarize_exe": self.binarize_exe_var.get().strip(),
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "imagetopaa_exe": self.imagetopaa_exe_var.get().strip(),
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
            "preflight_check_script_checks": bool(self.preflight_check_script_checks_var.get()),
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
