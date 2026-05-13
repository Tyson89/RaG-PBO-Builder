import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from rag_builder_common import (
    WIN_SEP,
    format_duration,
    get_hidden_startupinfo,
    get_pbo_prefix,
    get_safe_temp_name,
    get_subprocess_creationflags,
    normalize_working_dir,
    parse_exclude_patterns,
    should_skip_dir,
    should_skip_file,
)
from rag_config_tools import (
    find_class_body,
    find_matching_brace,
    get_line_number_from_index,
    iter_class_blocks,
    iter_top_level_class_blocks,
    parse_array_values,
    strip_cpp_comments,
)
from rag_version import APP_VERSION

APP_TITLE = "RaG PBO Builder"
DEFAULT_TEMP_DIR = str(Path("P:/Temp"))
DEFAULT_PROJECT_ROOT = "P:"

REFERENCE_FILE_EXTENSIONS = (
    "paa", "rvmat", "p3d", "wrp", "wss", "ogg", "cfg", "cpp", "hpp", "h", "emat", "edds", "ptc", "bisurf", "wav", "shp", "dbf", "shx", "prj",
)
REFERENCE_REGEX = re.compile(
    r"[\"']([^\"']+\.(?:" + "|".join(REFERENCE_FILE_EXTENSIONS) + r"))[\"']",
    re.IGNORECASE,
)
RVMAT_TEXTURE_REGEX = re.compile(
    r"\btexture\s*=\s*[\"]?([^\";\r\n]+\.(?:paa|png|tga|psd|rvmat|emat|edds|ptc))[\"]?",
    re.IGNORECASE,
)
P3D_INTERNAL_REFERENCE_REGEX = re.compile(
    rb"([A-Za-z0-9_@#$%&()\-+={}\[\],.;: /\\]+\.(?:paa|rvmat|p3d|wrp|emat|edds|ptc|bisurf|shp|dbf|shx|prj))",
    re.IGNORECASE,
)
PREFLIGHT_TEXT_EXTENSIONS = (".cpp", ".hpp", ".h", ".rvmat", ".cfg", ".c", ".xml", ".json", ".layout", ".imageset")
RISKY_REFERENCE_EXTENSIONS = {".paa", ".rvmat", ".p3d", ".wss", ".ogg", ".wav", ".emat", ".edds", ".ptc", ".bisurf"}
SOURCE_TEXTURE_EXTENSIONS = {".png", ".tga", ".psd"}
MODDED_CLASS_INHERITANCE_REGEX = re.compile(
    r"^\s*modded\s+class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:(extends)\s+([A-Za-z_][A-Za-z0-9_]*)|(:)\s*([A-Za-z_][A-Za-z0-9_]*))",
    re.IGNORECASE | re.MULTILINE,
)
SCRIPT_CLASS_BLOCK_REGEX = re.compile(
    r"\b(?P<modded>modded\s+)?class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b(?P<header>[^;{]*)\{",
    re.IGNORECASE,
)
SCRIPT_SETACTIONS_METHOD_REGEX = re.compile(
    r"\b(?:override\s+)?(?:void|bool|int|float|string|vector|typename|auto|autoptr|ref|[A-Za-z_][A-Za-z0-9_<>,\s]*)\s+SetActions\s*\([^)]*\)\s*(?:override\s*)?\{",
    re.IGNORECASE,
)
SCRIPT_SUPER_SETACTIONS_REGEX = re.compile(r"\bsuper\s*\.\s*SetActions\s*\(", re.IGNORECASE)

SCRIPT_MODULE_FOLDERS = {
    "engineScriptModule": "scripts/1_Core",
    "gamelibScriptModule": "scripts/2_GameLib",
    "gameScriptModule": "scripts/3_Game",
    "worldScriptModule": "scripts/4_World",
    "missionScriptModule": "scripts/5_Mission",
}
SCRIPT_FOLDER_TO_MODULE = {value.lower().replace("/", WIN_SEP): key for key, value in SCRIPT_MODULE_FOLDERS.items()}
TERRAIN_ROAD_SHAPE_EXTENSIONS = {".shp", ".dbf", ".shx", ".prj"}
TERRAIN_WRP_INTERNAL_REFERENCE_REGEX = re.compile(
    rb"([A-Za-z0-9_@#$%&()\-+={}\[\],.;: /\\]+\.(?:paa|rvmat|p3d|wrp|emat|edds|ptc|bisurf|shp|dbf|shx|prj|xml))",
    re.IGNORECASE,
)
TERRAIN_SOURCE_FOLDER_NAMES = {"source", "sources", "terrainbuilder", "terrain_builder", "tb", "export", "exports"}
TERRAIN_SOURCE_EXPORT_EXTENSIONS = {".pew", ".asc", ".xyz", ".tif", ".tiff", ".lbt", ".psd", ".bmp", ".tv4p", ".tv4l", ".raw", ".png", ".tga"}
TERRAIN_ALWAYS_SOURCE_EXPORT_EXTENSIONS = {".pew", ".asc", ".xyz", ".tif", ".tiff", ".lbt", ".tv4p", ".tv4l", ".raw"}
TERRAIN_SOURCE_IMAGE_EXTENSIONS = {".png", ".tga", ".psd", ".bmp"}
TERRAIN_SOURCE_IMAGE_KEYWORDS = {"sat", "satellite", "mask", "height", "heightmap", "normal", "slope", "rough", "spec", "surface"}
TERRAIN_LARGE_SOURCE_FILE_BYTES = 100 * 1024 * 1024
TERRAIN_LAYER_FOLDER_NAMES = {"layers", "data\\layers", "data/layers"}
MODULAR_TERRAIN_FOLDER_NAMES = {
    "world", "data", "terrain", "roads", "road", "nature", "navmesh", "city", "cities",
    "military", "structures", "objects", "clutter", "surfaces", "surface", "environment",
}
TERRAIN_SIZE_WARNING_BYTES = 1500 * 1024 * 1024
TERRAIN_SIZE_HIGH_WARNING_BYTES = 3000 * 1024 * 1024
TERRAIN_2D_MAP_REFERENCE_REGEX = re.compile(
    r"\b(?:mapTexture|mapImage|worldMap|satelliteMap|topoMap|textureMap|mapLegend|terrainMap|paperMap|navMap)\b\s*=\s*[\"']([^\"']+\.(?:paa|edds|png|tga))[\"']",
    re.IGNORECASE,
)
SAFE_INTERNAL_BASE_CLASSES = {
    "object", "managed", "pluginbase", "missionbase", "house", "buildingsuper", "itembase",
}
REQUIRED_ADDON_HINTS = {
    "Inventory_Base": "DZ_Data",
    "Clothing_Base": "DZ_Characters",
    "Clothing": "DZ_Characters",
    "CarScript": "DZ_Vehicles_Wheeled",
    "Truck_01_Base": "DZ_Vehicles_Wheeled",
    "Weapon_Base": "DZ_Weapons_Firearms",
    "Rifle_Base": "DZ_Weapons_Firearms",
    "Magazine_Base": "DZ_Weapons_Magazines",
    "Edible_Base": "DZ_Gear_Food",
    "Bottle_Base": "DZ_Gear_Drinks",
    "Container_Base": "DZ_Gear_Containers",
    "TentBase": "DZ_Gear_Camping",
}

class PreflightResult:
    def __init__(self):
        self.errors = 0
        self.warnings = 0
        self.info = 0
        self.checked_files = 0
        self.checked_references = 0
        self.checked_configs = 0
        self.checked_script_modules = 0
        self.checked_prefixes = 0
        self.checked_paths = 0
        self.checked_terrain = 0
        self.events = []
        self.report_txt = ""
        self.report_json = ""

    def add_event(self, severity, message):
        self.events.append({
            "severity": severity,
            "message": message,
        })

    def error(self, log, message):
        self.errors += 1
        self.add_event("ERROR", message)
        log("ERROR: " + message)

    def warning(self, log, message):
        self.warnings += 1
        self.add_event("WARNING", message)
        log("WARNING: " + message)

    def note(self, log, message):
        self.info += 1
        self.add_event("INFO", message)
        log("INFO: " + message)


def strip_dayz_resource_guid_prefix(value):
    # DayZ .layout and some GUI/resource files can prefix asset paths with a
    # Workbench/resource GUID, for example:
    #   {03C79F5D93FF384F}RaG_Config/Data/LoadingScreens/1.edds
    # The GUID is not part of the actual packed PBO path and must be ignored
    # during reference resolution, exclude checks, and missing-file checks.
    value = str(value).strip()

    match = re.match(r"^\{[0-9A-Fa-f]{8,32}\}(.+)$", value)

    if match:
        return match.group(1).strip()

    return value


def normalize_reference_path(reference):
    value = str(reference).strip().strip('"').strip("'")
    value = strip_dayz_resource_guid_prefix(value)
    value = value.replace("/", WIN_SEP)

    while value.startswith(WIN_SEP):
        value = value[1:]

    return value


def normalize_rel_path_key(path_value):
    return normalize_reference_path(path_value).lower()


def is_path_inside(child, parent):
    try:
        Path(child).resolve(strict=False).relative_to(Path(parent).resolve(strict=False))
        return True
    except Exception:
        return False


def path_would_be_excluded(relative_path, extra_patterns=None):
    parts = [part for part in normalize_reference_path(relative_path).split(WIN_SEP) if part]

    if not parts:
        return False

    for directory in parts[:-1]:
        if should_skip_dir(directory, extra_patterns):
            return True

    return should_skip_file(parts[-1], extra_patterns)


