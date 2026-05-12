import fnmatch
import os
import subprocess


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


def get_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def normalize_working_dir(project_root):
    value = project_root.rstrip(WIN_SEP + "/")
    if len(value) == 2 and value[1] == ":":
        return value + WIN_SEP
    return value


def get_safe_temp_name(name):
    safe = name.strip() if name else "addon"
    safe = safe.replace("/", "_").replace(WIN_SEP, "_").replace(":", "_")
    return safe or "addon"


def read_pbo_prefix_file(source_dir):
    names = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
    try:
        entries = os.listdir(source_dir)
    except OSError:
        return ""
    for entry in entries:
        if entry.lower() not in names:
            continue
        path = os.path.join(source_dir, entry)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as file:
                for line in file:
                    prefix = line.strip().strip('"').strip("'")
                    if prefix:
                        return prefix.replace("/", WIN_SEP).strip(WIN_SEP + "/")
        except OSError:
            return ""
    return ""


def get_pbo_prefix(pbo_base_name, source_dir=None):
    file_prefix = read_pbo_prefix_file(source_dir) if source_dir else ""
    return file_prefix or pbo_base_name


def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
