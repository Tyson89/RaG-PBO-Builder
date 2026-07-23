import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from rag_builder_storage import get_app_data_dir, load_json_file, resource_path, save_json_file
from rag_publisher_core import (
    PublishRequest,
    SteamBridgeError,
    SteamworksBackend,
    WORKSHOP_LEGAL_URL,
    format_file_size,
    parse_workshop_id,
    publish_workshop_item,
    scan_mod_folder,
    validate_publish_request,
    workshop_url,
)
from rag_version import APP_VERSION


APP_TITLE = "RaG Workshop Publisher"
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
GRAPHITE_ERROR = "#df7777"


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
            wraplength=500,
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
    return get_app_data_dir() / "publisher_settings.json"


def get_logs_directory():
    path = get_app_data_dir() / "publisher_logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_initial_directory(value):
    value = str(value or "").strip()
    if value and os.path.isdir(value):
        return value
    if value and os.path.isfile(value):
        return os.path.dirname(value)
    return str(Path.home())


def report_signature(report):
    files = tuple((entry.relative_path.casefold(), entry.size, entry.modified_ns) for entry in report.files)
    excluded = tuple((entry.relative_path.casefold(), entry.size, entry.modified_ns, entry.detail) for entry in report.excluded)
    return files, excluded


class RaGWorkshopPublisherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_json_file(get_settings_path())
        profiles = self.settings.get("profiles", {})
        self.profiles = profiles if isinstance(profiles, dict) else {}
        for profile in self.profiles.values():
            if isinstance(profile, dict):
                profile.pop("steamcmd_path", None)
                profile.pop("steam_username", None)
        recent_ids = self.settings.get("recent_workshop_ids", [])
        self.recent_workshop_ids = [str(value) for value in recent_ids if str(value).isdigit()][:12]
        self.report = None
        self.worker_queue = queue.Queue()
        self.worker_running = False
        self.last_log_path = None
        self.current_item_title = None
        self.current_item_description = None
        self.current_item_tags = None
        self.current_item_visibility = None
        self.current_item_description_id = None
        self.backend = SteamworksBackend()

        self.profile_var = tk.StringVar(value=self.settings.get("active_profile", ""))
        self.mod_folder_var = tk.StringVar(value=self.settings.get("mod_folder", ""))
        self.workshop_item_var = tk.StringVar(value=self.settings.get("workshop_item", ""))
        self.preview_var = tk.StringVar(value=self.settings.get("preview_file", ""))
        self.open_after_var = tk.BooleanVar(value=self.settings.get("open_after_publish", True))
        self.update_metadata_var = tk.BooleanVar(value=self.settings.get("update_metadata", False))
        self.metadata_title_var = tk.StringVar(value=self.settings.get("metadata_title", ""))
        self.metadata_tags_var = tk.StringVar(value=self.settings.get("metadata_tags", ""))
        self.metadata_visibility_var = tk.StringVar(value=self.settings.get("metadata_visibility", ""))
        self.steam_client_var = tk.StringVar(value="Checking...")
        self.steam_connection_var = tk.StringVar(value="Checking...")
        self.steam_user_var = tk.StringVar(value="Checking...")
        self.steam_id_var = tk.StringVar(value="Checking...")
        self.steam_context_var = tk.StringVar(value="Checking...")
        self.item_details_var = tk.StringVar(value="Not validated")
        self.status_var = tk.StringVar(value="Select prepared mod folder, then run Preflight.")
        self.summary_var = tk.StringVar(value="No preflight yet")
        self.target_var = tk.StringVar(value="<Workshop item>")
        self.content_var = tk.StringVar(value="<prepared mod folder>")
        self.safety_var = tk.StringVar(value="Update-only; metadata preserved")

        self.title(APP_TITLE)
        self.geometry("1400x1000")
        self.minsize(1100, 800)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.mod_folder_var.trace_add("write", self.invalidate_report)
        self.workshop_item_var.trace_add("write", self.refresh_summary_cards)
        self.refresh_profile_names()
        self.refresh_summary_cards()
        if self.profile_var.get() in self.profiles:
            self.load_profile(self.profile_var.get())
        if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
            self.mod_folder_var.set(os.path.abspath(sys.argv[1]))
        self.after(200, self.refresh_steam_status)

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
        style.configure("TCombobox", fieldbackground=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, arrowcolor=GRAPHITE_TEXT, padding=6)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", GRAPHITE_FIELD), ("disabled", GRAPHITE_FIELD)],
            foreground=[("readonly", GRAPHITE_TEXT), ("disabled", GRAPHITE_MUTED)],
            selectbackground=[("readonly", GRAPHITE_FIELD)],
            selectforeground=[("readonly", GRAPHITE_TEXT)],
            background=[("readonly", GRAPHITE_CARD_SOFT), ("active", GRAPHITE_BORDER)],
            arrowcolor=[("readonly", GRAPHITE_TEXT), ("disabled", GRAPHITE_MUTED)],
        )
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=4)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("TButton", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_CARD_SOFT, relief="flat", padding=(12, 8))
        style.map("TButton", background=[("active", GRAPHITE_BORDER), ("pressed", GRAPHITE_ACCENT_DARK)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("Publisher.Treeview", background=GRAPHITE_FIELD, fieldbackground=GRAPHITE_FIELD, foreground=GRAPHITE_TEXT, bordercolor=GRAPHITE_BORDER, rowheight=25)
        style.configure("Publisher.Treeview.Heading", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Publisher.Treeview", background=[("selected", GRAPHITE_ACCENT_DARK)], foreground=[("selected", "#ffffff")])
        style.configure("Publisher.TNotebook", background=GRAPHITE_BG, borderwidth=0)
        style.configure("Publisher.TNotebook.Tab", background=GRAPHITE_CARD_SOFT, foreground=GRAPHITE_TEXT, padding=(14, 7))
        style.map("Publisher.TNotebook.Tab", background=[("selected", GRAPHITE_ACCENT_DARK), ("active", GRAPHITE_BORDER)])

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
        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)
        self.content_canvas = tk.Canvas(content, bg=GRAPHITE_BG, highlightthickness=0, borderwidth=0)
        content_scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.content_canvas.yview)
        content_scrollbar.pack(side="right", fill="y")
        self.content_canvas.pack(side="left", fill="both", expand=True)
        self.content_canvas.configure(yscrollcommand=content_scrollbar.set)

        outer = ttk.Frame(self.content_canvas, padding=16)
        self.content_window = self.content_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self.update_content_scrollregion, add="+")
        self.content_canvas.bind("<Configure>", self.resize_content_width, add="+")
        self.bind("<MouseWheel>", self.scroll_content, add="+")

        header = tk.Frame(outer, bg=GRAPHITE_HEADER, bd=0, highlightthickness=0)
        header.pack(fill="x", pady=(0, 10))
        header_top = tk.Frame(header, bg=GRAPHITE_HEADER)
        header_top.pack(fill="x", padx=14, pady=(8, 3))
        left = tk.Frame(header_top, bg=GRAPHITE_HEADER)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=APP_TITLE, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="Update DayZ Workshop mods through the signed-in desktop Steam account.", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(header_top, text=f"v{APP_VERSION}", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 0))

        steam_status = tk.Frame(header, bg=GRAPHITE_HEADER)
        steam_status.pack(fill="x", padx=14, pady=(3, 9))
        status_fields = (
            ("Steam", self.steam_client_var),
            ("Connection", self.steam_connection_var),
            ("User", self.steam_user_var),
            ("Steam ID", self.steam_id_var),
            ("DayZ", self.steam_context_var),
        )
        for label, variable in status_fields:
            card = tk.Frame(steam_status, bg=GRAPHITE_FIELD, padx=8, pady=3)
            card.pack(side="left", fill="x", expand=True, padx=(0, 5))
            tk.Label(card, text=label.upper(), bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=variable, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, font=("Segoe UI", 8), anchor="w").pack(fill="x")
        refresh_steam_button = ttk.Button(steam_status, text="Refresh", command=self.refresh_steam_status)
        refresh_steam_button.pack(side="right", fill="y")
        add_tooltip(refresh_steam_button, "Check desktop Steam client, signed-in account, DayZ license, and DayZ Tools context.")

        profiles = ttk.LabelFrame(outer, text="Publishing profile", padding=10)
        profiles.pack(fill="x", pady=(0, 10))
        profiles.columnconfigure(1, weight=1)
        ttk.Label(profiles, text="Profile", style="FieldName.TLabel").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(profiles, textvariable=self.profile_var, state="readonly")
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda event: self.load_profile(self.profile_var.get()), add="+")
        save_profile_button = ttk.Button(profiles, text="Save Profile", command=self.save_profile)
        save_profile_button.grid(row=0, column=2, padx=(0, 6))
        delete_profile_button = ttk.Button(profiles, text="Delete", command=self.delete_profile)
        delete_profile_button.grid(row=0, column=3)
        add_tooltip(self.profile_combo, "Saved mod, Workshop item, preview, change-note, and optional metadata settings.")
        add_tooltip(save_profile_button, "Save current Publisher fields as a named reusable profile.")
        add_tooltip(delete_profile_button, "Delete selected Publisher profile. Mod files and Workshop content remain untouched.")

        fields = ttk.LabelFrame(outer, text="Workshop update", padding=12)
        fields.pack(fill="x", pady=(0, 10))
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(4, weight=1)

        ttk.Label(fields, text="Prepared mod folder", style="FieldName.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        mod_entry = ttk.Entry(fields, textvariable=self.mod_folder_var)
        mod_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        mod_button = ttk.Button(fields, text="Browse", command=self.choose_mod_folder)
        mod_button.grid(row=0, column=2, sticky="ew", pady=4)
        add_tooltip(mod_entry, "Prepared @mod folder containing root Addons folder, signed PBOs, public keys, and optional mod.cpp.")
        add_tooltip(mod_button, "Select prepared mod folder to validate and upload.")

        ttk.Label(fields, text="Workshop item", style="FieldName.TLabel").grid(row=0, column=3, sticky="w", padx=(20, 0), pady=4)
        self.workshop_combo = ttk.Combobox(
            fields,
            textvariable=self.workshop_item_var,
            values=self.recent_workshop_ids,
        )
        workshop_entry = self.workshop_combo
        workshop_entry.grid(row=0, column=4, sticky="ew", padx=8, pady=4)
        workshop_buttons = ttk.Frame(fields, style="Card.TFrame")
        workshop_buttons.grid(row=0, column=5, sticky="ew", pady=4)
        validate_item_button = ttk.Button(workshop_buttons, text="Validate", command=self.validate_workshop_item)
        validate_item_button.pack(side="left", fill="x", expand=True)
        workshop_button = ttk.Button(workshop_buttons, text="Open", command=self.open_workshop_page)
        workshop_button.pack(side="left", fill="x", expand=True, padx=(5, 0))
        add_tooltip(workshop_entry, "Existing Workshop URL or numeric Published File ID. Steam decides whether logged-in account may update it.")
        add_tooltip(validate_item_button, "Query item through Steam and verify it belongs to DayZ. Owner mismatch is allowed for contributors.")
        add_tooltip(workshop_button, "Open entered Workshop item in browser.")

        ttk.Label(fields, text="Preview image", style="FieldName.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        preview_entry = ttk.Entry(fields, textvariable=self.preview_var)
        preview_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        preview_buttons = ttk.Frame(fields, style="Card.TFrame")
        preview_buttons.grid(row=1, column=2, sticky="ew", pady=4)
        preview_button = ttk.Button(preview_buttons, text="Browse", command=self.choose_preview)
        preview_button.pack(side="left", fill="x", expand=True)
        clear_preview_button = ttk.Button(preview_buttons, text="Clear", command=lambda: self.preview_var.set(""))
        clear_preview_button.pack(side="left", fill="x", expand=True, padx=(5, 0))
        add_tooltip(preview_entry, "Optional Workshop preview image smaller than 1 MB. Leave empty to preserve existing preview.")
        add_tooltip(preview_button, "Select optional JPG, PNG, GIF, or BMP preview image smaller than 1 MB.")
        add_tooltip(clear_preview_button, "Do not send previewfile field, preserving current Workshop preview.")

        options_label = ttk.Label(fields, text="Options", style="FieldName.TLabel")
        options_label.grid(row=1, column=3, sticky="w", padx=(20, 0), pady=4)
        options = ttk.Frame(fields, style="Card.TFrame")
        options.grid(row=1, column=4, columnspan=2, sticky="w", padx=8, pady=4)
        open_after_check = ttk.Checkbutton(options, text="Open Workshop page after successful upload", variable=self.open_after_var)
        open_after_check.pack(side="left")
        add_tooltip(open_after_check, "Open updated Workshop item for verification after Steam confirms success.")

        ttk.Label(fields, text="Change notes", style="FieldName.TLabel").grid(row=2, column=0, sticky="nw", pady=(7, 4))
        change_notes_frame = ttk.Frame(fields, style="Card.TFrame")
        change_notes_frame.grid(row=2, column=1, columnspan=5, sticky="ew", padx=(8, 0), pady=(7, 4))
        self.change_notes = tk.Text(
            change_notes_frame,
            height=3,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_ACCENT_DARK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=GRAPHITE_BORDER,
            highlightcolor=GRAPHITE_ACCENT,
            padx=8,
            pady=7,
            wrap="word",
            font=("Segoe UI", 10),
        )
        change_notes_scrollbar = ttk.Scrollbar(change_notes_frame, orient="vertical", command=self.change_notes.yview)
        change_notes_scrollbar.pack(side="right", fill="y")
        self.change_notes.pack(side="left", fill="both", expand=True)
        self.change_notes.configure(yscrollcommand=change_notes_scrollbar.set)
        self.change_notes.insert("1.0", self.settings.get("change_note", ""))
        add_tooltip(self.change_notes, "Required Workshop update notes. Content-only mode leaves all metadata unchanged.")

        metadata = ttk.LabelFrame(outer, text="Optional metadata", padding=10)
        metadata.pack(fill="x", pady=(0, 10))
        metadata.columnconfigure(1, weight=1)
        metadata.columnconfigure(3, weight=1)
        metadata_check = ttk.Checkbutton(
            metadata,
            text="Update metadata",
            variable=self.update_metadata_var,
            command=self.toggle_metadata_controls,
        )
        metadata_check.grid(row=0, column=0, sticky="w", pady=4)
        metadata_title_label = ttk.Label(metadata, text="Title", style="FieldName.TLabel")
        metadata_title_label.grid(row=0, column=1, sticky="e", padx=(12, 4))
        self.metadata_title_entry = ttk.Entry(metadata, textvariable=self.metadata_title_var)
        self.metadata_title_entry.grid(row=0, column=2, sticky="ew", padx=(0, 12))
        metadata_visibility_label = ttk.Label(metadata, text="Visibility", style="FieldName.TLabel")
        metadata_visibility_label.grid(row=0, column=3, sticky="e", padx=(0, 4))
        self.metadata_visibility_combo = ttk.Combobox(
            metadata,
            textvariable=self.metadata_visibility_var,
            values=("", "public", "friends_only", "private", "unlisted"),
            state="readonly",
            width=16,
        )
        self.metadata_visibility_combo.grid(row=0, column=4, sticky="ew")
        metadata_tags_label = ttk.Label(metadata, text="Tags", style="FieldName.TLabel")
        metadata_tags_label.grid(row=1, column=0, sticky="w", pady=4)
        self.metadata_tags_entry = ttk.Entry(metadata, textvariable=self.metadata_tags_var)
        self.metadata_tags_entry.grid(row=1, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)
        metadata_description_label = ttk.Label(metadata, text="Description (Steam BBCode)", style="FieldName.TLabel")
        metadata_description_label.grid(row=2, column=0, sticky="w", pady=(8, 4))
        self.load_description_button = ttk.Button(
            metadata,
            text="Load Current Metadata",
            command=self.load_current_metadata,
            state="disabled",
        )
        self.load_description_button.grid(row=2, column=4, sticky="e", pady=(8, 4))
        metadata_description_frame = ttk.Frame(metadata)
        metadata_description_frame.grid(row=3, column=0, columnspan=5, sticky="ew")
        self.metadata_description = tk.Text(
            metadata_description_frame,
            height=12,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_ACCENT_DARK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=GRAPHITE_BORDER,
            highlightcolor=GRAPHITE_ACCENT,
            padx=8,
            pady=5,
            wrap="word",
            font=("Segoe UI", 9),
        )
        metadata_description_scrollbar = ttk.Scrollbar(
            metadata_description_frame,
            orient="vertical",
            command=self.metadata_description.yview,
        )
        metadata_description_scrollbar.pack(side="right", fill="y")
        self.metadata_description.pack(side="left", fill="both", expand=True)
        self.metadata_description.configure(yscrollcommand=metadata_description_scrollbar.set)
        self.metadata_description.insert("1.0", self.settings.get("metadata_description", ""))
        self.metadata_optional_widgets = (
            metadata_title_label,
            self.metadata_title_entry,
            metadata_visibility_label,
            self.metadata_visibility_combo,
            metadata_tags_label,
            self.metadata_tags_entry,
            metadata_description_label,
            self.load_description_button,
            metadata_description_frame,
        )
        add_tooltip(metadata_check, "Off by default. When off, title, description, tags, visibility, and preview remain unchanged except an explicitly selected preview.")
        add_tooltip(self.metadata_tags_entry, "Comma-separated Workshop tags. Empty list clears tags when metadata update is enabled.")
        add_tooltip(self.load_description_button, "Load current Workshop title, tags, visibility, and description. Submitted BBCode and line breaks are preserved.")
        self.toggle_metadata_controls()

        mapping = ttk.LabelFrame(outer, text="Publish target", padding=10)
        mapping.pack(fill="x", pady=(0, 10))
        target_card = tk.Frame(mapping, bg=GRAPHITE_FIELD, padx=12, pady=8)
        target_card.pack(side="left", fill="x", expand=True)
        tk.Label(target_card, text="WORKSHOP ITEM", bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(target_card, textvariable=self.target_var, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, font=("Consolas", 11, "bold"), anchor="w").pack(fill="x", pady=(3, 0))
        content_card = tk.Frame(mapping, bg=GRAPHITE_FIELD, padx=12, pady=8)
        content_card.pack(side="left", fill="x", expand=True, padx=8)
        tk.Label(content_card, text="UPLOAD CONTENT", bg=GRAPHITE_FIELD, fg=GRAPHITE_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(content_card, textvariable=self.content_var, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", pady=(3, 0))
        safety_card = tk.Frame(mapping, bg=GRAPHITE_ACCENT_DARK, padx=12, pady=8)
        safety_card.pack(side="left", fill="x", expand=True)
        tk.Label(safety_card, text="SAFE UPDATE MODE", bg=GRAPHITE_ACCENT_DARK, fg="#e2caca", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(safety_card, textvariable=self.safety_var, bg=GRAPHITE_ACCENT_DARK, fg="#ffffff", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(3, 0))
        add_tooltip(safety_card, "Default sends only existing item ID, staged content, optional preview, and change notes through ISteamUGC.")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        self.preflight_button = self.make_button(actions, "Preflight", self.start_preflight, primary=True, tooltip="Build exact upload manifest without changing local or Workshop files.")
        self.publish_button = self.make_button(actions, "Publish Update", self.start_publish, tooltip="Stage approved files, then upload through signed-in desktop Steam account.")
        self.publish_button.configure(state="disabled")
        self.log_button = self.make_button(actions, "Open Log", self.open_last_log, tooltip="Open latest Steamworks Publisher log.")
        self.log_button.configure(state="disabled")
        ttk.Label(actions, textvariable=self.summary_var, foreground=GRAPHITE_MUTED).pack(side="left", padx=(8, 0), pady=8)
        self.progress_bar = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress_bar.pack(side="right", padx=(8, 0), pady=8)

        status = tk.Label(outer, textvariable=self.status_var, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, anchor="w", padx=10, pady=7, font=("Segoe UI", 9))
        status.pack(side="bottom", fill="x")

        self.notebook = ttk.Notebook(outer, style="Publisher.TNotebook")
        self.notebook.pack(fill="both", expand=True, pady=(0, 10))
        manifest_tab = ttk.Frame(self.notebook, padding=8)
        log_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(manifest_tab, text="Upload Manifest")
        self.notebook.add(log_tab, text="Steamworks Log")

        manifest_tab.columnconfigure(0, weight=1)
        manifest_tab.rowconfigure(0, weight=1)
        columns = ("size", "kind", "action")
        self.tree = ttk.Treeview(manifest_tab, columns=columns, show="tree headings", style="Publisher.Treeview", height=6)
        self.tree.heading("#0", text="File / Check")
        self.tree.heading("size", text="Size")
        self.tree.heading("kind", text="Type")
        self.tree.heading("action", text="Action")
        self.tree.column("#0", width=650, stretch=True)
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.column("kind", width=130, stretch=False)
        self.tree.column("action", width=120, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        manifest_scrollbar = ttk.Scrollbar(manifest_tab, command=self.tree.yview)
        manifest_scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=manifest_scrollbar.set)
        self.tree.tag_configure("upload", foreground=GRAPHITE_SUCCESS)
        self.tree.tag_configure("excluded", foreground=GRAPHITE_MUTED)
        self.tree.tag_configure("warning", foreground=GRAPHITE_WARNING)
        self.tree.tag_configure("error", foreground=GRAPHITE_ERROR)
        add_tooltip(self.tree, "Exact staged upload content plus excluded private, development, cache, backup, and linked paths.")

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_tab,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_ACCENT_DARK,
            relief="flat",
            borderwidth=0,
            height=6,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_tab, command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

    def update_content_scrollregion(self, event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def resize_content_width(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def scroll_content(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (tk.Text, ttk.Treeview)):
            return
        scrollregion = self.content_canvas.bbox("all")
        if not scrollregion or scrollregion[3] <= self.content_canvas.winfo_height():
            return
        self.content_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def invalidate_report(self, *args):
        if self.report is not None:
            self.report = None
            self.publish_button.configure(state="disabled")
            self.summary_var.set("Folder changed; run Preflight")
            self.status_var.set("Prepared mod folder changed. Run Preflight again.")
            self.clear_manifest()
        self.refresh_summary_cards()

    def refresh_summary_cards(self, *args):
        try:
            item_id = parse_workshop_id(self.workshop_item_var.get())
            self.target_var.set(f"DayZ / {item_id}")
            if item_id != self.current_item_description_id:
                self.load_description_button.configure(text="Load Current Metadata", state="disabled")
        except ValueError:
            self.target_var.set("<Workshop URL or ID>")
            self.load_description_button.configure(text="Load Current Metadata", state="disabled")
        folder = self.mod_folder_var.get().strip()
        self.content_var.set(os.path.basename(os.path.normpath(folder)) if folder else "<prepared mod folder>")

    def choose_mod_folder(self):
        path = filedialog.askdirectory(title="Select prepared DayZ mod folder", initialdir=get_initial_directory(self.mod_folder_var.get()), parent=self)
        if path:
            self.mod_folder_var.set(path)
            self.status_var.set("Mod folder selected. Run Preflight.")

    def toggle_metadata_controls(self):
        enabled = bool(self.update_metadata_var.get())
        state = "normal" if enabled else "disabled"
        for widget in self.metadata_optional_widgets:
            if enabled:
                widget.grid()
            else:
                widget.grid_remove()
        self.metadata_title_entry.configure(state=state)
        self.metadata_tags_entry.configure(state=state)
        self.metadata_visibility_combo.configure(state="readonly" if enabled else "disabled")
        self.metadata_description.configure(state=state)
        self.safety_var.set("Content + selected metadata" if enabled else "Content-only; metadata preserved")

    def refresh_steam_status(self):
        if self.worker_running:
            return
        self.steam_client_var.set("Checking...")
        self.steam_connection_var.set("Checking...")
        self.steam_user_var.set("Checking...")
        self.steam_id_var.set("Checking...")
        self.steam_context_var.set("Checking...")
        self.set_busy(True)

        def worker():
            try:
                self.worker_queue.put(("status_done", self.backend.status()))
            except Exception as error:
                self.worker_queue.put(("status_error", error))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def validate_workshop_item(self):
        if self.worker_running:
            return
        try:
            item_id = parse_workshop_id(self.workshop_item_var.get())
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        self.item_details_var.set("Checking...")
        self.status_var.set(f"Querying Workshop item {item_id}...")
        self.set_busy(True)

        def worker():
            try:
                self.worker_queue.put(("item_done", self.backend.query_item(item_id)))
            except Exception as error:
                self.worker_queue.put(("error", error))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def choose_preview(self):
        path = filedialog.askopenfilename(
            title="Select Workshop preview image",
            initialdir=get_initial_directory(self.preview_var.get() or self.mod_folder_var.get()),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.bmp"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.preview_var.set(path)

    def open_workshop_page(self):
        try:
            webbrowser.open(workshop_url(self.workshop_item_var.get()))
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def load_current_metadata(self):
        try:
            item_id = parse_workshop_id(self.workshop_item_var.get())
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        if item_id != self.current_item_description_id or self.current_item_title is None:
            messagebox.showinfo(APP_TITLE, "Validate this Workshop item first.", parent=self)
            return
        self.metadata_title_var.set(self.current_item_title)
        self.metadata_tags_var.set(self.current_item_tags or "")
        self.metadata_visibility_var.set(self.current_item_visibility or "")
        self.metadata_description.configure(state="normal")
        self.metadata_description.delete("1.0", "end")
        self.metadata_description.insert("1.0", self.current_item_description or "")
        self.metadata_description.see("1.0")
        self.metadata_description.focus_set()
        self.update_metadata_var.set(True)
        self.toggle_metadata_controls()
        self.status_var.set("Loaded current Workshop title, tags, visibility, and description.")

    def remember_workshop_id(self, value):
        item_id = parse_workshop_id(value)
        self.recent_workshop_ids = [item_id] + [
            existing for existing in self.recent_workshop_ids if existing != item_id
        ]
        self.recent_workshop_ids = self.recent_workshop_ids[:12]
        self.workshop_combo.configure(values=self.recent_workshop_ids)

    def current_profile_data(self):
        return {
            "schema_version": 1,
            "name": self.profile_var.get().strip(),
            "mod_folder": self.mod_folder_var.get().strip(),
            "workshop_item": self.workshop_item_var.get().strip(),
            "preview_file": self.preview_var.get().strip(),
            "change_note": self.change_notes.get("1.0", "end-1c").strip(),
            "open_after_publish": bool(self.open_after_var.get()),
            "update_metadata": bool(self.update_metadata_var.get()),
            "metadata_title": self.metadata_title_var.get().strip(),
            "metadata_description": self.metadata_description.get("1.0", "end-1c"),
            "metadata_tags": self.metadata_tags_var.get().strip(),
            "metadata_visibility": self.metadata_visibility_var.get().strip(),
        }

    def refresh_profile_names(self):
        names = sorted(self.profiles, key=str.casefold)
        self.profile_combo.configure(values=names)
        if self.profile_var.get() not in self.profiles:
            self.profile_var.set("")

    def save_profile(self):
        current = self.profile_var.get().strip()
        name = simpledialog.askstring(APP_TITLE, "Profile name:", initialvalue=current, parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        self.profiles[name] = self.current_profile_data()
        self.profile_var.set(name)
        self.refresh_profile_names()
        self.save_settings()
        self.status_var.set(f"Saved profile: {name}")

    def load_profile(self, name):
        profile = self.profiles.get(name)
        if not isinstance(profile, dict):
            return
        self.mod_folder_var.set(profile.get("mod_folder", ""))
        self.workshop_item_var.set(profile.get("workshop_item", ""))
        self.preview_var.set(profile.get("preview_file", ""))
        self.open_after_var.set(profile.get("open_after_publish", True))
        self.update_metadata_var.set(profile.get("update_metadata", False))
        self.metadata_title_var.set(profile.get("metadata_title", ""))
        self.metadata_tags_var.set(profile.get("metadata_tags", ""))
        self.metadata_visibility_var.set(profile.get("metadata_visibility", ""))
        self.metadata_description.configure(state="normal")
        self.metadata_description.delete("1.0", "end")
        self.metadata_description.insert("1.0", profile.get("metadata_description", ""))
        self.toggle_metadata_controls()
        self.change_notes.delete("1.0", "end")
        self.change_notes.insert("1.0", profile.get("change_note", ""))
        self.status_var.set(f"Loaded profile: {name}. Run Preflight.")

    def delete_profile(self):
        name = self.profile_var.get().strip()
        if not name or name not in self.profiles:
            return
        if not messagebox.askyesno(APP_TITLE, f"Delete Publisher profile '{name}'?", parent=self):
            return
        del self.profiles[name]
        self.profile_var.set("")
        self.refresh_profile_names()
        self.save_settings()
        self.status_var.set(f"Deleted profile: {name}")

    def set_busy(self, busy, publishing=False):
        self.worker_running = busy
        self.preflight_button.configure(state="disabled" if busy else "normal")
        self.publish_button.configure(state="disabled" if busy or not self.report or not self.report.valid else "normal")
        self.configure(cursor="wait" if busy else "")
        if busy:
            self.progress_bar.start(12)
        else:
            self.progress_bar.stop()

    def clear_manifest(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def start_preflight(self):
        if self.worker_running:
            return
        source = self.mod_folder_var.get().strip()
        self.report = None
        self.clear_manifest()
        self.summary_var.set("Scanning...")
        self.status_var.set("Building safe upload manifest...")
        self.set_busy(True)

        def worker():
            try:
                progress = lambda count, path: self.worker_queue.put(("progress", f"Examining file {count}: {path}"))
                self.worker_queue.put(("preflight_done", scan_mod_folder(source, progress)))
            except Exception as error:
                self.worker_queue.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self.poll_worker)

    def show_report(self, report):
        self.report = report
        self.clear_manifest()
        for issue in report.issues:
            tag = "error" if issue.severity == "Error" else "warning"
            self.tree.insert("", "end", text=issue.message, values=("", issue.severity, "Fix" if tag == "error" else "Review"), tags=(tag,))
        for entry in report.files:
            self.tree.insert("", "end", text=entry.relative_path, values=(format_file_size(entry.size), entry.kind, "Upload"), tags=("upload",))
        for entry in report.excluded:
            self.tree.insert("", "end", text=entry.relative_path, values=(format_file_size(entry.size), entry.detail, "Excluded"), tags=("excluded",))
        self.publish_button.configure(state="normal" if report.valid else "disabled")
        self.summary_var.set(f"{len(report.files)} files, {format_file_size(report.total_bytes)}, {report.pbo_count} PBOs")
        excluded = f", {len(report.excluded)} excluded" if report.excluded else ""
        warnings = f", {len(report.warnings)} warning(s)" if report.warnings else ""
        if report.errors:
            self.status_var.set(f"Preflight failed: {len(report.errors)} error(s){warnings}{excluded}.")
        else:
            self.status_var.set(f"Preflight passed{warnings}{excluded}. Review upload manifest before publishing.")

    def build_request(self):
        update_metadata = bool(self.update_metadata_var.get())
        tags_text = self.metadata_tags_var.get().strip()
        return PublishRequest(
            source_folder=self.mod_folder_var.get().strip(),
            workshop_id=self.workshop_item_var.get().strip(),
            change_note=self.change_notes.get("1.0", "end-1c").strip(),
            preview_file=self.preview_var.get().strip(),
            log_directory=str(get_logs_directory()),
            title=self.metadata_title_var.get().strip() or None if update_metadata else None,
            description=self.metadata_description.get("1.0", "end-1c").strip() or None if update_metadata else None,
            tags=tuple(tag.strip() for tag in tags_text.split(",") if tag.strip()) if update_metadata and tags_text else None,
            visibility=self.metadata_visibility_var.get().strip() or None if update_metadata else None,
        )

    def start_publish(self):
        if self.worker_running or not self.report or not self.report.valid:
            return
        request = self.build_request()
        try:
            validate_publish_request(request)
            item_id = parse_workshop_id(request.workshop_id)
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        warning_text = f"\n\nPreflight warnings: {len(self.report.warnings)}" if self.report.warnings else ""
        prompt = (
            f"Update existing DayZ Workshop item {item_id}?\n\n"
            f"Upload: {len(self.report.files)} files ({format_file_size(self.report.total_bytes)})"
            f"{warning_text}\n\n"
            "Steam will replace current Workshop content. Steam provides no automatic rollback.\n"
            f"Metadata: {'explicitly entered fields will change' if self.update_metadata_var.get() else 'preserved'}.\n\n"
            "Upload cannot be cancelled after submission."
        )
        if not messagebox.askyesno(APP_TITLE, prompt, parent=self):
            return
        expected_signature = report_signature(self.report)
        self.clear_log()
        self.append_log(f"Application version: {APP_VERSION}")
        self.append_log(f"Target Workshop item: {item_id}")
        self.append_log(f"Validated source path: {Path(request.source_folder).resolve()}")
        self.append_log("Authentication: signed-in desktop Steam account.")
        self.status_var.set("Rechecking manifest before upload...")
        self.set_busy(True, publishing=True)

        def worker():
            try:
                progress = lambda message: self.worker_queue.put(("progress", message))
                output = lambda line: self.worker_queue.put(("log", line))
                fresh_report = scan_mod_folder(request.source_folder)
                if report_signature(fresh_report) != expected_signature:
                    self.worker_queue.put(("manifest_changed", fresh_report))
                    return
                result = publish_workshop_item(
                    request,
                    report=fresh_report,
                    progress=progress,
                    output_callback=output,
                    backend=self.backend,
                )
                self.worker_queue.put(("publish_done", result))
            except Exception as error:
                self.worker_queue.put(("error", error))

        threading.Thread(target=worker, daemon=True).start()
        self.notebook.select(1)
        self.after(100, self.poll_worker)

    def poll_worker(self):
        terminal = None
        while True:
            try:
                result_type, payload = self.worker_queue.get_nowait()
            except queue.Empty:
                break
            if result_type == "progress":
                self.status_var.set(payload)
                continue
            if result_type == "log":
                self.append_log(payload)
                continue
            terminal = (result_type, payload)
            break
        if terminal is None:
            if self.worker_running:
                self.after(100, self.poll_worker)
            return
        self.set_busy(False)
        result_type, payload = terminal
        if result_type == "status_done":
            self.steam_client_var.set("Running")
            self.steam_connection_var.set("Online" if payload.get("steam_online") else "Offline")
            self.steam_user_var.set(payload.get("persona_name", "Unknown"))
            self.steam_id_var.set(payload.get("steam_id", "Unknown"))
            self.steam_context_var.set("Valid" if str(payload.get("app_id")) == "221100" else "Invalid")
            self.status_var.set("Steamworks ready.")
            return
        if result_type == "status_error":
            message = str(payload)
            self.steam_client_var.set("Unavailable")
            self.steam_connection_var.set("Unavailable")
            self.steam_user_var.set("Unavailable")
            self.steam_id_var.set("Unavailable")
            self.steam_context_var.set("Invalid")
            self.status_var.set(message)
            return
        if result_type == "item_done":
            owner = payload.get("owner_steam_id", "Unknown")
            title = payload.get("title", "Untitled")
            self.current_item_description_id = str(payload.get("workshop_id"))
            self.current_item_title = title
            self.current_item_description = payload.get("description", "")
            self.current_item_tags = payload.get("tags", "")
            self.current_item_visibility = payload.get("visibility", "")
            description_length = len(self.current_item_description)
            if description_length:
                description_status = f"{description_length} description characters available"
            else:
                description_status = "Description is empty"
            self.load_description_button.configure(text="Load Current Metadata", state="normal")
            self.item_details_var.set(f"{title} / owner {owner}")
            self.target_var.set(f"DayZ / {payload.get('workshop_id')}")
            self.remember_workshop_id(payload.get("workshop_id"))
            self.status_var.set(f"Validated Workshop item: {title}")
            messagebox.showinfo(
                APP_TITLE,
                f"DayZ Workshop item validated.\n\nTitle: {title}\nOwner Steam ID: {owner}\n\n"
                f"{description_status}.\n\n"
                "Different owner is allowed. Steam verifies contributor permission during upload.",
                parent=self,
            )
            return
        if result_type == "error":
            message = str(payload)
            self.summary_var.set("Operation failed")
            self.status_var.set(message)
            self.append_log(f"ERROR: {message}")
            if isinstance(payload, SteamBridgeError) and payload.code == "LEGAL_AGREEMENT_REQUIRED":
                if messagebox.askyesno(
                    APP_TITLE,
                    f"{message}\n\nOpen Steam Workshop legal agreement?",
                    parent=self,
                ):
                    webbrowser.open(WORKSHOP_LEGAL_URL)
            else:
                messagebox.showerror(APP_TITLE, message, parent=self)
            return
        if result_type == "manifest_changed":
            self.show_report(payload)
            self.notebook.select(0)
            self.status_var.set("Folder contents changed after Preflight. Review refreshed manifest before publishing.")
            messagebox.showwarning(APP_TITLE, "Prepared mod folder changed after Preflight.\n\nManifest refreshed. Review it before publishing.", parent=self)
            return
        if result_type == "preflight_done":
            self.show_report(payload)
            self.notebook.select(0)
            return
        self.last_log_path = payload.log_path
        self.log_button.configure(state="normal" if self.last_log_path else "disabled")
        self.status_var.set(payload.message)
        self.summary_var.set("Published successfully" if payload.success else "Publishing failed")
        if payload.success:
            self.remember_workshop_id(payload.workshop_id)
            self.append_log(payload.message)
            messagebox.showinfo(APP_TITLE, payload.message, parent=self)
            if self.open_after_var.get():
                webbrowser.open(workshop_url(payload.workshop_id))
        else:
            self.append_log(payload.message)
            messagebox.showerror(APP_TITLE, payload.message, parent=self)

    def append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(line).rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_last_log(self):
        if not self.last_log_path:
            return
        try:
            os.startfile(self.last_log_path)
        except AttributeError:
            subprocess.Popen(["explorer", str(self.last_log_path)])
        except OSError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def save_settings(self):
        current = self.current_profile_data()
        current.update({
            "active_profile": self.profile_var.get().strip(),
            "profiles": self.profiles,
            "recent_workshop_ids": self.recent_workshop_ids,
        })
        save_json_file(get_settings_path(), current)

    def on_close(self):
        if self.worker_running:
            if not messagebox.askyesno(
                APP_TITLE,
                "Operation still running.\n\nClosing this window does not cancel a submitted Steam upload. Close anyway?",
                parent=self,
            ):
                return
        try:
            self.save_settings()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = RaGWorkshopPublisherApp()
    app.mainloop()