def resolve_reference_path(reference, addon_source_dir, project_root):
    ref = normalize_reference_path(reference)

    if not ref:
        return "", "missing"

    ref_os = ref.replace(WIN_SEP, os.sep)
    candidates = []

    if os.path.isabs(ref_os):
        candidates.append(ref_os)

    addon_source_dir = os.path.normpath(addon_source_dir)
    addon_parent = os.path.dirname(addon_source_dir)

    candidates.append(os.path.join(addon_source_dir, ref_os))
    candidates.append(os.path.join(addon_parent, ref_os))

    parts = [part for part in ref.split(WIN_SEP) if part]
    addon_folder = os.path.basename(os.path.normpath(addon_source_dir))
    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
    prefix_first = explicit_prefix.split(WIN_SEP)[0] if explicit_prefix else ""

    if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
        candidates.append(os.path.join(addon_source_dir, *parts[1:]))

    if project_root:
        candidates.append(os.path.join(normalize_working_dir(project_root), ref_os))

    seen = set()

    for candidate in candidates:
        candidate = os.path.normpath(candidate)
        key = os.path.normcase(os.path.abspath(candidate))

        if key in seen:
            continue

        seen.add(key)

        if os.path.isfile(candidate):
            return candidate, "ok"

    return candidates[0] if candidates else ref_os, "missing"


def format_source_location(source_file, addon_source_dir, line_number=0):
    if source_file:
        try:
            rel_file = os.path.relpath(source_file, addon_source_dir).replace(os.sep, WIN_SEP)
        except Exception:
            rel_file = str(source_file)
    else:
        rel_file = "<unknown>"

    if line_number and line_number > 0:
        return f"{rel_file}: line {line_number}"

    return rel_file


def report_reference_status(reference, source_file, addon_source_dir, project_root, extra_patterns, result, log, severity="error", context="referenced file", line_number=0):
    ref = normalize_reference_path(reference)

    if not ref:
        return

    source_location = format_source_location(source_file, addon_source_dir, line_number)
    resolved, status = resolve_reference_path(ref, addon_source_dir, project_root)

    result.checked_references += 1

    if status == "missing":
        message = f"Missing {context} in {source_location}: {ref}"
        if severity == "warning":
            result.warning(log, message)
        else:
            result.error(log, message)
        return

    if is_path_inside(resolved, addon_source_dir):
        rel_resolved = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)

        if path_would_be_excluded(rel_resolved, extra_patterns):
            result.error(log, f"Referenced file exists but is excluded from the packed PBO in {source_location}: {ref} -> {rel_resolved}")


def collect_config_cpp_files(source_dir, extra_patterns=None):
    configs = []

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if file.lower() == "config.cpp":
                configs.append(os.path.join(root, file))

    configs.sort(key=lambda path: os.path.relpath(path, source_dir).lower())
    return configs


def format_config_path(config_cpp, base_dir=""):
    if base_dir:
        try:
            return os.path.relpath(config_cpp, base_dir).replace(os.sep, WIN_SEP)
        except Exception:
            pass

    return str(config_cpp)


def collect_pbo_prefix_files(source_dir):
    prefix_names = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
    matches = []

    try:
        entries = os.listdir(source_dir)
    except OSError:
        return matches

    for entry in entries:
        if entry.lower() in prefix_names:
            full = os.path.join(source_dir, entry)
            if os.path.isfile(full):
                matches.append(full)

    matches.sort(key=lambda value: os.path.basename(value).lower())
    return matches


def read_raw_prefix_file(prefix_file):
    try:
        with open(prefix_file, "r", encoding="utf-8-sig", errors="ignore") as file:
            for line in file:
                value = line.strip().strip('"').strip("'")
                if value:
                    return value
    except OSError:
        return ""

    return ""


def preflight_check_prefix(addon_name, addon_source_dir, result, log):
    result.checked_prefixes += 1
    prefix_files = collect_pbo_prefix_files(addon_source_dir)

    if len(prefix_files) > 1:
        names = ", ".join(os.path.basename(path) for path in prefix_files)
        result.warning(log, f"Multiple PBO prefix files found in {addon_name}: {names}")

    if not prefix_files:
        result.note(log, f"No PBO prefix file found in {addon_name}. The PBO Name/folder name will be used as prefix.")
        return

    raw_prefix = read_raw_prefix_file(prefix_files[0])

    if not raw_prefix:
        result.warning(log, f"PBO prefix file is empty: {prefix_files[0]}")
        return

    if raw_prefix.startswith("P:" + WIN_SEP) or raw_prefix.startswith("P:/"):
        result.warning(log, f"PBO prefix starts with P: in {addon_name}: {raw_prefix}")

    if raw_prefix.startswith(WIN_SEP) or raw_prefix.startswith("/"):
        result.warning(log, f"PBO prefix has a leading slash in {addon_name}: {raw_prefix}")

    if raw_prefix.endswith(WIN_SEP) or raw_prefix.endswith("/"):
        result.warning(log, f"PBO prefix has a trailing slash in {addon_name}: {raw_prefix}")

    if "/" in raw_prefix:
        result.warning(log, f"PBO prefix uses forward slashes in {addon_name}. Backslashes are recommended: {raw_prefix}")

    normalized_prefix = raw_prefix.replace("/", WIN_SEP).strip(WIN_SEP)
    last_prefix_part = normalized_prefix.split(WIN_SEP)[-1]
    folder_name = os.path.basename(os.path.normpath(addon_source_dir))
    prefix_norm = re.sub(r"[^a-z0-9]", "", last_prefix_part.lower())
    folder_norm = re.sub(r"[^a-z0-9]", "", folder_name.lower())

    if prefix_norm and folder_norm and prefix_norm not in folder_norm and not folder_norm.endswith(prefix_norm):
        result.warning(log, f"PBO prefix seems unrelated to the addon folder in {addon_name}: prefix '{raw_prefix}', folder '{folder_name}'")

    result.note(log, f"Detected PBO prefix for {addon_name}: {normalized_prefix}")


def preflight_check_config_cpp(config_cpp, cfgconvert_exe, temp_root, addon_name, result, log, addon_source_dir=""):
    config_label = format_config_path(config_cpp, addon_source_dir)

    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        result.warning(log, f"CfgConvert.exe is not configured. Skipping config.cpp syntax check for {config_label}.")
        return

    check_dir = os.path.join(temp_root, "preflight", get_safe_temp_name(addon_name))
    os.makedirs(check_dir, exist_ok=True)
    output_bin = os.path.join(check_dir, get_safe_temp_name(config_label) + ".bin")

    if os.path.isfile(output_bin):
        os.remove(output_bin)

    cmd = [cfgconvert_exe, "-bin", "-dst", output_bin, config_cpp]
    completed = subprocess.run(
        cmd,
        cwd=os.path.dirname(config_cpp),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=get_subprocess_creationflags(),
        startupinfo=get_hidden_startupinfo(),
    )

    if completed.returncode != 0 or not os.path.isfile(output_bin):
        reason = f"exit code {completed.returncode}" if completed.returncode != 0 else "config.bin was not produced"
        result.error(log, f"Config syntax check failed in {config_label} ({reason})")
        log(f"  File: {config_cpp}")

        output_lines = (completed.stdout or "").splitlines()

        if output_lines:
            log(f"  CfgConvert output for {config_label}:")

            for line in output_lines:
                log("    " + line)
        else:
            log(f"  CfgConvert returned no output for {config_label}.")
    else:
        log(f"Config syntax OK: {config_label}")


def get_external_base_classes(content):
    clean = strip_cpp_comments(content)
    classes = []
    class_names = set()

    for class_name, base_name, _, _, _ in iter_class_blocks(clean):
        classes.append((class_name, base_name))
        class_names.add(class_name)

    external_bases = []

    for class_name, base_name in classes:
        if not base_name:
            continue

        if base_name in class_names:
            continue

        if base_name.lower() in SAFE_INTERNAL_BASE_CLASSES:
            continue

        external_bases.append((class_name, base_name))

    return external_bases


def get_required_addon_hints_for_bases(external_bases):
    hints = set()

    for _, base_name in external_bases:
        for base_hint, required_addon in REQUIRED_ADDON_HINTS.items():
            if base_name == base_hint or base_name.endswith(base_hint) or base_hint.lower() in base_name.lower():
                hints.add(required_addon)

    return sorted(hints)


def preflight_check_cfgpatches(config_cpp, addon_source_dir, result, log, enable_required_addons_hints=True, project_root=""):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        result.warning(log, f"Could not read config.cpp for CfgPatches check: {config_cpp}")
        return

    result.checked_configs += 1
    rel_config = os.path.relpath(config_cpp, addon_source_dir).replace(os.sep, WIN_SEP)
    clean = strip_cpp_comments(content)
    cfgpatches_body = find_class_body(clean, "CfgPatches")

    if not cfgpatches_body:
        result.error(log, f"config.cpp has no CfgPatches class: {rel_config}")
        return

    patch_classes = list(iter_top_level_class_blocks(cfgpatches_body))

    if not patch_classes:
        result.error(log, f"CfgPatches exists but contains no addon patch class: {rel_config}")
        return

    external_bases = get_external_base_classes(content) if enable_required_addons_hints else []
    required_hints = get_required_addon_hints_for_bases(external_bases) if enable_required_addons_hints else []

    for patch_name, _, patch_body in patch_classes:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", patch_name):
            result.warning(log, f"CfgPatches class name contains unsafe characters in {rel_config}: {patch_name}")

        required_addons = parse_array_values(patch_body, "requiredAddons")

        if required_addons is None:
            result.warning(log, f"requiredAddons[] is missing in CfgPatches class {patch_name} ({rel_config})")

            if external_bases:
                sample = ", ".join([f"{child}: {base}" for child, base in external_bases[:5]])
                result.warning(log, f"{patch_name} inherits from external-looking classes but has no requiredAddons[] entry: {sample}")

            continue

        if not required_addons:
            result.note(log, f"requiredAddons[] is empty in CfgPatches class {patch_name} ({rel_config}). This can be valid, but verify load order manually.")

            if external_bases:
                sample = ", ".join([f"{child}: {base}" for child, base in external_bases[:5]])
                result.warning(log, f"requiredAddons[] is empty, but {rel_config} inherits from external-looking classes: {sample}")

        if required_hints:
            missing_hints = [hint for hint in required_hints if hint not in required_addons]

            if missing_hints:
                result.note(log, f"Possible requiredAddons[] hints for {patch_name}: {', '.join(missing_hints)}")


