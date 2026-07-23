import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DAYZ_APP_ID = "221100"
WORKSHOP_ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"
WORKSHOP_LEGAL_URL = "https://steamcommunity.com/sharedfiles/workshoplegalagreement"

EXCLUDED_DIRECTORIES = {
    ".git": "Source-control data",
    ".hg": "Source-control data",
    ".idea": "Editor settings",
    ".pytest_cache": "Python cache",
    ".svn": "Source-control data",
    ".vscode": "Editor settings",
    "__pycache__": "Python cache",
}
EXCLUDED_FILENAMES = {
    ".ds_store": "Operating-system metadata",
    "desktop.ini": "Operating-system metadata",
    "thumbs.db": "Operating-system metadata",
}
EXCLUDED_SUFFIXES = {
    ".bak": "Backup file",
    ".biprivatekey": "Private signing key",
    ".log": "Log file",
    ".pyc": "Python cache",
    ".pyo": "Python cache",
    ".tmp": "Temporary file",
}


@dataclass(frozen=True)
class ManifestEntry:
    relative_path: str
    size: int
    kind: str
    detail: str = ""
    modified_ns: int = 0


@dataclass(frozen=True)
class PreflightIssue:
    severity: str
    message: str


@dataclass
class PreflightReport:
    source_folder: Path
    files: list[ManifestEntry] = field(default_factory=list)
    excluded: list[ManifestEntry] = field(default_factory=list)
    issues: list[PreflightIssue] = field(default_factory=list)
    total_bytes: int = 0
    pbo_count: int = 0

    @property
    def errors(self):
        return [issue for issue in self.issues if issue.severity == "Error"]

    @property
    def warnings(self):
        return [issue for issue in self.issues if issue.severity == "Warning"]

    @property
    def valid(self):
        return not self.errors


@dataclass(frozen=True)
class PublishRequest:
    source_folder: str
    workshop_id: str
    change_note: str
    preview_file: str = ""
    log_directory: str = ""
    title: str | None = None
    description: str | None = None
    tags: tuple[str, ...] | None = None
    visibility: str | None = None


@dataclass(frozen=True)
class PublishResult:
    success: bool
    message: str
    workshop_id: str
    return_code: int
    output: str
    log_path: Path | None = None


@dataclass
class PreparedUpload:
    root: Path
    content_folder: Path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class SteamBridgeError(RuntimeError):
    def __init__(self, code, message, event=None):
        super().__init__(message)
        self.code = str(code)
        self.event = event or {}


