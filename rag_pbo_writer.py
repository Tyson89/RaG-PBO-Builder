import hashlib
import os
import struct

from rag_builder_common import (
    BuildError,
    COPY_CHUNK_SIZE,
    PBO_VERSION_MAGIC,
    WIN_SEP,
    ZERO,
    safe_ascii,
    try_relpath,
    should_skip_dir,
    should_skip_file,
)


def pack_pbo(source_dir, output_path, prefix, log, extra_patterns=None):
    source_dir = os.path.normpath(source_dir)
    output_path = os.path.normpath(output_path)
    if not os.path.isdir(source_dir):
        raise BuildError(f"Source is not a directory: {source_dir}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            log(f"WARNING: Skipped external folder while packing because it is on a different drive than the pack source: {root}")
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in filenames:
            if should_skip_file(fname, extra_patterns):
                continue
            full = os.path.join(root, fname)
            rel = try_relpath(full, source_dir)
            if not rel:
                log(f"WARNING: Skipped external file while packing because it is on a different drive than the pack source: {full}")
                continue
            rel = rel.replace(os.sep, WIN_SEP)
            files.append({"name": rel, "path": full, "data": None, "size": os.path.getsize(full)})

    if prefix:
        normalized_prefix = prefix.replace("/", WIN_SEP).strip(WIN_SEP)
        prefix_data = safe_ascii(normalized_prefix + "\r\n", "PBO prefix file")
        files = [entry for entry in files if entry["name"].lower() not in {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}]
        files.append({"name": "$PBOPREFIX$", "path": None, "data": prefix_data, "size": len(prefix_data)})

    files.sort(key=lambda item: item["name"].lower())
    header = bytearray()
    header.extend(ZERO)
    header.extend(struct.pack("<I", PBO_VERSION_MAGIC))
    header.extend(struct.pack("<IIII", 0, 0, 0, 0))
    if prefix:
        header.extend(b"prefix")
        header.extend(ZERO)
        header.extend(safe_ascii(prefix, "PBO prefix"))
        header.extend(ZERO)
    header.extend(ZERO)
    for entry in files:
        header.extend(safe_ascii(entry["name"], "File path"))
        header.extend(ZERO)
        header.extend(struct.pack("<IIIII", 0, entry["size"], 0, 0, entry["size"]))
    header.extend(ZERO)
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))
    temp_output = output_path + ".tmp"
    sha = hashlib.sha1()
    total = 0
    try:
        with open(temp_output, "wb") as out:
            out.write(header)
            sha.update(header)
            total += len(header)
            for entry in files:
                data = entry["data"]
                if data is not None:
                    out.write(data)
                    sha.update(data)
                    total += len(data)
                    continue

                with open(entry["path"], "rb") as file:
                    while True:
                        chunk = file.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        sha.update(chunk)
                        total += len(chunk)
            out.write(ZERO)
            out.write(sha.digest())
            total += 21
        os.replace(temp_output, output_path)
    except Exception:
        if os.path.isfile(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass
        raise
    log(f"Packed {len(files):4d} files / {total:,} bytes -> {output_path}")


def read_packed_pbo_prefix(pbo_path):
    try:
        with open(pbo_path, "rb") as file:
            data = file.read(65536)
    except OSError:
        return ""

    marker = b"prefix\x00"
    index = data.find(marker)

    if index < 0:
        return ""

    start = index + len(marker)
    end = data.find(b"\x00", start)

    if end < 0:
        return ""

    return data[start:end].decode("ascii", errors="ignore")


def verify_packed_pbo(pbo_path, expected_prefix, log):
    if not os.path.isfile(pbo_path):
        raise BuildError(f"Post-pack verification failed. PBO does not exist: {pbo_path}")

    size = os.path.getsize(pbo_path)

    if size <= 0:
        raise BuildError(f"Post-pack verification failed. PBO is empty: {pbo_path}")

    packed_prefix = read_packed_pbo_prefix(pbo_path)

    if expected_prefix and packed_prefix and packed_prefix != expected_prefix:
        raise BuildError(f"Post-pack verification failed. PBO prefix mismatch. Expected '{expected_prefix}', got '{packed_prefix}'.")

    if expected_prefix and not packed_prefix:
        log("WARNING: Post-pack verification could not read the PBO prefix from the header.")
    else:
        log(f"Post-pack verification OK: size={size:,} bytes, prefix={packed_prefix or '<none>'}")


def pbo_entry_bytes_match_file(pbo_path, entry, source_file):
    try:
        source_size = os.path.getsize(source_file)
    except OSError:
        return False, "source WRP is missing"

    if entry.data_size != source_size:
        return False, f"size mismatch, packed={entry.data_size}, source={source_size}"

    if entry.packing_method != 0:
        return False, f"unexpected packed WRP method=0x{entry.packing_method:08X}"

    try:
        with open(pbo_path, "rb") as pbo_file, open(source_file, "rb") as source:
            pbo_file.seek(entry.offset)

            while True:
                left = source.read(COPY_CHUNK_SIZE)

                if not left:
                    break

                right = pbo_file.read(len(left))

                if left != right:
                    return False, "byte mismatch"
    except OSError as error:
        return False, str(error)

    return True, ""
