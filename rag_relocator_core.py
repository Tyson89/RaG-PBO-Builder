import codecs
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from pbo_core import (
    PBO_STORED_METHOD,
    PBO_VERSION_MAGIC,
    is_pbo_entry_supported,
    read_pbo_archive,
    read_pbo_entry_payload,
)


MAX_SCAN_FILE_BYTES = 32 * 1024 * 1024
MAX_SCAN_PBO_BYTES = 128 * 1024 * 1024
MAX_UNKNOWN_TEXT_BYTES = 4 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".bat", ".c", ".cfg", ".conf", ".cpp", ".csv", ".glsl", ".h", ".hpp",
    ".html", ".imageset", ".ini", ".json", ".layout", ".md", ".proto", ".ps1",
    ".shader", ".sqf", ".sqm", ".surface", ".txt", ".xml", ".yaml", ".yml",
}
BINARY_PATH_EXTENSIONS = {
    ".anm", ".bin", ".ebo", ".p3d", ".rtm", ".wrp",
}
HYBRID_PATH_EXTENSIONS = {".bisurf", ".mat", ".rvmat"}
IGNORED_ASSET_EXTENSIONS = {
    ".7z", ".avi", ".bikey", ".biprivatekey", ".bisign", ".bmp", ".dds", ".dll",
    ".edds", ".exe", ".fbx", ".gif", ".jpeg", ".jpg", ".max", ".mp3", ".mp4",
    ".ogg", ".paa", ".pew", ".png", ".psd", ".rar", ".tga", ".wav", ".webp", ".zip",
}
SKIPPED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".rag-relocator-backups",
    "__pycache__",
    "build",
    "dist",
}
PREFIX_FILE_NAMES = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
QUOTED_PATH_PATTERN = re.compile(r'''["']([^"'\r\n]*[\\/][^"'\r\n]*)["']''')


class RelocatorError(Exception):
    pass


@dataclass(frozen=True)
class FileChange:
    path: Path
    relative_path: str
    occurrences: int
    line_numbers: tuple
    original_data: bytes
    updated_data: bytes
    kind: str = "Text"


@dataclass(frozen=True)
class BinaryCandidate:
    relative_path: str
    occurrences: int
    reason: str = "Unsafe or length-growing binary string"


@dataclass(frozen=True)
class ScanResult:
    root: Path
    old_path: str
    new_path: str
    files_scanned: int
    files_too_large: int
    files_unreadable: int
    files_ignored: int
    binary_files_skipped: int
    changes: tuple
    binary_candidates: tuple
    suggested_paths: tuple

    @property
    def changed_files(self):
        return len(self.changes)

    @property
    def replacements(self):
        return sum(change.occurrences for change in self.changes)


@dataclass(frozen=True)
class ApplyResult:
    changed_files: int
    replacements: int
    backup_path: Path | None
    destination_path: Path | None = None


def normalize_virtual_path(value):
    value = str(value or "").strip().strip('"').strip("'")
    value = value.replace("/", "\\")
    value = re.sub(r"\\+", r"\\", value)
    value = value.strip("\\")
    return value


def validate_paths(root, old_path, new_path):
    root = Path(root).expanduser()
    old_path = normalize_virtual_path(old_path)
    new_path = normalize_virtual_path(new_path)
    if not root.is_dir():
        raise RelocatorError(f"Source folder does not exist: {root}")
    if not old_path:
        raise RelocatorError("Old path is empty.")
    if not new_path:
        raise RelocatorError("New path is empty.")
    if old_path.casefold() == new_path.casefold():
        raise RelocatorError("Old and new paths are identical.")
    return root.resolve(), old_path, new_path


def _build_path_pattern(old_path):
    parts = old_path.split("\\")
    body = r"[\\/]".join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![A-Za-z0-9_.-]){body}(?![A-Za-z0-9_.-])", re.IGNORECASE)


def _replacement_for_match(match, new_path):
    matched = match.group(0)
    separator = "/" if "/" in matched and "\\" not in matched else "\\"
    return new_path.replace("\\", separator)


def replace_path_references(text, old_path, new_path):
    pattern = _build_path_pattern(normalize_virtual_path(old_path))
    normalized_new = normalize_virtual_path(new_path)
    matches = list(pattern.finditer(text))
    if not matches:
        return text, 0, ()
    line_numbers = tuple(sorted({text.count("\n", 0, match.start()) + 1 for match in matches}))
    updated = pattern.sub(lambda match: _replacement_for_match(match, normalized_new), text)
    return updated, len(matches), line_numbers


