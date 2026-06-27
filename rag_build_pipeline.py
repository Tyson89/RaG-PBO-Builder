import glob
import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from pbo_core import PboError, read_pbo_archive
from rag_builder_common import (
    BuildError,
    COPY_CHUNK_SIZE,
    WIN_SEP,
    format_duration,
    get_pbo_prefix,
    get_safe_temp_name,
    normalize_working_dir,
    parse_exclude_patterns,
    read_pbo_prefix_file,
    run_hidden_text_subprocess,
    should_skip_dir,
    should_skip_file,
    source_file_should_be_staged,
    try_relpath,
)
from rag_builder_storage import get_app_data_dir, load_build_cache, save_build_cache
from rag_pbo_writer import pack_pbo, pbo_entry_bytes_match_file, verify_packed_pbo
from rag_config_tools import strip_cpp_comments
from rag_preflight import (
    TERRAIN_SOURCE_FOLDER_NAMES,
    collect_config_cpp_files,
    collect_wrp_files,
    find_worldname_references,
    format_source_location,
    infer_terrain_pbo_prefix_from_worldname,
    is_path_inside,
    normalize_reference_path,
    resolve_config_include_path,
    resolve_reference_path,
    run_preflight_for_targets,
)

TEMP_MARKER_FILE = ".rag_pbo_builder_temp"
BUILDER_TEMP_CHILDREN = {"addons", "preflight", "staging", "binarized", "configs", "_binarize_textures"}
PAA_SOURCE_TEXTURE_EXTENSIONS = {".png", ".tga"}
WRP_SUSPICIOUS_SOURCE_SIZE = 1024 * 1024
WRP_SUSPICIOUS_MIN_RATIO = 0.5
DIAGNOSTIC_CONTEXT_LIMIT = 500

def get_available_logical_threads():
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        try:
            count = process_cpu_count()
            if count and count > 0:
                return count
        except Exception:
            pass
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if callable(sched_getaffinity):
        try:
            return max(1, len(sched_getaffinity(0)))
        except Exception:
            pass
    return os.cpu_count() or 8


def get_build_failure_diagnostics(error_message, log_lines=None):
    text = "\n".join([str(error_message or "")] + [str(line) for line in (log_lines or [])])
    lower = text.lower()
    diagnostics = []

    def add(title, cause, fix):
        key = title.lower()
        if any(item["title"].lower() == key for item in diagnostics):
            return
        diagnostics.append({"title": title, "cause": cause, "fix": fix})

    if "cannot include file" in lower or "preprocessor failed" in lower:
        add(
            "Config include could not be resolved",
            "Binarize or CfgConvert tried to read a #include file that was missing from the path it was using.",
            "Check the include path in config.cpp and make sure the referenced .hpp exists where the config expects it. If the file is intentionally excluded from the PBO, keep it available as a build-time include.",
        )

    if "suspiciously small wrp" in lower or "did not output required wrp" in lower or "cannot load world wrp" in lower:
        add(
            "Terrain WRP was not built correctly",
            "Binarize did not produce a valid world file. This usually means it could not resolve terrain object/config dependencies or the world path/prefix context was wrong.",
            "Verify Project root, PBO prefix, and worldName. For terrain projects, add required extracted object/config folders in Options > Binarize addon folders, for example P:\\dz, P:\\ca, and custom object packs.",
        )

    if "worldname" in lower and ("missing pbo entry" in lower or "non-wrp" in lower or "effective pbo prefix" in lower):
        add(
            "worldName does not match the packed WRP path",
            "The config points to a WRP path that does not line up with the PBO prefix and the WRP entry that will be packed.",
            "Make the PBO prefix and config worldName resolve to the same in-PBO WRP path. Common modular map layouts may need a $PBOPREFIX$ file or a project-relative worldName.",
        )

    if "0xc0000005" in lower or "access violation" in lower:
        add(
            "Binarize crashed with an access violation",
            "DayZ Tools Binarize crashed internally. A common cause is feeding it already-binarized ODOL P3Ds or malformed source data.",
            "Run preflight with P3D internal checks enabled. Keep ODOL P3Ds out of Binarize input or rebuild from proper source models when possible.",
        )

    if "charmap" in lower and "codec" in lower:
        add(
            "External tool output used unsupported text bytes",
            "A DayZ Tools program printed bytes that the Windows text decoder could not read.",
            "Update to the latest Builder. Newer versions preserve the log by replacing unreadable bytes instead of crashing.",
        )

    if "cfgconvert failed" in lower or "config syntax check failed" in lower or "error 3 while parsing config" in lower:
        add(
            "Config syntax or conversion failed",
            "CfgConvert could not parse or convert one of the config files.",
            "Open the file path shown above the CfgConvert output and fix the reported line. Also check active #include files because errors can come from included .hpp fragments.",
        )

    if "dssignfile failed" in lower or "private key" in lower or "bisign" in lower:
        add(
            "Signing failed",
            "The signing step could not create the expected .bisign file or the selected private key is invalid/missing.",
            "Check DSSignFile.exe, the .biprivatekey path, write permissions in the output folder, and whether antivirus is locking the generated PBO.",
        )

    if "imagetopaa failed" in lower:
        add(
            "PAA conversion failed",
            "ImageToPAA could not convert one of the source textures.",
            "Open the source texture named in the log and verify it is a supported, readable PNG/TGA. Disable Update PAA if the texture should not be converted during packing.",
        )

    if "path is on mount" in lower or "different drive" in lower or "cross-drive" in lower:
        add(
            "Path is outside the expected drive/root",
            "A referenced file or build path is on a different drive than the source/project root. Some relative-path operations and BI tools are sensitive to this.",
            "Keep Project Source, Project root, temp, and referenced build inputs under consistent workspace paths where possible. Avoid references that jump to unrelated drives.",
        )

    if "no addon targets selected" in lower:
        add(
            "No addon was selected for build",
            "The selected Project Source did not produce a build target, or all detected targets were deselected.",
            "Refresh the addon list and select at least one addon folder. If building a single folder directly, make sure it contains a config.cpp or valid addon content.",
        )

    return diagnostics


def log_build_failure_diagnostics(error_message, log_lines, log):
    diagnostics = get_build_failure_diagnostics(error_message, log_lines)

    if not diagnostics:
        return 0

    log("")
    log("=" * 80)
    log("Build diagnostics")
    log("=" * 80)

    for index, item in enumerate(diagnostics, start=1):
        log(f"{index}. Likely cause: {item['title']}")
        log(f"   What it means: {item['cause']}")
        log(f"   Suggested fix: {item['fix']}")

    log("=" * 80)
    return len(diagnostics)


def get_default_max_processes():
    return max(1, min(get_available_logical_threads(), 64))

def file_sha1(file_path):
    digest = hashlib.sha1()
    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_sha1_cached_for_build(file_path, build_hash_cache=None):
    if build_hash_cache is None:
        return file_sha1(file_path)
    try:
        stat = os.stat(file_path)
    except OSError:
        return file_sha1(file_path)
    key = os.path.normcase(os.path.abspath(file_path))
    cached = build_hash_cache.get(key)
    if isinstance(cached, dict) and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("sha1"):
        return cached["sha1"]
    digest = file_sha1(file_path)
    build_hash_cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha1": digest}
    return digest


def files_have_same_content(source_file, target_file):
    try:
        with open(source_file, "rb") as src, open(target_file, "rb") as dst:
            while True:
                a = src.read(COPY_CHUNK_SIZE)
                b = dst.read(COPY_CHUNK_SIZE)
                if a != b:
                    return False
                if not a:
                    return True
    except OSError:
        return False


def files_are_same_for_staging(source_file, target_file, content_safe=True):
    if not os.path.isfile(target_file):
        return False
    try:
        source_stat = os.stat(source_file)
        target_stat = os.stat(target_file)
    except OSError:
        return False
    if source_stat.st_size != target_stat.st_size:
        return False
    if content_safe:
        return files_have_same_content(source_file, target_file)
    return source_stat.st_mtime_ns <= target_stat.st_mtime_ns


