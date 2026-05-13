import math
import re
import struct
from pathlib import Path

from pbo_core import format_byte_size, get_pbo_entry_unpacked_size

P3D_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{3,}")
P3D_RESOURCE_EXTENSIONS = {
    ".paa",
    ".pac",
    ".rvmat",
    ".p3d",
    ".rtm",
    ".cfg",
    ".cpp",
    ".bin",
    ".skeleton",
}
P3D_LOD_MARKERS = {
    "cargo",
    "edit",
    "fire",
    "geometry",
    "geom",
    "gunner",
    "hit",
    "hitpoints",
    "landcontact",
    "lod",
    "memory",
    "pilot",
    "phys",
    "physx",
    "roadway",
    "shadow",
    "view",
    "viewcargo",
    "viewgeometry",
    "viewgunner",
    "viewpilot",
}
P3D_LOD_CATEGORY_ORDER = [
    "Visual / resolution LODs",
    "View / crew LODs",
    "Geometry / collision LODs",
    "Fire / hit LODs",
    "Memory LODs",
    "Shadow LODs",
    "Other functional LODs",
    "Unknown LODs",
]
P3D_RESOLUTION_LABELS = {
    0x447A0000: ("View / crew LODs", "View Gunner"),
    0x44898000: ("View / crew LODs", "View Pilot"),
    0x44960000: ("View / crew LODs", "View Cargo"),
    0x461C4000: ("Shadow LODs", "Shadow Volume"),
    0x461C6800: ("Shadow LODs", "Shadow Volume 2"),
    0x462BE000: ("Shadow LODs", "Stencil Shadow"),
    0x462C0800: ("Shadow LODs", "Stencil Shadow 2"),
    0x551184E7: ("Geometry / collision LODs", "Geometry"),
    0x58635FA9: ("Memory LODs", "Memory"),
    0x58E35FA9: ("Geometry / collision LODs", "Land Contact"),
    0x592A87BF: ("Geometry / collision LODs", "Roadway"),
    0x59635FA9: ("Other functional LODs", "Paths"),
    0x598E1BCA: ("Fire / hit LODs", "HitPoints"),
    0x59AA87BF: ("Geometry / collision LODs", "View Geometry"),
    0x59C6F3B4: ("Fire / hit LODs", "Fire Geometry"),
    0x59E35FA9: ("Geometry / collision LODs", "View Cargo Geometry"),
    0x59FFCB9E: ("Fire / hit LODs", "View Cargo Fire Geometry"),
    0x5A0E1BCA: ("View / crew LODs", "View Commander"),
    0x5A1C51C4: ("Geometry / collision LODs", "View Commander Geometry"),
    0x5A2A87BF: ("Fire / hit LODs", "View Commander Fire Geometry"),
    0x5A38BDB9: ("Geometry / collision LODs", "View Pilot Geometry"),
    0x5A46F3B4: ("Fire / hit LODs", "View Pilot Fire Geometry"),
    0x5A5529AF: ("Geometry / collision LODs", "View Gunner Geometry"),
    0x5A635FA9: ("Fire / hit LODs", "View Gunner Fire Geometry"),
    0x5A7195A4: ("Other functional LODs", "Sub Parts"),
    0x5A7FCB9E: ("Shadow LODs", "Shadow Volume View Cargo"),
    0x5A8700CC: ("Shadow LODs", "Shadow Volume View Pilot"),
    0x5A8E1BCA: ("Shadow LODs", "Shadow Volume View Gunner"),
    0x5A9536C7: ("Other functional LODs", "Wreck"),
}

def clean_p3d_string(value):
    value = value.strip().strip("\x00")

    if not value or not any(char.isalnum() for char in value):
        return ""

    if len(value) > 260:
        return ""

    if len(set(value)) <= 2 and len(value) > 12:
        return ""

    return value


def extract_p3d_printable_strings(data, limit=20000):
    strings = []
    seen = set()

    for match in P3D_PRINTABLE_RE.finditer(data):
        value = clean_p3d_string(match.group(0).decode("ascii", errors="replace"))
        key = value.lower()

        if not value or key in seen:
            continue

        strings.append(value)
        seen.add(key)

        if len(strings) >= limit:
            break

    return strings


