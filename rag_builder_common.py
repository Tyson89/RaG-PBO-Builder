import fnmatch
import os


class BuildError(Exception):
    pass


EXCLUDE_DIRS = {".git", ".svn", ".vscode", ".idea", "__pycache__"}
EXCLUDE_FILES = {".gitignore", ".gitattributes", "thumbs.db", "desktop.ini", ".ds_store", "$prefix$", "$pboprefix$", "$prefix$.txt", "$pboprefix$.txt"}
EXCLUDE_EXTENSIONS = {".delete"}

ZERO = bytes([0])
WIN_SEP = chr(92)
COPY_CHUNK_SIZE = 1024 * 1024
PBO_VERSION_MAGIC = 0x56657273


def safe_ascii(value, label):
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise BuildError(f"{label} contains non-ASCII characters: {value}")


def parse_exclude_patterns(raw_patterns):
    if not raw_patterns:
        return []
    raw_patterns = raw_patterns.replace(";", ",").replace("\r", "").replace("\n", ",")
    return [item.strip() for item in raw_patterns.split(",") if item.strip()]


def matches_exclude_pattern(name, patterns):
    if not patterns:
        return False
    value = name.lower()
    for pattern in patterns:
        test = pattern.strip().lower()
        if test and (value == test or fnmatch.fnmatch(value, test)):
            return True
    return False


def should_skip_dir(dirname, extra_patterns=None):
    name = dirname.lower()
    return name in EXCLUDE_DIRS or matches_exclude_pattern(name, extra_patterns)


def should_skip_file(filename, extra_patterns=None):
    name = filename.lower()
    if name in {"config.cpp", "config.bin"}:
        return False
    if name in EXCLUDE_FILES or os.path.splitext(name)[1].lower() in EXCLUDE_EXTENSIONS:
        return True
    return matches_exclude_pattern(name, extra_patterns)


def source_file_should_be_staged(filename, extra_patterns=None):
    return filename.lower() == "config.cpp" or not should_skip_file(filename, extra_patterns)