def resolve_script_module_path(path_value, addon_source_dir, project_root, prefix=""):
    raw = normalize_reference_path(path_value).rstrip(WIN_SEP)

    if not raw:
        return "", False

    candidates = []
    addon_folder = os.path.basename(os.path.normpath(addon_source_dir))
    prefix_first = normalize_reference_path(prefix).split(WIN_SEP)[0] if prefix else ""

    candidates.append(os.path.join(addon_source_dir, raw))

    parts = [part for part in raw.split(WIN_SEP) if part]

    if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
        candidates.append(os.path.join(addon_source_dir, *parts[1:]))

    if project_root:
        candidates.append(os.path.join(normalize_working_dir(project_root), raw))

    seen = set()

    for candidate in candidates:
        candidate = os.path.normpath(candidate)
        key = os.path.normcase(os.path.abspath(candidate))

        if key in seen:
            continue

        seen.add(key)

        if os.path.isdir(candidate) or os.path.isfile(candidate):
            return candidate, True

    return candidates[0] if candidates else raw, False


def resolve_config_include_path(include_value, config_cpp, addon_source_dir="", project_root=""):
    raw = normalize_reference_path(include_value).strip(WIN_SEP)

    if not raw:
        return ""

    include_os = raw.replace(WIN_SEP, os.sep)
    config_dir = Path(config_cpp).parent
    candidates = [config_dir / include_os]

    if addon_source_dir:
        addon_source_dir = os.path.normpath(addon_source_dir)
        addon_parent = os.path.dirname(addon_source_dir)
        addon_folder = os.path.basename(addon_source_dir)
        explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
        prefix_first = explicit_prefix.split(WIN_SEP)[0] if explicit_prefix else ""
        parts = [part for part in raw.split(WIN_SEP) if part]

        candidates.append(Path(addon_source_dir) / include_os)
        candidates.append(Path(addon_parent) / include_os)

        if len(parts) > 1 and parts[0].lower() in {addon_folder.lower(), prefix_first.lower()}:
            candidates.append(Path(addon_source_dir).joinpath(*parts[1:]))

    if project_root:
        candidates.append(Path(normalize_working_dir(project_root)) / include_os)

    seen = set()

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate

        key = os.path.normcase(str(resolved))

        if key in seen:
            continue

        seen.add(key)

        if resolved.is_file():
            return str(resolved)

    return ""


def read_config_with_local_includes(config_cpp, seen=None, addon_source_dir="", project_root=""):
    if seen is None:
        seen = set()

    try:
        path = Path(config_cpp).resolve(strict=False)
    except Exception:
        path = Path(config_cpp)

    key = os.path.normcase(str(path))

    if key in seen:
        return ""

    seen.add(key)

    try:
        raw_content = Path(config_cpp).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    content = strip_cpp_comments(raw_content, preserve_lines=True)
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.IGNORECASE | re.MULTILINE)

    def replace_include(match):
        include_path = resolve_config_include_path(match.group(1).strip(), config_cpp, addon_source_dir, project_root)

        if include_path:
            return read_config_with_local_includes(include_path, seen, addon_source_dir, project_root)

        return match.group(0)

    return include_pattern.sub(replace_include, content)


def config_file_mentions_class(config_cpp, class_name, addon_source_dir="", project_root=""):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        return False

    clean = strip_cpp_comments(content)
    pattern = re.compile(r"\bclass\s+" + re.escape(class_name) + r"\b", re.IGNORECASE)

    return bool(pattern.search(clean))


def config_file_has_class(config_cpp, class_name, addon_source_dir="", project_root=""):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)

    if not content:
        return False

    clean = strip_cpp_comments(content)

    if find_class_body(clean, class_name):
        return True

    return config_file_mentions_class(config_cpp, class_name, addon_source_dir, project_root)


def find_config_cpp_with_class(config_files, class_name, addon_source_dir="", project_root=""):
    for config_cpp in config_files:
        if config_file_has_class(config_cpp, class_name, addon_source_dir, project_root):
            return config_cpp

    return ""


def get_root_config_cpp(addon_source_dir):
    return os.path.join(addon_source_dir, "config.cpp")


def is_root_config_cpp(config_cpp, addon_source_dir):
    try:
        return os.path.normcase(os.path.abspath(config_cpp)) == os.path.normcase(os.path.abspath(get_root_config_cpp(addon_source_dir)))
    except Exception:
        return False


def select_cfgpatches_check_configs(config_files, addon_source_dir, project_root=""):
    selected = []
    seen = set()
    root_config = get_root_config_cpp(addon_source_dir)

    def add(path):
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            selected.append(path)
            seen.add(key)

    if os.path.isfile(root_config):
        add(root_config)

    for config_cpp in config_files:
        if is_root_config_cpp(config_cpp, addon_source_dir):
            continue
        if config_file_has_class(config_cpp, "CfgPatches", addon_source_dir, project_root):
            add(config_cpp)

    return selected


def analyze_cfgmods_config(config_cpp, addon_source_dir, project_root):
    content = read_config_with_local_includes(config_cpp, None, addon_source_dir, project_root)
    clean = strip_cpp_comments(content) if content else ""
    cfgmods_body = find_class_body(clean, "CfgMods") if clean else ""
    declared = bool(re.search(r"\bclass\s+CfgMods\b", clean, re.IGNORECASE)) if clean else False
    has_defs = bool(re.search(r"\bclass\s+defs\b", cfgmods_body, re.IGNORECASE)) if cfgmods_body else False
    modules = {}
    score = 0

    if declared:
        score += 1

    if cfgmods_body:
        score += 10

    if has_defs:
        score += 10

    for module_name in SCRIPT_MODULE_FOLDERS:
        module_body = find_class_body(cfgmods_body, module_name) if cfgmods_body else ""
        files = parse_array_values(module_body, "files") if module_body else None
        modules[module_name] = {"body": module_body, "files": files}

        if module_body:
            score += 3

        if files is not None:
            score += 2

        if files:
            score += len(files)

    return {
        "config_cpp": config_cpp,
        "content": content,
        "clean": clean,
        "body": cfgmods_body,
        "declared": declared,
        "has_defs": has_defs,
        "modules": modules,
        "score": score,
    }


def find_best_cfgmods_config(config_files, addon_source_dir, project_root):
    analyses = []

    for config_cpp in config_files:
        analysis = analyze_cfgmods_config(config_cpp, addon_source_dir, project_root)

        if analysis["declared"] or analysis["body"]:
            analyses.append(analysis)

    if not analyses:
        return None

    analyses.sort(
        key=lambda item: (
            item["score"],
            1 if is_root_config_cpp(item["config_cpp"], addon_source_dir) else 0,
            -len(os.path.relpath(item["config_cpp"], addon_source_dir)),
        ),
        reverse=True,
    )
    return analyses[0]


def preflight_check_cfgmods(config_cpp, addon_name, addon_source_dir, project_root, result, log, cfgmods_analysis=None):
    analysis = cfgmods_analysis or analyze_cfgmods_config(config_cpp, addon_source_dir, project_root)
    content = analysis.get("content", "")

    if not content:
        result.warning(log, f"Could not read config.cpp for CfgMods check: {config_cpp}")
        return

    cfgmods_body = analysis.get("body", "")
    cfgmods_is_declared = bool(analysis.get("declared"))
    script_folders = []

    for folder in SCRIPT_MODULE_FOLDERS.values():
        folder_path = os.path.join(addon_source_dir, *folder.split("/"))

        if os.path.isdir(folder_path):
            script_folders.append((folder, folder_path))

    if not cfgmods_body:
        if cfgmods_is_declared:
            result.note(log, f"CfgMods class was found in addon configs, but the body could not be parsed for script module path checks: {addon_name}")
        elif script_folders:
            result.warning(log, f"Script folders exist but no CfgMods class was found in addon configs: {addon_name}")
        return

    rel_config = os.path.relpath(analysis.get("config_cpp", config_cpp), addon_source_dir).replace(os.sep, WIN_SEP)

    if not analysis.get("has_defs"):
        result.warning(log, f"CfgMods exists but has no class defs section in {rel_config}: {addon_name}")

    prefix = get_pbo_prefix(addon_name, addon_source_dir)
    referenced_paths = []
    missing_module_folder_keys = set()

    for module_name, expected_folder in SCRIPT_MODULE_FOLDERS.items():
        module_info = analysis.get("modules", {}).get(module_name, {})
        module_body = module_info.get("body", "")

        if not module_body:
            expected_path = os.path.join(addon_source_dir, *expected_folder.split("/"))
            if os.path.isdir(expected_path):
                result.warning(log, f"{expected_folder} exists but no {module_name} files[] entry was found in CfgMods ({rel_config}): {addon_name}")
                missing_module_folder_keys.add(os.path.normcase(os.path.abspath(expected_path)))
            continue

        result.checked_script_modules += 1
        files = module_info.get("files")

        if files is None:
            result.warning(log, f"{module_name} exists but has no files[] path: {addon_name}")
            continue

        if not files:
            result.warning(log, f"{module_name} files[] is empty: {addon_name}")
            continue

        for file_path in files:
            resolved, exists = resolve_script_module_path(file_path, addon_source_dir, project_root, prefix)
            referenced_paths.append(os.path.normcase(os.path.abspath(resolved)))

            if not exists:
                result.warning(log, f"{module_name} files[] path does not exist: {file_path}")

    for folder, folder_path in script_folders:
        folder_key = os.path.normcase(os.path.abspath(folder_path))
        if folder_key in missing_module_folder_keys:
            continue

        is_referenced = False

        for referenced in referenced_paths:
            if referenced == folder_key or folder_key.startswith(referenced + os.sep) or referenced.startswith(folder_key + os.sep):
                is_referenced = True
                break

        if not is_referenced:
            module_name = SCRIPT_FOLDER_TO_MODULE.get(folder.lower().replace("/", WIN_SEP), "script module")
            result.warning(log, f"{folder} exists but is not referenced by {module_name} files[] in CfgMods: {addon_name}")