def get_p3d_format_info(data):
    magic = data[:4].decode("ascii", errors="replace") if len(data) >= 4 else ""
    version = None

    if len(data) >= 8:
        version = struct.unpack("<I", data[4:8])[0]

    if magic == "ODOL":
        return "ODOL (binarized P3D)", version

    if magic == "MLOD":
        return "MLOD (editable P3D)", version

    return f"Unknown ({magic or 'no magic'})", version


def get_p3d_header_lod_count(data):
    if len(data) < 12:
        return None

    try:
        count = struct.unpack("<I", data[8:12])[0]
    except Exception:
        return None

    if 0 < count <= 512:
        return count

    return None


def classify_p3d_resolution_lod(raw_value, float_value):
    if raw_value in P3D_RESOLUTION_LABELS:
        return P3D_RESOLUTION_LABELS[raw_value]

    if not math.isfinite(float_value):
        return "Unknown LODs", "Unknown/non-finite resolution"

    if 0 <= float_value < 1000:
        return "Visual / resolution LODs", f"Resolution {float_value:g}"

    if 1000 <= float_value < 10000:
        return "View / crew LODs", f"Unknown view/crew resolution {float_value:g}"

    if 10000 <= float_value < 1.0e13:
        return "Shadow LODs", f"Unknown shadow/functional resolution {float_value:g}"

    return "Other functional LODs", f"Unknown functional resolution {float_value:g}"


def extract_p3d_resolution_lods(data):
    if data[:4] != b"ODOL":
        return []

    lod_count = get_p3d_header_lod_count(data)

    if not lod_count:
        return []

    start = 12
    end = start + lod_count * 4

    if end > len(data):
        return []

    lods = []

    for index in range(lod_count):
        raw_bytes = data[start + index * 4:start + index * 4 + 4]
        raw_value = struct.unpack("<I", raw_bytes)[0]
        float_value = struct.unpack("<f", raw_bytes)[0]
        category, label = classify_p3d_resolution_lod(raw_value, float_value)
        lods.append({
            "index": index,
            "raw": raw_value,
            "value": float_value,
            "category": category,
            "label": label,
        })

    return lods


def categorize_p3d_resolution_lods(lods):
    categorized = {category: [] for category in P3D_LOD_CATEGORY_ORDER}

    for lod in lods:
        categorized.setdefault(lod["category"], []).append(lod)

    return {category: values for category, values in categorized.items() if values}


def format_p3d_lod_value(value):
    if abs(value) >= 1.0e6 or (value and abs(value) < 0.001):
        return f"{value:.6g}"

    return f"{value:g}"


def has_path_extension(value, extensions):
    lower = value.lower()
    return any(extension in lower for extension in extensions)


def filter_resource_strings(strings, extensions):
    result = []

    for value in strings:
        if has_path_extension(value, extensions):
            result.append(value)

    return sorted(result, key=lambda item: item.lower())


def filter_marker_strings(strings, markers):
    result = []

    for value in strings:
        if has_path_extension(value, P3D_RESOURCE_EXTENSIONS) or "\\" in value or "/" in value:
            continue

        normalized = re.sub(r"[^a-z0-9]+", "", value.lower())

        if normalized in markers:
            result.append(value)

    return sorted(result, key=lambda item: item.lower())


def append_lod_category_sections(lines, categorized_lods):
    lines.append("")
    lines.append("LOD table from ODOL resolution array")
    lines.append("------------------------------------")

    if not categorized_lods:
        lines.append("No safe ODOL resolution table was detected.")
        return

    for label, values in categorized_lods.items():
        lines.append("")
        lines.append(label)

        for value in values[:80]:
            if isinstance(value, dict):
                lines.append(f"- LOD {value['index']}: {value['label']} | value={format_p3d_lod_value(value['value'])} | raw=0x{value['raw']:08X}")
            else:
                lines.append(f"- {value}")

        if len(values) > 80:
            lines.append(f"- ... {len(values) - 80} more")


def append_limited_section(lines, title, values, empty_text="None found.", limit=80):
    lines.append("")
    lines.append(title)
    lines.append("-" * len(title))

    if not values:
        lines.append(empty_text)
        return

    for value in values[:limit]:
        lines.append(f"- {value}")

    if len(values) > limit:
        lines.append(f"- ... {len(values) - limit} more")