def file_fingerprint(file_path, include_content=False, build_hash_cache=None):
    if not file_path or not os.path.isfile(file_path):
        return {"path": file_path or "", "exists": False}
    try:
        stat = os.stat(file_path)
        result = {"path": os.path.abspath(file_path), "exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if include_content:
            result["sha1"] = file_sha1_cached_for_build(file_path, build_hash_cache)
        return result
    except OSError:
        return {"path": file_path or "", "exists": False}


def get_p3d_magic(file_path):
    try:
        with open(file_path, "rb") as file:
            return file.read(4)
    except OSError:
        return b""


def is_odol_p3d(file_path):
    return get_p3d_magic(file_path) == b"ODOL"


def copy_source_to_staging(source_dir, staging_dir, extra_patterns=None, log=None, content_safe=True, skip_odol_p3d=False):
    os.makedirs(staging_dir, exist_ok=True)
    expected = set()
    copied = updated = unchanged = removed = skipped_odol_p3d = 0
    for root, dirs, files in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            if log:
                log(f"WARNING: Skipped external folder during staging because it is on a different drive than the source: {root}")
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not source_file_should_be_staged(file, extra_patterns):
                continue
            source_file = os.path.join(root, file)
            rel = try_relpath(source_file, source_dir)
            if not rel:
                if log:
                    log(f"WARNING: Skipped external file during staging because it is on a different drive than the source: {source_file}")
                continue
            if skip_odol_p3d and file.lower().endswith(".p3d") and is_odol_p3d(source_file):
                skipped_odol_p3d += 1
                continue
            expected.add(rel.replace(os.sep, WIN_SEP).lower())
            target_file = os.path.join(staging_dir, rel)
            if files_are_same_for_staging(source_file, target_file, content_safe):
                unchanged += 1
                continue
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            existed = os.path.isfile(target_file)
            shutil.copy2(source_file, target_file)
            updated += 1 if existed else 0
            copied += 0 if existed else 1
    for root, dirs, files in os.walk(staging_dir, topdown=False):
        for file in files:
            staged_file = os.path.join(root, file)
            rel = try_relpath(staged_file, staging_dir)
            if not rel:
                if log:
                    log(f"WARNING: Left external staged file untouched because it is on a different drive than staging: {staged_file}")
                continue
            rel = rel.replace(os.sep, WIN_SEP).lower()
            if rel not in expected:
                os.remove(staged_file)
                removed += 1
        if root != staging_dir:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
    if log:
        log(f"Incremental staging: copied={copied}, updated={updated}, unchanged={unchanged}, removed={removed}, content_safe={content_safe}")
        if skipped_odol_p3d:
            log(f"Skipped {skipped_odol_p3d} already-binarized ODOL P3D file(s) before Binarize. They will be copied back unchanged before packing.")


def overlay_tree(source_dir, destination_dir, skip_extensions=None, log=None):
    if not os.path.isdir(source_dir):
        return
    skip_extensions = {ext.lower() for ext in (skip_extensions or set())}
    copied = skipped = 0
    for root, dirs, files in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            if log:
                log(f"WARNING: Skipped external Binarize output folder because it is on a different drive than the output root: {root}")
            dirs[:] = []
            continue
        target_root = destination_dir if rel_root == "." else os.path.join(destination_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for file in files:
            if os.path.splitext(file)[1].lower() in skip_extensions:
                skipped += 1
                if log:
                    rel_file = try_relpath(os.path.join(root, file), source_dir)
                    rel_file = rel_file.replace(os.sep, WIN_SEP) if rel_file else os.path.join(root, file)
                    log(f"Skipped Binarize overlay for protected file: {rel_file}")
                continue
            shutil.copy2(os.path.join(root, file), os.path.join(target_root, file))
            copied += 1

    if log:
        log(f"Binarize overlay: copied={copied}, skipped={skipped}")


def paths_are_same(path_a, path_b):
    try:
        return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))
    except Exception:
        return False


def prefix_to_path_parts(prefix):
    return [part for part in normalize_reference_path(prefix).strip(WIN_SEP).split(WIN_SEP) if part]


def get_project_prefix_path(project_root, prefix):
    parts = prefix_to_path_parts(prefix)
    if not parts:
        return ""
    return os.path.join(normalize_working_dir(project_root), *parts)


def create_directory_junction(link_path, target_path, cwd):
    if os.name == "nt":
        result = run_hidden_text_subprocess(["cmd", "/c", "mklink", "/J", link_path, target_path], cwd=cwd if os.path.isdir(cwd) else None)
        return result.returncode == 0, result.stdout.strip()

    try:
        os.symlink(target_path, link_path, target_is_directory=True)
        return True, ""
    except OSError as error:
        return False, str(error)


def prepare_wrp_binarize_source(staging_dir, folder_path, prefix, project_root, log):
    virtual_source = get_project_prefix_path(project_root, prefix)

    if not virtual_source:
        log("WARNING: Terrain WRP prefix is empty. Binarize will use the staging folder path.")
        return staging_dir, None

    if paths_are_same(virtual_source, staging_dir):
        return staging_dir, None

    if os.path.isdir(virtual_source):
        if paths_are_same(virtual_source, folder_path):
            log(f"Terrain WRP Binarize source uses real project-prefix folder: {virtual_source}")
            return folder_path, None

        raise BuildError(
            "Cannot prepare terrain WRP Binarize workspace. The required project-prefix path already exists "
            f"and is not the selected addon source: {virtual_source}"
        )

    if os.path.exists(virtual_source):
        raise BuildError(f"Cannot prepare terrain WRP Binarize workspace. Required path is not a folder: {virtual_source}")

    workspace = normalize_working_dir(project_root)
    parent = os.path.dirname(virtual_source)
    rel_parent = try_relpath(parent, workspace)

    if not rel_parent or rel_parent.startswith(".."):
        raise BuildError(f"Cannot prepare terrain WRP Binarize workspace outside project root: {virtual_source}")

    created_parents = []
    current = workspace

    for part in [part for part in rel_parent.split(os.sep) if part and part != "."]:
        current = os.path.join(current, part)
        if not os.path.exists(current):
            os.mkdir(current)
            created_parents.append(current)
        elif not os.path.isdir(current):
            raise BuildError(f"Cannot prepare terrain WRP Binarize workspace. Parent path is not a folder: {current}")

    ok, output = create_directory_junction(virtual_source, staging_dir, workspace)

    if not ok:
        for created in reversed(created_parents):
            try:
                if not os.listdir(created):
                    os.rmdir(created)
            except OSError:
                pass
        raise BuildError(f"Could not create temporary terrain WRP Binarize junction: {virtual_source} -> {staging_dir}\n{output}")

    log(f"Terrain WRP Binarize source linked at project-prefix path: {virtual_source}")
    return virtual_source, {"link": virtual_source, "created_parents": created_parents}


def cleanup_wrp_binarize_source(context, log):
    if not context:
        return

    link = context.get("link", "")

    if link:
        try:
            os.rmdir(link)
            log(f"Removed temporary terrain WRP Binarize link: {link}")
        except OSError as error:
            log(f"WARNING: Could not remove temporary terrain WRP Binarize link: {link} ({error})")

    for created in reversed(context.get("created_parents", [])):
        try:
            if not os.listdir(created):
                os.rmdir(created)
        except OSError:
            pass


def validate_binarized_wrp_outputs(staging_dir, binarized_dir, log, extra_patterns=None):
    if not os.path.isdir(staging_dir) or not os.path.isdir(binarized_dir):
        raise BuildError(f"WRP Binarize verification failed. Missing staging or Binarize output folder: {staging_dir} / {binarized_dir}")

    checked = 0

    for staged_wrp in collect_wrp_files(staging_dir, extra_patterns):
        rel = try_relpath(staged_wrp, staging_dir)

        if not rel:
            continue

        binarized_wrp = os.path.join(binarized_dir, rel)
        rel_display = rel.replace(os.sep, WIN_SEP)

        if not os.path.isfile(binarized_wrp):
            raise BuildError(f"WRP Binarize verification failed. Binarize did not output required WRP: {rel_display}")

        try:
            staged_size = os.path.getsize(staged_wrp)
            binarized_size = os.path.getsize(binarized_wrp)
        except OSError as error:
            raise BuildError(f"WRP Binarize verification failed for {rel_display}: {error}")

        if binarized_size <= 0:
            raise BuildError(f"WRP Binarize verification failed. Binarize produced an empty WRP: {rel_display}")

        if staged_size >= WRP_SUSPICIOUS_SOURCE_SIZE:
            min_expected_size = int(staged_size * WRP_SUSPICIOUS_MIN_RATIO)

            if binarized_size < min_expected_size:
                raise BuildError(
                    "WRP Binarize verification failed. Binarize produced a suspiciously small WRP: "
                    f"{rel_display} staged={staged_size:,} bytes, binarized={binarized_size:,} bytes. "
                    "The source WRP will not be packed as a fallback. For terrain builds, add the extracted addon/config folders "
                    "used by the map objects to Options -> Binarize addon folders, then rebuild."
                )

        checked += 1
        log(f"WRP Binarize verification OK: {rel_display} staged={staged_size:,} bytes, binarized={binarized_size:,} bytes")

    if checked == 0:
        raise BuildError("WRP Binarize verification failed. No WRP files were checked.")

    return checked