def preflight_scan_references(file_path, addon_source_dir, project_root, extra_patterns, result, log, script_class_definitions=None, script_checks_enabled=True):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
            content = file.read()
    except Exception as e:
        result.warning(log, f"Could not read file for reference scan: {file_path} ({e})")
        return

    result.checked_files += 1
    seen = set()
    ext = os.path.splitext(file_path)[1].lower()
    scan_content = strip_cpp_comments(content, preserve_lines=True) if ext in {".cpp", ".hpp", ".h", ".c", ".cfg", ".rvmat"} else content

    if ext == ".c" and script_checks_enabled:
        preflight_scan_script_sanity(file_path, content, addon_source_dir, result, log)
        preflight_scan_script_modded_classes(file_path, scan_content, addon_source_dir, result, log)
        preflight_scan_script_setactions_super(file_path, scan_content, addon_source_dir, result, log)
        collect_script_class_definitions(file_path, scan_content, addon_source_dir, script_class_definitions)

    for match in REFERENCE_REGEX.finditer(scan_content):
        ref = normalize_reference_path(match.group(1).strip())
        ref_ext = os.path.splitext(ref)[1].lower()

        # Terrain-specific config references are handled by the WRP/terrain checks
        # so users do not get duplicate errors for worldName and road shape paths.
        if ext == ".cpp" and ref_ext in {".wrp", ".shp", ".dbf", ".shx", ".prj"}:
            continue

        key = ref.lower()
        line_number = get_line_number_from_index(scan_content, match.start(1))

        if key in seen:
            continue

        seen.add(key)
        report_reference_status(ref, file_path, addon_source_dir, project_root, extra_patterns, result, log, "error", "referenced file", line_number)

    if ext == ".rvmat":
        preflight_scan_rvmat_textures(file_path, scan_content, addon_source_dir, project_root, extra_patterns, result, log, seen)


def preflight_scan_script_modded_classes(file_path, content, addon_source_dir, result, log):
    for match in MODDED_CLASS_INHERITANCE_REGEX.finditer(content):
        class_name = match.group(1)
        operator = "extends" if match.group(2) else ":"
        base_class = match.group(3) or match.group(5) or ""
        line_number = get_line_number_from_index(content, match.start())
        source_location = format_source_location(file_path, addon_source_dir, line_number)
        result.warning(
            log,
            f"Modded class should not declare a base class in {source_location}: "
            f"modded class {class_name} {operator} {base_class}. Use 'modded class {class_name}' instead.",
        )


def iter_script_class_blocks(content):
    position = 0

    while True:
        match = SCRIPT_CLASS_BLOCK_REGEX.search(content, position)

        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_brace(content, open_index)

        if close_index < 0:
            position = match.end()
            continue

        yield {
            "name": match.group("name"),
            "modded": bool(match.group("modded")),
            "start": match.start(),
            "open": open_index,
            "close": close_index,
            "body": content[open_index + 1:close_index],
        }
        position = close_index + 1


def collect_script_class_definitions(file_path, content, addon_source_dir, script_class_definitions):
    if script_class_definitions is None:
        return

    for block in iter_script_class_blocks(content):
        if block["modded"]:
            continue

        line_number = get_line_number_from_index(content, block["start"])
        script_class_definitions.append({
            "name": block["name"],
            "file_path": file_path,
            "line": line_number,
            "source": format_source_location(file_path, addon_source_dir, line_number),
        })


def preflight_scan_duplicate_script_classes(addon_name, definitions, result, log):
    by_name = {}

    for definition in definitions:
        by_name.setdefault(definition["name"].lower(), []).append(definition)

    for duplicates in by_name.values():
        if len(duplicates) < 2:
            continue

        class_name = duplicates[0]["name"]
        locations = ", ".join(item["source"] for item in duplicates[:5])

        if len(duplicates) > 5:
            locations += f", ... {len(duplicates) - 5} more"

        result.warning(log, f"Duplicate script class definition in {addon_name}: class {class_name} appears in {locations}. Use 'modded class {class_name}' when extending an existing class.")


def preflight_scan_script_setactions_super(file_path, content, addon_source_dir, result, log):
    for block in iter_script_class_blocks(content):
        class_body = block["body"]

        for match in SCRIPT_SETACTIONS_METHOD_REGEX.finditer(class_body):
            open_index = class_body.find("{", match.start())
            close_index = find_matching_brace(class_body, open_index)

            if open_index < 0 or close_index < 0:
                continue

            method_body = class_body[open_index + 1:close_index]

            if SCRIPT_SUPER_SETACTIONS_REGEX.search(method_body):
                continue

            line_number = get_line_number_from_index(content, block["open"] + 1 + match.start())
            source_location = format_source_location(file_path, addon_source_dir, line_number)
            result.warning(log, f"SetActions() does not call super.SetActions() in {source_location}: class {block['name']}. This can remove inherited actions.")


def preflight_scan_script_sanity(file_path, content, addon_source_dir, result, log):
    stack = []
    in_string = ""
    string_start_line = 0
    escaped = False
    in_line_comment = False
    in_block_comment = False
    block_comment_start_line = 0
    line = 1
    index = 0
    closing_for = {"}": "{", ")": "(", "]": "["}

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if char == "\n":
            line += 1
            in_line_comment = False

        if in_line_comment:
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue

            index += 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            elif char in "\r\n":
                source_location = format_source_location(file_path, addon_source_dir, string_start_line)
                result.warning(log, f"Possible unterminated string in script file at {source_location}.")
                in_string = ""

            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            in_block_comment = True
            block_comment_start_line = line
            index += 2
            continue

        if char in {'"', "'"}:
            in_string = char
            string_start_line = line
            index += 1
            continue

        if char in "{([":
            stack.append((char, line))
        elif char in "}])":
            expected = closing_for[char]

            if not stack:
                source_location = format_source_location(file_path, addon_source_dir, line)
                result.warning(log, f"Unexpected closing '{char}' in script file at {source_location}.")
            elif stack[-1][0] == expected:
                stack.pop()
            else:
                opened, opened_line = stack[-1]
                source_location = format_source_location(file_path, addon_source_dir, line)
                opened_location = format_source_location(file_path, addon_source_dir, opened_line)
                result.warning(log, f"Mismatched '{char}' in script file at {source_location}; last opened '{opened}' at {opened_location}.")
                stack.pop()

        index += 1

    if in_block_comment:
        source_location = format_source_location(file_path, addon_source_dir, block_comment_start_line)
        result.warning(log, f"Unterminated block comment in script file at {source_location}.")

    if in_string:
        source_location = format_source_location(file_path, addon_source_dir, string_start_line)
        result.warning(log, f"Unterminated string in script file at {source_location}.")

    for opened, opened_line in stack[-10:]:
        source_location = format_source_location(file_path, addon_source_dir, opened_line)
        result.warning(log, f"Unclosed '{opened}' in script file at {source_location}.")


def preflight_scan_rvmat_textures(file_path, content, addon_source_dir, project_root, extra_patterns, result, log, seen=None):
    seen = seen if seen is not None else set()
    rel_file = os.path.relpath(file_path, addon_source_dir).replace(os.sep, WIN_SEP)

    for match in RVMAT_TEXTURE_REGEX.finditer(content):
        ref = normalize_reference_path(match.group(1).strip())
        key = ref.lower()
        line_number = get_line_number_from_index(content, match.start(1))
        source_location = format_source_location(file_path, addon_source_dir, line_number)

        if key in seen:
            continue

        seen.add(key)
        ext = os.path.splitext(ref)[1].lower()

        if ext in SOURCE_TEXTURE_EXTENSIONS:
            result.warning(log, f"RVMAT references a source texture format instead of .paa in {source_location}: {ref}")

        report_reference_status(ref, file_path, addon_source_dir, project_root, extra_patterns, result, log, "error", "RVMAT texture", line_number)


def preflight_scan_p3d_internal_references(p3d_file, addon_source_dir, project_root, extra_patterns, result, log):
    rel_file = os.path.relpath(p3d_file, addon_source_dir).replace(os.sep, WIN_SEP)

    try:
        with open(p3d_file, "rb") as file:
            data = file.read()
    except Exception as e:
        result.warning(log, f"Could not read P3D for internal reference scan: {rel_file} ({e})")
        return

    result.checked_files += 1
    seen = set()
    found = 0

    for match in P3D_INTERNAL_REFERENCE_REGEX.finditer(data):
        ref = normalize_reference_path(match.group(1).decode("ascii", errors="ignore").strip())
        key = ref.lower()

        if not ref or key in seen or len(ref) < 5:
            continue

        seen.add(key)
        found += 1
        report_reference_status(ref, p3d_file, addon_source_dir, project_root, extra_patterns, result, log, "warning", "internal P3D reference")

    if found:
        log(f"P3D internal scan checked {found} reference(s): {rel_file}")


