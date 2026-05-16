import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from pbo_core import PboError, read_pbo_archive
from rag_builder_common import (
    BuildError,
    COPY_CHUNK_SIZE,
    WIN_SEP,
    format_duration,
    get_hidden_startupinfo,
    get_pbo_prefix,
    get_safe_temp_name,
    get_subprocess_creationflags,
    normalize_working_dir,
    parse_exclude_patterns,
    read_pbo_prefix_file,
    should_skip_dir,
    should_skip_file,
    source_file_should_be_staged,
)
from rag_builder_storage import get_app_data_dir, load_build_cache, save_build_cache
from rag_pbo_writer import pack_pbo, pbo_entry_bytes_match_file, verify_packed_pbo
from rag_preflight import (
    TERRAIN_SOURCE_FOLDER_NAMES,
    collect_config_cpp_files,
    collect_wrp_files,
    find_worldname_references,
    format_source_location,
    infer_terrain_pbo_prefix_from_worldname,
    is_path_inside,
    normalize_reference_path,
    resolve_reference_path,
    run_preflight_for_targets,
)

TEMP_MARKER_FILE = ".rag_pbo_builder_temp"
BUILDER_TEMP_CHILDREN = {"addons", "preflight", "staging", "binarized", "configs", "_binarize_textures"}
PAA_SOURCE_TEXTURE_EXTENSIONS = {".png", ".tga"}
RVMAT_REWRITE_SOURCE_EXTENSIONS = ("png", "tga")

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
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not source_file_should_be_staged(file, extra_patterns):
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
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
            rel = os.path.relpath(staged_file, staging_dir).replace(os.sep, WIN_SEP).lower()
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
        rel_root = os.path.relpath(root, source_dir)
        target_root = destination_dir if rel_root == "." else os.path.join(destination_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for file in files:
            if os.path.splitext(file)[1].lower() in skip_extensions:
                skipped += 1
                if log:
                    rel_file = os.path.relpath(os.path.join(root, file), source_dir).replace(os.sep, WIN_SEP)
                    log(f"Skipped Binarize overlay for protected file: {rel_file}")
                continue
            shutil.copy2(os.path.join(root, file), os.path.join(target_root, file))
            copied += 1

    if log:
        log(f"Binarize overlay: copied={copied}, skipped={skipped}")


def ensure_p3d_files_in_staging(source_dir, staging_dir, log, extra_patterns=None):
    copied = already_present = skipped = 0
    os.makedirs(staging_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        for file in files:
            if not file.lower().endswith(".p3d"):
                continue
            if should_skip_file(file, extra_patterns):
                skipped += 1
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
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
        before = len(dirs)
        dirs[:] = [d for d in dirs if not should_skip_dir(d, extra_patterns)]
        skipped_dirs += before - len(dirs)
        for file in files:
            if file.lower() != "config.cpp":
                continue
            source_file = os.path.join(root, file)
            rel = os.path.relpath(source_file, source_dir)
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


def get_effective_pbo_prefix(pbo_base_name, folder_path, project_root, extra_patterns, log=None):
    prefix = get_pbo_prefix(pbo_base_name, folder_path)

    if read_pbo_prefix_file(folder_path):
        return prefix

    wrp_files = collect_wrp_files(folder_path, extra_patterns)

    if not wrp_files:
        return prefix

    config_files = collect_config_cpp_files(folder_path, extra_patterns)
    worldname_refs = find_worldname_references(config_files, folder_path, project_root)
    inferred_prefix = infer_terrain_pbo_prefix_from_worldname(folder_path, wrp_files, worldname_refs)

    if inferred_prefix and inferred_prefix.lower() != prefix.lower():
        if log:
            log(f"Terrain worldName implies PBO prefix '{inferred_prefix}'. Using it instead of fallback prefix '{prefix}'.")
        return inferred_prefix

    return prefix


def normalize_project_root_arg(project_root):
    return project_root.rstrip(WIN_SEP + "/")


def find_tool(possible):
    for path in possible:
        if Path(path).is_file():
            return str(path)
    return ""


def find_dayz_binarize():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"])


def find_cfgconvert():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe"])


def find_imagetopaa():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/ImageToPAA/ImageToPAA.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/ImageToPAA/ImageToPAA.exe"])


def find_dssignfile():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool([Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/DSUtils/DSSignFile.exe", Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe", Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/DSSignFile/DSSignFile.exe"])


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
        result = subprocess.run(cmd, cwd=str(work_dir), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
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
    risky = {"steam", "steamapps", "common", "dayz tools", "dayz", "program files", "program files (x86)", "windows"}
    if {part.lower() for part in root_path.parts}.intersection(risky):
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
        "rewrite_rvmat_texture_refs": bool(settings.get("rewrite_rvmat_texture_refs", False)),
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
            rel = os.path.relpath(full, source_dir).replace(os.sep, WIN_SEP).lower()
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
        rel = os.path.relpath(remaining_config_cpp[0], pack_source).replace(os.sep, WIN_SEP)
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
        return os.path.relpath(resolved, addon_source_dir).replace(os.sep, WIN_SEP)

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
        rel_wrp = os.path.relpath(wrp_file, pack_source).replace(os.sep, WIN_SEP)
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



def run_dayz_binarize(source_dir, binarized_output_dir, binarize_exe, project_root, temp_dir, max_processes, exclude_file, log, addon_name=""):
    if os.path.exists(binarized_output_dir):
        shutil.rmtree(binarized_output_dir)
    os.makedirs(binarized_output_dir, exist_ok=True)
    project_root_arg = normalize_project_root_arg(project_root)
    working_dir = normalize_working_dir(project_root)
    binpath = str(Path(binarize_exe).parent)
    source_name = addon_name or os.path.basename(os.path.normpath(source_dir)) or "addon"
    texture_temp_dir = os.path.join(temp_dir, "addons", get_safe_temp_name(source_name), "textures")
    if os.path.isdir(texture_temp_dir):
        shutil.rmtree(texture_temp_dir)
    os.makedirs(texture_temp_dir, exist_ok=True)
    cmd = [binarize_exe, "-targetBonesInterval=56", f"-maxProcesses={max_processes}", "-always", "-silent", f"-addon={project_root_arg}", f"-textures={texture_temp_dir}", f"-binpath={binpath}"]
    if exclude_file:
        cmd.append(f"-exclude={exclude_file}")
    cmd.extend([source_dir, binarized_output_dir])
    log("")
    log("Binarizing P3D files:")
    log(f"  Source:       {source_dir}")
    log(f"  Output:       {binarized_output_dir}")
    log(f"  Project root: {project_root_arg}")
    log(f"  Texture temp: {texture_temp_dir}")
    log("")
    result = subprocess.run(cmd, cwd=working_dir if os.path.isdir(working_dir) else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
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
    config_files.sort(key=lambda path: os.path.relpath(path, staging_dir).lower())
    log("")
    log(f"Converting {len(config_files)} config.cpp file(s) to config.bin:")
    for config_cpp in config_files:
        config_dir = os.path.dirname(config_cpp)
        config_bin = os.path.join(config_dir, "config.bin")
        rel_config = os.path.relpath(config_cpp, staging_dir).replace(os.sep, WIN_SEP)
        rel_bin = os.path.relpath(config_bin, staging_dir).replace(os.sep, WIN_SEP)
        if os.path.isfile(config_bin):
            os.remove(config_bin)
        cmd = [cfgconvert_exe, "-bin", "-dst", config_bin, config_cpp]
        log("")
        log(f"Converting: {rel_config} -> {rel_bin}")
        result = subprocess.run(cmd, cwd=config_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
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
            rel_source = os.path.relpath(source_file, source_dir)
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
    result = subprocess.run(cmd, cwd=os.path.dirname(source_file), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())
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


def staged_paa_reference_exists(paa_ref, rvmat_dir, staging_dir):
    normalized = paa_ref.replace("\\", "/").lstrip("/")
    segments = [segment for segment in normalized.split("/") if segment]

    if not segments:
        return False

    candidates = [
        os.path.join(rvmat_dir, *segments),
        os.path.join(staging_dir, *segments),
    ]

    if len(segments) > 1:
        # Handles prefix/project-root-rooted refs like "\MyMod\data\tex_co.paa"
        # where the staging root maps to the addon prefix.
        candidates.append(os.path.join(staging_dir, *segments[1:]))

    return any(os.path.isfile(candidate) for candidate in candidates)


def rewrite_staging_rvmat_texture_refs(staging_dir, log, extra_patterns=None):
    if not os.path.isdir(staging_dir):
        raise BuildError(f"Staging folder does not exist for RVMAT rewrite: {staging_dir}")

    rvmat_files = []

    for root, dirs, files in os.walk(staging_dir):
        dirs[:] = [directory for directory in dirs if not should_skip_dir(directory, extra_patterns)]

        for file in files:
            if os.path.splitext(file)[1].lower() == ".rvmat":
                rvmat_files.append(os.path.join(root, file))

    if not rvmat_files:
        log("No .rvmat files found for texture reference rewrite.")
        return 0

    rvmat_files.sort(key=lambda path: os.path.relpath(path, staging_dir).lower())
    pattern = re.compile(r'(["\'])([^"\']+?)\.(' + "|".join(RVMAT_REWRITE_SOURCE_EXTENSIONS) + r')\1', re.IGNORECASE)
    rewritten = 0

    log("")
    log(f"Rewriting .png/.tga texture references to .paa in {len(rvmat_files)} .rvmat file(s):")

    for rvmat_path in rvmat_files:
        rel = os.path.relpath(rvmat_path, staging_dir).replace(os.sep, WIN_SEP)

        try:
            with open(rvmat_path, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue

        # Already-rapified RVMATs store references in binary; skip them.
        if raw[:4] == b"\x00raP":
            continue

        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
            encoding = "latin-1"

        rvmat_dir = os.path.dirname(rvmat_path)

        def replace(match):
            quote, path_body = match.group(1), match.group(2)
            paa_ref = path_body + ".paa"

            # Conservative: only rewrite when the target .paa exists in staging,
            # so deliberately-source refs and dangling refs are left untouched.
            if not staged_paa_reference_exists(paa_ref, rvmat_dir, staging_dir):
                return match.group(0)

            return quote + paa_ref + quote

        new_text = pattern.sub(replace, text)

        if new_text != text:
            with open(rvmat_path, "w", encoding=encoding, newline="") as handle:
                handle.write(new_text)
            log(f"Rewrote texture references to .paa: {rel}")
            rewritten += 1

    log(f"Rewrote texture references in {rewritten} .rvmat file(s).")
    return rewritten


def build_all(settings, log, progress_callback):
    start = time.time()
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
    rewrite_rvmat_texture_refs = bool(settings.get("rewrite_rvmat_texture_refs", False))
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
    if rewrite_rvmat_texture_refs:
        log("RVMAT texture reference rewrite enabled: .png/.tga refs will be retargeted to .paa in staging.")
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
    summary = {"built": 0, "skipped": 0, "signed": 0, "failed": 0, "keys_copied": 0, "p3d_fallbacks": 0, "paa_updates": 0, "rvmat_rewrites": 0, "targets": len(targets), "log_file": settings.get("log_file", "")}
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
        needs_staging = convert_config or folder_needs_binarize or update_paa_from_sources or rewrite_rvmat_texture_refs
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
            if rewrite_rvmat_texture_refs:
                summary["rvmat_rewrites"] += rewrite_staging_rvmat_texture_refs(job["pack_source"], log, exclude_pattern_list)
            if use_binarize and job["folder_needs_binarize"]:
                log("Running Binarize against filtered staging folder...")
                run_dayz_binarize(job["binarize_source"], job["binarized_dir"], binarize_exe, project_root, temp_root, max_processes, exclude_file, log, job["folder_name"])
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
        except Exception:
            summary["failed"] += 1
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
    log(f"RVMAT rewrites: {summary['rvmat_rewrites']}")
    log(f"Failed:        {summary['failed']}")
    log(f"Time:          {format_duration(elapsed)}")
    if settings.get("log_file"):
        log(f"Log:         {settings.get('log_file')}")
    log("=" * 80)
    log("")
    log("Build finished.")
    return summary