def _decode_text(data):
    try:
        if data.startswith(codecs.BOM_UTF8):
            return data[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8", codecs.BOM_UTF8
        if data.startswith(codecs.BOM_UTF16_LE):
            return data[len(codecs.BOM_UTF16_LE):].decode("utf-16-le"), "utf-16-le", codecs.BOM_UTF16_LE
        if data.startswith(codecs.BOM_UTF16_BE):
            return data[len(codecs.BOM_UTF16_BE):].decode("utf-16-be"), "utf-16-be", codecs.BOM_UTF16_BE
    except UnicodeDecodeError:
        return None
    if b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError:
            return None
    if text:
        controls = sum(ord(char) < 32 and char not in "\t\n\r\f" for char in text)
        if controls / len(text) > 0.01:
            return None
    return text, encoding, b""


def _count_binary_candidates(data, old_path):
    total = 0
    lowered = data.lower()
    for variant in {old_path.replace("\\", "\\"), old_path.replace("\\", "/")}:
        for encoding in ("ascii", "utf-16-le", "utf-16-be"):
            needle = variant.encode(encoding, errors="ignore")
            if needle:
                total += lowered.count(needle.lower())
    return total


def _replace_binary_strings(data, old_path, new_path):
    possible = _count_binary_candidates(data, old_path)
    if not possible:
        return data, 0, 0
    updated_data = bytearray(data)
    replacements = 0

    for match in list(re.finditer(rb"[\x20-\x7e]{4,}\x00", data)):
        start, end = match.span()
        raw_text = data[start:end - 1]
        try:
            text = raw_text.decode("ascii")
        except UnicodeDecodeError:
            continue
        updated, count, _lines = replace_path_references(text, old_path, new_path)
        if not count:
            continue
        encoded = updated.encode("ascii", errors="strict")
        if len(encoded) > len(raw_text):
            continue
        updated_data[start:end - 1] = encoded + bytes(len(raw_text) - len(encoded))
        replacements += count

    for byte_order in ("little", "big"):
        for alignment in (0, 1):
            offset = alignment
            while offset + 3 < len(data):
                start = offset
                chars = []
                while offset + 1 < len(data):
                    pair = data[offset:offset + 2]
                    value = int.from_bytes(pair, byte_order)
                    if 32 <= value <= 126:
                        chars.append(chr(value))
                        offset += 2
                        continue
                    break
                if len(chars) >= 4 and offset + 1 < len(data) and data[offset:offset + 2] == b"\x00\x00":
                    text = "".join(chars)
                    updated, count, _lines = replace_path_references(text, old_path, new_path)
                    if count:
                        encoding = "utf-16-le" if byte_order == "little" else "utf-16-be"
                        encoded = updated.encode(encoding)
                        original_length = len(chars) * 2
                        if len(encoded) <= original_length:
                            updated_data[start:start + original_length] = encoded + bytes(original_length - len(encoded))
                            replacements += count
                    offset += 2
                else:
                    offset = start + 2

    return bytes(updated_data), replacements, max(0, possible - replacements)


def _encode_pbo_string(value):
    return str(value).encode("utf-8") + b"\x00"


def _rewrite_pbo(path, old_path, new_path):
    archive = read_pbo_archive(str(path))
    entry_payloads = []
    replacements = 0
    unsafe_matches = 0

    with open(path, "rb") as source:
        for entry in archive["entries"]:
            source.seek(entry.offset)
            raw_payload = source.read(entry.data_size)
            if len(raw_payload) != entry.data_size:
                raise RelocatorError(f"Could not read PBO entry: {entry.name}")
            entry_name, name_count, _lines = replace_path_references(entry.name, old_path, new_path)
            replacements += name_count
            if not is_pbo_entry_supported(entry):
                unsafe_matches += _count_binary_candidates(raw_payload, old_path)
                entry_payloads.append((entry, entry_name, raw_payload, False))
                continue
            payload = read_pbo_entry_payload(source, entry)
            decoded = _decode_text(payload)
            if decoded is not None:
                text, encoding, byte_order_mark = decoded
                updated_text, count, _lines = replace_path_references(text, old_path, new_path)
                updated_payload = byte_order_mark + updated_text.encode(encoding)
            else:
                updated_payload, count, unsafe = _replace_binary_strings(payload, old_path, new_path)
                unsafe_matches += unsafe
            replacements += count
            entry_payloads.append((entry, entry_name, updated_payload if count else raw_payload, bool(count)))

    properties = []
    for key, value in archive["properties"].items():
        updated_key, key_count, _lines = replace_path_references(key, old_path, new_path)
        updated_value, value_count, _lines = replace_path_references(value, old_path, new_path)
        replacements += key_count + value_count
        properties.append((updated_key, updated_value))

    if not replacements:
        return None, 0, unsafe_matches

    header = bytearray()
    header.extend(b"\x00")
    header.extend(struct.pack("<I", PBO_VERSION_MAGIC))
    header.extend(struct.pack("<IIII", 0, 0, 0, 0))
    for key, value in properties:
        header.extend(_encode_pbo_string(key))
        header.extend(_encode_pbo_string(value))
    header.extend(b"\x00")

    output_payloads = []
    for entry, entry_name, payload, modified in entry_payloads:
        header.extend(_encode_pbo_string(entry_name))
        if modified:
            fields = (PBO_STORED_METHOD, len(payload), entry.reserved, entry.timestamp, len(payload))
        else:
            fields = (entry.packing_method, entry.original_size, entry.reserved, entry.timestamp, entry.data_size)
        header.extend(struct.pack("<IIIII", *fields))
        output_payloads.append(payload)
    header.extend(b"\x00")
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))

    output = bytearray(header)
    for payload in output_payloads:
        output.extend(payload)
    digest = hashlib.sha1(output).digest()
    output.extend(b"\x00")
    output.extend(digest)
    return bytes(output), replacements, unsafe_matches