def preflight_scan_case_conflicts(addon_source_dir, extra_patterns, result, log):
    seen = {}

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)
            rel = os.path.relpath(full, addon_source_dir).replace(os.sep, WIN_SEP)
            key = rel.lower()

            if key in seen and seen[key] != rel:
                result.warning(log, f"Case-only path conflict detected: {seen[key]} <-> {rel}")
            else:
                seen[key] = rel


def preflight_scan_texture_freshness(addon_source_dir, extra_patterns, result, log):
    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]
        file_map = {file.lower(): file for file in files}

        for file in files:
            ext = os.path.splitext(file)[1].lower()

            if ext not in SOURCE_TEXTURE_EXTENSIONS:
                continue

            source_texture = os.path.join(root, file)
            paa_name = os.path.splitext(file)[0] + ".paa"
            paa_file = file_map.get(paa_name.lower())
            rel_source = os.path.relpath(source_texture, addon_source_dir).replace(os.sep, WIN_SEP)

            if should_skip_file(file, extra_patterns):
                continue

            if not paa_file:
                result.warning(log, f"Source texture exists without matching .paa: {rel_source}")
                continue

            paa_path = os.path.join(root, paa_file)

            try:
                if os.path.getmtime(source_texture) > os.path.getmtime(paa_path):
                    rel_paa = os.path.relpath(paa_path, addon_source_dir).replace(os.sep, WIN_SEP)
                    result.warning(log, f"Source texture is newer than .paa: {rel_source} -> {rel_paa}")
            except OSError:
                pass


def preflight_scan_invalid_paths(addon_source_dir, extra_patterns, result, log):
    invalid_chars = set('<>"|?*')

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for name in list(dirs) + [file for file in files if not should_skip_file(file, extra_patterns)]:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, addon_source_dir).replace(os.sep, WIN_SEP)
            result.checked_paths += 1

            if any(ord(char) < 32 for char in name):
                result.warning(log, f"Path contains control characters: {rel}")

            if any(char in invalid_chars for char in name):
                result.warning(log, f"Path contains Windows-invalid characters: {rel}")

            if name != name.strip():
                result.warning(log, f"Path has leading/trailing whitespace: {rel}")

            try:
                rel.encode("ascii")
            except UnicodeEncodeError:
                result.warning(log, f"Path contains non-ASCII characters: {rel}")

            if len(os.path.abspath(full)) > 240:
                result.warning(log, f"Path is very long and may cause tool issues: {rel}")


def collect_wrp_files(addon_source_dir, extra_patterns=None):
    wrp_files = []

    for root, dirs, files in os.walk(addon_source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if file.lower().endswith(".wrp") and not should_skip_file(file, extra_patterns):
                wrp_files.append(os.path.join(root, file))

    wrp_files.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return wrp_files


def collect_navmesh_files(addon_source_dir, extra_patterns=None):
    navmesh_root = os.path.join(addon_source_dir, "navmesh")
    navmesh_files = []

    if not os.path.isdir(navmesh_root):
        return navmesh_root, navmesh_files

    # Do not filter here. The preflight caller needs to see excluded navmesh files too,
    # otherwise it cannot warn that navmesh data exists but will not be packed.
    for root, dirs, files in os.walk(navmesh_root):
        for file in files:
            navmesh_files.append(os.path.join(root, file))

    navmesh_files.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return navmesh_root, navmesh_files


def get_explicit_pbo_prefix(addon_source_dir):
    prefix_files = collect_pbo_prefix_files(addon_source_dir)

    if not prefix_files:
        return ""

    raw_prefix = read_raw_prefix_file(prefix_files[0])
    return normalize_reference_path(raw_prefix).strip(WIN_SEP)


def get_detected_pbo_prefix_for_preflight(addon_source_dir):
    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)

    if explicit_prefix:
        return explicit_prefix

    folder_name = os.path.basename(os.path.normpath(addon_source_dir)) or "addon"
    return normalize_reference_path(folder_name).strip(WIN_SEP)


def iter_config_file_contents(config_files, addon_source_dir="", project_root="", include_resolved=False):
    seen = set()
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.IGNORECASE | re.MULTILINE)

    def visit(config_cpp):
        try:
            path = Path(config_cpp).resolve(strict=False)
        except Exception:
            path = Path(config_cpp)

        key = os.path.normcase(str(path))

        if key in seen:
            return

        seen.add(key)

        try:
            raw_content = Path(config_cpp).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raw_content = ""

        content = strip_cpp_comments(raw_content, preserve_lines=True)
        yield config_cpp, content

        if not include_resolved or not content:
            return

        for match in include_pattern.finditer(content):
            include_path = resolve_config_include_path(match.group(1).strip(), config_cpp, addon_source_dir, project_root)

            if include_path:
                yield from visit(include_path)

    for config_cpp in config_files:
        yield from visit(config_cpp)


def find_worldname_references(config_files, addon_source_dir="", project_root=""):
    pattern = re.compile(r"\bworldName\s*=\s*[\"']([^\"']+\.wrp)[\"']\s*;", re.IGNORECASE)
    results = []

    for config_cpp, content in iter_config_file_contents(config_files, addon_source_dir, project_root, True):
        if not content:
            continue

        for match in pattern.finditer(content):
            results.append((config_cpp, match.group(1).strip(), get_line_number_from_index(content, match.start(1))))

    return results


def find_terrain_shape_references(config_files, addon_source_dir="", project_root=""):
    # Terrain configs commonly use newRoadsShape = "...roads.shp";
    # The broader regex catches explicit quoted shape references as well.
    shape_regex = re.compile(r"[\"']([^\"']+\.(?:shp|dbf|shx|prj))[\"']", re.IGNORECASE)
    results = []
    seen = set()

    for config_cpp, content in iter_config_file_contents(config_files, addon_source_dir, project_root, True):
        if not content:
            continue

        for match in shape_regex.finditer(content):
            ref = normalize_reference_path(match.group(1).strip())
            key = (os.path.normcase(os.path.abspath(config_cpp)), ref.lower())

            if key in seen:
                continue

            seen.add(key)
            results.append((config_cpp, ref, get_line_number_from_index(content, match.start(1))))

    return results


def check_shape_sidecars(shape_path, addon_source_dir, result, log):
    if not shape_path or not os.path.isfile(shape_path):
        return

    ext = os.path.splitext(shape_path)[1].lower()

    if ext != ".shp":
        return

    base = os.path.splitext(shape_path)[0]

    for sidecar_ext in [".dbf", ".shx"]:
        sidecar = base + sidecar_ext

        if not os.path.isfile(sidecar):
            try:
                rel_shape = os.path.relpath(shape_path, addon_source_dir).replace(os.sep, WIN_SEP)
            except Exception:
                rel_shape = shape_path
            result.warning(log, f"Road shape sidecar is missing for {rel_shape}: {os.path.basename(sidecar)}")


def config_contains_class(config_files, class_name, addon_source_dir="", project_root=""):
    for config_cpp in config_files:
        if config_file_has_class(config_cpp, class_name, addon_source_dir, project_root):
            return True

    return False


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

    return f"{size:.2f} {units[index]}"


def estimate_packed_source_size(source_dir, extra_patterns=None):
    total_size = 0
    total_files = 0

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)

            try:
                total_size += os.path.getsize(full)
                total_files += 1
            except OSError:
                continue

    return total_size, total_files


def collect_packed_size_by_top_folder(source_dir, extra_patterns=None):
    breakdown = {}
    root_files_key = "<root files>"

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        rel_root = os.path.relpath(root, source_dir)
        rel_parts = [] if rel_root == "." else list(Path(rel_root).parts)
        top_key = rel_parts[0] if rel_parts else root_files_key

        for file in files:
            if should_skip_file(file, extra_patterns):
                continue

            full = os.path.join(root, file)

            try:
                file_size = os.path.getsize(full)
            except OSError:
                continue

            entry = breakdown.setdefault(top_key, {"size": 0, "files": 0})
            entry["size"] += file_size
            entry["files"] += 1

    result = []

    for folder_name, info in breakdown.items():
        result.append((folder_name, info["size"], info["files"]))

    result.sort(key=lambda item: item[1], reverse=True)
    return result


def is_terrain_source_like_path(file_path, addon_source_dir):
    try:
        rel_path = os.path.relpath(file_path, addon_source_dir)
    except Exception:
        rel_path = file_path

    rel_parts = [part.lower() for part in Path(rel_path).parts]
    file_name = os.path.basename(file_path).lower()
    file_stem = os.path.splitext(file_name)[0].lower()
    ext = os.path.splitext(file_name)[1].lower()

    if any(part in TERRAIN_SOURCE_FOLDER_NAMES for part in rel_parts[:-1]):
        return True, "inside terrain source/export folder"

    if ext in TERRAIN_ALWAYS_SOURCE_EXPORT_EXTENSIONS:
        return True, "terrain source/export file type"

    if ext in TERRAIN_SOURCE_IMAGE_EXTENSIONS:
        for keyword in TERRAIN_SOURCE_IMAGE_KEYWORDS:
            if keyword in file_stem:
                return True, "terrain source image name"

    return False, ""