def ensure_p3d_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    copied = already_present = skipped = 0
    os.makedirs(staging_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            log(f"WARNING: Skipped external folder during P3D fallback because it is on a different drive than the source: {root}")
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not file.lower().endswith(".p3d"):
                continue
            if should_skip_file(file, extra_patterns):
                skipped += 1
                continue
            source_file = os.path.join(root, file)
            rel = try_relpath(source_file, source_dir)
            if not rel:
                log(f"WARNING: Skipped external P3D fallback because it is on a different drive than the source: {source_file}")
                continue
            target_file = os.path.join(staging_dir, rel)
            if os.path.isfile(target_file):
                already_present += 1
                continue
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            shutil.copy2(source_file, target_file)
            copied += 1
            log(f"Copied original P3D missing from Binarize output: {rel.replace(os.sep, WIN_SEP)}")
    if copied:
        log(f"Copied {copied} original P3D file(s) that Binarize did not output.")
    else:
        log(f"All non-excluded source P3D files are already present in staging ({already_present} checked).")
    if skipped:
        log(f"Skipped {skipped} excluded P3D file(s) during P3D fallback check.")
    return copied


def ensure_config_cpp_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    copied = skipped_dirs = 0
    os.makedirs(staging_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        rel_root = try_relpath(root, source_dir)
        if not rel_root:
            log(f"WARNING: Skipped external folder while ensuring configs because it is on a different drive than the source: {root}")
            dirs[:] = []
            continue

        before = len(dirs)
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        skipped_dirs += before - len(dirs)
        for file in files:
            if file.lower() != "config.cpp":
                continue
            source_file = os.path.join(root, file)
            rel = try_relpath(source_file, source_dir)
            if not rel:
                log(f"WARNING: Skipped external config.cpp because it is on a different drive than the source: {source_file}")
                continue
            target_file = os.path.join(staging_dir, rel)
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            shutil.copy2(source_file, target_file)
            copied += 1
            log(f"Ensured config.cpp in staging: {rel.replace(os.sep, WIN_SEP)}")
    if copied:
        log(f"Ensured {copied} config.cpp file(s) are present in staging.")
    else:
        log("No included config.cpp files found while ensuring configs in staging.")
    if skipped_dirs:
        log(f"Skipped {skipped_dirs} excluded folder(s) while ensuring config.cpp files.")
    return copied


def get_staged_include_relative_path(parent_staged_rel, include_value):
    raw = normalize_reference_path(include_value).strip(WIN_SEP)

    if not raw:
        return ""

    raw_os = raw.replace(WIN_SEP, os.sep)

    if os.path.isabs(raw_os):
        return ""

    parent_dir = os.path.dirname(parent_staged_rel)
    staged_rel = os.path.normpath(os.path.join(parent_dir, raw_os)) if parent_dir else os.path.normpath(raw_os)

    if staged_rel == "." or staged_rel.startswith(".." + os.sep) or staged_rel == "..":
        return ""

    return staged_rel


def collect_config_include_files(config_files, source_dir, project_root):
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.IGNORECASE | re.MULTILINE)
    include_entries = []
    seen = set()

    def visit(config_file, staged_rel):
        try:
            path = Path(config_file).resolve(strict=False)
        except Exception:
            path = Path(config_file)

        key = (os.path.normcase(str(path)), staged_rel.lower())

        if key in seen:
            return

        seen.add(key)

        try:
            raw_content = Path(config_file).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return

        content = strip_cpp_comments(raw_content, preserve_lines=True)

        for match in include_pattern.finditer(content):
            include_value = match.group(1).strip()
            include_path = resolve_config_include_path(include_value, str(path), source_dir, project_root)
            include_staged_rel = get_staged_include_relative_path(staged_rel, include_value)

            if include_path and os.path.isfile(include_path) and include_staged_rel:
                include_entries.append({"source": include_path, "staged_rel": include_staged_rel})
                visit(include_path, include_staged_rel)

    for config_file in config_files:
        config_rel = try_relpath(config_file, source_dir)

        if not config_rel:
            continue

        visit(config_file, os.path.normpath(config_rel))

    unique = []
    emitted = set()

    for include_entry in include_entries:
        key = (
            os.path.normcase(os.path.abspath(include_entry["source"])),
            include_entry["staged_rel"].lower(),
        )

        if key in emitted:
            continue

        emitted.add(key)
        unique.append(include_entry)

    unique.sort(key=lambda item: item["staged_rel"].lower())
    return unique


def ensure_config_include_files_in_staging(source_dir, staging_dir, project_root, log, extra_patterns=None):
    source_configs = collect_config_cpp_files(source_dir, extra_patterns)

    if not source_configs:
        return 0

    include_entries = collect_config_include_files(source_configs, source_dir, project_root)
    copied = already_present = outside_source = 0

    for include_entry in include_entries:
        include_file = include_entry["source"]
        rel = include_entry["staged_rel"]
        target_file = os.path.join(staging_dir, rel)

        if os.path.isfile(target_file) and files_are_same_for_staging(include_file, target_file, True):
            already_present += 1
            continue

        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        shutil.copy2(include_file, target_file)
        copied += 1
        if not is_path_inside(include_file, source_dir):
            outside_source += 1
            log(f"Copied external config include needed for Binarize/CfgConvert: {include_file} -> {rel.replace(os.sep, WIN_SEP)}")
        else:
            log(f"Copied config include needed for Binarize/CfgConvert: {rel.replace(os.sep, WIN_SEP)}")

    if include_entries:
        log(f"Config include staging: copied={copied}, already_present={already_present}, outside_source={outside_source}")

    return copied


def has_p3d_files(source_dir, extra_patterns=None):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower().endswith(".p3d") and not should_skip_file(file, extra_patterns):
                return True
    return False


def has_binarizable_p3d_files(source_dir, extra_patterns=None):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not file.lower().endswith(".p3d") or should_skip_file(file, extra_patterns):
                continue
            if not is_odol_p3d(os.path.join(root, file)):
                return True
    return False


def has_wrp_files(source_dir, extra_patterns=None):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower().endswith(".wrp") and not should_skip_file(file, extra_patterns):
                return True
    return False


def get_project_relative_pbo_prefix(folder_path, project_root):
    if not folder_path or not project_root:
        return ""

    rel = try_relpath(os.path.abspath(folder_path), normalize_working_dir(project_root))

    if not rel or rel in {".", ""} or rel.startswith(".." + os.sep) or rel == "..":
        return ""

    return rel.replace(os.sep, WIN_SEP).strip(WIN_SEP)


def get_effective_pbo_prefix(pbo_base_name, folder_path, project_root, extra_patterns, log=None):
    prefix = get_pbo_prefix(pbo_base_name, folder_path)

    if read_pbo_prefix_file(folder_path):
        return prefix

    wrp_files = collect_wrp_files(folder_path, extra_patterns)

    if wrp_files:
        config_files = collect_config_cpp_files(folder_path, extra_patterns)
        worldname_refs = find_worldname_references(config_files, folder_path, project_root)
        inferred_prefix = infer_terrain_pbo_prefix_from_worldname(folder_path, wrp_files, worldname_refs)

        if inferred_prefix and inferred_prefix.lower() != prefix.lower():
            if log:
                log(f"Terrain worldName implies PBO prefix '{inferred_prefix}'. Using it instead of fallback prefix '{prefix}'.")
            return inferred_prefix

    project_relative_prefix = get_project_relative_pbo_prefix(folder_path, project_root)

    if project_relative_prefix and project_relative_prefix.lower() != prefix.lower():
        if log:
            log(f"Project-relative path implies PBO prefix '{project_relative_prefix}'. Using it instead of fallback prefix '{prefix}'.")
        return project_relative_prefix

    return prefix


def normalize_project_root_arg(project_root):
    return project_root.rstrip(WIN_SEP + "/")


def parse_binarize_addon_folders(raw_value):
    if not raw_value:
        return []

    items = []
    for item in re.split(r"[\r\n,;]+", str(raw_value)):
        value = item.strip().strip('"').strip("'")
        if value:
            items.append(os.path.normpath(value))

    return items


def get_binarize_addon_folders(settings, log=None):
    project_root = normalize_project_root_arg(settings.get("project_root", ""))
    folders = []
    seen = set()

    def add_folder(folder):
        if not folder:
            return
        normalized = os.path.normpath(folder)
        key = os.path.normcase(os.path.abspath(normalized))
        if key in seen:
            return
        seen.add(key)
        folders.append(normalized)

    add_folder(project_root)

    for folder in parse_binarize_addon_folders(settings.get("binarize_addon_folders", "")):
        if os.path.isdir(folder):
            add_folder(folder)
        elif log:
            log(f"WARNING: Ignoring missing Binarize addon folder: {folder}")

    return folders


def find_tool(possible):
    for path in possible:
        if Path(path).is_file():
            return str(path)
    return ""


def decode_steam_vdf_path(value):
    return value.replace("\\\\", WIN_SEP).replace("/", WIN_SEP)


def parse_steam_libraryfolders(text):
    roots = []
    seen = set()

    for match in re.finditer(r'"(?:path|\d+)"\s+"([^"]+)"', text or "", re.IGNORECASE):
        path_value = os.path.normpath(decode_steam_vdf_path(match.group(1).strip()))
        key = os.path.normcase(os.path.abspath(path_value))

        if path_value and key not in seen:
            seen.add(key)
            roots.append(path_value)

    return roots


def get_registry_steam_paths():
    if os.name != "nt":
        return []

    try:
        import winreg
    except Exception:
        return []

    paths = []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", 0),
    ]

    for hive, key_path, flags in keys:
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | flags) as key:
                value, _kind = winreg.QueryValueEx(key, "InstallPath")
                if value:
                    paths.append(os.path.normpath(value))
        except OSError:
            continue

    return paths


def get_default_steam_paths():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return [str(Path(pf86) / "Steam"), str(Path(pf) / "Steam")]


def get_steam_library_roots():
    roots = []
    seen = set()

    def add_root(path_value):
        if not path_value:
            return
        path_value = os.path.normpath(path_value)
        key = os.path.normcase(os.path.abspath(path_value))
        if key in seen:
            return
        seen.add(key)
        roots.append(path_value)

    for root in get_default_steam_paths() + get_registry_steam_paths():
        add_root(root)

    for root in list(roots):
        library_file = Path(root) / "steamapps" / "libraryfolders.vdf"
        if not library_file.is_file():
            continue
        try:
            for library_root in parse_steam_libraryfolders(library_file.read_text(encoding="utf-8", errors="ignore")):
                add_root(library_root)
        except OSError:
            continue

    return roots


def get_dayz_tools_candidates(*relative_parts):
    candidates = []

    for root in get_steam_library_roots():
        candidates.append(Path(root) / "steamapps" / "common" / "DayZ Tools" / Path(*relative_parts))

    return candidates


def find_dayz_binarize():
    return find_tool(get_dayz_tools_candidates("Bin", "Binarize", "binarize.exe"))


def find_cfgconvert():
    return find_tool(get_dayz_tools_candidates("Bin", "CfgConvert", "CfgConvert.exe"))


def find_imagetopaa():
    return find_tool(get_dayz_tools_candidates("Bin", "ImageToPAA", "ImageToPAA.exe"))


def find_dssignfile():
    return find_tool(get_dayz_tools_candidates("Bin", "DSUtils", "DSSignFile.exe") + get_dayz_tools_candidates("Bin", "DSSignFile", "DSSignFile.exe"))


