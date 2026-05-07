import os
import re
import struct
from datetime import datetime
from pathlib import Path


ZERO = bytes([0])
WIN_SEP = chr(92)
COPY_CHUNK_SIZE = 1024 * 1024
PBO_VERSION_MAGIC = 0x56657273
PBO_STORED_METHOD = 0


class PboError(Exception):
    pass


class PboArchiveEntry:
    def __init__(self, name, packing_method, original_size, reserved, timestamp, data_size, offset=0):
        self.name = name
        self.packing_method = packing_method
        self.original_size = original_size
        self.reserved = reserved
        self.timestamp = timestamp
        self.data_size = data_size
        self.offset = offset


def format_byte_size(size):
    try:
        size = float(size)
    except Exception:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0

    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1

    if index == 0:
        return f"{int(size)} {units[index]}"

    return f"{size:.1f} {units[index]}"


def read_pbo_cstring(file, max_length=8192):
    data = bytearray()

    while len(data) <= max_length:
        chunk = file.read(1)

        if not chunk:
            raise PboError("Unexpected end of file while reading PBO header string.")

        if chunk == ZERO:
            return data.decode("utf-8", errors="replace")

        data.extend(chunk)

    raise PboError("PBO header string is too long or corrupt.")


def read_pbo_header_fields(file):
    raw = file.read(20)

    if len(raw) != 20:
        raise PboError("Unexpected end of file while reading PBO header fields.")

    return struct.unpack("<IIIII", raw)


def read_pbo_properties(file):
    properties = {}

    while True:
        key = read_pbo_cstring(file, 1024)

        if not key:
            break

        properties[key] = read_pbo_cstring(file, 8192)

    return properties


def get_pbo_method_label(packing_method):
    if packing_method == PBO_STORED_METHOD:
        return "stored"

    try:
        raw_label = struct.pack("<I", packing_method).decode("ascii", errors="ignore").strip()
    except Exception:
        raw_label = ""

    if raw_label and all(32 <= ord(char) <= 126 for char in raw_label):
        return f"{raw_label} / 0x{packing_method:08X}"

    return f"0x{packing_method:08X}"


def format_pbo_timestamp(timestamp):
    if not timestamp:
        return ""

    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def read_pbo_archive(pbo_path):
    if not pbo_path or not os.path.isfile(pbo_path):
        raise PboError(f"PBO file does not exist: {pbo_path}")

    file_size = os.path.getsize(pbo_path)
    entries = []
    properties = {}

    with open(pbo_path, "rb") as file:
        first_name = read_pbo_cstring(file)
        pending_name = None

        if first_name:
            pending_name = first_name
        else:
            fields = read_pbo_header_fields(file)

            if fields[0] == PBO_VERSION_MAGIC:
                properties = read_pbo_properties(file)
            elif all(value == 0 for value in fields):
                return {
                    "path": pbo_path,
                    "size": file_size,
                    "properties": properties,
                    "entries": entries,
                    "data_start": file.tell(),
                    "payload_end": file.tell(),
                    "footer_size": max(0, file_size - file.tell()),
                }
            else:
                raise PboError(f"Unsupported or corrupt PBO header marker: 0x{fields[0]:08X}")

        while True:
            name = pending_name if pending_name is not None else read_pbo_cstring(file)
            pending_name = None
            packing_method, original_size, reserved, timestamp, data_size = read_pbo_header_fields(file)

            if not name:
                if all(value == 0 for value in [packing_method, original_size, reserved, timestamp, data_size]):
                    data_start = file.tell()
                    break

                if packing_method == PBO_VERSION_MAGIC:
                    properties.update(read_pbo_properties(file))
                    continue

                raise PboError(f"Unsupported or corrupt PBO header entry: 0x{packing_method:08X}")

            entries.append(PboArchiveEntry(name, packing_method, original_size, reserved, timestamp, data_size))

        offset = data_start

        for entry in entries:
            entry.offset = offset
            offset += entry.data_size

        if offset > file_size:
            raise PboError("PBO header file sizes exceed archive length. The PBO may be corrupt.")

    return {
        "path": pbo_path,
        "size": file_size,
        "properties": properties,
        "entries": entries,
        "data_start": data_start,
        "payload_end": offset,
        "footer_size": max(0, file_size - offset),
    }


def get_safe_pbo_extract_path(output_dir, entry_name):
    if not output_dir:
        raise PboError("Extract output folder is empty.")

    if not entry_name or "\x00" in entry_name:
        raise PboError("PBO entry has an invalid empty or NUL-containing filename.")

    raw = entry_name.replace("/", WIN_SEP)

    if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
        raise PboError(f"Refusing to extract absolute PBO path: {entry_name}")

    parts = []

    for part in re.split(r"[\\/]+", raw):
        if not part or part == ".":
            continue

        if part == ".." or ":" in part:
            raise PboError(f"Refusing unsafe PBO path: {entry_name}")

        parts.append(part)

    if not parts:
        raise PboError(f"Refusing empty PBO path after normalization: {entry_name}")

    root = Path(output_dir).resolve(strict=False)
    target = root.joinpath(*parts).resolve(strict=False)

    try:
        target.relative_to(root)
    except ValueError:
        raise PboError(f"Refusing to extract outside output folder: {entry_name}")

    return target


def extract_pbo_files(pbo_path, output_dir, selected_names=None, log=None):
    archive = read_pbo_archive(pbo_path)
    selected = set(selected_names or [])
    should_filter = bool(selected)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    extracted = 0
    total_bytes = 0

    with open(pbo_path, "rb") as source:
        for entry in archive["entries"]:
            if should_filter and entry.name not in selected:
                continue

            if entry.packing_method != PBO_STORED_METHOD:
                raise PboError(f"Cannot extract compressed or unsupported PBO entry: {entry.name} ({get_pbo_method_label(entry.packing_method)})")

            target = get_safe_pbo_extract_path(output_dir, entry.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.seek(entry.offset)
            remaining = entry.data_size

            with open(target, "wb") as out:
                while remaining > 0:
                    chunk = source.read(min(COPY_CHUNK_SIZE, remaining))

                    if not chunk:
                        raise PboError(f"Unexpected end of PBO data while extracting: {entry.name}")

                    out.write(chunk)
                    remaining -= len(chunk)

            extracted += 1
            total_bytes += entry.data_size

            if log:
                log(f"Extracted: {entry.name}")

    if extracted == 0:
        raise PboError("No PBO entries were selected for extraction.")

    return {
        "files": extracted,
        "bytes": total_bytes,
        "output_dir": str(output_root),
    }
