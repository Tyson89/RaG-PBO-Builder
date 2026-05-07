"""
RaG PBO Inspector

Standalone graphite UI for inspecting and extracting DayZ PBO archives.
"""

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pbo_core import (
    PBO_STORED_METHOD,
    extract_pbo_files,
    format_byte_size,
    format_pbo_timestamp,
    get_pbo_method_label,
    read_pbo_archive,
)


APP_TITLE = "RaG PBO Inspector"
APP_VERSION = "0.7.0 Beta"
APP_ICON_FILE = os.path.join("assets", "HEADONLY_SQUARE_2k.ico")

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


class PboInspectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.archive = None
        self.entries = []
        self.pbo_path_var = tk.StringVar(value="")
        self.output_dir_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="No PBO loaded")

        self.title(APP_TITLE)
        self.geometry("1040x760")
        self.minsize(860, 640)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()

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
        tk.Label(left, text="Inspect and extract DayZ PBO archives", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(header, text=f"v{APP_VERSION}", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 14))

        path_frame = ttk.LabelFrame(outer, text="Archive", padding=12)
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

        action_frame = ttk.Frame(outer)
        action_frame.pack(fill="x", pady=(0, 10))
        self.make_button(action_frame, "Inspect", self.inspect_pbo, primary=True)
        self.make_button(action_frame, "Extract selected", self.extract_selected)
        self.make_button(action_frame, "Extract all", self.extract_all)
        self.make_button(action_frame, "Open output", self.open_output_folder)
        ttk.Label(action_frame, textvariable=self.summary_var, foreground=GRAPHITE_MUTED).pack(side="left", padx=(6, 0))

        content_frame = ttk.LabelFrame(outer, text="Contents", padding=10)
        content_frame.pack(fill="both", expand=True, pady=(0, 10))
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)

        columns = ("path", "size", "method", "timestamp")
        self.tree = ttk.Treeview(content_frame, columns=columns, show="headings", selectmode="extended", style="Pbo.Treeview")
        self.tree.heading("path", text="Path")
        self.tree.heading("size", text="Size")
        self.tree.heading("method", text="Method")
        self.tree.heading("timestamp", text="Timestamp")
        self.tree.column("path", width=560, anchor="w")
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("method", width=120, anchor="w")
        self.tree.column("timestamp", width=150, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

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


if __name__ == "__main__":
    app = PboInspectorApp()
    app.mainloop()