def get_signature_pattern_for_pbo(pbo_path):
    return pbo_path + ".*.bisign"


def find_new_signature_for_pbo(pbo_path):
    signatures = glob.glob(get_signature_pattern_for_pbo(pbo_path))
    if not signatures:
        return ""
    signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return signatures[0]


def remove_old_signatures(pbo_path, log):
    for signature in glob.glob(get_signature_pattern_for_pbo(pbo_path)):
        try:
            os.remove(signature)
            log(f"Removed old signature: {signature}")
        except Exception as e:
            raise BuildError(f"Could not remove old signature: {signature} ({e})")


def wait_for_file_ready(file_path, log, timeout_seconds=10):
    start = time.time()
    last_size = -1
    stable = 0
    log(f"Waiting for file to be ready: {file_path}")
    while time.time() - start < timeout_seconds:
        if os.path.isfile(file_path):
            try:
                size = os.path.getsize(file_path)
                stable = stable + 1 if size > 0 and size == last_size else 0
                if stable >= 2:
                    log(f"File ready: {file_path} ({size} bytes)")
                    return
                last_size = size
            except OSError:
                stable = 0
        time.sleep(0.25)
    raise BuildError(f"File was not ready after {timeout_seconds} seconds: {file_path}")


def get_bikey_for_private_key(private_key):
    if not private_key:
        return ""
    key_path = Path(private_key)
    if key_path.suffix.lower() != ".biprivatekey":
        return ""
    bikey = key_path.with_suffix(".bikey")
    if bikey.is_file():
        return str(bikey)
    matches = list(key_path.parent.glob(key_path.stem + "*.bikey"))
    matches.sort(key=lambda p: p.name.lower())
    return str(matches[0]) if matches else ""


def copy_bikey_to_keys(private_key, output_keys_dir, log):
    bikey = get_bikey_for_private_key(private_key)
    if not bikey:
        log("WARNING: Matching .bikey was not found. Nothing copied to Keys folder.")
        return ""
    os.makedirs(output_keys_dir, exist_ok=True)
    target = os.path.join(output_keys_dir, os.path.basename(bikey))
    if os.path.isfile(target):
        log(f"Bikey already exists. Skipping copy: {target}")
        return ""
    shutil.copy2(bikey, target)
    log(f"Copied bikey -> {target}")
    return target


