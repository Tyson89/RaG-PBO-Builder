import difflib
import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from rag_inspector_settings import resource_path

APP_ICON_FILE = "assets/HEADONLY_SQUARE_2k.ico"
MAX_SYNTAX_HIGHLIGHT_CHARS = 1_500_000
MAX_SIDE_BY_SIDE_DIFF_LINES = 30000
GRAPHITE_BG = "#24262b"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT_DARK = "#7f3434"
GRAPHITE_ADDED = "#244934"
GRAPHITE_REMOVED = "#552d32"
GRAPHITE_CHANGED = "#594b29"

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


class PboComparisonWindow(tk.Toplevel):
    def __init__(self, parent, comparison, load_entry):
        super().__init__(parent)
        self.comparison = comparison
        self.load_entry = load_entry
        self.item_map = {}
        self.show_unchanged_var = tk.BooleanVar(value=False)
        self.title("PBO Comparison")
        self.geometry("1320x860")
        self.minsize(900, 620)
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

        counts = comparison["counts"]
        header = f"Added {counts['added']}  |  Removed {counts['removed']}  |  Changed {counts['changed']}  |  Metadata {counts['metadata']}  |  Unchanged {counts['unchanged']}"
        if comparison["property_changes"]:
            property_summary = ", ".join(f"{key}: {values['left']!r} -> {values['right']!r}" for key, values in comparison["property_changes"].items())
            header += f"  |  Header: {property_summary}"
        ttk.Label(outer, text="PBO Comparison", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text=header, foreground=GRAPHITE_MUTED).grid(row=1, column=0, sticky="w", pady=(2, 8))

        list_frame = ttk.Frame(outer)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("status", "left_size", "right_size")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", height=10, selectmode="browse", style="Pbo.Treeview")
        self.tree.heading("#0", text="Path")
        self.tree.heading("status", text="Status")
        self.tree.heading("left_size", text="Left size")
        self.tree.heading("right_size", text="Right size")
        self.tree.column("#0", width=760, stretch=True)
        self.tree.column("status", width=100, anchor="center", stretch=False)
        self.tree.column("left_size", width=100, anchor="e", stretch=False)
        self.tree.column("right_size", width=100, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("added", background=GRAPHITE_ADDED)
        self.tree.tag_configure("removed", background=GRAPHITE_REMOVED)
        self.tree.tag_configure("changed", background=GRAPHITE_CHANGED)
        self.tree.tag_configure("metadata", foreground=GRAPHITE_MUTED)
        self.tree.bind("<<TreeviewSelect>>", self.on_select, add="+")

        options = ttk.Frame(outer)
        options.grid(row=3, column=0, sticky="ew", pady=(8, 8))
        ttk.Checkbutton(options, text="Show unchanged", variable=self.show_unchanged_var, command=self.populate_tree).pack(side="left")
        ttk.Label(options, text="Select file to compare contents directly from both PBOs.", foreground=GRAPHITE_MUTED).pack(side="left", padx=(12, 0))
        self.change_status_var = tk.StringVar(value="No line changes")
        ttk.Label(options, textvariable=self.change_status_var, foreground=GRAPHITE_MUTED).pack(side="right", padx=(8, 0))
        self.next_change_button = ttk.Button(options, text="Next change", command=lambda: self.go_to_change(1), state="disabled")
        self.next_change_button.pack(side="right", padx=(8, 0))
        self.previous_change_button = ttk.Button(options, text="Previous change", command=lambda: self.go_to_change(-1), state="disabled")
        self.previous_change_button.pack(side="right", padx=(8, 0))

        panes = tk.PanedWindow(outer, orient="horizontal", bg=GRAPHITE_BORDER, sashwidth=6, relief="flat")
        panes.grid(row=4, column=0, sticky="nsew")
        outer.rowconfigure(4, weight=3)
        self.left_text, self.left_details, self.left_scroll = self.make_diff_pane(panes, "LEFT", comparison["left"]["path"])
        self.right_text, self.right_details, self.right_scroll = self.make_diff_pane(panes, "RIGHT", comparison["right"]["path"])
        self.configure_synced_scrollbars()
        self.bind_synced_scroll()

        bottom = ttk.Frame(outer)
        bottom.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Button(bottom, text="Close", command=self.destroy).grid(row=0, column=1, sticky="e")
        self.bind("<Escape>", lambda event: self.destroy())
        self.populate_tree()

    def make_diff_pane(self, panes, label, path):
        frame = ttk.Frame(panes, padding=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text=label, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        details = tk.StringVar(value=path)
        ttk.Label(frame, textvariable=details, foreground=GRAPHITE_MUTED).grid(row=1, column=0, sticky="ew", pady=(0, 5))
        text = tk.Text(frame, wrap="none", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, insertbackground=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, relief="flat", borderwidth=0, font=("Consolas", 10))
        text.grid(row=2, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, command=text.yview)
        y_scroll.grid(row=2, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        x_scroll.grid(row=3, column=0, sticky="ew")
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text.tag_configure("added", background=GRAPHITE_ADDED)
        text.tag_configure("removed", background=GRAPHITE_REMOVED)
        text.tag_configure("changed", background=GRAPHITE_CHANGED)
        text.tag_configure("missing", foreground=GRAPHITE_MUTED)
        text.tag_configure("current_change", background="#365f7c")
        panes.add(frame, stretch="always")
        return text, details, y_scroll

    def configure_synced_scrollbars(self):
        def scroll_both(*args):
            self.left_text.yview(*args)
            self.right_text.yview(*args)

        self.left_scroll.configure(command=scroll_both)
        self.right_scroll.configure(command=scroll_both)

    def bind_synced_scroll(self):
        def scroll(event):
            units = -1 if event.delta > 0 else 1
            self.left_text.yview_scroll(units * 3, "units")
            self.right_text.yview_scroll(units * 3, "units")
            return "break"

        self.left_text.bind("<MouseWheel>", scroll, add="+")
        self.right_text.bind("<MouseWheel>", scroll, add="+")

    def populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.item_map = {}
        status_order = {"added": 0, "changed": 1, "removed": 2, "metadata": 3, "unchanged": 4}
        sorted_entries = sorted(enumerate(self.comparison["entries"]), key=lambda pair: (status_order.get(pair[1]["status"], 99), pair[1]["name"].casefold()))
        for index, item in sorted_entries:
            if item["status"] == "unchanged" and not self.show_unchanged_var.get():
                continue
            iid = f"compare:{index}"
            self.item_map[iid] = item
            self.tree.insert("", "end", iid=iid, text=item["name"], values=(item["status"].title(), self.format_size(item["left_size"]), self.format_size(item["right_size"])), tags=(item["status"],))
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def format_size(self, size):
        if size is None:
            return "-"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def on_select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = self.item_map[selected[0]]
        left_content, left_details = self.get_content("left", item)
        right_content, right_details = self.get_content("right", item)
        self.left_details.set(left_details)
        self.right_details.set(right_details)
        self.render_diff(left_content, right_content)

    def get_content(self, side, item):
        name = item[f"{side}_name"]
        if not name:
            return "", "File not present"
        path = self.comparison[side]["path"]
        try:
            return self.load_entry(path, name, side, item)
        except Exception as error:
            return f"Could not preview file:\n{error}", f"{name} | preview failed"

    def render_diff(self, left_content, right_content):
        left_lines = left_content.splitlines()
        right_lines = right_content.splitlines()
        if len(left_lines) + len(right_lines) > MAX_SIDE_BY_SIDE_DIFF_LINES:
            count = max(len(left_lines), len(right_lines))
            left_rows = [(index + 1 if index < len(left_lines) else None, left_lines[index] if index < len(left_lines) else None, "changed") for index in range(count)]
            right_rows = [(index + 1 if index < len(right_lines) else None, right_lines[index] if index < len(right_lines) else None, "changed") for index in range(count)]
            self.finish_diff_render(left_rows, right_rows)
            return
        matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
        left_rows = []
        right_rows = []
        left_number = 1
        right_number = 1
        for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
            left_block = left_lines[left_start:left_end]
            right_block = right_lines[right_start:right_end]
            count = max(len(left_block), len(right_block))
            for offset in range(count):
                left_line = left_block[offset] if offset < len(left_block) else None
                right_line = right_block[offset] if offset < len(right_block) else None
                if operation == "equal":
                    left_tag = right_tag = ""
                elif operation == "replace":
                    if left_line is None:
                        left_tag, right_tag = "missing", "added"
                    elif right_line is None:
                        left_tag, right_tag = "removed", "missing"
                    else:
                        left_tag = right_tag = "changed"
                elif operation == "delete":
                    left_tag, right_tag = "removed", "missing"
                else:
                    left_tag, right_tag = "missing", "added"
                left_rows.append((left_number if left_line is not None else None, left_line, left_tag))
                right_rows.append((right_number if right_line is not None else None, right_line, right_tag))
                if left_line is not None:
                    left_number += 1
                if right_line is not None:
                    right_number += 1
        self.finish_diff_render(left_rows, right_rows)

    def finish_diff_render(self, left_rows, right_rows):
        self.fill_diff_text(self.left_text, left_rows)
        self.fill_diff_text(self.right_text, right_rows)
        changed_rows = [index + 1 for index, (left, right) in enumerate(zip(left_rows, right_rows)) if left[2] in {"added", "removed", "changed"} or right[2] in {"added", "removed", "changed"}]
        self.change_blocks = []
        for row in changed_rows:
            if not self.change_blocks or row > self.change_blocks[-1][1] + 1:
                self.change_blocks.append([row, row])
            else:
                self.change_blocks[-1][1] = row
        self.current_change_index = -1
        state = "normal" if self.change_blocks else "disabled"
        self.previous_change_button.configure(state=state)
        self.next_change_button.configure(state=state)
        self.change_status_var.set(f"{len(self.change_blocks)} change(s)" if self.change_blocks else "No line changes")

    def go_to_change(self, direction):
        if not self.change_blocks:
            return
        if self.current_change_index < 0:
            self.current_change_index = 0 if direction > 0 else len(self.change_blocks) - 1
        else:
            self.current_change_index = (self.current_change_index + direction) % len(self.change_blocks)
        start, end = self.change_blocks[self.current_change_index]
        for widget in (self.left_text, self.right_text):
            widget.tag_remove("current_change", "1.0", "end")
            widget.tag_add("current_change", f"{start}.0", f"{end + 1}.0")
            widget.see(f"{start}.0")
        self.change_status_var.set(f"Change {self.current_change_index + 1} / {len(self.change_blocks)}")

    def fill_diff_text(self, widget, rows):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for number, content, tag in rows:
            prefix = "       | " if number is None else f"{number:6d} | "
            widget.insert("end", prefix + (content or "") + "\n", tag if tag else None)
        widget.configure(state="disabled")
        widget.yview_moveto(0)
