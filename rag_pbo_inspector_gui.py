"""
RaG PBO Inspector

Standalone graphite UI for inspecting and extracting DayZ PBO archives.
"""

import os
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
from rag_inspector_extract import (
    convert_bin_to_cpp,
    convert_rap_to_text,
    is_cfgconvert_candidate_bin_path,
    is_rap_text_convert_candidate_path,
    is_rapified_data,
    is_texheaders_bin_path,
)
from rag_inspector_p3d import build_p3d_info_report, get_p3d_metadata
from rag_inspector_settings import (
    find_cfgconvert,
    get_initial_dir_from_value,
    load_settings,
    resource_path,
    save_settings,
)
from rag_inspector_viewer import (
    TextViewerWindow,
    decode_text_data,
    get_entry_path_parts,
    get_syntax_mode,
    is_text_viewable_entry,
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
APP_VERSION = "0.7.18 Beta"
APP_ICON_FILE = os.path.join("assets", "HEADONLY_SQUARE_2k.ico")
MAX_TEXT_PREVIEW_BYTES = 5 * 1024 * 1024
MAX_P3D_INSPECT_BYTES = 128 * 1024 * 1024
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
        metadata = get_p3d_metadata(entry, self.entries, data)
        report = build_p3d_info_report(entry, metadata)
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
