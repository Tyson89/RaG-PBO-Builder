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
PBO_COMPRESSED_METHOD = 0x43707273
PBO_ENCRYPTED_METHOD = 0x456E6372


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

    if packing_method == PBO_COMPRESSED_METHOD:
        return "compressed (Cprs)"

    if packing_method == PBO_ENCRYPTED_METHOD:
        return "encrypted/unsupported (Enco)"

    try:
        raw_label = struct.pack("<I", packing_method).decode("ascii", errors="ignore").strip()
    except Exception:
        raw_label = ""

    if raw_label and all(32 <= ord(char) <= 126 for char in raw_label):
        return f"{raw_label} / 0x{packing_method:08X}"

    return f"0x{packing_method:08X}"


def is_pbo_entry_compressed(entry):
    return entry.packing_method == PBO_COMPRESSED_METHOD


def is_pbo_entry_supported(entry):
    return entry.packing_method in {PBO_STORED_METHOD, PBO_COMPRESSED_METHOD}


def get_pbo_entry_unpacked_size(entry):
    if is_pbo_entry_compressed(entry):
        return entry.original_size

    return entry.data_size


def format_pbo_timestamp(timestamp):
    if not timestamp:
        return ""

    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def decompress_pbo_lzss(data, expected_size):
    if expected_size < 0:
        raise PboError("Compressed PBO entry has an invalid original size.")

    if len(data) < 4:
        raise PboError("Compressed PBO entry is too small to contain a checksum.")

    packed_end = len(data) - 4
    read_checksum = struct.unpack("<I", data[packed_end:])[0]
    output = bytearray()
    offset = 0

    while offset < packed_end and len(output) < expected_size:
        flag_byte = data[offset]
        offset += 1
        bit = 1

        while bit < 256 and offset < packed_end and len(output) < expected_size:
            if flag_byte & bit:
                output.append(data[offset])
                offset += 1
            else:
                if offset + 1 >= packed_end:
                    raise PboError("Compressed PBO entry ended inside an LZSS pointer.")

                b1 = data[offset]
                b2 = data[offset + 1]
                offset += 2
                rpos = len(output) - b1 - 256 * (b2 >> 4)
                rlen = (b2 & 0x0F) + 3

                while rlen > 0 and len(output) < expected_size:
                    if rpos < 0:
                        value = 0x20
                    elif rpos < len(output):
                        value = output[rpos]
                    else:
                        raise PboError("Compressed PBO entry has an invalid LZSS back-reference.")

                    output.append(value)
                    rpos += 1
                    rlen -= 1

            bit <<= 1

    if len(output) != expected_size:
        raise PboError(f"Compressed PBO entry expanded to {len(output)} bytes, expected {expected_size}.")

    calculated_checksum = sum(output) & 0xFFFFFFFF

    if calculated_checksum != read_checksum:
        raise PboError("Compressed PBO entry checksum does not match after decompression.")

    return bytes(output)


def read_pbo_entry_payload(source, entry):
    source.seek(entry.offset)
    data = source.read(entry.data_size)

    if len(data) != entry.data_size:
        raise PboError(f"Unexpected end of PBO data while reading: {entry.name}")

    if entry.packing_method == PBO_STORED_METHOD:
        return data

    if is_pbo_entry_compressed(entry):
        if entry.original_size <= 0:
            raise PboError(f"Compressed PBO entry has an invalid original size: {entry.name}")

        return decompress_pbo_lzss(data, entry.original_size)

    raise PboError(f"Cannot read unsupported PBO entry: {entry.name} ({get_pbo_method_label(entry.packing_method)})")


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

                if packing_method in {PBO_COMPRESSED_METHOD, PBO_ENCRYPTED_METHOD}:
                    data_start = file.tell()
                    break

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
    extracted_paths = []

    with open(pbo_path, "rb") as source:
        for entry in archive["entries"]:
            if should_filter and entry.name not in selected:
                continue

            if not is_pbo_entry_supported(entry):
                raise PboError(f"Cannot extract unsupported PBO entry: {entry.name} ({get_pbo_method_label(entry.packing_method)})")

            target = get_safe_pbo_extract_path(output_dir, entry.name)
            target.parent.mkdir(parents=True, exist_ok=True)

            if entry.packing_method == PBO_STORED_METHOD:
                source.seek(entry.offset)
                remaining = entry.data_size

                with open(target, "wb") as out:
                    while remaining > 0:
                        chunk = source.read(min(COPY_CHUNK_SIZE, remaining))

                        if not chunk:
                            raise PboError(f"Unexpected end of PBO data while extracting: {entry.name}")

                        out.write(chunk)
                        remaining -= len(chunk)

                output_size = entry.data_size
            else:
                data = read_pbo_entry_payload(source, entry)
                target.write_bytes(data)
                output_size = len(data)

            extracted += 1
            total_bytes += output_size
            extracted_paths.append(str(target))

            if log:
                suffix = " (decompressed)" if is_pbo_entry_compressed(entry) else ""
                log(f"Extracted: {entry.name}{suffix}")

    if extracted == 0:
        raise PboError("No PBO entries were selected for extraction.")

    return {
        "files": extracted,
        "bytes": total_bytes,
        "output_dir": str(output_root),
        "paths": extracted_paths,
    }


def read_pbo_entry_data(pbo_path, entry_name, max_bytes=None):
    archive = read_pbo_archive(pbo_path)

    for entry in archive["entries"]:
        if entry.name != entry_name:
            continue

        if not is_pbo_entry_supported(entry):
            raise PboError(f"Cannot read unsupported PBO entry: {entry.name} ({get_pbo_method_label(entry.packing_method)})")

        unpacked_size = get_pbo_entry_unpacked_size(entry)

        if max_bytes is not None and unpacked_size > max_bytes:
            raise PboError(f"PBO entry is too large to preview: {entry.name} ({format_byte_size(unpacked_size)})")

        with open(pbo_path, "rb") as source:
            data = read_pbo_entry_payload(source, entry)

        return data

    raise PboError(f"PBO entry not found: {entry_name}")