def find_terrain_source_roots(addon_source_dir):
    roots = []

    for root, dirs, files in os.walk(addon_source_dir):
        rel_root = os.path.relpath(root, addon_source_dir)
        depth = 0 if rel_root == "." else len(Path(rel_root).parts)

        # Keep this shallow. Source/export folders deeper in normal asset folders are less likely
        # to be full Terrain Builder source roots and can create noisy warnings.
        if depth > 2:
            dirs[:] = []
            continue

        for directory in dirs:
            if directory.lower() in TERRAIN_SOURCE_FOLDER_NAMES:
                roots.append(os.path.join(root, directory))

    roots.sort(key=lambda path: os.path.relpath(path, addon_source_dir).lower())
    return roots


def collect_terrain_source_export_files(addon_source_dir, max_examples=20):
    matches = []
    total = 0
    total_size = 0

    for root, dirs, files in os.walk(addon_source_dir):
        for file in files:
            full = os.path.join(root, file)
            is_source_like, reason = is_terrain_source_like_path(full, addon_source_dir)

            if not is_source_like:
                continue

            try:
                file_size = os.path.getsize(full)
            except OSError:
                file_size = 0

            total += 1
            total_size += file_size
            matches.append((full, file_size, reason))

    matches.sort(key=lambda item: (item[1], os.path.relpath(item[0], addon_source_dir).lower()), reverse=True)

    if max_examples and len(matches) > max_examples:
        return matches[:max_examples], total, total_size

    return matches, total, total_size


def find_terrain_layer_dirs(addon_source_dir, extra_patterns=None):
    candidates = [
        os.path.join(addon_source_dir, "data", "layers"),
        os.path.join(addon_source_dir, "layers"),
    ]
    result = []
    seen = set()

    def add_candidate(candidate):
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            return
        seen.add(key)
        if os.path.isdir(candidate):
            result.append(candidate)

    for candidate in candidates:
        add_candidate(candidate)

    for root, dirs, files in os.walk(addon_source_dir):
        rel_root = os.path.relpath(root, addon_source_dir)
        depth = 0 if rel_root == "." else len(Path(rel_root).parts)

        if depth > 3:
            dirs[:] = []
            continue

        dirs[:] = [
            directory for directory in dirs
            if not should_skip_dir(directory, extra_patterns) and directory.lower() not in TERRAIN_SOURCE_FOLDER_NAMES
        ]

        for directory in dirs:
            if directory.lower() == "layers":
                add_candidate(os.path.join(root, directory))

    return result


def detect_modular_terrain_layout(addon_source_dir):
    signals = set()
    addon_name = os.path.basename(os.path.normpath(addon_source_dir)).lower()

    if addon_name in MODULAR_TERRAIN_FOLDER_NAMES:
        signals.add(addon_name)

    try:
        for entry in os.listdir(addon_source_dir):
            full = os.path.join(addon_source_dir, entry)

            if os.path.isdir(full) and entry.lower() in MODULAR_TERRAIN_FOLDER_NAMES:
                signals.add(entry.lower())
    except OSError:
        pass

    prefix = get_detected_pbo_prefix_for_preflight(addon_source_dir)

    for part in normalize_reference_path(prefix).split(WIN_SEP):
        if part.lower() in MODULAR_TERRAIN_FOLDER_NAMES:
            signals.add(part.lower())

    return len(signals) >= 2 or addon_name in MODULAR_TERRAIN_FOLDER_NAMES


def collect_rvmat_files(folder):
    rvmats = []

    if not os.path.isdir(folder):
        return rvmats

    for root, dirs, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".rvmat"):
                rvmats.append(os.path.join(root, file))

    rvmats.sort(key=lambda path: os.path.relpath(path, folder).lower())
    return rvmats


def preflight_check_terrain_structure(addon_name, addon_source_dir, wrp_files, extra_patterns, result, log):
    data_dir = os.path.join(addon_source_dir, "data")
    world_dir = os.path.join(addon_source_dir, "world")
    modular_layout = detect_modular_terrain_layout(addon_source_dir)

    result.note(log, "Terrain-style folder layout detected. CE/server mission files are not validated by RaG PBO Builder.")

    if modular_layout:
        result.note(log, f"Modular terrain PBO layout detected. Classic world\\data\\layers layout warnings are relaxed for: {addon_name}")

    if not os.path.isdir(data_dir):
        result.note(log, f"Terrain/WRP addon has no local data folder. This is common in modular map PBO layouts: {addon_name}")

    if not os.path.isdir(world_dir):
        result.note(log, f"Terrain/WRP addon has no local world folder. This is common in modular map PBO layouts: {addon_name}")

    for wrp_file in wrp_files:
        rel_wrp = os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP)
        first_part = rel_wrp.split(WIN_SEP)[0].lower() if rel_wrp else ""

        if first_part not in {"world", "data"}:
            result.note(log, f"WRP is outside the classic world/data sample layout. This can be valid for modular maps: {rel_wrp}")

    for source_root in find_terrain_source_roots(addon_source_dir):
        rel_source = os.path.relpath(source_root, addon_source_dir).replace(os.sep, WIN_SEP)

        if not path_would_be_excluded(rel_source, extra_patterns):
            result.warning(log, f"Terrain source/export folder is not excluded and may be packed: {rel_source}")

    source_examples, source_total, source_total_size = collect_terrain_source_export_files(addon_source_dir)
    shown = 0
    unexcluded_total = 0
    unexcluded_size = 0

    for source_file, file_size, reason in source_examples:
        rel_source_file = os.path.relpath(source_file, addon_source_dir).replace(os.sep, WIN_SEP)

        if path_would_be_excluded(rel_source_file, extra_patterns):
            continue

        unexcluded_total += 1
        unexcluded_size += file_size
        shown += 1

        if file_size >= TERRAIN_LARGE_SOURCE_FILE_BYTES:
            result.warning(log, f"Large terrain source/export file may be packed ({format_byte_size(file_size)}, {reason}). Check exclude patterns: {rel_source_file}")
        else:
            result.warning(log, f"Terrain source/export file may be packed ({format_byte_size(file_size)}, {reason}). Check exclude patterns: {rel_source_file}")

    if source_total > len(source_examples):
        result.note(log, f"Additional terrain source/export-looking files were found but not listed individually: {source_total - len(source_examples)}")

    if unexcluded_total > shown:
        result.note(log, f"Additional unexcluded terrain source/export-looking files were found but not listed individually: {unexcluded_total - shown}")

    if unexcluded_size > 0 and unexcluded_total > 1:
        result.warning(log, f"Unexcluded terrain source/export-looking files may add {format_byte_size(unexcluded_size)} to the packed PBO: {addon_name}")


def preflight_check_terrain_layers(addon_name, addon_source_dir, project_root, extra_patterns, result, log):
    layer_dirs = find_terrain_layer_dirs(addon_source_dir, extra_patterns)
    detected_prefix = get_detected_pbo_prefix_for_preflight(addon_source_dir)

    if not layer_dirs:
        result.note(log, f"Terrain layers folder was not found in common shallow locations. This is valid for modular maps that keep layers in another PBO or omit layer RVMATs from this addon: {addon_name}")
        return

    for layer_dir in layer_dirs:
        rel_layer_dir = os.path.relpath(layer_dir, addon_source_dir).replace(os.sep, WIN_SEP)
        rvmat_files = collect_rvmat_files(layer_dir)

        if not rvmat_files:
            result.warning(log, f"Terrain layers folder contains no .rvmat files: {rel_layer_dir}")
            continue

        result.note(log, f"Terrain layers folder detected with {len(rvmat_files)} .rvmat file(s): {rel_layer_dir}")

        for rvmat_file in rvmat_files:
            try:
                content = Path(rvmat_file).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                result.warning(log, f"Could not read terrain layer RVMAT: {rvmat_file} ({e})")
                continue

            rel_rvmat = os.path.relpath(rvmat_file, addon_source_dir).replace(os.sep, WIN_SEP)

            for match in RVMAT_TEXTURE_REGEX.finditer(content):
                ref = normalize_reference_path(match.group(1).strip())
                ref_lower = ref.lower()
                line_number = get_line_number_from_index(content, match.start(1))

                if detected_prefix and not ref_lower.startswith(detected_prefix.lower() + WIN_SEP) and not ref_lower.startswith("dz" + WIN_SEP):
                    source_location = format_source_location(rvmat_file, addon_source_dir, line_number)
                    result.warning(log, f"Terrain layer RVMAT references a texture outside the detected prefix and outside DZ in {source_location}: {ref}")

                # Missing/excluded texture checks are intentionally delegated to the normal
                # RVMAT scan later to avoid duplicate terrain-specific errors.


def find_terrain_2d_map_references(config_files):
    results = []
    seen = set()

    for config_cpp, content in iter_config_file_contents(config_files):
        if not content:
            continue

        for match in TERRAIN_2D_MAP_REFERENCE_REGEX.finditer(content):
            ref = normalize_reference_path(match.group(1).strip())
            key = (os.path.normcase(os.path.abspath(config_cpp)), ref.lower())

            if key in seen:
                continue

            seen.add(key)
            results.append((config_cpp, ref, get_line_number_from_index(content, match.start(1))))

    return results