def find_related_model_cfg_entries(entry_name, entries):
    entry_folder = Path(entry_name.replace("\\", "/")).parent.as_posix()

    if entry_folder == ".":
        entry_folder = ""

    result = []

    for other in entries:
        normalized = other.name.replace("\\", "/")
        lower_name = Path(normalized).name.lower()

        if lower_name not in {"model.cfg", "model.bin"}:
            continue

        other_folder = Path(normalized).parent.as_posix()

        if other_folder == ".":
            other_folder = ""

        if other_folder == "" or entry_folder == other_folder or entry_folder.startswith(other_folder + "/"):
            result.append(normalized)

    return sorted(result, key=lambda item: (len(item), item.lower()))


def get_p3d_metadata(entry, entries, data):
    format_label, version = get_p3d_format_info(data)
    strings = extract_p3d_printable_strings(data)
    textures = filter_resource_strings(strings, {".paa", ".pac"})
    materials = filter_resource_strings(strings, {".rvmat"})
    linked_models = [value for value in filter_resource_strings(strings, {".p3d"}) if value.lower() != entry.name.lower()]
    animations = filter_resource_strings(strings, {".rtm"})
    config_refs = filter_resource_strings(strings, {".cfg", ".cpp", ".bin"})
    resolution_lods = extract_p3d_resolution_lods(data)
    categorized_lods = categorize_p3d_resolution_lods(resolution_lods)
    lod_markers = filter_marker_strings(strings, P3D_LOD_MARKERS)
    related_model_cfg = find_related_model_cfg_entries(entry.name, entries)
    expected_model_class = Path(entry.name.replace("\\", "/")).stem

    return {
        "format_label": format_label,
        "version": version,
        "textures": textures,
        "materials": materials,
        "linked_models": linked_models,
        "animations": animations,
        "config_refs": config_refs,
        "resolution_lods": resolution_lods,
        "categorized_lods": categorized_lods,
        "lod_markers": lod_markers,
        "related_model_cfg": related_model_cfg,
        "expected_model_class": expected_model_class,
    }


def build_p3d_info_report(entry, metadata):
    lines = [
        "P3D Information",
        "===============",
        "",
        f"File: {entry.name}",
        f"Size: {format_byte_size(get_pbo_entry_unpacked_size(entry))}",
        f"Format: {metadata['format_label']}",
        f"Version: {metadata['version'] if metadata['version'] is not None else 'unknown'}",
        f"Header LOD count: {len(metadata['resolution_lods']) if metadata['resolution_lods'] else 'unknown'}",
        f"Expected model.cfg class: {metadata['expected_model_class']}",
        "",
        "Notes",
        "-----",
        "- This is a metadata scan, not a P3D debinarizer.",
        "- This tool does not extract, recover, reconstruct, or debinarize model.cfg from ODOL/P3D data.",
        "- Loose model.cfg/model.bin entries listed below are separate files already present in the PBO, not recovered from the P3D.",
        "- Categorized LODs come from the ODOL header resolution array when it can be safely read.",
        "- The expected model.cfg class is inferred from the P3D filename.",
    ]

    append_limited_section(lines, "Related loose model.cfg/model.bin entries in this PBO", metadata["related_model_cfg"], "None found near this P3D. The Inspector will not attempt to reconstruct model.cfg from baked P3D data.", 30)
    append_lod_category_sections(lines, metadata["categorized_lods"])
    append_limited_section(lines, "Additional high-confidence LOD marker strings", metadata["lod_markers"], "No additional high-confidence LOD marker strings found.", 80)
    append_limited_section(lines, "Textures", metadata["textures"], "No texture references found.", 120)
    append_limited_section(lines, "Materials", metadata["materials"], "No material references found.", 120)
    append_limited_section(lines, "Linked models / proxies", metadata["linked_models"], "No linked model/proxy references found.", 120)
    append_limited_section(lines, "Animation files", metadata["animations"], "No RTM animation references found.", 80)
    append_limited_section(lines, "Config-like references", metadata["config_refs"], "No config-like file references found.", 80)

    return "\n".join(lines)
