import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rag_builder_storage import get_app_data_dir, load_json_file, resource_path, save_json_file
from rag_relocator_core import apply_scan, copy_and_apply_scan, find_path_candidates, normalize_virtual_path, scan_references
from rag_version import APP_VERSION


APP_TITLE = "RaG Mod Relocator"
APP_ICON_FILE = os.path.join("assets", "installer.ico")
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
GRAPHITE_SUCCESS = "#7fb087"


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

    def schedule(self, event=None):
        self.hide()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def show(self):
        self.after_id = None
        if self.window or not self.text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.configure(bg=GRAPHITE_BORDER)
        tk.Label(
            self.window,
            text=self.text,
            justify="left",
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            wraplength=480,
        ).pack(padx=1, pady=1)
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x = min(self.widget.winfo_rootx() + 18, self.widget.winfo_screenwidth() - width - 4)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + height > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - height - 6
        self.window.wm_geometry(f"+{max(0, x)}+{max(0, y)}")

    def hide(self, event=None):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.window:
            self.window.destroy()
            self.window = None


def add_tooltip(widget, text):
    tooltip = ToolTip(widget, text)
    widget._rag_tooltip = tooltip
    return tooltip


def get_settings_path():
    return get_app_data_dir() / "relocator_settings.json"


def get_initial_directory(value):
    value = str(value or "").strip()
    if value and os.path.isdir(value):
        return value
    return str(Path.home())


class RaGModRelocatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_json_file(get_settings_path())
        self.scan_result = None
        self.worker_queue = queue.Queue()
        self.worker_running = False
        self.last_backup_path = None
        self.selected_destination_folder = ""
        self.selected_destination_virtual = ""

        self.source_var = tk.StringVar(value=self.settings.get("source_folder", ""))
        self.old_path_var = tk.StringVar(value=self.settings.get("old_path", ""))
        self.new_path_var = tk.StringVar(value=self.settings.get("new_path", ""))
        self.backup_var = tk.BooleanVar(value=self.settings.get("create_backup", True))
        self.include_binary_var = tk.BooleanVar(value=self.settings.get("include_binary", True))
        self.include_pbo_var = tk.BooleanVar(value=self.settings.get("include_pbo", True))
        self.copy_to_new_folder_var = tk.BooleanVar(value=self.settings.get("copy_to_new_folder", False))
        self.backup_before_copy_value = bool(self.backup_var.get())
        self.copy_mode_active = False
        self.status_var = tk.StringVar(value="Select source folder and paths, then scan.")
        self.summary_var = tk.StringVar(value="No scan yet")
        self.mapping_old_var = tk.StringVar(value="<detect current path>")
        self.mapping_new_var = tk.StringVar(value="<select new path>")
        self.destination_display_var = tk.StringVar(value="Edits source folder in place")

        self.title(APP_TITLE)
        self.geometry("1140x840")
        self.minsize(960, 740)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        for variable in (self.source_var, self.old_path_var, self.new_path_var):
            variable.trace_add("write", self.invalidate_scan)
        self.refresh_mapping()

        if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
            self.source_var.set(os.path.abspath(sys.argv[1]))
            self.after_idle(self.start_detect)

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
                self._icon_image = image
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
        style.configure("Card.TFrame", background=GRAPHITE_CARD)
        style.configure("TLabelframe", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, relief="flat", padding=14)
        style.configure("TLabelframe.Label", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT)
        style.configure("FieldName.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT)
        style.configure("CardMuted.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_MUTED)
        style.configure("TEntry", fieldbackground=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, insertcolor=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, relief="flat", padding=7)
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=4)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("TButton", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_CARD_SOFT, relief="flat", padding=(12, 8))
        style.map("TButton", background=[("active", GRAPHITE_BORDER), ("pressed", GRAPHITE_ACCENT_DARK)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("Relocator.Treeview", background=GRAPHITE_FIELD, fieldbackground=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, rowheight=25)
        style.configure("Relocator.Treeview.Heading", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Relocator.Treeview", background=[("selected", GRAPHITE_ACCENT_DARK)], foreground=[("selected", "#ffffff")])

    def make_button(self, parent, text, command, primary=False, tooltip=""):
        if primary:
            background, hover, active, weight = GRAPHITE_ACCENT_DARK, GRAPHITE_ACCENT_HOVER, GRAPHITE_ACCENT, "bold"
        else:
            background, hover, active, weight = GRAPHITE_CARD_SOFT, GRAPHITE_BORDER, GRAPHITE_BORDER, "normal"
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg="#ffffff" if primary else GRAPHITE_TEXT,
            activebackground=active,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=8,
            font=("Segoe UI", 9, weight),
            cursor="hand2",
        )
        button.pack(side="left", padx=(0, 8))
        button.bind("<Enter>", lambda event: button.configure(bg=hover) if str(button.cget("state")) != "disabled" else None, add="+")
        button.bind("<Leave>", lambda event: button.configure(bg=background), add="+")
        if tooltip:
            add_tooltip(button, tooltip)
        return button

    def build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=GRAPHITE_HEADER, bd=0, highlightthickness=0)
        header.pack(fill="x", pady=(0, 10), ipady=5)
        left = tk.Frame(header, bg=GRAPHITE_HEADER)
        left.pack(side="left", fill="x", expand=True, padx=(14, 8))
        tk.Label(left, text=APP_TITLE, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="Rewrite DayZ mod path references across configs, scripts, materials, and other text source files.", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(header, text=f"v{APP_VERSION}", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 14))

        paths = ttk.LabelFrame(outer, text="Relocation", padding=12)
        paths.pack(fill="x", pady=(0, 10))
        paths.columnconfigure(1, weight=1)

        ttk.Label(paths, text="Source folder", style="FieldName.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        source_entry = ttk.Entry(paths, textvariable=self.source_var)
        source_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        source_button = ttk.Button(paths, text="Browse", command=self.choose_source)
        source_button.grid(row=0, column=2, sticky="e", pady=4)
        add_tooltip(source_entry, "Unpacked mod or addon folder to scan. This folder is edited only when copy mode is disabled.")
        add_tooltip(source_button, "Select unpacked mod or addon source folder.")

        ttk.Label(paths, text="Old mod path", style="FieldName.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.old_path_combo = ttk.Combobox(paths, textvariable=self.old_path_var)
        self.old_path_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        detect_button = ttk.Button(paths, text="Detect from Files", command=self.start_detect)
        detect_button.grid(row=1, column=2, sticky="e", pady=4)
        add_tooltip(self.old_path_combo, "Current virtual mod path. Detection alternatives appear in this editable dropdown.")
        add_tooltip(detect_button, "Find likely virtual path from nested prefix files, configs, scripts, and materials.")

        ttk.Label(paths, text="New mod path", style="FieldName.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        new_entry = ttk.Entry(paths, textvariable=self.new_path_var)
        new_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        new_entry.bind("<Return>", lambda event: self.start_scan(), add="+")
        new_button = ttk.Button(paths, text="Select Folder", command=self.choose_new_path)
        new_button.grid(row=2, column=2, sticky="e", pady=4)
        add_tooltip(new_entry, "Replacement virtual path written into matching references. Example: MyMod or MyMod\\Scripts.")
        add_tooltip(new_button, "Select destination folder in Explorer. Its drive-relative path becomes the new virtual mod path.")

        copy_check = ttk.Checkbutton(paths, text="Create new mod folder and copy everything", variable=self.copy_to_new_folder_var, command=self.on_copy_mode_changed)
        copy_check.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        self.backup_check = ttk.Checkbutton(paths, text="Create ZIP backup before applying changes", variable=self.backup_var)
        self.backup_check.grid(row=4, column=1, sticky="w", padx=8, pady=(2, 2))
        add_tooltip(copy_check, "Copy complete source tree to a new or empty destination, then relocate only the copy. Source stays untouched; ZIP backup is skipped.")
        add_tooltip(self.backup_check, "Back up every changed source file before in-place relocation. Disabled in copy mode.")
        scan_options = ttk.Frame(paths, style="Card.TFrame")
        scan_options.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(2, 2))
        binary_check = ttk.Checkbutton(scan_options, text="Scan binarized files", variable=self.include_binary_var, command=self.invalidate_scan)
        binary_check.pack(side="left")
        pbo_check = ttk.Checkbutton(scan_options, text="Scan PBO archives (slower)", variable=self.include_pbo_var, command=self.invalidate_scan)
        pbo_check.pack(side="left", padx=(12, 0))
        add_tooltip(binary_check, "Scan P3D, WRP, BIN, RTM, and similar files for size-preserving null-terminated path strings.")
        add_tooltip(pbo_check, "Open PBO archives and decompress supported Cprs entries. Slower; changed PBOs must be re-signed.")
        ttk.Label(
            paths,
            text="Text, null-terminated binary path strings, and supported PBO entries are scanned. Binary rewrites stay size-preserving. Modified PBOs must be re-signed.",
            style="CardMuted.TLabel",
            wraplength=850,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))

        mapping = ttk.LabelFrame(outer, text="Path mapping", padding=10)
        mapping.pack(fill="x", pady=(0, 10))
        current_card = tk.Frame(mapping, bg=GRAPHITE_FIELD, padx=12, pady=8)
        current_card.pack(side="left", fill="x", expand=True)
        tk.Label(current_card, text="CURRENT VIRTUAL PATH", bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(current_card, textvariable=self.mapping_old_var, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, font=("Consolas", 11, "bold"), anchor="w").pack(fill="x", pady=(3, 0))
        tk.Label(mapping, text="→", bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold"), padx=14).pack(side="left")
        new_card = tk.Frame(mapping, bg=GRAPHITE_ACCENT_DARK, padx=12, pady=8)
        new_card.pack(side="left", fill="x", expand=True)
        tk.Label(new_card, text="NEW VIRTUAL PATH", bg=GRAPHITE_ACCENT_DARK, fg="#e2caca", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(new_card, textvariable=self.mapping_new_var, bg=GRAPHITE_ACCENT_DARK, fg="#ffffff", font=("Consolas", 11, "bold"), anchor="w").pack(fill="x", pady=(3, 0))
        tk.Label(new_card, textvariable=self.destination_display_var, bg=GRAPHITE_ACCENT_DARK, fg="#e2caca", font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(3, 0))
        add_tooltip(current_card, "Current virtual path prefix found in file references.")
        add_tooltip(new_card, "Replacement virtual prefix and physical copy destination when copy mode is enabled.")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        self.scan_button = self.make_button(actions, "Scan / Preview", self.start_scan, primary=True, tooltip="Scan without changing files. Review every planned replacement and skipped binary match below.")
        self.apply_button = self.make_button(actions, "Apply Changes", self.start_apply, tooltip="Apply reviewed changes. Uses source backup in normal mode or creates a separate relocated copy in copy mode.")
        self.apply_button.configure(state="disabled")
        self.backup_button = self.make_button(actions, "Open Backup Folder", self.open_backup_folder, tooltip="Open folder containing latest relocation ZIP backup.")
        self.backup_button.configure(state="disabled")
        ttk.Label(actions, textvariable=self.summary_var, foreground=GRAPHITE_MUTED).pack(side="left", padx=(8, 0), pady=8)
        self.progress_bar = ttk.Progressbar(actions, mode="indeterminate", length=150)
        self.progress_bar.pack(side="right", padx=(8, 0), pady=8)
        add_tooltip(self.progress_bar, "Animated while detection, scanning, copying, or applying is active. Exact progress appears in status bar.")

        preview = ttk.LabelFrame(outer, text="Preview", padding=10)
        preview.pack(fill="both", expand=True, pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)

        columns = ("occurrences", "lines", "result")
        self.tree = ttk.Treeview(preview, columns=columns, show="tree headings", style="Relocator.Treeview")
        self.tree.heading("#0", text="File")
        self.tree.heading("occurrences", text="Matches")
        self.tree.heading("lines", text="Lines")
        self.tree.heading("result", text="Action")
        self.tree.column("#0", width=560, stretch=True)
        self.tree.column("occurrences", width=80, anchor="e", stretch=False)
        self.tree.column("lines", width=180, stretch=False)
        self.tree.column("result", width=130, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(preview, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("change", foreground=GRAPHITE_SUCCESS)
        self.tree.tag_configure("binary", foreground=GRAPHITE_WARNING)
        add_tooltip(self.tree, "Preview of affected files. Green rows will change; amber rows are unsafe binary matches left untouched.")

        status = tk.Label(outer, textvariable=self.status_var, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, anchor="w", padx=10, pady=7, font=("Segoe UI", 9))
        status.pack(fill="x")
        self.on_copy_mode_changed(invalidate=False)

    def invalidate_scan(self, *args):
        had_scan = self.scan_result is not None
        self.scan_result = None
        if hasattr(self, "apply_button"):
            self.apply_button.configure(state="disabled")
        if had_scan and hasattr(self, "tree"):
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.summary_var.set("Inputs changed; scan again")
            self.status_var.set("Path inputs changed. Run a new scan before applying.")
        self.refresh_mapping()

    def choose_source(self):
        path = filedialog.askdirectory(title="Select mod or addon source folder", initialdir=get_initial_directory(self.source_var.get()), parent=self)
        if path:
            self.source_var.set(path)
            self.status_var.set("Source selected. Click Detect from Files to find its virtual mod path.")

    def choose_new_path(self):
        source = self.source_var.get().strip()
        source_drive = os.path.splitdrive(source)[0]
        current = normalize_virtual_path(self.new_path_var.get())
        candidate = os.path.join(source_drive + os.sep, *current.split("\\")) if source_drive and current else ""
        initial = candidate if candidate and os.path.isdir(candidate) else get_initial_directory(source)
        path = filedialog.askdirectory(title="Select new mod path folder", initialdir=initial, parent=self)
        if not path:
            return
        drive, tail = os.path.splitdrive(os.path.abspath(path))
        virtual_path = normalize_virtual_path(tail) if drive else normalize_virtual_path(path)
        if not virtual_path:
            messagebox.showerror(APP_TITLE, "Drive root cannot be used as a mod path.", parent=self)
            return
        self.selected_destination_folder = os.path.abspath(path)
        self.selected_destination_virtual = virtual_path
        self.new_path_var.set(virtual_path)
        self.status_var.set(f"Selected folder: {path} → virtual path: {virtual_path}")

    def resolve_destination_folder(self):
        virtual_path = normalize_virtual_path(self.new_path_var.get())
        if not virtual_path:
            return ""
        if self.selected_destination_folder and virtual_path.casefold() == self.selected_destination_virtual.casefold():
            return self.selected_destination_folder
        source = self.source_var.get().strip()
        drive = os.path.splitdrive(source)[0]
        if drive:
            return os.path.abspath(os.path.join(drive + os.sep, *virtual_path.split("\\")))
        if source:
            return os.path.abspath(os.path.join(os.path.dirname(source), *virtual_path.split("\\")))
        return ""

    def on_copy_mode_changed(self, invalidate=True):
        copy_mode = bool(self.copy_to_new_folder_var.get())
        if copy_mode and not self.copy_mode_active:
            self.backup_before_copy_value = bool(self.backup_var.get())
            self.backup_var.set(False)
        elif not copy_mode and self.copy_mode_active:
            self.backup_var.set(self.backup_before_copy_value)
        self.copy_mode_active = copy_mode
        self.backup_check.configure(state="disabled" if copy_mode else "normal")
        if invalidate:
            self.invalidate_scan()
        else:
            self.refresh_mapping()

    def refresh_mapping(self):
        old_path = normalize_virtual_path(self.old_path_var.get()) or "<detect current path>"
        new_path = normalize_virtual_path(self.new_path_var.get()) or "<select new path>"
        self.mapping_old_var.set(old_path + ("\\..." if not old_path.startswith("<") else ""))
        self.mapping_new_var.set(new_path + ("\\..." if not new_path.startswith("<") else ""))
        if self.copy_to_new_folder_var.get():
            destination = self.resolve_destination_folder()
            self.destination_display_var.set(f"Copy destination: {destination or '<select destination folder>'}")
        else:
            self.destination_display_var.set("Edits source folder in place; ZIP backup used")

    def start_detect(self):
        if self.worker_running:
            return
        source = self.source_var.get().strip()
        if not os.path.isdir(source):
            messagebox.showerror(APP_TITLE, "Select a valid source folder first.", parent=self)
            return
        self.status_var.set("Detecting virtual path from prefix files, configs, scripts, and materials...")
        self.set_busy(True)

        def worker():
            try:
                progress = lambda count, path: self.worker_queue.put(("progress_detect", (count, path)))
                self.worker_queue.put(("detect_done", find_path_candidates(source, limit=10, progress=progress)))
            except Exception as error:
                self.worker_queue.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def set_busy(self, busy):
        self.worker_running = busy
        self.scan_button.configure(state="disabled" if busy else "normal")
        self.apply_button.configure(state="disabled" if busy or not self.scan_result or not self.scan_result.changes else "normal")
        self.configure(cursor="wait" if busy else "")
        if busy:
            self.progress_bar.start(12)
        else:
            self.progress_bar.stop()

    def start_scan(self):
        if self.worker_running:
            return
        source = self.source_var.get().strip()
        old_path = self.old_path_var.get().strip()
        new_path = self.new_path_var.get().strip()
        include_binary = bool(self.include_binary_var.get())
        include_pbo = bool(self.include_pbo_var.get())
        self.clear_preview()
        self.summary_var.set("Scanning...")
        self.status_var.set("Scanning source files...")
        self.set_busy(True)

        def worker():
            try:
                progress = lambda count, path: self.worker_queue.put(("progress", (count, path)))
                self.worker_queue.put(("scan_done", scan_references(source, old_path, new_path, include_binary, include_pbo, progress)))
            except Exception as error:
                self.worker_queue.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def start_apply(self):
        if self.worker_running or not self.scan_result or not self.scan_result.changes:
            return
        result = self.scan_result
        copy_mode = bool(self.copy_to_new_folder_var.get())
        destination = self.resolve_destination_folder() if copy_mode else ""
        if copy_mode and not destination:
            messagebox.showerror(APP_TITLE, "Select or enter a valid new mod path folder.", parent=self)
            return
        compiled_changes = [change for change in result.changes if change.kind != "Text"]
        if compiled_changes and not copy_mode and not self.backup_var.get():
            messagebox.showerror(APP_TITLE, "ZIP backup is required for binary or PBO changes.", parent=self)
            return
        if copy_mode:
            prompt = (
                f"Copy everything to this new folder, then replace {result.replacements} path reference(s) in the copy?\n\n"
                f"Destination: {destination}\n\nSource remains untouched. No ZIP backup will be created."
            )
        else:
            prompt = (
                f"Replace {result.replacements} path reference(s) in {result.changed_files} file(s)?\n\n"
                f"{result.old_path}\n→ {result.new_path}"
            )
        if result.binary_candidates:
            prompt += f"\n\n{len(result.binary_candidates)} unsafe binary match group(s) will stay unchanged."
        if any(change.kind == "PBO archive" for change in compiled_changes):
            prompt += "\n\nModified PBO signatures will become invalid. Re-sign every changed PBO."
        if not messagebox.askyesno(APP_TITLE, prompt, parent=self):
            return
        create_backup = bool(self.backup_var.get()) and not copy_mode
        self.status_var.set("Copying source folder..." if copy_mode else "Applying changes...")
        self.set_busy(True)

        def worker():
            try:
                if copy_mode:
                    progress = lambda message: self.worker_queue.put(("progress_copy", message))
                    applied = copy_and_apply_scan(result, destination, progress)
                else:
                    applied = apply_scan(result, create_backup)
                self.worker_queue.put(("apply_done", applied))
            except Exception as error:
                self.worker_queue.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def poll_worker(self):
        while True:
            try:
                result_type, payload = self.worker_queue.get_nowait()
            except queue.Empty:
                self.after(100, self.poll_worker)
                return
            if result_type in {"progress", "progress_detect"}:
                count, path = payload
                prefix = "Detecting from" if result_type == "progress_detect" else "Examining"
                self.status_var.set(f"{prefix} file {count}: {path}")
                continue
            if result_type == "progress_copy":
                self.status_var.set(payload)
                continue
            break
        self.set_busy(False)
        if result_type == "error":
            self.summary_var.set("Operation failed")
            self.status_var.set(payload)
            messagebox.showerror(APP_TITLE, payload, parent=self)
            return
        if result_type == "scan_done":
            self.show_scan(payload)
            return
        if result_type == "detect_done":
            if not payload:
                self.status_var.set("No virtual mod path detected.")
                messagebox.showwarning(APP_TITLE, "No virtual path found in prefix files, configs, scripts, or materials. Enter the current virtual path manually.", parent=self)
                return
            self.old_path_combo.configure(values=payload)
            self.old_path_var.set(payload[0])
            alternatives = f" {len(payload) - 1} alternative(s) available in dropdown." if len(payload) > 1 else ""
            self.status_var.set(f"Detected main mod path: {payload[0]}.{alternatives}")
            return
        self.scan_result = None
        self.apply_button.configure(state="disabled")
        for item in self.tree.get_children():
            values = list(self.tree.item(item, "values"))
            if len(values) == 3 and "safe" not in str(values[2]).casefold() and "unsupported" not in str(values[2]).casefold():
                verb = "Copied" if payload.destination_path else "Updated"
                values[2] = f"{verb} ({values[2]})"
                self.tree.item(item, values=values)
        self.last_backup_path = payload.backup_path
        self.backup_button.configure(state="normal" if self.last_backup_path else "disabled")
        if payload.destination_path:
            self.summary_var.set(f"Copied complete folder; updated {payload.changed_files} files, {payload.replacements} replacements")
        else:
            self.summary_var.set(f"Applied: {payload.changed_files} files, {payload.replacements} replacements")
        backup_text = f" Backup: {payload.backup_path}" if payload.backup_path else ""
        destination_text = f" Destination: {payload.destination_path}" if payload.destination_path else ""
        self.status_var.set(f"Relocation complete.{destination_text}{backup_text}")
        if payload.destination_path:
            message = f"Copied complete source folder.\nUpdated {payload.changed_files} file(s).\nReplaced {payload.replacements} path reference(s).{destination_text}"
        else:
            message = f"Updated {payload.changed_files} file(s).\nReplaced {payload.replacements} path reference(s).{backup_text}"
        messagebox.showinfo(APP_TITLE, message, parent=self)

    def clear_preview(self):
        self.scan_result = None
        self.apply_button.configure(state="disabled")
        for item in self.tree.get_children():
            self.tree.delete(item)

    def show_scan(self, result):
        self.scan_result = result
        if not result.changes and result.suggested_paths:
            suggestion = next((path for path in result.suggested_paths if path.casefold() != result.old_path.casefold()), "")
            if suggestion and messagebox.askyesno(
                APP_TITLE,
                f"No references matched '{result.old_path}'.\n\nLikely virtual path: {suggestion}\n\nUse it and scan again?",
                parent=self,
            ):
                self.old_path_var.set(suggestion)
                self.after_idle(self.start_scan)
                return
        for change in result.changes:
            lines = ", ".join(str(line) for line in change.line_numbers[:10])
            if len(change.line_numbers) > 10:
                lines += ", ..."
            self.tree.insert("", "end", text=change.relative_path, values=(change.occurrences, lines or "—", change.kind), tags=("change",))
        for candidate in result.binary_candidates:
            self.tree.insert("", "end", text=candidate.relative_path, values=(candidate.occurrences, "—", candidate.reason), tags=("binary",))
        self.apply_button.configure(state="normal" if result.changes else "disabled")
        binary_changes = sum(change.kind != "Text" for change in result.changes)
        binary = f", {binary_changes} compiled/PBO" if binary_changes else ""
        self.summary_var.set(f"{result.changed_files} files, {result.replacements} replacements{binary}")
        skipped = f"; {len(result.binary_candidates)} binary candidate(s) skipped" if result.binary_candidates else ""
        large = f"; {result.files_too_large} oversized file(s) skipped" if result.files_too_large else ""
        unreadable = f"; {result.files_unreadable} unreadable file(s) skipped" if result.files_unreadable else ""
        ignored = f"; {result.files_ignored} unrelated asset(s) ignored" if result.files_ignored else ""
        if result.changes:
            self.status_var.set(f"Scanned {result.files_scanned} relevant files{ignored}{skipped}{large}{unreadable}. Review preview before applying.")
        else:
            self.status_var.set(f"No safe replacements found in {result.files_scanned} relevant files{ignored}{skipped}{large}{unreadable}. Check old virtual path and skipped rows.")

    def open_backup_folder(self):
        if not self.last_backup_path:
            return
        folder = str(self.last_backup_path.parent)
        try:
            os.startfile(folder)
        except AttributeError:
            subprocess.Popen(["explorer", folder])
        except OSError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def save_settings(self):
        save_json_file(get_settings_path(), {
            "source_folder": self.source_var.get().strip(),
            "old_path": self.old_path_var.get().strip(),
            "new_path": self.new_path_var.get().strip(),
            "create_backup": self.backup_before_copy_value if self.copy_to_new_folder_var.get() else bool(self.backup_var.get()),
            "include_binary": bool(self.include_binary_var.get()),
            "include_pbo": bool(self.include_pbo_var.get()),
            "copy_to_new_folder": bool(self.copy_to_new_folder_var.get()),
        })

    def on_close(self):
        if self.worker_running and not messagebox.askyesno(APP_TITLE, "Operation still running. Close anyway?", parent=self):
            return
        try:
            self.save_settings()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = RaGModRelocatorApp()
    app.mainloop()