def _get_scan_mode(path, size, include_binary, include_pbo):
    extension = path.suffix.casefold()
    if extension == ".pbo":
        return "pbo" if include_pbo else "ignore"
    if extension in IGNORED_ASSET_EXTENSIONS:
        return "ignore"
    if extension in BINARY_PATH_EXTENSIONS:
        return "binary" if include_binary else "ignore"
    if extension in HYBRID_PATH_EXTENSIONS:
        return "auto" if include_binary else "text"
    if extension in TEXT_EXTENSIONS or not extension:
        return "text"
    return "auto" if size <= MAX_UNKNOWN_TEXT_BYTES else "ignore"


def scan_references(root, old_path, new_path, include_binary=True, include_pbo=True, progress=None):
    root, old_path, new_path = validate_paths(root, old_path, new_path)
    changes = []
    binary_candidates = []
    files_scanned = 0
    files_too_large = 0
    files_unreadable = 0
    files_ignored = 0
    binary_files_skipped = 0
    files_seen = 0

    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory.casefold() not in SKIPPED_DIRECTORIES and not Path(current_root, directory).is_symlink()
        )
        for filename in sorted(filenames):
            path = Path(current_root, filename)
            files_seen += 1
            if progress and (files_seen == 1 or files_seen % 50 == 0):
                progress(files_seen, str(path.relative_to(root)))
            if path.is_symlink():
                files_ignored += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                files_unreadable += 1
                continue
            mode = _get_scan_mode(path, size, include_binary, include_pbo)
            if mode == "ignore":
                files_ignored += 1
                continue
            size_limit = MAX_SCAN_PBO_BYTES if mode == "pbo" else MAX_SCAN_FILE_BYTES
            if size > size_limit:
                files_too_large += 1
                continue
            try:
                data = path.read_bytes()
            except OSError:
                files_unreadable += 1
                continue
            files_scanned += 1
            relative_path = str(path.relative_to(root))
            if mode == "pbo":
                try:
                    updated_data, occurrences, unsafe = _rewrite_pbo(path, old_path, new_path)
                except Exception:
                    binary_files_skipped += 1
                    occurrences = _count_binary_candidates(data, old_path)
                    if occurrences:
                        binary_candidates.append(BinaryCandidate(relative_path, occurrences, "Unsupported or invalid PBO"))
                    continue
                if updated_data is not None:
                    changes.append(FileChange(
                        path=path,
                        relative_path=relative_path,
                        occurrences=occurrences,
                        line_numbers=(),
                        original_data=data,
                        updated_data=updated_data,
                        kind="PBO archive",
                    ))
                if unsafe:
                    binary_candidates.append(BinaryCandidate(relative_path, unsafe, "Unsafe PBO binary string"))
                continue
            if mode == "binary":
                decoded = None
            else:
                decoded = _decode_text(data)
            if decoded is None:
                binary_files_skipped += 1
                updated_data, occurrences, unsafe = _replace_binary_strings(data, old_path, new_path)
                if occurrences:
                    changes.append(FileChange(
                        path=path,
                        relative_path=relative_path,
                        occurrences=occurrences,
                        line_numbers=(),
                        original_data=data,
                        updated_data=updated_data,
                        kind="Binary strings",
                    ))
                if unsafe:
                    binary_candidates.append(BinaryCandidate(relative_path, unsafe))
                continue
            text, encoding, byte_order_mark = decoded
            updated, occurrences, line_numbers = replace_path_references(text, old_path, new_path)
            if not occurrences:
                continue
            changes.append(FileChange(
                path=path,
                relative_path=relative_path,
                occurrences=occurrences,
                line_numbers=line_numbers,
                original_data=data,
                updated_data=byte_order_mark + updated.encode(encoding),
            ))

    suggested_paths = tuple(find_path_candidates(root, limit=5)) if not changes else ()
    return ScanResult(
        root=root,
        old_path=old_path,
        new_path=new_path,
        files_scanned=files_scanned,
        files_too_large=files_too_large,
        files_unreadable=files_unreadable,
        files_ignored=files_ignored,
        binary_files_skipped=binary_files_skipped,
        changes=tuple(changes),
        binary_candidates=tuple(binary_candidates),
        suggested_paths=suggested_paths,
    )


