import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from rag_inspector_settings import resource_path

APP_ICON_FILE = "assets/HEADONLY_SQUARE_2k.ico"
MAX_SYNTAX_HIGHLIGHT_CHARS = 1_500_000
GRAPHITE_BG = "#24262b"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT_DARK = "#7f3434"

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
