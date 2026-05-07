"""
RaG PBO Inspector

Standalone graphite UI for inspecting and extracting DayZ PBO archives.
"""

import os
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pbo_core import (
    PboError,
    extract_pbo_files,
    format_byte_size,
    format_pbo_timestamp,
    get_pbo_entry_unpacked_size,
    get_pbo_method_label,
    is_pbo_entry_compressed,
    is_pbo_entry_supported,
    read_pbo_entry_data,
    read_pbo_archive,
)

try:
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD

    DND_ROOT_CLASS = TkinterDnD.Tk
    TKDND_AVAILABLE = True
except Exception:
    COPY = "copy"
    DND_FILES = None
    DND_ROOT_CLASS = tk.Tk
    TKDND_AVAILABLE = False


APP_TITLE = "RaG PBO Inspector"
APP_VERSION = "0.7.10 Beta"
APP_ICON_FILE = os.path.join("assets", "HEADONLY_SQUARE_2k.ico")
INSPECTOR_SETTINGS_DIR = "RaG_PBO_Inspector"
TEXHEADERS_BIN_NAME = "texheaders.bin"
MAX_TEXT_PREVIEW_BYTES = 5 * 1024 * 1024
MAX_P3D_INSPECT_BYTES = 128 * 1024 * 1024
MAX_SYNTAX_HIGHLIGHT_CHARS = 1_500_000
TEXT_VIEW_EXTENSIONS = {
    ".bisurf",
    ".c",
    ".cfg",
    ".cpp",
    ".csv",
    ".ext",
    ".h",
    ".hpp",
    ".html",
    ".inc",
    ".json",
    ".layout",
    ".log",
    ".mat",
    ".md",
    ".meta",
    ".profile",
    ".rvmat",
    ".sqm",
    ".sqf",
    ".sqs",
    ".surface",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_DECODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1250", "cp1252", "latin-1")
C_LIKE_SYNTAX_EXTENSIONS = {
    ".bisurf",
    ".c",
    ".cfg",
    ".cpp",
    ".ext",
    ".h",
    ".hpp",
    ".inc",
    ".mat",
    ".rvmat",
    ".sqf",
    ".sqs",
    ".surface",
}
RAP_TEXT_CONVERT_EXTENSIONS = {
    ".bisurf",
    ".mat",
    ".rvmat",
    ".surface",
}
C_LIKE_KEYWORDS = {
    "autoptr",
    "bool",
    "break",
    "case",
    "class",
    "const",
    "continue",
    "default",
    "delete",
    "do",
    "else",
    "enum",
    "false",
    "float",
    "for",
    "foreach",
    "if",
    "int",
    "modded",
    "new",
    "override",
    "private",
    "protected",
    "public",
    "ref",
    "return",
    "static",
    "string",
    "super",
    "switch",
    "this",
    "true",
    "typedef",
    "typename",
    "vector",
    "void",
    "while",
}
C_LIKE_KEYWORD_RE = re.compile(r"\b(" + "|".join(sorted(re.escape(keyword) for keyword in C_LIKE_KEYWORDS)) + r")\b")
NUMBER_RE = re.compile(r"(?<![\w.])(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)(?![\w.])")
STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
PREPROCESSOR_RE = re.compile(r"(?m)^[ \t]*#[^\n]*")
P3D_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{3,}")
P3D_RESOURCE_EXTENSIONS = {
    ".paa",
    ".pac",
    ".rvmat",
    ".p3d",
    ".rtm",
    ".cfg",
    ".cpp",
    ".bin",
    ".skeleton",
}
P3D_LOD_MARKERS = {
    "cargo",
    "edit",
    "fire",
    "geometry",
    "geom",
    "gunner",
    "hit",
    "hitpoints",
    "landcontact",
    "lod",
    "memory",
    "pilot",
    "phys",
    "physx",
    "roadway",
    "shadow",
    "view",
    "viewcargo",
    "viewgeometry",
    "viewgunner",
    "viewpilot",
}
P3D_LOD_CATEGORY_ORDER = [
    "Visual / resolution LODs",
    "View / crew LODs",
    "Geometry / collision LODs",
    "Fire / hit LODs",
    "Memory LODs",
    "Shadow LODs",
    "Other functional LODs",
    "Unknown LODs",
]
P3D_RESOLUTION_LABELS = {
    0x447A0000: ("View / crew LODs", "View Gunner"),
    0x44898000: ("View / crew LODs", "View Pilot"),
    0x44960000: ("View / crew LODs", "View Cargo"),
    0x461C4000: ("Shadow LODs", "Shadow Volume"),
    0x461C6800: ("Shadow LODs", "Shadow Volume 2"),
    0x462BE000: ("Shadow LODs", "Stencil Shadow"),
    0x462C0800: ("Shadow LODs", "Stencil Shadow 2"),
    0x551184E7: ("Geometry / collision LODs", "Geometry"),
    0x58635FA9: ("Memory LODs", "Memory"),
    0x58E35FA9: ("Geometry / collision LODs", "Land Contact"),
    0x592A87BF: ("Geometry / collision LODs", "Roadway"),
    0x59635FA9: ("Other functional LODs", "Paths"),
    0x598E1BCA: ("Fire / hit LODs", "HitPoints"),
    0x59AA87BF: ("Geometry / collision LODs", "View Geometry"),
    0x59C6F3B4: ("Fire / hit LODs", "Fire Geometry"),
    0x59E35FA9: ("Geometry / collision LODs", "View Cargo Geometry"),
    0x59FFCB9E: ("Fire / hit LODs", "View Cargo Fire Geometry"),
    0x5A0E1BCA: ("View / crew LODs", "View Commander"),
    0x5A1C51C4: ("Geometry / collision LODs", "View Commander Geometry"),
    0x5A2A87BF: ("Fire / hit LODs", "View Commander Fire Geometry"),
    0x5A38BDB9: ("Geometry / collision LODs", "View Pilot Geometry"),
    0x5A46F3B4: ("Fire / hit LODs", "View Pilot Fire Geometry"),
    0x5A5529AF: ("Geometry / collision LODs", "View Gunner Geometry"),
    0x5A635FA9: ("Fire / hit LODs", "View Gunner Fire Geometry"),
    0x5A7195A4: ("Other functional LODs", "Sub Parts"),
    0x5A7FCB9E: ("Shadow LODs", "Shadow Volume View Cargo"),
    0x5A8700CC: ("Shadow LODs", "Shadow Volume View Pilot"),
    0x5A8E1BCA: ("Shadow LODs", "Shadow Volume View Gunner"),
    0x5A9536C7: ("Other functional LODs", "Wreck"),
}

GRAPHITE_BG = "#24262b"
GRAPHITE_HEADER = "#1f2126"
GRAPHITE_CARD = "#2f3238"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT = "#a74747"
GRAPHITE_ACCENT_DARK = "#7f3434"
GRAPHITE_ACCENT_HOVER = "#b65353"
GRAPHITE_WARNING = "#d6aa5f"


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


def is_texheaders_bin_path(path):
    return Path(str(path).replace("\\", "/")).name.lower() == TEXHEADERS_BIN_NAME


def is_cfgconvert_candidate_bin_path(path):
    file_path = Path(str(path).replace("\\", "/"))
    return file_path.suffix.lower() == ".bin" and file_path.name.lower() != TEXHEADERS_BIN_NAME


def is_rapified_data(data):
    return data[:8].find(b"raP") in {0, 1}


def is_rap_text_convert_candidate_path(path):
    file_path = Path(str(path).replace("\\", "/"))
    return file_path.suffix.lower() in RAP_TEXT_CONVERT_EXTENSIONS


def get_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def convert_bin_to_cpp(cfgconvert_exe, bin_path, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise RuntimeError("CfgConvert.exe not found.")

    bin_file = Path(bin_path)

    if bin_file.suffix.lower() != ".bin":
        return ""

    if is_texheaders_bin_path(bin_file):
        raise RuntimeError("texHeaders.bin is not a config bin. Leave it as .bin.")

    cpp_path = str(bin_file.with_suffix(".cpp"))

    if os.path.isfile(cpp_path):
        os.remove(cpp_path)

    cmd = [cfgconvert_exe, "-txt", "-dst", cpp_path, str(bin_file)]
    result = subprocess.run(cmd, cwd=str(bin_file.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())

    output = result.stdout or ""

    if output:
        for line in output.splitlines():
            log(line)

    if result.returncode != 0 or not os.path.isfile(cpp_path):
        raise RuntimeError(f"CfgConvert failed for {bin_path} with exit code {result.returncode}")

    return cpp_path


def convert_rap_to_text(cfgconvert_exe, source_path, destination_path, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise RuntimeError("CfgConvert.exe not found.")

    source_file = Path(source_path)
    destination_file = Path(destination_path)

    if not source_file.is_file():
        raise RuntimeError(f"Source file does not exist: {source_path}")

    destination_file.parent.mkdir(parents=True, exist_ok=True)

    if destination_file.is_file():
        destination_file.unlink()

    cmd = [cfgconvert_exe, "-txt", "-dst", str(destination_file), str(source_file)]
    result = subprocess.run(cmd, cwd=str(source_file.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())

    output = result.stdout or ""

    if output:
        for line in output.splitlines():
            log(line)

    if result.returncode != 0 or not destination_file.is_file():
        raise RuntimeError(f"CfgConvert failed for {source_path} with exit code {result.returncode}")

    return str(destination_file)


def is_text_viewable_entry(entry_name):
    return Path(entry_name).suffix.lower() in TEXT_VIEW_EXTENSIONS


def get_entry_path_parts(entry_name):
    parts = [part for part in re.split(r"[\\/]+", entry_name.strip()) if part]
    return parts or [entry_name]


def get_syntax_mode(entry_name):
    suffix = Path(entry_name).suffix.lower()

    if suffix == ".bin" or suffix in C_LIKE_SYNTAX_EXTENSIONS:
        return "c_like"

    return None


def decode_text_data(data):
    for encoding in TEXT_DECODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue

    return data.decode("utf-8", errors="replace"), "utf-8 replacement"


def clean_p3d_string(value):
    value = value.strip().strip("\x00")

    if not value or not any(char.isalnum() for char in value):
        return ""

    if len(value) > 260:
        return ""

    if len(set(value)) <= 2 and len(value) > 12:
        return ""

    return value


def extract_p3d_printable_strings(data, limit=20000):
    strings = []
    seen = set()

    for match in P3D_PRINTABLE_RE.finditer(data):
        value = clean_p3d_string(match.group(0).decode("ascii", errors="replace"))
        key = value.lower()

        if not value or key in seen:
            continue

        strings.append(value)
        seen.add(key)

        if len(strings) >= limit:
            break

    return strings


def get_p3d_format_info(data):
    magic = data[:4].decode("ascii", errors="replace") if len(data) >= 4 else ""
    version = None

    if len(data) >= 8:
        version = struct.unpack("<I", data[4:8])[0]

    if magic == "ODOL":
        return "ODOL (binarized P3D)", version

    if magic == "MLOD":
        return "MLOD (editable P3D)", version

    return f"Unknown ({magic or 'no magic'})", version


def get_p3d_header_lod_count(data):
    if len(data) < 12:
        return None

    try:
        count = struct.unpack("<I", data[8:12])[0]
    except Exception:
        return None

    if 0 < count <= 512:
        return count

    return None


def classify_p3d_resolution_lod(raw_value, float_value):
    if raw_value in P3D_RESOLUTION_LABELS:
        return P3D_RESOLUTION_LABELS[raw_value]

    if not math.isfinite(float_value):
        return "Unknown LODs", "Unknown/non-finite resolution"

    if 0 <= float_value < 1000:
        return "Visual / resolution LODs", f"Resolution {float_value:g}"

    if 1000 <= float_value < 10000:
        return "View / crew LODs", f"Unknown view/crew resolution {float_value:g}"

    if 10000 <= float_value < 1.0e13:
        return "Shadow LODs", f"Unknown shadow/functional resolution {float_value:g}"

    return "Other functional LODs", f"Unknown functional resolution {float_value:g}"


def extract_p3d_resolution_lods(data):
    if data[:4] != b"ODOL":
        return []

    lod_count = get_p3d_header_lod_count(data)

    if not lod_count:
        return []

    start = 12
    end = start + lod_count * 4

    if end > len(data):
        return []

    lods = []

    for index in range(lod_count):
        raw_bytes = data[start + index * 4:start + index * 4 + 4]
        raw_value = struct.unpack("<I", raw_bytes)[0]
        float_value = struct.unpack("<f", raw_bytes)[0]
        category, label = classify_p3d_resolution_lod(raw_value, float_value)
        lods.append({
            "index": index,
            "raw": raw_value,
            "value": float_value,
            "category": category,
            "label": label,
        })

    return lods


def categorize_p3d_resolution_lods(lods):
    categorized = {category: [] for category in P3D_LOD_CATEGORY_ORDER}

    for lod in lods:
        categorized.setdefault(lod["category"], []).append(lod)

    return {category: values for category, values in categorized.items() if values}


def format_p3d_lod_value(value):
    if abs(value) >= 1.0e6 or (value and abs(value) < 0.001):
        return f"{value:.6g}"

    return f"{value:g}"


def has_path_extension(value, extensions):
    lower = value.lower()
    return any(extension in lower for extension in extensions)


def filter_resource_strings(strings, extensions):
    result = []

    for value in strings:
        if has_path_extension(value, extensions):
            result.append(value)

    return sorted(result, key=lambda item: item.lower())


def filter_marker_strings(strings, markers):
    result = []

    for value in strings:
        if has_path_extension(value, P3D_RESOURCE_EXTENSIONS) or "\\" in value or "/" in value:
            continue

        normalized = re.sub(r"[^a-z0-9]+", "", value.lower())

        if normalized in markers:
            result.append(value)

    return sorted(result, key=lambda item: item.lower())


def append_lod_category_sections(lines, categorized_lods):
    lines.append("")
    lines.append("LOD table from ODOL resolution array")
    lines.append("------------------------------------")

    if not categorized_lods:
        lines.append("No safe ODOL resolution table was detected.")
        return

    for label, values in categorized_lods.items():
        lines.append("")
        lines.append(label)

        for value in values[:80]:
            if isinstance(value, dict):
                lines.append(f"- LOD {value['index']}: {value['label']} | value={format_p3d_lod_value(value['value'])} | raw=0x{value['raw']:08X}")
            else:
                lines.append(f"- {value}")

        if len(values) > 80:
            lines.append(f"- ... {len(values) - 80} more")


def append_limited_section(lines, title, values, empty_text="None found.", limit=80):
    lines.append("")
    lines.append(title)
    lines.append("-" * len(title))

    if not values:
        lines.append(empty_text)
        return

    for value in values[:limit]:
        lines.append(f"- {value}")

    if len(values) > limit:
        lines.append(f"- ... {len(values) - limit} more")


class TextViewerWindow(tk.Toplevel):
    def __init__(self, parent, title, content, details, syntax_mode=None, actions=None):
        super().__init__(parent)
        self.title(title)
        self.geometry("980x720")
        self.minsize(720, 460)
        self.configure(bg=GRAPHITE_BG)
        self.transient(parent)

        try:
            self.iconbitmap(resource_path(APP_ICON_FILE))
        except Exception:
            pass

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        tk.Label(outer, text=title, bg=GRAPHITE_BG, fg=GRAPHITE_TEXT, font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(outer, text=details, bg=GRAPHITE_BG, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 8))

        text_frame = tk.Frame(outer, bg=GRAPHITE_BORDER, highlightthickness=1, highlightbackground=GRAPHITE_BORDER)
        text_frame.grid(row=2, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text = tk.Text(text_frame, wrap="none", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, font=("Consolas", 10), undo=False)
        self.text.grid(row=0, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(text_frame, command=self.text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.text.insert("1.0", content)
        self.configure_syntax_tags()
        self.apply_syntax_highlighting(content, syntax_mode)
        self.text.configure(state="disabled")

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        actions = actions or []
        bottom.columnconfigure(len(actions) + 1, weight=1)

        ttk.Button(bottom, text="Copy all", command=self.copy_all).grid(row=0, column=0, sticky="w")

        for index, (label, callback) in enumerate(actions, start=1):
            ttk.Button(bottom, text=label, command=lambda cb=callback: cb(self)).grid(row=0, column=index, sticky="w", padx=(8, 0))

        ttk.Button(bottom, text="Close", command=self.destroy).grid(row=0, column=len(actions) + 2, sticky="e")

        self.bind("<Escape>", lambda event: self.destroy())
        self.bind("<Control-a>", self.select_all)
        self.bind("<Control-A>", self.select_all)
        self.text.focus_set()

    def configure_syntax_tags(self):
        self.text.tag_configure("syntax_comment", foreground="#6a9955")
        self.text.tag_configure("syntax_string", foreground="#d7ba7d")
        self.text.tag_configure("syntax_keyword", foreground="#7cc7ff")
        self.text.tag_configure("syntax_number", foreground="#b5cea8")
        self.text.tag_configure("syntax_preprocessor", foreground="#c586c0")
        self.text.tag_raise("syntax_comment")
        self.text.tag_raise("syntax_string")
        self.text.tag_raise("sel")

    def apply_syntax_highlighting(self, content, syntax_mode):
        if syntax_mode != "c_like" or len(content) > MAX_SYNTAX_HIGHLIGHT_CHARS:
            return

        for tag, pattern in [
            ("syntax_keyword", C_LIKE_KEYWORD_RE),
            ("syntax_number", NUMBER_RE),
            ("syntax_preprocessor", PREPROCESSOR_RE),
            ("syntax_string", STRING_RE),
            ("syntax_comment", COMMENT_RE),
        ]:
            for match in pattern.finditer(content):
                self.text.tag_add(tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def copy_all(self):
        content = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)

    def select_all(self, event=None):
        self.text.configure(state="normal")
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.configure(state="disabled")
        return "break"


class PboInspectorApp(DND_ROOT_CLASS):
    def __init__(self):
        super().__init__()
        self.saved_settings = load_settings()
        self.archive = None
        self.entries = []
        self.pbo_path_var = tk.StringVar(value="")
        self.output_dir_var = tk.StringVar(value="")
        self.cfgconvert_exe_var = tk.StringVar(value=self.saved_settings.get("cfgconvert_exe", find_cfgconvert()))
        self.convert_bin_var = tk.BooleanVar(value=self.saved_settings.get("convert_bin_to_cpp", True))
        self.summary_var = tk.StringVar(value="No PBO loaded")
        self.drop_registered_widgets = []
        self.entry_iid_to_index = {}
        self.folder_iids = set()

        self.title(APP_TITLE)
        self.geometry("1040x760")
        self.minsize(860, 640)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()
        self.after(250, self.register_file_drop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pbo"):
            self.pbo_path_var.set(sys.argv[1])
            self.output_dir_var.set(self.get_default_output_dir())
            self.after(100, self.inspect_pbo)

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

    def apply_theme(self):
        self.option_add("*TCombobox*Listbox.background", GRAPHITE_FIELD)
        self.option_add("*TCombobox*Listbox.foreground", GRAPHITE_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", GRAPHITE_ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT, fieldbackground=GRAPHITE_FIELD, font=("Segoe UI", 10))
        style.configure("TFrame", background=GRAPHITE_BG)
        style.configure("TLabelframe", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, relief="flat", padding=14)
        style.configure("TLabelframe.Label", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT)
        style.configure("FieldName.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=GRAPHITE_FIELD, background=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, insertcolor=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, relief="flat", padding=7)
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=4)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)])
        style.configure("TButton", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_CARD_SOFT, relief="flat", padding=(12, 8))
        style.map("TButton", background=[("active", GRAPHITE_BORDER), ("pressed", GRAPHITE_ACCENT_DARK)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("Vertical.TScrollbar", background=GRAPHITE_CARD_SOFT, troughcolor=GRAPHITE_BG, arrowcolor=GRAPHITE_MUTED, relief="flat")
        style.configure("Pbo.Treeview", background=GRAPHITE_FIELD, fieldbackground=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, rowheight=24)
        style.configure("Pbo.Treeview.Heading", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Pbo.Treeview", background=[("selected", GRAPHITE_ACCENT_DARK)], foreground=[("selected", "#ffffff")])

    def attach_button_hover(self, button, normal_bg, hover_bg, pressed_bg=None):
        pressed_bg = pressed_bg or hover_bg

        def on_enter(event=None):
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover_bg, activebackground=pressed_bg)

        def on_leave(event=None):
            button.configure(bg=normal_bg, activebackground=pressed_bg)

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")

    def make_button(self, parent, text, command, primary=False):
        if primary:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_ACCENT_DARK, "#ffffff", GRAPHITE_ACCENT, GRAPHITE_ACCENT_HOVER, "bold"
        else:
            bg, fg, active_bg, hover_bg, weight = GRAPHITE_CARD_SOFT, GRAPHITE_TEXT, GRAPHITE_BORDER, GRAPHITE_BORDER, "normal"

        button = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active_bg, activeforeground="#ffffff" if fg == "#ffffff" else GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=12, pady=7, font=("Segoe UI", 9, weight), cursor="hand2")
        button.pack(side="left", padx=(0, 8))
        self.attach_button_hover(button, bg, hover_bg, active_bg)
        return button

    def build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=GRAPHITE_HEADER, bd=0, highlightthickness=0)
        header.pack(fill="x", pady=(0, 10), ipady=5)
        left = tk.Frame(header, bg=GRAPHITE_HEADER)
        left.pack(side="left", fill="x", expand=True, padx=(14, 8))
        tk.Label(left, text=APP_TITLE, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="Inspect and extract DayZ PBO archives. Drop a .pbo anywhere in this window.", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(header, text=f"v{APP_VERSION}", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 14))

        path_frame = ttk.LabelFrame(outer, text="Archive", padding=12)
        path_frame.pack(fill="x", pady=(0, 10))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="PBO file", style="FieldName.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        pbo_entry = ttk.Entry(path_frame, textvariable=self.pbo_path_var)
        pbo_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4)
        pbo_entry.bind("<Return>", lambda event: self.inspect_pbo(), add="+")
        ttk.Button(path_frame, text="Browse", command=self.choose_pbo).grid(row=0, column=2, sticky="e", pady=4)

        ttk.Label(path_frame, text="Extract to", style="FieldName.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        output_entry = ttk.Entry(path_frame, textvariable=self.output_dir_var)
        output_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(path_frame, text="Browse", command=self.choose_output_dir).grid(row=1, column=2, sticky="e", pady=4)

        ttk.Label(path_frame, text="CfgConvert.exe", style="FieldName.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        cfgconvert_entry = ttk.Entry(path_frame, textvariable=self.cfgconvert_exe_var)
        cfgconvert_entry.grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(path_frame, text="Browse", command=self.choose_cfgconvert_exe).grid(row=2, column=2, sticky="e", pady=4)

        convert_check = tk.Checkbutton(path_frame, text="", variable=self.convert_bin_var, command=self.on_convert_option_changed, indicatoron=False, selectcolor=GRAPHITE_CARD_SOFT, relief="flat", borderwidth=0, padx=12, pady=7, font=("Segoe UI", 10), cursor="hand2", anchor="w", justify="left", width=48)
        convert_check.grid(row=3, column=1, sticky="w", padx=(8, 8), pady=(4, 0))
        self.convert_check = convert_check
        self.refresh_convert_check()

        action_frame = ttk.Frame(outer)
        action_frame.pack(fill="x", pady=(0, 10))
        self.make_button(action_frame, "View selected", self.view_selected_entry, primary=True)
        self.make_button(action_frame, "Reload PBO", self.inspect_pbo)
        self.make_button(action_frame, "Extract selected", self.extract_selected)
        self.make_button(action_frame, "Extract all", self.extract_all)
        self.make_button(action_frame, "Open output", self.open_output_folder)
        ttk.Label(action_frame, textvariable=self.summary_var, foreground=GRAPHITE_MUTED).pack(side="left", padx=(6, 0))

        content_frame = ttk.LabelFrame(outer, text="Contents", padding=10)
        content_frame.pack(fill="both", expand=True, pady=(0, 10))
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)

        columns = ("size", "method", "timestamp")
        self.tree = ttk.Treeview(content_frame, columns=columns, show="tree headings", selectmode="extended", style="Pbo.Treeview")
        self.tree.heading("#0", text="Path")
        self.tree.heading("size", text="Size")
        self.tree.heading("method", text="Method")
        self.tree.heading("timestamp", text="Timestamp")
        self.tree.column("#0", width=560, anchor="w", stretch=True)
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("method", width=120, anchor="w")
        self.tree.column("timestamp", width=150, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self.on_tree_double_click, add="+")
        self.tree.tag_configure("folder", foreground=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold"))
        self.tree.tag_configure("file", foreground=GRAPHITE_TEXT)

        tree_scroll = ttk.Scrollbar(content_frame, command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        log_frame = ttk.LabelFrame(outer, text="Inspector log", padding=10)
        log_frame.pack(fill="x")
        self.log_text = tk.Text(log_frame, height=7, wrap="word", bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, selectforeground="#ffffff", relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=GRAPHITE_BORDER, highlightcolor=GRAPHITE_ACCENT, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def log(self, message):
        self.log_text.insert("end", str(message) + chr(10))
        self.log_text.see("end")

    def save_settings(self):
        data = {
            "cfgconvert_exe": self.cfgconvert_exe_var.get().strip(),
            "convert_bin_to_cpp": bool(self.convert_bin_var.get()),
        }
        self.saved_settings = data
        save_settings(data)

    def on_convert_option_changed(self):
        self.refresh_convert_check()
        self.save_settings()

    def refresh_convert_check(self):
        label = "Convert extracted .bin and rapified material files with CfgConvert"

        if self.convert_bin_var.get():
            self.convert_check.configure(text="✓ " + label, bg=GRAPHITE_CARD_SOFT, fg=GRAPHITE_TEXT, activebackground=GRAPHITE_BORDER, activeforeground=GRAPHITE_TEXT)
        else:
            self.convert_check.configure(text="  " + label, bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, activebackground=GRAPHITE_CARD_SOFT, activeforeground=GRAPHITE_TEXT)

    def on_close(self):
        try:
            self.save_settings()
            self.unregister_file_drop()
        except Exception:
            pass

        self.destroy()

    def get_default_output_dir(self):
        pbo_path = self.pbo_path_var.get().strip()

        if not pbo_path:
            return ""

        pbo = Path(pbo_path)
        return str(pbo.with_name(pbo.stem + "_extracted"))

    def choose_pbo(self):
        path = filedialog.askopenfilename(title="Select PBO file", initialdir=get_initial_dir_from_value(self.pbo_path_var.get()), filetypes=[("PBO files", "*.pbo"), ("All files", "*.*")], parent=self)

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

    def choose_cfgconvert_exe(self):
        path = filedialog.askopenfilename(title="Select CfgConvert.exe", initialdir=get_initial_dir_from_value(self.cfgconvert_exe_var.get()), filetypes=[("CfgConvert.exe", "CfgConvert.exe"), ("Executable", "*.exe"), ("All files", "*.*")], parent=self)

        if path:
            self.cfgconvert_exe_var.set(path)
            self.save_settings()

    def register_file_drop(self):
        if self.drop_registered_widgets:
            return

        if not TKDND_AVAILABLE:
            self.log("WARNING: tkinterdnd2 is not installed. You can still drag a .pbo onto the EXE or use Browse.")
            return

        try:
            self.update_idletasks()
            registered = []

            for widget in self.iter_drop_widgets(self):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self.on_dropped_files, add="+")
                    registered.append(widget)
                except Exception:
                    continue

            if not registered:
                raise RuntimeError("tkdnd did not accept any inspector drop targets.")

            self.drop_registered_widgets = registered
            self.log(f"Drag and drop enabled on {len(registered)} widget(s). Drop a .pbo anywhere in this window.")
        except Exception as e:
            self.log(f"WARNING: Could not enable drag/drop into the window: {e}")
            self.drop_registered_widgets = []

    def iter_drop_widgets(self, widget):
        yield widget

        for child in widget.winfo_children():
            yield from self.iter_drop_widgets(child)

    def unregister_file_drop(self):
        if not self.drop_registered_widgets:
            return

        for widget in reversed(self.drop_registered_widgets):
            try:
                widget.drop_target_unregister()
            except Exception:
                pass

        self.drop_registered_widgets = []

    def on_dropped_files(self, event):
        try:
            paths = list(self.tk.splitlist(event.data))
            self.handle_dropped_files(paths)
        except Exception as e:
            self.log(f"WARNING: Could not read dropped file path: {e}")

        return COPY

    def handle_dropped_files(self, paths):
        pbo_paths = [path for path in paths if os.path.isfile(path) and path.lower().endswith(".pbo")]

        if not pbo_paths:
            self.log("Dropped item ignored. Drop a .pbo file.")
            return

        path = pbo_paths[0]
        self.pbo_path_var.set(path)
        self.output_dir_var.set(self.get_default_output_dir())
        self.log(f"Dropped PBO: {path}")
        self.inspect_pbo()

    def inspect_pbo(self):
        pbo_path = self.pbo_path_var.get().strip()

        try:
            archive = read_pbo_archive(pbo_path)
        except Exception as e:
            self.archive = None
            messagebox.showerror(APP_TITLE, str(e), parent=self)
            return

        self.archive = archive
        self.entries = list(archive["entries"])
        self.tree.delete(*self.tree.get_children())
        self.entry_iid_to_index = {}
        self.folder_iids = set()

        total_bytes = sum(get_pbo_entry_unpacked_size(entry) for entry in self.entries)
        unsupported = 0
        compressed = 0

        for index, entry in sorted(enumerate(self.entries), key=lambda item: item[1].name.replace("\\", "/").lower()):
            if is_pbo_entry_compressed(entry):
                compressed += 1

            if not is_pbo_entry_supported(entry):
                unsupported += 1

            self.add_entry_to_tree(index, entry)

        prefix = archive["properties"].get("prefix", "")
        footer = archive.get("footer_size", 0)
        compressed_text = f", compressed entries: {compressed}" if compressed else ""
        unsupported_text = f", unsupported entries: {unsupported}" if unsupported else ""
        self.summary_var.set(f"{len(self.entries)} file(s), {format_byte_size(total_bytes)}, prefix: {prefix or '<none>'}, footer: {format_byte_size(footer)}{compressed_text}{unsupported_text}")
        self.log(f"Loaded: {pbo_path}")
        self.log(f"Files: {len(self.entries)}, payload: {format_byte_size(total_bytes)}, prefix: {prefix or '<none>'}")

        if compressed:
            self.log(f"Compressed entries supported: {compressed}. They will be decompressed for preview and extraction.")

        if unsupported:
            self.log(f"WARNING: {unsupported} unsupported entry/entries can be listed but not extracted.")

    def add_entry_to_tree(self, index, entry):
        parts = get_entry_path_parts(entry.name)
        parent = ""

        for depth, folder_name in enumerate(parts[:-1], start=1):
            folder_path = "/".join(parts[:depth])
            folder_iid = f"folder:{folder_path}"

            if not self.tree.exists(folder_iid):
                self.tree.insert(parent, "end", iid=folder_iid, text=folder_name, values=("", "folder", ""), open=depth <= 1, tags=("folder",))
                self.folder_iids.add(folder_iid)

            parent = folder_iid

        entry_iid = f"entry:{index}"
        self.entry_iid_to_index[entry_iid] = index
        self.tree.insert(parent, "end", iid=entry_iid, text=parts[-1], values=(format_byte_size(get_pbo_entry_unpacked_size(entry)), get_pbo_method_label(entry.packing_method), format_pbo_timestamp(entry.timestamp)), tags=("file",))

    def ensure_current_archive_loaded(self):
        pbo_path = self.pbo_path_var.get().strip()
        archive_path = self.archive.get("path", "") if self.archive else ""
        archive_matches_path = bool(archive_path and pbo_path and os.path.normcase(os.path.abspath(archive_path)) == os.path.normcase(os.path.abspath(pbo_path)))

        if not self.archive or not archive_matches_path:
            self.inspect_pbo()

        return bool(self.archive)

    def get_single_selected_entry(self):
        selected_items = self.tree.selection()

        if not selected_items:
            messagebox.showerror(APP_TITLE, "Select one PBO entry to view.", parent=self)
            return None

        entry_indices = [self.entry_iid_to_index[item] for item in selected_items if item in self.entry_iid_to_index]

        if not entry_indices:
            messagebox.showerror(APP_TITLE, "Select a file entry to view. Folder rows can be extracted, but not previewed.", parent=self)
            return None

        if len(entry_indices) > 1:
            messagebox.showerror(APP_TITLE, "Select only one PBO entry to view.", parent=self)
            return None

        return self.entries[entry_indices[0]]

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)

        if item:
            if item in self.folder_iids:
                return

            self.tree.selection_set(item)
            self.tree.focus(item)
            self.view_selected_entry()

    def view_selected_entry(self):
        if not self.ensure_current_archive_loaded():
            return

        entry = self.get_single_selected_entry()

        if not entry:
            return

        if not is_pbo_entry_supported(entry):
            messagebox.showerror(APP_TITLE, f"Cannot preview unsupported entry:\n\n{entry.name}\n\n{get_pbo_method_label(entry.packing_method)}", parent=self)
            return

        suffix = Path(entry.name).suffix.lower()

        try:
            if suffix == ".p3d":
                self.view_p3d_info(entry)
                return

            if suffix == ".bin":
                content, details = self.load_bin_preview(entry)
            else:
                if not is_text_viewable_entry(entry.name):
                    if not messagebox.askyesno(APP_TITLE, "This file extension is not in the text-preview list.\n\nTry to view it as text anyway?\n\n" + entry.name, parent=self):
                        return

                data = read_pbo_entry_data(self.pbo_path_var.get().strip(), entry.name, MAX_TEXT_PREVIEW_BYTES)
                content, details = self.load_text_preview(entry, data)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self)
            return

        TextViewerWindow(self, f"View: {Path(entry.name).name}", content, details, get_syntax_mode(entry.name))
        self.log(f"Previewed: {entry.name}")

    def view_p3d_info(self, entry):
        data = read_pbo_entry_data(self.pbo_path_var.get().strip(), entry.name, MAX_P3D_INSPECT_BYTES)
        metadata = self.get_p3d_metadata(entry, data)
        report = self.build_p3d_info_report(entry, metadata)
        TextViewerWindow(self, f"P3D Info: {Path(entry.name).name}", report, f"{entry.name} | {format_byte_size(get_pbo_entry_unpacked_size(entry))} | best-effort metadata scan")
        self.log(f"Inspected P3D: {entry.name}")

    def load_text_preview(self, entry, data):
        if is_rapified_data(data) and is_rap_text_convert_candidate_path(entry.name):
            cfgconvert_exe = self.cfgconvert_exe_var.get().strip()

            if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
                raise PboError(f"{entry.name} is rapified/binarized config data. Select CfgConvert.exe to preview it as readable text.")

            with tempfile.TemporaryDirectory(prefix="rag_pbo_preview_") as temp_dir:
                source_name = Path(entry.name.replace("\\", "/")).name or "preview.rvmat"
                source_path = Path(temp_dir) / source_name
                text_path = Path(temp_dir) / f"{source_path.stem}_text{source_path.suffix}"
                source_path.write_bytes(data)
                converted_path = convert_rap_to_text(cfgconvert_exe, str(source_path), str(text_path), self.log)
                converted_data = Path(converted_path).read_bytes()

            content, encoding = decode_text_data(converted_data)
            details = f"{entry.name} derapified with CfgConvert | {format_byte_size(get_pbo_entry_unpacked_size(entry))} | {encoding}"
            return content, details

        content, encoding = decode_text_data(data)
        details = f"{entry.name} | {format_byte_size(get_pbo_entry_unpacked_size(entry))} | {encoding}"
        return content, details

    def get_p3d_metadata(self, entry, data):
        format_label, version = get_p3d_format_info(data)
        strings = extract_p3d_printable_strings(data)
        textures = filter_resource_strings(strings, {".paa", ".pac"})
        materials = filter_resource_strings(strings, {".rvmat"})
        linked_models = [value for value in filter_resource_strings(strings, {".p3d"}) if value.lower() != entry.name.lower()]
        animations = filter_resource_strings(strings, {".rtm"})
        config_refs = filter_resource_strings(strings, {".cfg", ".cpp", ".bin"})
        resolution_lods = extract_p3d_resolution_lods(data)
        categorized_lods = categorize_p3d_resolution_lods(resolution_lods)
        lod_markers = filter_marker_strings(strings, P3D_LOD_MARKERS)
        related_model_cfg = self.find_related_model_cfg_entries(entry.name)
        expected_model_class = Path(entry.name.replace("\\", "/")).stem

        return {
            "format_label": format_label,
            "version": version,
            "textures": textures,
            "materials": materials,
            "linked_models": linked_models,
            "animations": animations,
            "config_refs": config_refs,
            "resolution_lods": resolution_lods,
            "categorized_lods": categorized_lods,
            "lod_markers": lod_markers,
            "related_model_cfg": related_model_cfg,
            "expected_model_class": expected_model_class,
        }

    def build_p3d_info_report(self, entry, metadata):
        lines = [
            "P3D Information",
            "===============",
            "",
            f"File: {entry.name}",
            f"Size: {format_byte_size(get_pbo_entry_unpacked_size(entry))}",
            f"Format: {metadata['format_label']}",
            f"Version: {metadata['version'] if metadata['version'] is not None else 'unknown'}",
            f"Header LOD count: {len(metadata['resolution_lods']) if metadata['resolution_lods'] else 'unknown'}",
            f"Expected model.cfg class: {metadata['expected_model_class']}",
            "",
            "Notes",
            "-----",
            "- This is a metadata scan, not a P3D debinarizer.",
            "- Categorized LODs come from the ODOL header resolution array when it can be safely read.",
            "- The expected model.cfg class is inferred from the P3D filename.",
            "- This tool does not recover model.cfg. Use Mikero DeP3d/ExtractModelCfg for real model.cfg extraction from supported ODOL versions.",
        ]

        append_limited_section(lines, "Related loose model.cfg/model.bin entries in this PBO", metadata["related_model_cfg"], "None found in this PBO. This is normal for binarized models because model.cfg data is usually baked into the P3D.", 30)
        append_lod_category_sections(lines, metadata["categorized_lods"])
        append_limited_section(lines, "Additional high-confidence LOD marker strings", metadata["lod_markers"], "No additional high-confidence LOD marker strings found.", 80)
        append_limited_section(lines, "Textures", metadata["textures"], "No texture references found.", 120)
        append_limited_section(lines, "Materials", metadata["materials"], "No material references found.", 120)
        append_limited_section(lines, "Linked models / proxies", metadata["linked_models"], "No linked model/proxy references found.", 120)
        append_limited_section(lines, "Animation files", metadata["animations"], "No RTM animation references found.", 80)
        append_limited_section(lines, "Config-like references", metadata["config_refs"], "No config-like file references found.", 80)

        return "\n".join(lines)

    def find_related_model_cfg_entries(self, entry_name):
        entry_folder = Path(entry_name.replace("\\", "/")).parent.as_posix()

        if entry_folder == ".":
            entry_folder = ""

        result = []

        for other in self.entries:
            normalized = other.name.replace("\\", "/")
            lower_name = Path(normalized).name.lower()

            if lower_name not in {"model.cfg", "model.bin"}:
                continue

            other_folder = Path(normalized).parent.as_posix()

            if other_folder == ".":
                other_folder = ""

            if other_folder == "" or entry_folder == other_folder or entry_folder.startswith(other_folder + "/"):
                result.append(normalized)

        return sorted(result, key=lambda item: (len(item), item.lower()))

    def load_bin_preview(self, entry):
        if is_texheaders_bin_path(entry.name):
            raise PboError("texHeaders.bin is not a config bin and cannot be converted with CfgConvert. Leave it as .bin.")

        cfgconvert_exe = self.cfgconvert_exe_var.get().strip()

        if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
            raise PboError("CfgConvert.exe is required to preview .bin files as text.")

        data = read_pbo_entry_data(self.pbo_path_var.get().strip(), entry.name, MAX_TEXT_PREVIEW_BYTES)

        with tempfile.TemporaryDirectory(prefix="rag_pbo_preview_") as temp_dir:
            bin_name = Path(entry.name.replace("\\", "/")).name or "preview.bin"
            bin_path = Path(temp_dir) / bin_name
            bin_path.write_bytes(data)
            cpp_path = convert_bin_to_cpp(cfgconvert_exe, str(bin_path), self.log)
            cpp_data = Path(cpp_path).read_bytes()

        content, encoding = decode_text_data(cpp_data)
        details = f"{entry.name} converted with CfgConvert | {format_byte_size(get_pbo_entry_unpacked_size(entry))} | {encoding}"
        return content, details

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
            messagebox.showerror(APP_TITLE, "Select at least one file or folder to extract.", parent=self)
            return

        selected_indices = []

        for item in selected_items:
            selected_indices.extend(self.get_entry_indices_under_tree_item(item))

        selected_names = []
        seen_names = set()

        for index in selected_indices:
            name = self.entries[index].name

            if name not in seen_names:
                selected_names.append(name)
                seen_names.add(name)

        if not selected_names:
            messagebox.showerror(APP_TITLE, "Select at least one file or folder to extract.", parent=self)
            return

        self.extract_entries(selected_names)

    def get_entry_indices_under_tree_item(self, item):
        if item in self.entry_iid_to_index:
            return [self.entry_iid_to_index[item]]

        indices = []

        for child in self.tree.get_children(item):
            indices.extend(self.get_entry_indices_under_tree_item(child))

        return indices

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

        self.convert_extracted_rap_text_files(result.get("paths", []))
        self.convert_extracted_bin_files(result.get("paths", []))
        self.log(f"Extracted {result['files']} file(s) / {format_byte_size(result['bytes'])} -> {result['output_dir']}")
        messagebox.showinfo(APP_TITLE, f"Extracted {result['files']} file(s).", parent=self)

    def convert_extracted_rap_text_files(self, paths):
        candidate_paths = [path for path in paths if is_rap_text_convert_candidate_path(path)]

        if not candidate_paths:
            return

        rap_paths = []

        for path in candidate_paths:
            try:
                with open(path, "rb") as file:
                    if is_rapified_data(file.read(16)):
                        rap_paths.append(path)
            except Exception as e:
                self.log(f"WARNING: Could not check material file for raP data: {path} ({e})")

        if not rap_paths:
            return

        if not self.convert_bin_var.get():
            self.log(f"Skipped rapified material conversion for {len(rap_paths)} file(s).")
            return

        cfgconvert_exe = self.cfgconvert_exe_var.get().strip()

        if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
            self.log(f"WARNING: {len(rap_paths)} rapified material file(s) were extracted, but CfgConvert.exe is not configured. They were left as binary raP data.")
            messagebox.showwarning(APP_TITLE, "Extracted rapified material file(s), but CfgConvert.exe is not configured.\n\nSelect CfgConvert.exe to make them readable.", parent=self)
            return

        converted = 0
        failed = 0

        for path in rap_paths:
            try:
                path_obj = Path(path)
                temp_path = path_obj.with_name(f"{path_obj.stem}.__rag_text{path_obj.suffix}")
                converted_path = convert_rap_to_text(cfgconvert_exe, path, str(temp_path), self.log)
                os.replace(converted_path, path)
                converted += 1
                self.log(f"Converted rapified material to text: {path}")
            except Exception as e:
                failed += 1
                self.log(f"WARNING: Could not convert rapified material to text: {path} ({e})")

        if converted or failed:
            self.log(f"Rapified material conversion summary: converted={converted}, failed={failed}")

        if failed:
            messagebox.showwarning(APP_TITLE, f"Converted {converted} rapified material file(s), failed {failed}. Check the inspector log.", parent=self)

    def convert_extracted_bin_files(self, paths):
        bin_paths = [path for path in paths if str(path).lower().endswith(".bin")]

        if not bin_paths:
            return

        skipped_bin_paths = [path for path in bin_paths if not is_cfgconvert_candidate_bin_path(path)]
        bin_paths = [path for path in bin_paths if is_cfgconvert_candidate_bin_path(path)]

        for skipped_path in skipped_bin_paths:
            if is_texheaders_bin_path(skipped_path):
                self.log(f"Skipped .bin -> .cpp conversion for texHeaders.bin: {skipped_path}")
            else:
                self.log(f"Skipped .bin -> .cpp conversion for unsupported .bin file: {skipped_path}")

        if not bin_paths:
            return

        if not self.convert_bin_var.get():
            self.log(f"Skipped .bin -> .cpp conversion for {len(bin_paths)} file(s).")
            return

        cfgconvert_exe = self.cfgconvert_exe_var.get().strip()

        if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
            self.log(f"WARNING: {len(bin_paths)} .bin file(s) were extracted, but CfgConvert.exe is not configured. They were not converted to .cpp.")
            messagebox.showwarning(APP_TITLE, "Extracted .bin file(s), but CfgConvert.exe is not configured.\n\nSelect CfgConvert.exe to convert .bin files to .cpp.", parent=self)
            return

        converted = 0
        failed = 0

        for bin_path in bin_paths:
            try:
                cpp_path = convert_bin_to_cpp(cfgconvert_exe, bin_path, self.log)
                converted += 1
                self.log(f"Converted .bin -> .cpp: {cpp_path}")
            except Exception as e:
                failed += 1
                self.log(f"WARNING: Could not convert .bin to .cpp: {bin_path} ({e})")

        if converted or failed:
            self.log(f"CfgConvert summary: converted={converted}, failed={failed}")

        if failed:
            messagebox.showwarning(APP_TITLE, f"Converted {converted} .bin file(s), failed {failed}. Check the inspector log.", parent=self)

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


if __name__ == "__main__":
    app = PboInspectorApp()
    app.mainloop()