def _next_backup_path(root):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = root.parent / f"{root.name}_RaG_Relocator_Backup_{timestamp}.zip"
    candidate = base
    index = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}_{index}{base.suffix}")
        index += 1
    return candidate


def _create_backup(scan):
    backup_path = _next_backup_path(scan.root)
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(scan.root),
        "old_path": scan.old_path,
        "new_path": scan.new_path,
        "files": [change.relative_path for change in scan.changes],
    }
    try:
        with zipfile.ZipFile(backup_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("relocation_manifest.json", json.dumps(manifest, indent=2))
            for change in scan.changes:
                archive.writestr(change.relative_path.replace("\\", "/"), change.original_data)
    except Exception:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return backup_path


def _atomic_write(path, data, original_stat=None):
    temp_path = path.with_name(f".{path.name}.ragrelocator.tmp")
    try:
        with open(temp_path, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        if original_stat is not None:
            try:
                os.chmod(path, stat.S_IMODE(original_stat.st_mode))
                os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            except OSError:
                pass
    finally:
        temp_path.unlink(missing_ok=True)


def apply_scan(scan, create_backup=True):
    if not scan.changes:
        raise RelocatorError("Scan contains no changes.")
    for change in scan.changes:
        try:
            current_data = change.path.read_bytes()
        except OSError as error:
            raise RelocatorError(f"Could not recheck {change.relative_path}: {error}") from error
        if current_data != change.original_data:
            raise RelocatorError(f"File changed after scan. Scan again: {change.relative_path}")

    backup_path = None
    if create_backup:
        try:
            backup_path = _create_backup(scan)
        except Exception as error:
            raise RelocatorError(f"Could not create backup: {error}") from error

    written = []
    try:
        for change in scan.changes:
            original_stat = change.path.stat()
            _atomic_write(change.path, change.updated_data, original_stat)
            written.append((change, original_stat))
    except Exception as error:
        rollback_errors = []
        for change, original_stat in reversed(written):
            try:
                _atomic_write(change.path, change.original_data, original_stat)
            except Exception as rollback_error:
                rollback_errors.append(f"{change.relative_path}: {rollback_error}")
        if rollback_errors:
            raise RelocatorError(f"Write failed: {error}. Rollback also failed: {'; '.join(rollback_errors)}") from error
        raise RelocatorError(f"Write failed; completed changes were rolled back: {error}") from error

    return ApplyResult(scan.changed_files, scan.replacements, backup_path)


def copy_and_apply_scan(scan, destination, progress=None):
    source = scan.root.resolve()
    destination = Path(destination).expanduser().resolve(strict=False)
    if source == destination:
        raise RelocatorError("Destination folder is the source folder.")
    try:
        destination.relative_to(source)
        raise RelocatorError("Destination folder cannot be inside the source folder.")
    except ValueError:
        pass
    try:
        source.relative_to(destination)
        raise RelocatorError("Destination folder cannot contain the source folder.")
    except ValueError:
        pass
    destination_was_empty = False
    if destination.exists():
        if not destination.is_dir():
            raise RelocatorError(f"Destination exists and is not a folder: {destination}")
        try:
            destination_was_empty = not any(destination.iterdir())
        except OSError as error:
            raise RelocatorError(f"Could not inspect destination folder: {error}") from error
        if not destination_was_empty:
            raise RelocatorError(f"Destination folder must be new or empty: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.ragrelocator-copy-{os.getpid()}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    destination_removed = False
    try:
        if progress:
            progress(f"Copying source to temporary folder: {destination.name}")
        shutil.copytree(source, staging, copy_function=shutil.copy2, symlinks=True)
        relocated_changes = tuple(
            replace(change, path=staging.joinpath(*Path(change.relative_path).parts))
            for change in scan.changes
        )
        relocated_scan = replace(scan, root=staging, changes=relocated_changes)
        if progress:
            progress(f"Applying {scan.replacements} replacement(s) to copied files")
        result = apply_scan(relocated_scan, create_backup=False)
        if destination_was_empty:
            destination.rmdir()
            destination_removed = True
        os.replace(staging, destination)
        return ApplyResult(result.changed_files, result.replacements, None, destination)
    except Exception as error:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if destination_removed and not destination.exists():
            try:
                destination.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if isinstance(error, RelocatorError):
            raise
        raise RelocatorError(f"Could not create relocated copy: {error}") from error


def _read_prefix_values(root):
    values = []
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory.casefold() not in SKIPPED_DIRECTORIES and not Path(current_root, directory).is_symlink()
        )
        for filename in filenames:
            if filename.casefold() not in PREFIX_FILE_NAMES:
                continue
            path = Path(current_root, filename)
            try:
                data = path.read_bytes()
            except OSError:
                continue
            decoded = _decode_text(data)
            if decoded is None:
                continue
            value = normalize_virtual_path(decoded[0])
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
    return values


def _common_path_prefix(values):
    if not values:
        return ""
    split_values = [value.split("\\") for value in values]
    common = []
    for components in zip(*split_values):
        if len({component.casefold() for component in components}) != 1:
            break
        common.append(components[0])
    return "\\".join(common)


def find_path_candidates(root, limit=10, progress=None):
    root = Path(root)
    if not root.is_dir():
        return []
    prefix_values = _read_prefix_values(root)
    candidates = {}
    for value in prefix_values:
        candidates[value.casefold()] = [value, 1000]
    common_prefix = _common_path_prefix(prefix_values)
    if common_prefix:
        candidates[common_prefix.casefold()] = [common_prefix, 2000 + len(prefix_values)]
        ranked_prefixes = sorted(candidates.values(), key=lambda item: (-item[1], item[0].casefold()))
        return [item[0] for item in ranked_prefixes[:limit]]

    files_seen = 0
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory.casefold() not in SKIPPED_DIRECTORIES and not Path(current_root, directory).is_symlink()
        )
        for filename in sorted(filenames):
            path = Path(current_root, filename)
            files_seen += 1
            if progress and (files_seen == 1 or files_seen % 50 == 0):
                progress(files_seen, str(path.relative_to(root)))
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
                if size > MAX_SCAN_FILE_BYTES or _get_scan_mode(path, size, False, False) == "ignore":
                    continue
                decoded = _decode_text(path.read_bytes())
            except OSError:
                continue
            if decoded is None:
                continue
            for match in QUOTED_PATH_PATTERN.finditer(decoded[0]):
                reference = match.group(1).strip().replace("/", "\\")
                if reference.casefold().startswith("proxy:"):
                    reference = reference[6:]
                reference = reference.lstrip("\\")
                parts = [part for part in reference.split("\\") if part]
                if len(parts) < 2 or parts[0].endswith(":") or any(part in {".", ".."} for part in parts):
                    continue
                for index in range(1, len(parts)):
                    target = root.joinpath(*parts[index:])
                    if target.exists():
                        candidate = "\\".join(parts[:index])
                        key = candidate.casefold()
                        if key not in candidates:
                            candidates[key] = [candidate, 0]
                        candidates[key][1] += 1
                        break
    ranked = sorted(candidates.values(), key=lambda item: (-item[1], item[0].casefold()))
    return [item[0] for item in ranked[:limit]]


def detect_old_path(root):
    root = Path(root)
    if not root.is_dir():
        return ""
    candidates = find_path_candidates(root, limit=1)
    if candidates:
        return candidates[0]
    fallback = normalize_virtual_path(root.name)
    pattern = _build_path_pattern(fallback)
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [directory for directory in directories if directory.casefold() not in SKIPPED_DIRECTORIES]
        for filename in filenames:
            path = Path(current_root, filename)
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
                if size > MAX_SCAN_FILE_BYTES or _get_scan_mode(path, size, False, False) == "ignore":
                    continue
                decoded = _decode_text(path.read_bytes())
            except OSError:
                continue
            if decoded is not None and pattern.search(decoded[0]):
                return fallback
    return ""