def run_dssignfile(dssignfile_exe, private_key, pbo_path, log):
    if not dssignfile_exe or not os.path.isfile(dssignfile_exe):
        raise BuildError("DSSignFile.exe not found. Select the DayZ Tools DSSignFile.exe path.")
    if not private_key or not os.path.isfile(private_key):
        raise BuildError("Private key not found. Select your .biprivatekey file.")
    if not private_key.lower().endswith(".biprivatekey"):
        raise BuildError("Selected private key does not end with .biprivatekey.")
    work_dir = get_app_data_dir() / "signing_temp" / f"sign_{os.getpid()}_{time.time_ns()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        work_pbo = work_dir / os.path.basename(pbo_path)
        work_key = work_dir / os.path.basename(private_key)
        shutil.copy2(pbo_path, work_pbo)
        shutil.copy2(private_key, work_key)
        remove_old_signatures(str(work_pbo), log)
        cmd = [dssignfile_exe, work_key.name, work_pbo.name]
        log("")
        log("Signing PBO in isolated temp folder:")
        log(f"  PBO:         {work_pbo.name}")
        log(f"  Key:         {work_key.name}")
        log(f"  Work folder: {work_dir}")
        result = run_hidden_text_subprocess(cmd, cwd=str(work_dir))
        if result.stdout:
            for line in result.stdout.splitlines():
                log(line)
        signatures = glob.glob(str(work_pbo) + ".*.bisign")
        signatures.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        if result.returncode != 0:
            raise BuildError(f"DSSignFile failed with exit code {result.returncode}: {pbo_path}")
        if not signatures:
            raise BuildError(f"DSSignFile finished but no .bisign was created for: {pbo_path}")
        original_dir = os.path.dirname(os.path.abspath(pbo_path))
        for signature in signatures:
            final_signature = os.path.join(original_dir, os.path.basename(signature))
            shutil.copy2(signature, final_signature)
            log(f"Created signature: {final_signature}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def create_output_work_dir(output_pbo, addon_name):
    output_dir = os.path.dirname(os.path.abspath(output_pbo))
    work_dir = os.path.join(output_dir, "_rag_build_tmp", f"{get_safe_temp_name(addon_name)}_{os.getpid()}_{time.time_ns()}")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def create_publish_backup_dir(final_pbo):
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    name = os.path.splitext(os.path.basename(final_pbo))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(final_dir, "_rag_build_backup", f"{name}_{stamp}_{os.getpid()}_{time.time_ns()}")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def copy_existing_output_artifacts_to_backup(final_pbo, backup_dir, log):
    if os.path.isfile(final_pbo):
        backup_pbo = os.path.join(backup_dir, os.path.basename(final_pbo))
        shutil.copy2(final_pbo, backup_pbo)
        log(f"Backed up existing PBO: {backup_pbo}")
    for signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
        backup_signature = os.path.join(backup_dir, os.path.basename(signature))
        shutil.copy2(signature, backup_signature)
        log(f"Backed up existing signature: {backup_signature}")


def validate_publish_backup(final_pbo, backup_dir, existing_signatures):
    if os.path.isfile(final_pbo) and not os.path.isfile(os.path.join(backup_dir, os.path.basename(final_pbo))):
        raise BuildError("Backup validation failed. Missing backup PBO.")
    for signature in existing_signatures:
        if not os.path.isfile(os.path.join(backup_dir, os.path.basename(signature))):
            raise BuildError(f"Backup validation failed. Missing backup signature: {signature}")


def remove_current_output_artifacts(final_pbo, log):
    if os.path.isfile(final_pbo):
        os.remove(final_pbo)
        log(f"Removed partially published PBO: {final_pbo}")
    for signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
        try:
            os.remove(signature)
            log(f"Removed partially published signature: {signature}")
        except FileNotFoundError:
            pass


def restore_output_artifacts_from_backup(final_pbo, backup_dir, log):
    if not os.path.isdir(backup_dir):
        return
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    log("Attempting to restore previous output artifacts from backup.")
    remove_current_output_artifacts(final_pbo, log)
    backup_pbo = os.path.join(backup_dir, os.path.basename(final_pbo))
    if os.path.isfile(backup_pbo):
        shutil.copy2(backup_pbo, final_pbo)
        log(f"Restored previous PBO: {final_pbo}")
    for backup_signature in glob.glob(os.path.join(backup_dir, os.path.basename(final_pbo) + ".*.bisign")):
        final_signature = os.path.join(final_dir, os.path.basename(backup_signature))
        shutil.copy2(backup_signature, final_signature)
        log(f"Restored previous signature: {final_signature}")


def safe_remove_empty_parent(path_value, stop_at):
    try:
        current = Path(path_value)
        stop = Path(stop_at).resolve(strict=False)
        while current.exists() and current.is_dir():
            if current.resolve(strict=False) == stop or any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except Exception:
        pass


def replace_output_artifacts(temp_pbo, final_pbo, sign_pbos, log):
    if not os.path.isfile(temp_pbo):
        raise BuildError(f"Temporary PBO does not exist and cannot replace output: {temp_pbo}")
    final_dir = os.path.dirname(os.path.abspath(final_pbo))
    os.makedirs(final_dir, exist_ok=True)
    temp_signatures = glob.glob(get_signature_pattern_for_pbo(temp_pbo))
    temp_signatures.sort(key=lambda path: os.path.basename(path).lower())
    if sign_pbos and not temp_signatures:
        raise BuildError(f"Signed build expected a .bisign but none was created for: {temp_pbo}")
    backup_dir = create_publish_backup_dir(final_pbo)
    backup_root = os.path.dirname(backup_dir)
    prepared = []
    publish_started = False
    publish_id = f"{os.getpid()}_{time.time_ns()}"
    try:
        log("Preparing output publish set.")
        existing_signatures = glob.glob(get_signature_pattern_for_pbo(final_pbo))
        existing_signatures.sort(key=lambda path: os.path.basename(path).lower())
        copy_existing_output_artifacts_to_backup(final_pbo, backup_dir, log)
        validate_publish_backup(final_pbo, backup_dir, existing_signatures)
        for temp_signature in temp_signatures:
            final_signature = os.path.join(final_dir, os.path.basename(temp_signature))
            prepared_signature = final_signature + f".new_{publish_id}"
            shutil.copy2(temp_signature, prepared_signature)
            prepared.append((prepared_signature, final_signature))
            log(f"Prepared signature for publish: {prepared_signature}")
        log("Publishing output artifacts after successful build validation.")
        publish_started = True
        os.replace(temp_pbo, final_pbo)
        log(f"Output PBO updated: {final_pbo}")
        new_names = {os.path.basename(final_signature) for _, final_signature in prepared}
        for prepared_signature, final_signature in prepared:
            os.replace(prepared_signature, final_signature)
            log(f"Output signature updated: {final_signature}")
        for old_signature in glob.glob(get_signature_pattern_for_pbo(final_pbo)):
            if os.path.basename(old_signature) not in new_names:
                os.remove(old_signature)
                log(f"Removed stale signature: {old_signature}")
        shutil.rmtree(backup_dir, ignore_errors=True)
        safe_remove_empty_parent(backup_root, final_dir)
        log("Output publish set completed successfully.")
    except Exception as e:
        log(f"ERROR: Output publish failed: {e}")
        for prepared_signature, _ in prepared:
            if os.path.isfile(prepared_signature):
                try:
                    os.remove(prepared_signature)
                except Exception:
                    pass
        if publish_started:
            try:
                restore_output_artifacts_from_backup(final_pbo, backup_dir, log)
            except Exception as restore_error:
                log(f"ERROR: Could not restore previous output from backup: {restore_error}")
        else:
            log("Publish had not started yet. Existing output was left untouched.")
            shutil.rmtree(backup_dir, ignore_errors=True)
            safe_remove_empty_parent(backup_root, final_dir)
        raise BuildError(f"Output publish failed. Existing output was left untouched or restored if needed. Details: {e}")


def cleanup_output_work_dir(work_dir, log=None):
    if not work_dir:
        return
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        parent = os.path.dirname(work_dir)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except Exception as e:
        if log:
            log(f"WARNING: Could not clean output work folder: {work_dir} ({e})")


def resolve_for_safety(path_value):
    return Path(path_value).expanduser().resolve(strict=False)


def paths_overlap(path_a, path_b):
    if not path_a or not path_b:
        return False
    try:
        a = resolve_for_safety(path_a)
        b = resolve_for_safety(path_b)
        if a == b:
            return True
        try:
            a.relative_to(b)
            return True
        except ValueError:
            pass
        try:
            b.relative_to(a)
            return True
        except ValueError:
            return False
    except Exception:
        return False


def path_is_inside_or_same(path_value, parent_value):
    if not path_value or not parent_value:
        return False

    try:
        path = resolve_for_safety(path_value)
        parent = resolve_for_safety(parent_value)

        if path == parent:
            return True

        path.relative_to(parent)
        return True
    except Exception:
        return False


def path_has_steam_common_dayz_parent(path_value):
    try:
        path_text = str(resolve_for_safety(path_value))
    except Exception:
        path_text = str(path_value or "")

    parts = [part.strip().lower() for part in re.split(r"[\\/]+", path_text) if part.strip()]

    for index in range(0, max(0, len(parts) - 2)):
        if parts[index] == "steamapps" and parts[index + 1] == "common" and parts[index + 2] in {"dayz", "dayz tools"}:
            return True

    return False


def get_dangerous_temp_root_reason(temp_root, source_root="", output_root=""):
    if not temp_root:
        return "Temp dir is empty."
    try:
        root_path = resolve_for_safety(temp_root)
    except Exception as e:
        return f"Could not resolve temp dir: {e}"
    root_text = str(root_path)
    if len(root_text) < 5:
        return f"Temp dir path is too short: {root_text}"
    if root_path.parent == root_path:
        return f"Temp dir points to a filesystem root: {root_text}"
    drive, tail = os.path.splitdrive(root_text)
    if drive and tail in {"\\", "/"}:
        return f"Temp dir points to a drive root: {root_text}"
    important = [Path.home(), Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads"]
    for env_name in ["ProgramFiles", "ProgramFiles(x86)", "SystemRoot", "WINDIR", "LOCALAPPDATA", "APPDATA"]:
        value = os.environ.get(env_name)
        if value:
            important.append(Path(value))
    for item in important:
        try:
            if root_path == resolve_for_safety(item):
                return f"Temp dir points to an important folder: {root_text}"
        except Exception:
            pass

    protected_roots = []
    for env_name in ["ProgramFiles", "ProgramFiles(x86)", "SystemRoot", "WINDIR"]:
        value = os.environ.get(env_name)
        if value:
            protected_roots.append(value)

    for protected_root in protected_roots:
        if path_is_inside_or_same(root_path, protected_root):
            return f"Temp dir appears to be inside an important game/system folder: {root_text}"

    if path_has_steam_common_dayz_parent(root_path):
        return f"Temp dir appears to be inside an important game/system folder: {root_text}"

    if source_root and paths_overlap(root_path, source_root):
        return "Temp dir overlaps with the selected Project Source."
    if output_root and paths_overlap(root_path, output_root):
        return "Temp dir overlaps with the selected Build Output."
    return ""


def ensure_builder_temp_root(temp_root, log=None, source_root="", output_root=""):
    reason = get_dangerous_temp_root_reason(temp_root, source_root, output_root)
    if reason:
        raise BuildError(f"Unsafe temp dir. {reason}")
    root_path = resolve_for_safety(temp_root)
    root_path.mkdir(parents=True, exist_ok=True)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.exists():
        marker.write_text("RaG PBO Builder temp folder marker.\nThis file allows the builder to safely clean only known builder temp folders.\n", encoding="utf-8")
        if log:
            log(f"Created temp marker: {marker}")
    return root_path


def clear_temp_folder(temp_root, log, source_root="", output_root=""):
    root_path = ensure_builder_temp_root(temp_root, None, source_root, output_root)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.is_file():
        raise BuildError("Temp marker file is missing. Refusing cleanup for safety: " + str(marker))
    log(f"Safe temp cleanup: {root_path}")
    log("Only known RaG PBO Builder temp folders will be removed.")
    removed = 0
    for child_name in sorted(BUILDER_TEMP_CHILDREN):
        child = root_path / child_name
        if not child.exists():
            continue
        resolved = resolve_for_safety(child)
        try:
            resolved.relative_to(root_path)
        except ValueError:
            raise BuildError(f"Refusing to delete path outside temp root: {resolved}")
        if resolved == root_path:
            raise BuildError(f"Refusing to delete temp root itself: {resolved}")
        was_dir = child.is_dir()
        if was_dir:
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
        log(f"Removed temp {'folder' if was_dir else 'file'}: {child}")
    if removed == 0:
        log("No known builder temp folders found to remove.")
    log("Safe temp cleanup finished.")


def clear_full_temp_folder(temp_root, log, source_root="", output_root=""):
    root_path = ensure_builder_temp_root(temp_root, None, source_root, output_root)
    marker = root_path / TEMP_MARKER_FILE
    if not marker.is_file():
        raise BuildError("Temp marker file is missing. Refusing full cleanup for safety: " + str(marker))
    log(f"Full temp cleanup: {root_path}")
    log("All files and folders inside the temp root will be removed, except the builder marker file.")
    removed = 0
    for item in root_path.iterdir():
        if item.name == TEMP_MARKER_FILE:
            continue
        resolved = resolve_for_safety(item)
        try:
            resolved.relative_to(root_path)
        except ValueError:
            raise BuildError(f"Refusing to delete path outside temp root: {resolved}")
        if resolved == root_path:
            raise BuildError(f"Refusing to delete temp root itself: {resolved}")
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
        log(f"Removed temp item: {item}")
    if removed == 0:
        log("Full temp cleanup found nothing to remove.")
    log("Full temp cleanup finished.")


def create_temp_exclude_file(temp_root, raw_patterns, log):
    if parse_exclude_patterns(raw_patterns):
        log("Using exclude patterns internally only. No generated exclude.lst will be created.")
    return ""


def get_addon_temp_root(temp_root, addon_name):
    return os.path.join(temp_root, "addons", get_safe_temp_name(addon_name))


def get_pbo_base_name(folder_name, pbo_name, selected_count):
    clean = pbo_name.strip() if pbo_name else ""
    if clean and selected_count == 1:
        return clean.replace(".pbo", "").replace("/", "_").replace(WIN_SEP, "_")
    return folder_name


def detect_addon_targets(source_root, output_addons_dir, extra_patterns=None):
    if not os.path.isdir(source_root):
        return []
    source_root = os.path.normpath(source_root)
    if os.path.isfile(os.path.join(source_root, "config.cpp")):
        return [(os.path.basename(source_root) or "addon", source_root)]
    result = []
    output_addons_abs = os.path.abspath(output_addons_dir) if output_addons_dir else ""
    for name in os.listdir(source_root):
        full = os.path.join(source_root, name)
        lower_name = name.lower()
        if (
            not os.path.isdir(full)
            or should_skip_dir(name, extra_patterns)
            or lower_name in {"output", "addons", "keys"}
            or lower_name in TERRAIN_SOURCE_FOLDER_NAMES
        ):
            continue
        try:
            full_abs = os.path.abspath(full)
            if output_addons_abs and (full_abs == output_addons_abs or output_addons_abs.startswith(full_abs + os.sep)):
                continue
        except Exception:
            pass
        result.append((name, full))
    result.sort(key=lambda item: item[0].lower())
    return result


def compute_addon_state_hash(source_dir, prefix, settings, extra_patterns=None, build_hash_cache=None):
    digest = hashlib.sha1()
    tracked = {
        "prefix": prefix,
        "pbo_name": settings.get("pbo_name", ""),
        "use_binarize": bool(settings["use_binarize"]),
        "convert_config": bool(settings["convert_config"]),
        "sign_pbos": bool(settings["sign_pbos"]),
        "project_root": settings["project_root"],
        "exclude_patterns": settings["exclude_patterns"],
        "max_processes": settings["max_processes"],
        "update_paa_from_sources": bool(settings.get("update_paa_from_sources", False)),
        "binarize_exe": file_fingerprint(settings.get("binarize_exe", ""), True, build_hash_cache),
        "cfgconvert_exe": file_fingerprint(settings.get("cfgconvert_exe", ""), True, build_hash_cache),
        "imagetopaa_exe": file_fingerprint(settings.get("imagetopaa_exe", ""), True, build_hash_cache),
        "dssignfile_exe": file_fingerprint(settings.get("dssignfile_exe", ""), True, build_hash_cache),
    }
    private_key = settings.get("private_key", "")
    if settings.get("sign_pbos") and os.path.isfile(private_key):
        tracked["private_key"] = file_fingerprint(private_key, True, build_hash_cache)
    digest.update(json.dumps(tracked, sort_keys=True).encode("utf-8"))
    for root, dirs, filenames in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for fname in sorted(filenames, key=lambda value: value.lower()):
            ext = os.path.splitext(fname)[1].lower()
            is_paa_source = bool(settings.get("update_paa_from_sources", False)) and ext in PAA_SOURCE_TEXTURE_EXTENSIONS

            if should_skip_file(fname, extra_patterns) and not is_paa_source:
                continue

            full = os.path.join(root, fname)
            rel = try_relpath(full, source_dir)
            if not rel:
                continue
            rel = rel.replace(os.sep, WIN_SEP).lower()
            try:
                stat = os.stat(full)
            except OSError:
                continue
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(file_sha1_cached_for_build(full, build_hash_cache).encode("ascii"))
    return digest.hexdigest()


def verify_pack_source_before_packing(original_source_dir, pack_source, convert_config, log, extra_patterns=None):
    if not os.path.isdir(pack_source):
        raise BuildError(f"Pack source does not exist before verification: {pack_source}")

    if not convert_config:
        return

    original_configs = collect_config_cpp_files(original_source_dir, extra_patterns)

    if not original_configs:
        return

    remaining_config_cpp = []
    config_bin_count = 0

    for root, dirs, files in os.walk(pack_source):
        for file in files:
            lower = file.lower()
            if lower == "config.cpp":
                remaining_config_cpp.append(os.path.join(root, file))
            elif lower == "config.bin":
                config_bin_count += 1

    if remaining_config_cpp:
        rel = try_relpath(remaining_config_cpp[0], pack_source)
        rel = rel.replace(os.sep, WIN_SEP) if rel else remaining_config_cpp[0]
        raise BuildError(f"Post-conversion verification failed. config.cpp is still in pack source: {rel}")

    if config_bin_count == 0:
        raise BuildError("Post-conversion verification failed. Source had config.cpp but no config.bin exists in pack source.")

    log(f"Post-conversion verification OK: config.bin files found={config_bin_count}, config.cpp packed=0")


def worldname_to_pbo_entry_name(world_ref, prefix, addon_source_dir, project_root):
    normalized_ref = normalize_reference_path(world_ref)
    normalized_prefix = normalize_reference_path(prefix).strip(WIN_SEP)

    if normalized_prefix and normalized_ref.lower().startswith(normalized_prefix.lower() + WIN_SEP):
        return normalized_ref[len(normalized_prefix) + 1:]

    resolved, status = resolve_reference_path(normalized_ref, addon_source_dir, project_root)

    if status == "ok" and is_path_inside(resolved, addon_source_dir):
        rel = try_relpath(resolved, addon_source_dir)
        return rel.replace(os.sep, WIN_SEP) if rel else ""

    return ""


def verify_packed_wrp_entries(pbo_path, pack_source, original_source_dir, prefix, project_root, extra_patterns, log):
    wrp_files = collect_wrp_files(pack_source, extra_patterns)

    if not wrp_files:
        return

    try:
        archive = read_pbo_archive(pbo_path)
    except PboError as error:
        raise BuildError(f"Post-pack WRP verification failed. Could not read PBO: {error}")

    entries_by_name = {entry.name.replace("/", WIN_SEP).lower(): entry for entry in archive["entries"]}
    wrp_entry_names = set()

    for wrp_file in wrp_files:
        rel_wrp = try_relpath(wrp_file, pack_source)
        if not rel_wrp:
            raise BuildError(f"Post-pack WRP verification failed. WRP is outside the packed source drive: {wrp_file}")
        rel_wrp = rel_wrp.replace(os.sep, WIN_SEP)
        key = rel_wrp.lower()
        entry = entries_by_name.get(key)

        if not entry:
            raise BuildError(f"Post-pack WRP verification failed. WRP is missing from PBO: {rel_wrp}")

        matches, reason = pbo_entry_bytes_match_file(pbo_path, entry, wrp_file)

        if not matches:
            raise BuildError(f"Post-pack WRP verification failed for {rel_wrp}: {reason}")

        wrp_entry_names.add(key)
        log(f"Post-pack WRP verification OK: {rel_wrp} ({entry.data_size:,} bytes)")

        original_wrp = os.path.join(original_source_dir, rel_wrp.replace(WIN_SEP, os.sep))

        if os.path.isfile(original_wrp) and not files_have_same_content(original_wrp, wrp_file):
            log(f"Post-pack WRP note: processed WRP differs from original source, likely from Binarize: {rel_wrp}")

    config_files = collect_config_cpp_files(original_source_dir, extra_patterns)
    worldname_refs = find_worldname_references(config_files, original_source_dir, project_root)

    if not worldname_refs:
        log("WARNING: WRP is packed, but no active worldName .wrp reference was found in addon configs.")
        return

    for config_cpp, world_ref, line_number in worldname_refs:
        source_location = format_source_location(config_cpp, original_source_dir, line_number)
        expected_entry = worldname_to_pbo_entry_name(world_ref, prefix, original_source_dir, project_root)

        if not expected_entry:
            log(f"WARNING: Could not map worldName to a packed PBO entry in {source_location}: {normalize_reference_path(world_ref)}")
            continue

        expected_key = expected_entry.lower()

        if expected_key not in entries_by_name:
            raise BuildError(f"Post-pack WRP verification failed. worldName in {source_location} points to missing PBO entry: {normalize_reference_path(world_ref)} -> {expected_entry}")

        if expected_key not in wrp_entry_names:
            raise BuildError(f"Post-pack WRP verification failed. worldName in {source_location} points to a non-WRP or unexpected entry: {expected_entry}")

        log(f"Post-pack worldName verification OK: {normalize_reference_path(world_ref)} -> {expected_entry}")


def verify_published_output(pbo_path, sign_pbos, log):
    if not os.path.isfile(pbo_path):
        raise BuildError(f"Published output verification failed. PBO is missing: {pbo_path}")

    if sign_pbos and not find_new_signature_for_pbo(pbo_path):
        raise BuildError(f"Published output verification failed. Signature is missing for: {pbo_path}")

    log("Published output verification OK.")


def parse_tool_output_summary(tool_name, lines):
    summary = {
        "errors": 0,
        "warnings": 0,
        "missing": 0,
        "model": 0,
        "texture": 0,
    }

    for line in lines:
        lower = line.lower()

        if "error" in lower or "cannot" in lower or "failed" in lower or "bad version" in lower:
            summary["errors"] += 1

        if "warning" in lower or "unsupported" in lower:
            summary["warnings"] += 1

        if "missing" in lower or "cannot open" in lower or "cannot load" in lower:
            summary["missing"] += 1

        if "model" in lower or "model.cfg" in lower or "skeleton" in lower:
            summary["model"] += 1

        if "texture" in lower or ".paa" in lower or ".rvmat" in lower:
            summary["texture"] += 1

    return summary


def log_tool_output_summary(tool_name, lines, log):
    summary = parse_tool_output_summary(tool_name, lines)
    log("")
    log(f"{tool_name} output summary:")
    log(f"  Errors / critical lines: {summary['errors']}")
    log(f"  Warnings:                {summary['warnings']}")
    log(f"  Missing references:      {summary['missing']}")
    log(f"  Model-related lines:     {summary['model']}")
    log(f"  Texture/material lines:  {summary['texture']}")
    log("")
    return summary



def run_dayz_binarize(source_dir, binarized_output_dir, binarize_exe, project_root, temp_dir, max_processes, exclude_file, log, addon_name="", addon_folders=None):
    if os.path.exists(binarized_output_dir):
        shutil.rmtree(binarized_output_dir)
    os.makedirs(binarized_output_dir, exist_ok=True)
    project_root_arg = normalize_project_root_arg(project_root)
    working_dir = normalize_working_dir(project_root)
    binpath = working_dir
    addon_folders = addon_folders or [project_root_arg]
    source_name = addon_name or os.path.basename(os.path.normpath(source_dir)) or "addon"
    texture_temp_dir = os.path.join(temp_dir, "addons", get_safe_temp_name(source_name), "textures")
    if os.path.isdir(texture_temp_dir):
        shutil.rmtree(texture_temp_dir)
    os.makedirs(texture_temp_dir, exist_ok=True)
    cmd = [binarize_exe, "-targetBonesInterval=56", f"-maxProcesses={max_processes}", "-always", "-silent"]
    for addon_folder in addon_folders:
        cmd.append(f"-addon={normalize_project_root_arg(addon_folder)}")
    cmd.extend([f"-textures={texture_temp_dir}", f"-binpath={binpath}"])
    if exclude_file:
        cmd.append(f"-exclude={exclude_file}")
    cmd.extend([source_dir, binarized_output_dir])
    log("")
    log("Binarizing addon files:")
    log(f"  Source:       {source_dir}")
    log(f"  Output:       {binarized_output_dir}")
    log(f"  Project root: {project_root_arg}")
    log(f"  Bin path:     {binpath}")
    log("  Addon scan folders:")
    for addon_folder in addon_folders:
        log(f"    - {normalize_project_root_arg(addon_folder)}")
    log(f"  Texture temp: {texture_temp_dir}")
    log("")
    result = run_hidden_text_subprocess(cmd, cwd=working_dir if os.path.isdir(working_dir) else None)
    output_lines = result.stdout.splitlines() if result.stdout else []
    if output_lines:
        for line in output_lines:
            log(line)
    else:
        log("Binarize returned no output.")
    log_tool_output_summary("Binarize", output_lines, log)
    if result.returncode != 0:
        raise BuildError(f"Binarize failed with exit code {result.returncode}: {source_dir}")
    return parse_tool_output_summary("Binarize", output_lines)

def run_cfgconvert_to_bin(staging_dir, cfgconvert_exe, log, extra_patterns=None):
    if not os.path.isdir(staging_dir):
        raise BuildError(f"Staging folder does not exist: {staging_dir}")
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise BuildError("CfgConvert.exe not found. Select the DayZ Tools CfgConvert.exe path.")
    config_files = []
    for root, dirs, files in os.walk(staging_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if file.lower() == "config.cpp":
                config_files.append(os.path.join(root, file))
    if not config_files:
        log("No included config.cpp found. Skipping CPP to BIN.")
        return
    config_files.sort(key=lambda path: (try_relpath(path, staging_dir) or path).lower())
    log("")
    log(f"Converting {len(config_files)} config.cpp file(s) to config.bin:")
    for config_cpp in config_files:
        config_dir = os.path.dirname(config_cpp)
        config_bin = os.path.join(config_dir, "config.bin")
        rel_config = try_relpath(config_cpp, staging_dir)
        rel_config = rel_config.replace(os.sep, WIN_SEP) if rel_config else config_cpp
        rel_bin = try_relpath(config_bin, staging_dir)
        rel_bin = rel_bin.replace(os.sep, WIN_SEP) if rel_bin else config_bin
        if os.path.isfile(config_bin):
            os.remove(config_bin)
        cmd = [cfgconvert_exe, "-bin", "-dst", config_bin, config_cpp]
        log("")
        log(f"Converting: {rel_config} -> {rel_bin}")
        result = run_hidden_text_subprocess(cmd, cwd=config_dir)
        output_lines = result.stdout.splitlines() if result.stdout else []

        if output_lines:
            log(f"CfgConvert output for {rel_config}:")

            for line in output_lines:
                log("  " + line)
        else:
            log(f"CfgConvert returned no output for {rel_config}.")

        if result.returncode != 0 or not os.path.isfile(config_bin):
            reason = f"exit code {result.returncode}" if result.returncode != 0 else "config.bin was not produced"
            raise BuildError(f"CfgConvert failed while converting {rel_config} ({reason}). Staged path: {config_cpp}")
        os.remove(config_cpp)
        log(f"Removed source config.cpp from staging: {rel_config}")


def collect_paa_update_jobs(source_dir, staging_dir, extra_patterns=None):
    jobs_by_target = {}

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            source_ext = os.path.splitext(file)[1].lower()

            if source_ext not in PAA_SOURCE_TEXTURE_EXTENSIONS:
                continue

            source_file = os.path.join(root, file)
            rel_source = try_relpath(source_file, source_dir)
            if not rel_source:
                continue
            rel_paa = os.path.splitext(rel_source)[0] + ".paa"
            target_paa = os.path.join(staging_dir, rel_paa)

            try:
                source_mtime = os.path.getmtime(source_file)
            except OSError:
                continue

            key = rel_paa.replace(os.sep, WIN_SEP).lower()
            existing = jobs_by_target.get(key)

            if existing and existing["source_mtime"] >= source_mtime:
                continue

            jobs_by_target[key] = {
                "source": source_file,
                "target": target_paa,
                "rel_source": rel_source.replace(os.sep, WIN_SEP),
                "rel_paa": rel_paa.replace(os.sep, WIN_SEP),
                "source_mtime": source_mtime,
            }

    jobs = []

    for job in jobs_by_target.values():
        target_paa = job["target"]

        if os.path.isfile(target_paa):
            try:
                if job["source_mtime"] <= os.path.getmtime(target_paa):
                    continue
            except OSError:
                pass

        jobs.append(job)

    jobs.sort(key=lambda item: item["rel_paa"].lower())
    return jobs


def run_imagetopaa_to_paa(imagetopaa_exe, source_file, target_paa, log):
    if not imagetopaa_exe or not os.path.isfile(imagetopaa_exe):
        raise BuildError("ImageToPAA.exe not found. Select the DayZ Tools ImageToPAA.exe path.")

    if not os.path.isfile(source_file):
        raise BuildError(f"Source texture does not exist: {source_file}")

    target_dir = os.path.dirname(target_paa)
    os.makedirs(target_dir, exist_ok=True)
    temp_paa = os.path.join(target_dir, f".{os.path.splitext(os.path.basename(target_paa))[0]}.{os.getpid()}_{time.time_ns()}.tmp.paa")

    if os.path.isfile(temp_paa):
        os.remove(temp_paa)

    cmd = [imagetopaa_exe, source_file, temp_paa]
    result = run_hidden_text_subprocess(cmd, cwd=os.path.dirname(source_file))
    output = result.stdout or ""

    if output:
        for line in output.splitlines():
            log(line)

    if result.returncode != 0 or not os.path.isfile(temp_paa):
        if os.path.isfile(temp_paa):
            try:
                os.remove(temp_paa)
            except OSError:
                pass
        raise BuildError(f"ImageToPAA failed with exit code {result.returncode}: {source_file}")

    os.replace(temp_paa, target_paa)


def update_staging_paa_from_source_textures(source_dir, staging_dir, imagetopaa_exe, log, extra_patterns=None):
    if not os.path.isdir(staging_dir):
        raise BuildError(f"Staging folder does not exist for PAA update: {staging_dir}")

    jobs = collect_paa_update_jobs(source_dir, staging_dir, extra_patterns)

    if not jobs:
        log("No stale or missing .paa files found for source textures.")
        return 0

    log("")
    log(f"Updating {len(jobs)} .paa file(s) from newer/missing source textures:")

    converted = 0

    for job in jobs:
        log(f"Converting: {job['rel_source']} -> {job['rel_paa']}")
        run_imagetopaa_to_paa(imagetopaa_exe, job["source"], job["target"], log)
        converted += 1

    log(f"Updated {converted} .paa file(s) in staging.")
    return converted


def build_all(settings, log, progress_callback):
    start = time.time()
    original_log = log
    diagnostic_log_lines = []

    def capture_log(message):
        text = str(message)
        diagnostic_log_lines.append(text)
        if len(diagnostic_log_lines) > DIAGNOSTIC_CONTEXT_LIMIT:
            del diagnostic_log_lines[:-DIAGNOSTIC_CONTEXT_LIMIT]
        original_log(message)

    log = capture_log
    source_root = os.path.normpath(settings["source_root"])
    output_root = os.path.normpath(settings["output_root_dir"])
    output_addons_dir = os.path.join(output_root, "Addons")
    output_keys_dir = os.path.join(output_root, "Keys")
    temp_root = os.path.normpath(settings["temp_dir"])
    if not os.path.isdir(source_root):
        raise BuildError(f"Project Source is not a directory: {source_root}")
    os.makedirs(output_addons_dir, exist_ok=True)
    os.makedirs(output_keys_dir, exist_ok=True)
    ensure_builder_temp_root(temp_root, log, source_root, output_root)

    use_binarize = settings["use_binarize"]
    convert_config = settings["convert_config"]
    sign_pbos = settings["sign_pbos"]
    update_paa_from_sources = bool(settings.get("update_paa_from_sources", False))
    binarize_exe = settings["binarize_exe"]
    cfgconvert_exe = settings["cfgconvert_exe"]
    imagetopaa_exe = settings.get("imagetopaa_exe", "")
    dssignfile_exe = settings["dssignfile_exe"]
    private_key = settings["private_key"]
    exclude_patterns = settings["exclude_patterns"]
    exclude_pattern_list = parse_exclude_patterns(exclude_patterns)
    project_root = settings["project_root"]
    pbo_name = settings["pbo_name"]
    max_processes = settings["max_processes"]
    binarize_addon_folders = []
    selected_addons = set(settings.get("selected_addons", []))
    force_rebuild = bool(settings.get("force_rebuild", False))
    preflight_before_build = bool(settings.get("preflight_before_build", False))
    exclude_file = ""

    log(f"Build Output:   {output_root}")
    log(f"Output Addons: {output_addons_dir}")
    log(f"Output Keys:   {output_keys_dir}")
    log(f"Force rebuild {'enabled' if force_rebuild else 'disabled'}. Temp: {temp_root}")
    log("Content-safe checks enabled internally. File contents are hashed for cache/staging checks.")
    log("Using per-build SHA1 cache for repeated file fingerprints. Source hashes are not persisted across runs.")
    log(f"Detected total logical CPU threads: {os.cpu_count() or 'unknown'}")
    log(f"Detected available logical threads: {get_available_logical_threads()}")
    log(f"Configured Binarize max processes: {max_processes}")
    if use_binarize:
        if not binarize_exe or not os.path.isfile(binarize_exe):
            raise BuildError("binarize.exe not found. Select the DayZ Tools binarize.exe path.")
        log(f"Using binarize.exe: {binarize_exe}")
        binarize_addon_folders = get_binarize_addon_folders(settings, log)
        log("Configured Binarize addon scan folders:")
        for addon_folder in binarize_addon_folders:
            log(f"  - {addon_folder}")
        exclude_file = create_temp_exclude_file(temp_root, exclude_patterns, log)
        if not exclude_file:
            log("No exclude file will be passed to Binarize. Binarize uses the filtered staging folder instead.")
    if convert_config:
        if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
            raise BuildError("CfgConvert.exe not found. Select the DayZ Tools CfgConvert.exe path.")
        log(f"Using CfgConvert.exe: {cfgconvert_exe}")
    if update_paa_from_sources:
        if not imagetopaa_exe or not os.path.isfile(imagetopaa_exe):
            raise BuildError("ImageToPAA.exe not found. Select the DayZ Tools ImageToPAA.exe path or disable Update PAA.")
        log(f"Using ImageToPAA.exe: {imagetopaa_exe}")
    if sign_pbos:
        if not dssignfile_exe or not os.path.isfile(dssignfile_exe):
            raise BuildError("DSSignFile.exe not found. Select the DayZ Tools DSSignFile.exe path.")
        if not private_key or not os.path.isfile(private_key):
            raise BuildError("Private key not found. Select your .biprivatekey file.")
        log(f"Using DSSignFile.exe: {dssignfile_exe}")
        log(f"Using private key: {os.path.basename(private_key)}")

    all_targets = detect_addon_targets(source_root, output_addons_dir, exclude_pattern_list)
    targets = [(name, path) for name, path in all_targets if name in selected_addons] if selected_addons else []
    if not targets:
        raise BuildError("No addon targets selected.")
    log(f"Found {len(all_targets)} addon target(s). Selected {len(targets)} for build.")

    if preflight_before_build:
        log("Preflight before build enabled. Running checks before packing.")
        preflight = run_preflight_for_targets(settings, targets, log, progress_callback)
        if preflight.errors > 0:
            raise BuildError(f"Preflight failed with {preflight.errors} error(s). Build aborted.")
        log(f"Preflight completed with {preflight.warnings} warning(s). Continuing build." if preflight.warnings else "Preflight completed without errors or warnings. Continuing build.")

    cache = load_build_cache()
    build_hash_cache = {}
    cache_key_root = os.path.abspath(source_root).lower()
    source_cache = cache.setdefault(cache_key_root, {})
    summary = {"built": 0, "skipped": 0, "signed": 0, "failed": 0, "keys_copied": 0, "p3d_fallbacks": 0, "paa_updates": 0, "targets": len(targets), "log_file": settings.get("log_file", "")}
    jobs = []

    if force_rebuild:
        log("Force rebuild enabled. Cache will be ignored for selected addons.")

    for index, (folder_name, folder_path) in enumerate(targets, start=1):
        progress_callback(index - 1, len(targets))
        log("")
        log("=" * 80)
        log(f"Preparing addon {index}/{len(targets)}: {folder_name}")
        log("=" * 80)
        pbo_base_name = get_pbo_base_name(folder_name, pbo_name, len(targets))
        output_pbo = os.path.join(output_addons_dir, pbo_base_name + ".pbo")
        prefix = get_effective_pbo_prefix(pbo_base_name, folder_path, project_root, exclude_pattern_list, log)
        state_hash = compute_addon_state_hash(folder_path, prefix, settings, exclude_pattern_list, build_hash_cache)
        can_skip = (not force_rebuild and source_cache.get(folder_name, {}).get("hash") == state_hash and os.path.isfile(output_pbo) and (not sign_pbos or find_new_signature_for_pbo(output_pbo)))
        if can_skip:
            log(f"Skipping {folder_name} - no changes detected.")
            summary["skipped"] += 1
            continue
        addon_temp_root = get_addon_temp_root(temp_root, folder_name)
        if force_rebuild:
            for subfolder in ["staging", "binarized", "textures", "configs"]:
                path = os.path.join(addon_temp_root, subfolder)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    log(f"Force rebuild: removed selected addon temp folder only: {path}")
        folder_has_p3d = use_binarize and has_p3d_files(folder_path, exclude_pattern_list)
        folder_has_binarizable_p3d = use_binarize and has_binarizable_p3d_files(folder_path, exclude_pattern_list)
        folder_has_wrp = use_binarize and has_wrp_files(folder_path, exclude_pattern_list)
        folder_needs_binarize = use_binarize and (folder_has_binarizable_p3d or folder_has_wrp)
        needs_staging = convert_config or folder_needs_binarize or update_paa_from_sources
        pack_source = folder_path
        staging_dir = ""
        binarized_dir = ""
        if needs_staging:
            staging_dir = os.path.join(addon_temp_root, "staging")
            log("Copying source to staging folder...")
            copy_source_to_staging(folder_path, staging_dir, exclude_pattern_list, log, True, folder_needs_binarize)
            pack_source = staging_dir
        if folder_needs_binarize:
            binarized_dir = os.path.join(addon_temp_root, "binarized")
        elif use_binarize:
            if folder_has_p3d:
                log("Only already-binarized ODOL P3D files found. Skipping Binarize for this addon.")
            else:
                log("No P3D or WRP files found. Skipping Binarize for this addon.")
        output_work_dir = create_output_work_dir(output_pbo, folder_name)
        jobs.append({"folder_name": folder_name, "folder_path": folder_path, "output_pbo": output_pbo, "temp_output_pbo": os.path.join(output_work_dir, os.path.basename(output_pbo)), "output_work_dir": output_work_dir, "prefix": prefix, "pack_source": pack_source, "folder_has_p3d": folder_has_p3d, "folder_has_binarizable_p3d": folder_has_binarizable_p3d, "folder_has_wrp": folder_has_wrp, "folder_needs_binarize": folder_needs_binarize, "staging_dir": staging_dir, "binarized_dir": binarized_dir, "binarize_source": staging_dir if folder_needs_binarize and staging_dir else folder_path, "state_hash": state_hash})

    for build_index, job in enumerate(jobs, start=1):
        progress_callback(build_index - 1, len(jobs))
        log("")
        log("=" * 80)
        log(f"Packing addon {build_index}/{len(jobs)}: {job['folder_name']}")
        log("=" * 80)
        try:
            if update_paa_from_sources:
                summary["paa_updates"] += update_staging_paa_from_source_textures(job["folder_path"], job["pack_source"], imagetopaa_exe, log, exclude_pattern_list)
            if job["staging_dir"] and (job["folder_needs_binarize"] or convert_config):
                ensure_config_include_files_in_staging(job["folder_path"], job["pack_source"], project_root, log, exclude_pattern_list)
            if use_binarize and job["folder_needs_binarize"]:
                binarize_source = job["binarize_source"]
                wrp_binarize_context = None

                if job["folder_has_wrp"]:
                    binarize_source, wrp_binarize_context = prepare_wrp_binarize_source(job["staging_dir"], job["folder_path"], job["prefix"], project_root, log)

                try:
                    log("Running Binarize against project-aware source folder..." if job["folder_has_wrp"] else "Running Binarize against filtered staging folder...")
                    run_dayz_binarize(binarize_source, job["binarized_dir"], binarize_exe, project_root, temp_root, max_processes, exclude_file, log, job["folder_name"], binarize_addon_folders)
                    if job["folder_has_wrp"]:
                        validate_binarized_wrp_outputs(job["staging_dir"], job["binarized_dir"], log, exclude_pattern_list)
                finally:
                    cleanup_wrp_binarize_source(wrp_binarize_context, log)
                log("Overlaying binarized files onto staging folder...")
                overlay_tree(job["binarized_dir"], job["staging_dir"], None, log)
                if job["folder_has_p3d"]:
                    fallback_count = ensure_p3d_files_in_staging(job["folder_path"], job["staging_dir"], log, exclude_pattern_list)
                    summary["p3d_fallbacks"] += fallback_count
            if convert_config:
                ensure_config_cpp_files_in_staging(job["folder_path"], job["pack_source"], log, exclude_pattern_list)
                run_cfgconvert_to_bin(job["pack_source"], cfgconvert_exe, log, exclude_pattern_list)
            verify_pack_source_before_packing(job["folder_path"], job["pack_source"], convert_config, log, exclude_pattern_list)
            log(f"PBO Name:   {os.path.basename(job['output_pbo'])}")
            log(f"PBO prefix: {job['prefix']}")
            pack_pbo(job["pack_source"], job["temp_output_pbo"], job["prefix"], log, exclude_pattern_list)
            verify_packed_pbo(job["temp_output_pbo"], job["prefix"], log)
            verify_packed_wrp_entries(job["temp_output_pbo"], job["pack_source"], job["folder_path"], job["prefix"], project_root, exclude_pattern_list, log)
            if sign_pbos:
                wait_for_file_ready(job["temp_output_pbo"], log)
                run_dssignfile(dssignfile_exe, private_key, job["temp_output_pbo"], log)
                summary["signed"] += 1
            replace_output_artifacts(job["temp_output_pbo"], job["output_pbo"], sign_pbos, log)
            verify_published_output(job["output_pbo"], sign_pbos, log)
            cleanup_output_work_dir(job["output_work_dir"], log)
            summary["built"] += 1
            if sign_pbos:
                if copy_bikey_to_keys(private_key, output_keys_dir, log):
                    summary["keys_copied"] += 1
            source_cache[job["folder_name"]] = {"hash": job["state_hash"], "pbo": job["output_pbo"], "updated": datetime.now().isoformat(timespec="seconds")}
            save_build_cache(cache)
        except Exception as error:
            summary["failed"] += 1
            cleanup_output_work_dir(job.get("output_work_dir", ""), log)
            log_build_failure_diagnostics(str(error), diagnostic_log_lines, log)
            raise

    progress_callback(len(targets), len(targets))
    save_build_cache(cache)
    elapsed = time.time() - start
    log("")
    log("=" * 80)
    log("Build summary")
    log("=" * 80)
    log(f"Targets:       {summary['targets']}")
    log(f"Built:         {summary['built']}")
    log(f"Skipped:       {summary['skipped']}")
    log(f"Signed:        {summary['signed']}")
    log(f"Keys copied:   {summary['keys_copied']}")
    log(f"P3D fallbacks: {summary['p3d_fallbacks']}")
    log(f"PAA updates:   {summary['paa_updates']}")
    log(f"Failed:        {summary['failed']}")
    log(f"Time:          {format_duration(elapsed)}")
    if settings.get("log_file"):
        log(f"Log:         {settings.get('log_file')}")
    log("=" * 80)
    log("")
    log("Build finished.")
    return summary