def preflight_check_terrain_2d_map_config(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log):
    if not config_files:
        return

    map_refs = find_terrain_2d_map_references(config_files)

    if not map_refs:
        result.warning(log, f"Terrain/WRP addon has no obvious 2D map image config reference. This can be valid if the map item/UI is handled elsewhere: {addon_name}")
        return

    result.note(log, f"Detected {len(map_refs)} possible 2D map image reference(s): {addon_name}")

    for config_cpp, map_ref, line_number in map_refs:
        # The normal reference scanner also checks these files. This terrain-specific pass
        # keeps the message contextual and warning-only to avoid blocking unusual map setups.
        resolved, status = resolve_reference_path(map_ref, addon_source_dir, project_root)
        source_location = format_source_location(config_cpp, addon_source_dir, line_number)

        if status == "missing":
            result.warning(log, f"Missing possible 2D map image reference in {source_location}: {map_ref}")
            continue

        if is_path_inside(resolved, addon_source_dir):
            rel_resolved = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)

            if path_would_be_excluded(rel_resolved, extra_patterns):
                result.warning(log, f"Possible 2D map image exists but is excluded from the packed PBO in {source_location}: {map_ref} -> {rel_resolved}")


def preflight_check_terrain_size(addon_name, addon_source_dir, extra_patterns, result, log):
    total_size, total_files = estimate_packed_source_size(addon_source_dir, extra_patterns)
    result.note(log, f"Estimated packed terrain source size before PBO overhead: {format_byte_size(total_size)} / {total_files} file(s)")

    breakdown = collect_packed_size_by_top_folder(addon_source_dir, extra_patterns)

    if breakdown:
        result.note(log, "Terrain size breakdown by top-level folder/file group:")
        shown = 0
        other_size = 0
        other_files = 0

        for folder_name, folder_size, folder_files in breakdown:
            if shown < 8:
                marker = ""

                if folder_name.lower() in TERRAIN_SOURCE_FOLDER_NAMES:
                    marker = " WARNING: source/export folder is being packed"

                result.note(log, f"  {folder_name}: {format_byte_size(folder_size)} / {folder_files} file(s){marker}")
                shown += 1
            else:
                other_size += folder_size
                other_files += folder_files

        if other_files:
            result.note(log, f"  <other>: {format_byte_size(other_size)} / {other_files} file(s)")

        for folder_name, folder_size, folder_files in breakdown:
            if folder_name.lower() in TERRAIN_SOURCE_FOLDER_NAMES and folder_size > 0:
                result.warning(log, f"Terrain source/export top-level folder is included in estimated packed files: {folder_name} ({format_byte_size(folder_size)} / {folder_files} file(s))")

    if total_size >= TERRAIN_SIZE_HIGH_WARNING_BYTES:
        result.warning(log, f"Terrain addon is very large ({format_byte_size(total_size)}). Check that source/export folders are excluded: {addon_name}")
    elif total_size >= TERRAIN_SIZE_WARNING_BYTES:
        result.warning(log, f"Terrain addon is large ({format_byte_size(total_size)}). Check that only runtime files are being packed: {addon_name}")


def preflight_check_terrain_wrp(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log, checks):
    wrp_files = collect_wrp_files(addon_source_dir, extra_patterns)

    if not wrp_files:
        return

    result.checked_terrain += 1
    result.note(log, f"Terrain/WRP addon detected: {addon_name}")
    result.note(log, "Terrain PBO detected. Server mission/world selection setup is outside the PBO and is not validated here.")

    wrp_rel_paths = [os.path.relpath(path, addon_source_dir).replace(os.sep, WIN_SEP) for path in wrp_files]

    if len(wrp_files) > 1:
        result.warning(log, f"Multiple WRP files found in terrain addon {addon_name}: {', '.join(wrp_rel_paths)}")
    else:
        result.note(log, f"Detected WRP: {wrp_rel_paths[0]}")

    if checks.get("terrain_structure", True):
        preflight_check_terrain_structure(addon_name, addon_source_dir, wrp_files, extra_patterns, result, log)
    else:
        result.note(log, "Terrain folder/source structure check disabled.")

    if checks.get("terrain_size", True):
        preflight_check_terrain_size(addon_name, addon_source_dir, extra_patterns, result, log)
    else:
        result.note(log, "Terrain size/source warning check disabled.")

    explicit_prefix = get_explicit_pbo_prefix(addon_source_dir)
    detected_prefix = get_detected_pbo_prefix_for_preflight(addon_source_dir)

    if checks.get("terrain_cfgworlds", True):
        if not explicit_prefix:
            result.warning(log, f"Terrain/WRP addon has no explicit PBO prefix file. For maps, a $PBOPREFIX$ file is strongly recommended: {addon_name}")

        if not config_files:
            result.error(log, f"WRP found but no config.cpp exists in terrain addon: {addon_name}")
        else:
            has_cfgworlds = config_contains_class(config_files, "CfgWorlds", addon_source_dir, project_root)
            has_cfgworldlist = config_contains_class(config_files, "CfgWorldList", addon_source_dir, project_root) or config_contains_class(config_files, "CfgWorldsList", addon_source_dir, project_root)

            if not has_cfgworlds:
                result.error(log, f"WRP found but no CfgWorlds class found in addon configs: {addon_name}")

            if not has_cfgworldlist:
                result.warning(log, f"WRP found but no CfgWorldList class found in addon configs: {addon_name}")

            worldname_refs = find_worldname_references(config_files, addon_source_dir, project_root)

            if not worldname_refs:
                result.warning(log, f"WRP found but no worldName .wrp path was found in addon configs: {addon_name}")
            else:
                if len(wrp_files) > 1 and len(worldname_refs) == 1:
                    result.warning(log, f"Multiple WRP files were found, but only one worldName entry was detected. Remove old/test WRP files or verify the intended terrain WRP: {addon_name}")

                if len(worldname_refs) > 1:
                    result.warning(log, f"Multiple worldName .wrp entries were detected in terrain configs. Verify only the intended terrain world is active: {addon_name}")

                detected_wrp_keys = {os.path.normcase(os.path.abspath(path)) for path in wrp_files}
                resolved_worldname_keys = set()

                for config_cpp, world_ref, line_number in worldname_refs:
                    source_location = format_source_location(config_cpp, addon_source_dir, line_number)
                    normalized_world_ref = normalize_reference_path(world_ref)
                    report_reference_status(normalized_world_ref, config_cpp, addon_source_dir, project_root, extra_patterns, result, log, "error", "worldName WRP", line_number)
                    resolved, status = resolve_reference_path(normalized_world_ref, addon_source_dir, project_root)

                    if status == "ok":
                        resolved_key = os.path.normcase(os.path.abspath(resolved))
                        resolved_worldname_keys.add(resolved_key)

                        if resolved_key not in detected_wrp_keys:
                            try:
                                resolved_rel = os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)
                            except Exception:
                                resolved_rel = resolved
                            result.warning(log, f"worldName in {source_location} points to a WRP that differs from detected addon WRP files: {resolved_rel}")

                    if detected_prefix and not normalized_world_ref.lower().startswith(detected_prefix.lower() + WIN_SEP):
                        result.warning(log, f"worldName path does not start with detected PBO prefix in {source_location}: prefix '{detected_prefix}', worldName '{normalized_world_ref}'")

                unused_wrp_paths = []

                for wrp_file in wrp_files:
                    wrp_key = os.path.normcase(os.path.abspath(wrp_file))

                    if wrp_key not in resolved_worldname_keys:
                        unused_wrp_paths.append(os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP))

                if resolved_worldname_keys and unused_wrp_paths:
                    result.warning(log, f"WRP file(s) are present but not referenced by worldName. Check for stale terrain exports: {', '.join(unused_wrp_paths)}")

        if checks.get("terrain_2d_map", False):
            preflight_check_terrain_2d_map_config(addon_name, addon_source_dir, config_files, project_root, extra_patterns, result, log)
        else:
            result.note(log, "2D map image config check disabled.")

    if checks.get("terrain_layers", True):
        preflight_check_terrain_layers(addon_name, addon_source_dir, project_root, extra_patterns, result, log)
    else:
        result.note(log, "Terrain layer/RVMAT check disabled.")

    if checks.get("terrain_navmesh", False):
        navmesh_root, navmesh_files = collect_navmesh_files(addon_source_dir, extra_patterns)

        if not os.path.isdir(navmesh_root):
            result.warning(log, f"Terrain/WRP addon has no navmesh folder. This can be valid for early tests, but released maps usually need navmesh data: {addon_name}")
        else:
            result.note(log, f"Navmesh folder detected: {os.path.relpath(navmesh_root, addon_source_dir).replace(os.sep, WIN_SEP)}")

            if not navmesh_files:
                result.warning(log, f"Navmesh folder exists but contains no files: {addon_name}")

            excluded_navmesh_count = 0
            packed_navmesh_count = 0

            for navmesh_file in navmesh_files:
                rel_navmesh = os.path.relpath(navmesh_file, addon_source_dir).replace(os.sep, WIN_SEP)

                if path_would_be_excluded(rel_navmesh, extra_patterns):
                    excluded_navmesh_count += 1
                    result.warning(log, f"Navmesh file exists but is excluded from the packed PBO: {rel_navmesh}")
                else:
                    packed_navmesh_count += 1

            if navmesh_files and packed_navmesh_count == 0:
                result.warning(log, f"Navmesh folder contains files, but all navmesh files appear to be excluded from the packed PBO: {addon_name}")

    if checks.get("terrain_road_shapes", True):
        shape_refs = find_terrain_shape_references(config_files, addon_source_dir, project_root)

        if shape_refs:
            for config_cpp, shape_ref, line_number in shape_refs:
                report_reference_status(shape_ref, config_cpp, addon_source_dir, project_root, extra_patterns, result, log, "error", "terrain road/shape reference", line_number)
                resolved, status = resolve_reference_path(shape_ref, addon_source_dir, project_root)

                if status == "ok":
                    check_shape_sidecars(resolved, addon_source_dir, result, log)
        else:
            result.note(log, f"No terrain road/shape references found in addon configs: {addon_name}")

    if checks.get("wrp_internal", False):
        for wrp_file in wrp_files:
            preflight_scan_wrp_internal_references(wrp_file, addon_source_dir, project_root, extra_patterns, result, log)
    else:
        result.note(log, "WRP internal reference scan disabled.")