def parse_workshop_id(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Enter Workshop item URL or ID.")
    if text.isdigit():
        workshop_id = text
    else:
        try:
            query = parse_qs(urlparse(text).query)
            workshop_id = (query.get("id") or [""])[0]
        except Exception:
            workshop_id = ""
        if not workshop_id:
            match = re.search(r"(?:\?|&)id=(\d+)", text, re.IGNORECASE)
            workshop_id = match.group(1) if match else ""
    if not workshop_id.isdigit() or not 1 <= len(workshop_id) <= 20 or int(workshop_id) <= 0:
        raise ValueError("Workshop item must be a valid numeric ID or Steam Workshop URL.")
    return workshop_id


def workshop_url(workshop_id):
    return WORKSHOP_ITEM_URL.format(workshop_id=parse_workshop_id(workshop_id))


def format_file_size(size):
    value = float(max(0, int(size)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _is_reparse_point(path):
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(file_stat.st_mode):
        return True
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _classify_file(path):
    lower = path.name.casefold()
    if lower.endswith(".pbo"):
        return "PBO"
    if lower.endswith(".bisign"):
        return "Signature"
    if lower.endswith(".bikey"):
        return "Public key"
    if lower in {"mod.cpp", "meta.cpp"}:
        return "Metadata"
    if lower in {"readme", "readme.md", "license", "license.txt"}:
        return "Documentation"
    return "File"


def _excluded_file_reason(path):
    lower = path.name.casefold()
    if lower in EXCLUDED_FILENAMES:
        return EXCLUDED_FILENAMES[lower]
    for suffix, reason in EXCLUDED_SUFFIXES.items():
        if lower.endswith(suffix):
            return reason
    return ""


def _relative_display(path, root):
    return str(path.relative_to(root)).replace("/", "\\")


def scan_mod_folder(source_folder, progress=None):
    source = Path(source_folder).expanduser()
    if not source.is_dir():
        raise ValueError("Select a valid prepared mod folder.")
    source = source.resolve()
    report = PreflightReport(source_folder=source)
    private_keys = 0
    reparse_points = 0
    unreadable = 0
    examined = 0

    root_children = {child.name.casefold(): child for child in source.iterdir()}
    addons_folder = root_children.get("addons")
    if not addons_folder or not addons_folder.is_dir():
        report.issues.append(PreflightIssue("Error", "Root Addons folder is missing."))

    for current_root, directory_names, file_names in os.walk(source, topdown=True, followlinks=False):
        current = Path(current_root)
        retained_directories = []
        for directory_name in sorted(directory_names, key=str.casefold):
            directory = current / directory_name
            relative_path = _relative_display(directory, source)
            reason = EXCLUDED_DIRECTORIES.get(directory_name.casefold(), "")
            if not reason and _is_reparse_point(directory):
                reason = "Linked directory"
                reparse_points += 1
            if reason:
                report.excluded.append(ManifestEntry(relative_path, 0, "Excluded", reason))
            else:
                retained_directories.append(directory_name)
        directory_names[:] = retained_directories

        for file_name in sorted(file_names, key=str.casefold):
            path = current / file_name
            examined += 1
            if progress and (examined == 1 or examined % 25 == 0):
                progress(examined, _relative_display(path, source))
            relative_path = _relative_display(path, source)
            reason = _excluded_file_reason(path)
            if not reason and _is_reparse_point(path):
                reason = "Linked file"
                reparse_points += 1
            try:
                path_stat = path.stat()
                size = path_stat.st_size
                modified_ns = path_stat.st_mtime_ns
            except OSError:
                size = 0
                modified_ns = 0
                unreadable += 1
                reason = reason or "Unreadable file"
            if reason:
                report.excluded.append(ManifestEntry(relative_path, size, "Excluded", reason, modified_ns))
                if path.name.casefold().endswith(".biprivatekey"):
                    private_keys += 1
                continue
            entry = ManifestEntry(relative_path, size, _classify_file(path), modified_ns=modified_ns)
            report.files.append(entry)
            report.total_bytes += size

    report.files.sort(key=lambda entry: entry.relative_path.casefold())
    report.excluded.sort(key=lambda entry: entry.relative_path.casefold())
    pbo_entries = [entry for entry in report.files if entry.kind == "PBO"]
    report.pbo_count = len(pbo_entries)
    if not pbo_entries:
        report.issues.append(PreflightIssue("Error", "No PBO files found in prepared mod folder."))

    if addons_folder and addons_folder.is_dir():
        addons_prefix = _relative_display(addons_folder, source).casefold() + "\\"
        outside_addons = [entry.relative_path for entry in pbo_entries if not entry.relative_path.casefold().startswith(addons_prefix)]
        if outside_addons:
            report.issues.append(PreflightIssue("Warning", f"{len(outside_addons)} PBO file(s) are outside root Addons folder."))

    signature_paths = {entry.relative_path.casefold() for entry in report.files if entry.kind == "Signature"}
    unsigned_pbos = []
    for entry in pbo_entries:
        pbo_prefix = entry.relative_path.casefold() + "."
        if not any(path.startswith(pbo_prefix) and path.endswith(".bisign") for path in signature_paths):
            unsigned_pbos.append(entry.relative_path)
    if unsigned_pbos:
        report.issues.append(PreflightIssue("Warning", f"{len(unsigned_pbos)} PBO file(s) have no matching BISIGN signature."))

    if not any(entry.kind == "Public key" for entry in report.files):
        report.issues.append(PreflightIssue("Warning", "No public BIKEY found."))
    if private_keys:
        report.issues.append(PreflightIssue("Warning", f"{private_keys} private signing key file(s) detected and excluded."))
    if reparse_points:
        report.issues.append(PreflightIssue("Warning", f"{reparse_points} linked file or folder path(s) excluded."))
    if unreadable:
        report.issues.append(PreflightIssue("Error", f"{unreadable} file(s) could not be read."))
    if not report.files:
        report.issues.append(PreflightIssue("Error", "Upload manifest is empty."))
    return report


def _copy_manifest(report, destination, progress=None):
    source = report.source_folder
    destination.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(report.files, 1):
        source_path = source.joinpath(*entry.relative_path.split("\\"))
        destination_path = destination.joinpath(*entry.relative_path.split("\\"))
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        if progress and (index == 1 or index % 10 == 0 or index == len(report.files)):
            progress(f"Staging {index}/{len(report.files)}: {entry.relative_path}")


def prepare_upload(report, progress=None, parent_directory=None):
    if not report.valid:
        raise ValueError("Preflight contains errors. Fix them before publishing.")
    parent = str(parent_directory) if parent_directory else None
    root = Path(tempfile.mkdtemp(prefix="rag_workshop_", dir=parent))
    prepared = PreparedUpload(root=root, content_folder=root / "content")
    try:
        _copy_manifest(report, prepared.content_folder, progress)
        return prepared
    except Exception:
        prepared.cleanup()
        raise


def _read_steam_library_paths(steam_root):
    roots = [steam_root]
    library_file = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = library_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return roots
    for match in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
        path = Path(match.group(1).replace("\\\\", "\\"))
        if path not in roots:
            roots.append(path)
    return roots


def _registry_steam_root():
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
        return Path(value)
    except Exception:
        return None


def _steam_roots():
    roots = []
    registry_root = _registry_steam_root()
    if registry_root:
        roots.extend(_read_steam_library_paths(registry_root))
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(variable)
        if not base:
            continue
        root = Path(base) / "Steam"
        if root not in roots:
            roots.extend(_read_steam_library_paths(root))
    return roots


def find_dayz_publisher_context():
    candidates = []
    environment_path = os.environ.get("RAG_STEAM_APP_CONTEXT_DIR", "").strip()
    if environment_path:
        candidates.append(Path(environment_path))
    for root in _steam_roots():
        candidates.append(root / "steamapps" / "common" / "DayZ Tools" / "Bin" / "Publisher")
    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_dir() and (candidate / "steam_appid.txt").is_file():
            return str(candidate.resolve())
    return ""


def find_steamworks_bridge():
    executable = "rag_steamworks_bridge.exe" if os.name == "nt" else "rag_steamworks_bridge"
    candidates = []
    configured = os.environ.get("RAG_STEAMWORKS_BRIDGE", "").strip()
    if configured:
        candidates.append(Path(configured))
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates.extend([
        bundle_root / executable,
        Path(sys.executable).resolve().parent / executable,
        Path(__file__).resolve().parent / executable,
        Path(__file__).resolve().parent / "native" / "build" / "Release" / executable,
    ])
    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


class SteamworksBackend:
    def __init__(self, bridge_path=None, context_path=None):
        self.bridge_path = str(bridge_path or find_steamworks_bridge())
        self.context_path = str(context_path or find_dayz_publisher_context())

    def validate_runtime(self):
        if not self.bridge_path or not Path(self.bridge_path).is_file():
            raise SteamBridgeError("BRIDGE_NOT_FOUND", "Steamworks bridge is missing. Reinstall RaG PBO Tools.")
        context = Path(self.context_path) if self.context_path else None
        if not context or not context.is_dir() or not (context / "steam_appid.txt").is_file():
            raise SteamBridgeError(
                "DAYZ_TOOLS_REQUIRED",
                "DayZ Tools Publisher context was not found. Install DayZ Tools through Steam.",
            )

    def _run(self, request, event_callback=None, log_callback=None):
        self.validate_runtime()
        environment = os.environ.copy()
        environment["RAG_STEAM_APP_CONTEXT_DIR"] = self.context_path
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            [self.bridge_path, "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=creation_flags,
        )
        diagnostics = []

        def read_stderr():
            for raw_line in process.stderr:
                line = raw_line.rstrip()
                if not line:
                    continue
                diagnostics.append(line)
                if log_callback:
                    log_callback(line)

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()
        terminal = None
        transcript = []
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.close()
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                transcript.append(line)
                if log_callback:
                    log_callback(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise SteamBridgeError("INVALID_BRIDGE_OUTPUT", f"Steamworks bridge returned invalid JSON: {error}.")
                if event.get("event") == "progress":
                    if event_callback:
                        event_callback(event)
                else:
                    terminal = event
            return_code = process.wait()
            stderr_thread.join(timeout=2)
        except Exception:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise
        if terminal and terminal.get("event") == "error":
            raise SteamBridgeError(
                terminal.get("code", "STEAM_ERROR"),
                terminal.get("message", "Steam rejected the request."),
                terminal,
            )
        if return_code != 0 or not terminal:
            detail = diagnostics[-1] if diagnostics else f"Bridge exited with code {return_code}."
            raise SteamBridgeError("BRIDGE_FAILED", detail)
        return terminal, "\n".join(transcript + diagnostics)

    def status(self, log_callback=None):
        event, _ = self._run({"command": "status"}, log_callback=log_callback)
        return event

    def query_item(self, workshop_id, log_callback=None):
        event, _ = self._run(
            {"command": "query_item", "workshop_id": parse_workshop_id(workshop_id)},
            log_callback=log_callback,
        )
        return event

    def update_item(self, request, event_callback=None, log_callback=None):
        return self._run(request, event_callback=event_callback, log_callback=log_callback)


def validate_publish_request(request):
    source = Path(request.source_folder)
    if not source.is_dir():
        raise ValueError("Select a valid prepared mod folder.")
    parse_workshop_id(request.workshop_id)
    if not str(request.change_note or "").strip():
        raise ValueError("Enter change notes for this update.")
    if request.preview_file:
        preview = Path(request.preview_file)
        if not preview.is_file():
            raise ValueError("Selected preview image does not exist.")
        if preview.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".gif", ".bmp"}:
            raise ValueError("Preview image must be JPG, PNG, GIF, or BMP.")
        if preview.stat().st_size >= 1_000_000:
            raise ValueError("Preview image must be smaller than 1 MB.")


def _save_publish_log(directory, workshop_id, output):
    log_directory = Path(directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_directory / f"publisher_{workshop_id}_{timestamp}.log"
    path.write_text(output, encoding="utf-8", errors="replace")
    return path


def publish_workshop_item(
    request,
    report=None,
    progress=None,
    output_callback=None,
    backend=None,
):
    validate_publish_request(request)
    item_id = parse_workshop_id(request.workshop_id)
    report = report or scan_mod_folder(request.source_folder)
    if Path(request.source_folder).resolve() != report.source_folder.resolve():
        raise ValueError("Manifest source no longer matches selected mod folder. Run preflight again.")
    if not report.valid:
        raise ValueError("Preflight contains errors. Fix them before publishing.")

    if progress:
        progress("Creating safe upload staging folder...")
    prepared = prepare_upload(report, progress)
    log_path = None
    try:
        backend = backend or SteamworksBackend()
        payload = {
            "command": "update_item",
            "workshop_id": item_id,
            "content_path": str(prepared.content_folder.resolve()),
            "change_note": request.change_note.strip(),
            "preview_path": str(Path(request.preview_file).resolve()) if request.preview_file else None,
            "title": request.title,
            "description": request.description,
            "tags": list(request.tags) if request.tags is not None else None,
            "visibility": request.visibility,
        }

        def handle_event(event):
            stage_names = {
                "preparing_configuration": "Preparing configuration",
                "preparing_content": "Scanning and preparing content",
                "uploading_content": "Uploading mod content",
                "uploading_preview": "Uploading preview image",
                "committing_changes": "Committing Workshop update",
                "waiting": "Waiting for Steam",
            }
            label = stage_names.get(event.get("stage"), "Publishing")
            percent = event.get("percent")
            if progress:
                progress(f"{label}: {percent:.1f}%" if isinstance(percent, (int, float)) else label)

        if progress:
            progress("Submitting through signed-in desktop Steam account...")
        if output_callback:
            output_callback(f"Validated staged content path: {prepared.content_folder.resolve()}")
        terminal, output = backend.update_item(payload, event_callback=handle_event, log_callback=output_callback)
        if request.log_directory:
            log_path = _save_publish_log(request.log_directory, item_id, output)
        return PublishResult(
            success=terminal.get("event") == "completed",
            message="Workshop update completed successfully.",
            workshop_id=item_id,
            return_code=0,
            output=output,
            log_path=log_path,
        )
    finally:
        prepared.cleanup()