def preflight_scan_wrp_internal_references(wrp_file, addon_source_dir, project_root, extra_patterns, result, log):
    rel_file = os.path.relpath(wrp_file, addon_source_dir).replace(os.sep, WIN_SEP)

    try:
        with open(wrp_file, "rb") as file:
            data = file.read()
    except Exception as e:
        result.warning(log, f"Could not read WRP for internal reference scan: {rel_file} ({e})")
        return

    result.checked_files += 1
    seen = set()
    found = 0

    for match in TERRAIN_WRP_INTERNAL_REFERENCE_REGEX.finditer(data):
        try:
            ref = normalize_reference_path(match.group(1).decode("ascii", errors="ignore").strip())
        except Exception:
            continue

        key = ref.lower()

        if not ref or key in seen or len(ref) < 5:
            continue

        seen.add(key)
        found += 1
        report_reference_status(ref, wrp_file, addon_source_dir, project_root, extra_patterns, result, log, "warning", "possible internal WRP reference")

    if found:
        log(f"WRP internal scan checked {found} possible reference(s): {rel_file}")


def get_preflight_check_settings(settings):
    return {
        "required_addons_hints": bool(settings.get("preflight_check_required_addons_hints", True)),
        "texture_freshness": bool(settings.get("preflight_check_texture_freshness", True)),
        "risky_paths": bool(settings.get("preflight_check_risky_paths", True)),
        "case_conflicts": bool(settings.get("preflight_check_case_conflicts", True)),
        "script_checks": bool(settings.get("preflight_check_script_checks", True)),
        "p3d_internal": bool(settings.get("preflight_check_p3d_internal", True)),
        "terrain_cfgworlds": bool(settings.get("preflight_check_terrain_cfgworlds", True)),
        "terrain_navmesh": bool(settings.get("preflight_check_terrain_navmesh", False)),
        "terrain_road_shapes": bool(settings.get("preflight_check_terrain_road_shapes", True)),
        "terrain_structure": bool(settings.get("preflight_check_terrain_structure", True)),
        "terrain_layers": bool(settings.get("preflight_check_terrain_layers", True)),
        "terrain_2d_map": bool(settings.get("preflight_check_terrain_2d_map", False)),
        "terrain_size": bool(settings.get("preflight_check_terrain_size", True)),
        "wrp_internal": bool(settings.get("preflight_check_wrp_internal", False)),
    }


def get_preflight_report_paths(log_file):
    if not log_file:
        return "", ""

    base = Path(log_file)
    return str(base.with_name(base.stem + "_preflight_report.txt")), str(base.with_name(base.stem + "_preflight_report.json"))


def export_preflight_report(settings, targets, result, elapsed, log):
    txt_path, json_path = get_preflight_report_paths(settings.get("log_file", ""))

    if not txt_path or not json_path:
        return

    enabled_checks = get_preflight_check_settings(settings)
    report_data = {
        "app": APP_TITLE,
        "version": APP_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "targets": [
            {
                "name": name,
                "path": path,
            }
            for name, path in targets
        ],
        "enabled_checks": enabled_checks,
        "summary": {
            "addons": len(targets),
            "checked_files": result.checked_files,
            "checked_references": result.checked_references,
            "checked_configs": result.checked_configs,
            "script_modules": result.checked_script_modules,
            "checked_paths": result.checked_paths,
            "checked_terrain": result.checked_terrain,
            "errors": result.errors,
            "warnings": result.warnings,
            "info": result.info,
            "time": format_duration(elapsed),
        },
        "events": result.events,
    }

    lines = []
    lines.append(f"{APP_TITLE} Preflight Report")
    lines.append(f"Version: {APP_VERSION}")
    lines.append(f"Created: {report_data['created']}")
    lines.append("")
    lines.append("Targets:")
    for target in report_data["targets"]:
        lines.append(f"- {target['name']}: {target['path']}")
    lines.append("")
    lines.append("Enabled checks:")
    for check_name, enabled in enabled_checks.items():
        lines.append(f"- {check_name}: {'enabled' if enabled else 'disabled'}")
    lines.append("")
    lines.append("Summary:")
    for key, value in report_data["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("Issues / notes:")
    if result.events:
        for event in result.events:
            lines.append(f"[{event['severity']}] {event['message']}")
    else:
        lines.append("No errors, warnings, or info notes were recorded.")
    lines.append("")

    try:
        Path(txt_path).write_text(chr(10).join(lines), encoding="utf-8")
        Path(json_path).write_text(json.dumps(report_data, indent=4), encoding="utf-8")
        result.report_txt = txt_path
        result.report_json = json_path
        log(f"Preflight report saved: {txt_path}")
        log(f"Preflight report JSON saved: {json_path}")
    except Exception as e:
        result.warning(log, f"Could not export preflight report: {e}")


def run_preflight_for_targets(settings, targets, log, progress_callback=None):
    start = time.time()
    result = PreflightResult()
    project_root = settings.get("project_root", DEFAULT_PROJECT_ROOT)
    extra_patterns = parse_exclude_patterns(settings.get("exclude_patterns", ""))
    preflight_checks = get_preflight_check_settings(settings)

    log("")
    log("=" * 80)
    log("DayZ Preflight Check")
    log("=" * 80)

    for index, (addon_name, addon_source_dir) in enumerate(targets, start=1):
        if progress_callback:
            progress_callback(index - 1, len(targets))

        log("")
        log(f"Checking addon {index}/{len(targets)}: {addon_name}")

        preflight_check_prefix(addon_name, addon_source_dir, result, log)

        if preflight_checks["case_conflicts"]:
            preflight_scan_case_conflicts(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Case-only path conflict check disabled.")

        if preflight_checks["risky_paths"]:
            preflight_scan_invalid_paths(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Risky filename/path check disabled.")

        if preflight_checks["texture_freshness"]:
            preflight_scan_texture_freshness(addon_source_dir, extra_patterns, result, log)
        else:
            result.note(log, "Texture freshness check disabled.")

        configs = collect_config_cpp_files(addon_source_dir, extra_patterns)
        if configs:
            log(f"Found {len(configs)} config.cpp file(s).")

            for config_cpp in configs:
                preflight_check_config_cpp(config_cpp, settings.get("cfgconvert_exe", ""), settings.get("temp_dir", DEFAULT_TEMP_DIR), addon_name, result, log, addon_source_dir)

            cfgpatches_configs = select_cfgpatches_check_configs(configs, addon_source_dir, project_root)

            if cfgpatches_configs:
                for config_cpp in cfgpatches_configs:
                    preflight_check_cfgpatches(config_cpp, addon_source_dir, result, log, preflight_checks["required_addons_hints"], project_root)
            else:
                result.error(log, f"No CfgPatches class found in addon configs: {addon_name}")

            # DayZ only needs one CfgMods class for script module registration.
            # Prefer the config/include graph with the strongest CfgMods body instead of
            # stopping at a weaker nested config fragment.
            cfgmods_analysis = find_best_cfgmods_config(configs, addon_source_dir, project_root)
            cfgmods_check_cpp = (cfgmods_analysis or {}).get("config_cpp") or get_root_config_cpp(addon_source_dir)

            if not os.path.isfile(cfgmods_check_cpp):
                cfgmods_check_cpp = configs[0]

            preflight_check_cfgmods(cfgmods_check_cpp, addon_name, addon_source_dir, project_root, result, log, cfgmods_analysis)
        else:
            result.warning(log, f"No config.cpp found in addon source: {addon_source_dir}")

        preflight_check_terrain_wrp(
            addon_name,
            addon_source_dir,
            configs,
            project_root,
            extra_patterns,
            result,
            log,
            preflight_checks,
        )

        if not preflight_checks["p3d_internal"]:
            result.note(log, "P3D internal reference scan disabled.")

        script_class_definitions = []

        for root, dirs, files in os.walk(addon_source_dir):
            dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

            for file in files:
                if should_skip_file(file, extra_patterns):
                    continue

                full = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()

                if ext in PREFLIGHT_TEXT_EXTENSIONS:
                    preflight_scan_references(full, addon_source_dir, project_root, extra_patterns, result, log, script_class_definitions, preflight_checks["script_checks"])
                elif ext == ".p3d" and preflight_checks["p3d_internal"]:
                    preflight_scan_p3d_internal_references(full, addon_source_dir, project_root, extra_patterns, result, log)

        if preflight_checks["script_checks"]:
            preflight_scan_duplicate_script_classes(addon_name, script_class_definitions, result, log)
        else:
            result.note(log, "Script checks disabled.")

    if progress_callback:
        progress_callback(len(targets), len(targets))

    elapsed = time.time() - start
    log("")
    log("=" * 80)
    log("Preflight summary")
    log("=" * 80)
    log(f"Addons:             {len(targets)}")
    log(f"Scanned files:      {result.checked_files}")
    log(f"Checked references: {result.checked_references}")
    log(f"Checked configs:    {result.checked_configs}")
    log(f"Script modules:     {result.checked_script_modules}")
    log(f"Checked paths:      {result.checked_paths}")
    log(f"Terrain checks:     {result.checked_terrain}")
    log(f"Errors:             {result.errors}")
    log(f"Warnings:           {result.warnings}")
    log(f"Info:               {result.info}")
    log(f"Time:               {format_duration(elapsed)}")
    log("=" * 80)

    export_preflight_report(settings, targets, result, elapsed, log)

    return result
