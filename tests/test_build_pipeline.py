import os

from pbo_core import read_pbo_archive, read_pbo_entry_data
from rag_builder_common import BuildError, run_hidden_text_subprocess
from rag_build_pipeline import (
    build_all,
    copy_source_to_staging,
    detect_addon_targets,
    ensure_config_include_files_in_staging,
    ensure_p3d_files_in_staging,
    get_effective_pbo_prefix,
    get_project_relative_pbo_prefix,
    get_build_failure_diagnostics,
    has_binarizable_p3d_files,
    parse_binarize_addon_folders,
    parse_steam_libraryfolders,
    prepare_wrp_binarize_source,
    cleanup_wrp_binarize_source,
    run_dayz_binarize,
    validate_binarized_wrp_outputs,
)


def test_detect_addon_targets_skips_terrain_source_folder(tmp_path):
    source = tmp_path / "project"
    addon = source / "MapWorld"
    source_folder = source / "source"
    output_addons = tmp_path / "output" / "Addons"
    addon.mkdir(parents=True)
    source_folder.mkdir()

    targets = detect_addon_targets(str(source), str(output_addons), [])

    assert ("MapWorld", str(addon)) in targets
    assert all(name.lower() != "source" for name, _ in targets)


def test_effective_pbo_prefix_uses_project_relative_terrain_worldname(tmp_path):
    addon = tmp_path / "outpost" / "world"
    addon.mkdir(parents=True)
    (addon / "outpost.wrp").write_bytes(b"wrp")
    (addon / "config.cpp").write_text(
        r"""
class CfgPatches
{
    class outpost_world
    {
        requiredAddons[] = {};
    };
};
class CfgWorlds
{
    class CAWorld;
    class outpost: CAWorld
    {
        worldName = "outpost\world\outpost.wrp";
    };
};
class CfgWorldList
{
    class outpost {};
};
""",
        encoding="utf-8",
    )

    logs = []
    prefix = get_effective_pbo_prefix("world", str(addon), str(tmp_path), [], logs.append)

    assert prefix == r"outpost\world"
    assert "Terrain worldName implies PBO prefix 'outpost\\world'" in "\n".join(logs)


def test_effective_pbo_prefix_uses_project_relative_nested_addon_path(tmp_path):
    addon = tmp_path / "rag_baseitems" / "rag_baseitems_scripts"
    addon.mkdir(parents=True)
    (addon / "config.cpp").write_text("class CfgPatches { class rag_baseitems_scripts {}; };", encoding="utf-8")

    logs = []
    prefix = get_effective_pbo_prefix("rag_baseitems_scripts", str(addon), str(tmp_path), [], logs.append)

    assert get_project_relative_pbo_prefix(str(addon), str(tmp_path)) == r"rag_baseitems\rag_baseitems_scripts"
    assert prefix == r"rag_baseitems\rag_baseitems_scripts"
    assert "Project-relative path implies PBO prefix 'rag_baseitems\\rag_baseitems_scripts'" in "\n".join(logs)


def test_odol_p3ds_are_kept_out_of_binarize_staging_then_restored(tmp_path):
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    models = source / "models"
    models.mkdir(parents=True)
    (models / "packed.p3d").write_bytes(b"ODOL already binarized")
    (models / "source.p3d").write_bytes(b"MLOD source model")

    logs = []
    copy_source_to_staging(str(source), str(staging), [], logs.append, True, True)

    assert not (staging / "models" / "packed.p3d").exists()
    assert (staging / "models" / "source.p3d").read_bytes() == b"MLOD source model"
    assert has_binarizable_p3d_files(str(source), []) is True

    copied = ensure_p3d_files_in_staging(str(source), str(staging), logs.append, [])

    assert copied == 1
    assert (staging / "models" / "packed.p3d").read_bytes() == b"ODOL already binarized"


def test_only_odol_p3ds_do_not_require_binarize(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "packed.p3d").write_bytes(b"ODOL already binarized")

    assert has_binarizable_p3d_files(str(source), []) is False


def test_config_includes_are_copied_to_staging_even_when_excluded(tmp_path):
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "config.cpp").write_text(
        """
class CfgWorlds
{
    #include "cfgNavmesh.hpp"
    // #include "commented.hpp"
};
""",
        encoding="utf-8",
    )
    (source / "cfgNavmesh.hpp").write_text('#include "nested/cfgNames.hpp"\n', encoding="utf-8")
    (nested / "cfgNames.hpp").write_text("class Names {};\n", encoding="utf-8")
    (source / "commented.hpp").write_text("class Commented {};\n", encoding="utf-8")

    copy_source_to_staging(str(source), str(staging), ["*.hpp"], None, True)

    assert (staging / "config.cpp").is_file()
    assert not (staging / "cfgNavmesh.hpp").exists()

    logs = []
    copied = ensure_config_include_files_in_staging(str(source), str(staging), str(tmp_path), logs.append, ["*.hpp"])

    assert copied == 2
    assert (staging / "cfgNavmesh.hpp").read_text(encoding="utf-8") == '#include "nested/cfgNames.hpp"\n'
    assert (staging / "nested" / "cfgNames.hpp").read_text(encoding="utf-8") == "class Names {};\n"
    assert not (staging / "commented.hpp").exists()
    assert "Copied config include needed for Binarize/CfgConvert: cfgNavmesh.hpp" in "\n".join(logs)


def test_config_includes_outside_source_are_copied_to_staged_include_path(tmp_path):
    project = tmp_path / "project"
    source = project / "world"
    staging = tmp_path / "staging"
    source.mkdir(parents=True)
    (source / "config.cpp").write_text(
        """
class CfgWorlds
{
    #include "cfgNavmesh.hpp"
};
""",
        encoding="utf-8",
    )
    (project / "cfgNavmesh.hpp").write_text("class CfgNavmesh {};\n", encoding="utf-8")

    logs = []
    copied = ensure_config_include_files_in_staging(str(source), str(staging), str(project), logs.append, ["*.hpp"])

    assert copied == 1
    assert (staging / "cfgNavmesh.hpp").read_text(encoding="utf-8") == "class CfgNavmesh {};\n"
    assert "Copied external config include needed for Binarize/CfgConvert" in "\n".join(logs)


def test_wrp_binarize_source_uses_project_prefix_path(tmp_path, monkeypatch):
    project_root = tmp_path / "P"
    source = tmp_path / "source" / "world"
    staging = tmp_path / "temp" / "staging"
    project_root.mkdir()
    source.mkdir(parents=True)
    staging.mkdir(parents=True)

    created_links = []

    def fake_create_directory_junction(link_path, target_path, cwd):
        created_links.append((link_path, target_path, cwd))
        os.mkdir(link_path)
        return True, ""

    monkeypatch.setattr("rag_build_pipeline.create_directory_junction", fake_create_directory_junction)

    logs = []
    binarize_source, context = prepare_wrp_binarize_source(str(staging), str(source), r"outpost\world", str(project_root), logs.append)

    assert binarize_source == str(project_root / "outpost" / "world")
    assert created_links == [(str(project_root / "outpost" / "world"), str(staging), str(project_root))]
    assert "project-prefix path" in "\n".join(logs)

    cleanup_wrp_binarize_source(context, logs.append)

    assert not (project_root / "outpost").exists()


def test_run_dayz_binarize_uses_project_root_as_binpath(tmp_path, monkeypatch):
    binarize_exe = tmp_path / "tools" / "binarize.exe"
    source = tmp_path / "P" / "outpost" / "world"
    output = tmp_path / "out"
    temp = tmp_path / "temp"
    binarize_exe.parent.mkdir(parents=True)
    source.mkdir(parents=True)
    temp.mkdir()
    binarize_exe.write_text("", encoding="utf-8")
    captured = {}

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return Result()

    monkeypatch.setattr("rag_builder_common.subprocess.run", fake_run)

    addon_scan = [str(tmp_path / "P"), str(tmp_path / "objects")]
    run_dayz_binarize(str(source), str(output), str(binarize_exe), str(tmp_path / "P"), str(temp), 1, "", lambda _message: None, "world", addon_scan)

    assert f"-binpath={tmp_path / 'P'}" in captured["cmd"]
    assert f"-addon={tmp_path / 'P'}" in captured["cmd"]
    assert f"-addon={tmp_path / 'objects'}" in captured["cmd"]
    assert captured["cwd"] == str(tmp_path / "P")


def test_parse_binarize_addon_folders_accepts_common_separators():
    folders = parse_binarize_addon_folders("P:\\dz; P:\\custom\n\"P:\\more\"")

    assert folders == [r"P:\dz", r"P:\custom", r"P:\more"]


def test_parse_steam_libraryfolders_finds_non_c_drive_libraries():
    content = r'''
"libraryfolders"
{
    "0"
    {
        "path"        "C:\\Program Files (x86)\\Steam"
    }
    "1"
    {
        "path"        "E:\\SteamLibrary"
    }
    "2"        "F:\\Games\\Steam"
}
'''

    folders = parse_steam_libraryfolders(content)

    assert r"E:\SteamLibrary" in folders
    assert r"F:\Games\Steam" in folders


def test_hidden_text_subprocess_replaces_undecodable_output(monkeypatch):
    class Result:
        returncode = 0
        stdout = "tool output with replacement"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr("rag_builder_common.subprocess.run", fake_run)

    result = run_hidden_text_subprocess(["tool.exe"])

    assert result.stdout == "tool output with replacement"
    assert captured["errors"] == "replace"
    assert captured["text"] is True


def test_build_failure_diagnostics_explain_common_failures():
    diagnostics = get_build_failure_diagnostics(
        "WRP Binarize verification failed. Binarize produced a suspiciously small WRP",
        ["Cannot include file cfgNavmesh.hpp", "Binarize access violation 0xC0000005"],
    )
    titles = [item["title"] for item in diagnostics]

    assert "Config include could not be resolved" in titles
    assert "Terrain WRP was not built correctly" in titles
    assert "Binarize crashed with an access violation" in titles


def test_suspicious_tiny_binarized_wrp_fails_build(tmp_path):
    staging = tmp_path / "staging"
    binarized = tmp_path / "binarized"
    staging.mkdir()
    binarized.mkdir()
    (staging / "map.wrp").write_bytes(b"S" * (2 * 1024 * 1024))
    (binarized / "map.wrp").write_bytes(b"B" * 128 * 1024)

    logs = []

    try:
        validate_binarized_wrp_outputs(str(staging), str(binarized), logs.append, [])
    except BuildError as error:
        assert "suspiciously small WRP" in str(error)
        assert "source WRP will not be packed as a fallback" in str(error)
        assert "Binarize addon folders" in str(error)
    else:
        raise AssertionError("Expected suspicious WRP output to fail build")

    assert (binarized / "map.wrp").exists()


def test_missing_binarized_wrp_fails_build(tmp_path):
    staging = tmp_path / "staging"
    binarized = tmp_path / "binarized"
    staging.mkdir()
    binarized.mkdir()
    (staging / "map.wrp").write_bytes(b"S" * (2 * 1024 * 1024))

    try:
        validate_binarized_wrp_outputs(str(staging), str(binarized), lambda _message: None, [])
    except BuildError as error:
        assert "did not output required WRP" in str(error)
    else:
        raise AssertionError("Expected missing Binarize WRP output to fail build")


def test_valid_binarized_wrp_passes_verification(tmp_path):
    staging = tmp_path / "staging"
    binarized = tmp_path / "binarized"
    staging.mkdir()
    binarized.mkdir()
    (staging / "map.wrp").write_bytes(b"S" * (2 * 1024 * 1024))
    (binarized / "map.wrp").write_bytes(b"B" * (1536 * 1024))

    logs = []
    checked = validate_binarized_wrp_outputs(str(staging), str(binarized), logs.append, [])

    assert checked == 1
    assert "WRP Binarize verification OK" in "\n".join(logs)


def test_build_all_packs_selected_addon_without_touching_real_cache(tmp_path, monkeypatch):
    source = tmp_path / "project"
    addon = source / "AddonA"
    data = addon / "data"
    data.mkdir(parents=True)
    (addon / "$PBOPREFIX$").write_text("AddonA", encoding="utf-8")
    (addon / "config.cpp").write_text("class CfgPatches { class AddonA { requiredAddons[] = {}; }; };", encoding="utf-8")
    (data / "script.c").write_text("class Smoke {};", encoding="utf-8")
    (data / "notes.txt").write_text("excluded", encoding="utf-8")

    saved_caches = []
    monkeypatch.setattr("rag_build_pipeline.load_build_cache", lambda: {})
    monkeypatch.setattr("rag_build_pipeline.save_build_cache", lambda cache: saved_caches.append(cache.copy()))

    logs = []
    progress = []
    output = tmp_path / "out"
    settings = {
        "source_root": str(source),
        "output_root_dir": str(output),
        "temp_dir": str(tmp_path / "temp"),
        "use_binarize": False,
        "convert_config": False,
        "sign_pbos": False,
        "update_paa_from_sources": False,
        "binarize_exe": "",
        "cfgconvert_exe": "",
        "imagetopaa_exe": "",
        "dssignfile_exe": "",
        "private_key": "",
        "exclude_patterns": "*.txt,*.cpp",
        "project_root": str(source),
        "pbo_name": "",
        "max_processes": 1,
        "selected_addons": ["AddonA"],
        "force_rebuild": True,
        "preflight_before_build": False,
        "log_file": str(tmp_path / "build.log"),
    }

    summary = build_all(settings, logs.append, lambda current, total: progress.append((current, total)))

    assert summary["built"] == 1
    assert summary["failed"] == 0
    assert saved_caches

    pbo = output / "Addons" / "AddonA.pbo"
    archive = read_pbo_archive(str(pbo))
    names = {entry.name.lower() for entry in archive["entries"]}

    assert "config.cpp" in names
    assert "data\\script.c" in names
    assert "data\\notes.txt" not in names


def test_build_all_packs_nested_addon_with_project_relative_prefix(tmp_path, monkeypatch):
    source = tmp_path / "project"
    addon = source / "rag_baseitems" / "rag_baseitems_scripts"
    scripts = addon / "4_World" / "rag_baseitems_scripts"
    scripts.mkdir(parents=True)
    (addon / "config.cpp").write_text("class CfgPatches { class rag_baseitems_scripts { requiredAddons[] = {}; }; };", encoding="utf-8")
    (scripts / "base.c").write_text("class rag_baseitems_container_base {};", encoding="utf-8")

    monkeypatch.setattr("rag_build_pipeline.load_build_cache", lambda: {})
    monkeypatch.setattr("rag_build_pipeline.save_build_cache", lambda _cache: None)

    logs = []
    output = tmp_path / "out"
    settings = {
        "source_root": str(source / "rag_baseitems"),
        "output_root_dir": str(output),
        "temp_dir": str(tmp_path / "temp"),
        "use_binarize": False,
        "convert_config": False,
        "sign_pbos": False,
        "update_paa_from_sources": False,
        "binarize_exe": "",
        "cfgconvert_exe": "",
        "imagetopaa_exe": "",
        "dssignfile_exe": "",
        "private_key": "",
        "exclude_patterns": "",
        "project_root": str(source),
        "pbo_name": "",
        "max_processes": 1,
        "selected_addons": ["rag_baseitems_scripts"],
        "force_rebuild": True,
        "preflight_before_build": False,
        "log_file": str(tmp_path / "build.log"),
    }

    summary = build_all(settings, logs.append, lambda _current, _total: None)

    assert summary["built"] == 1
    pbo = output / "Addons" / "rag_baseitems_scripts.pbo"
    archive = read_pbo_archive(str(pbo))

    assert archive["properties"].get("prefix") == r"rag_baseitems\rag_baseitems_scripts"
    assert read_pbo_entry_data(str(pbo), "$PBOPREFIX$").decode("ascii") == "rag_baseitems\\rag_baseitems_scripts\r\n"


def test_failed_build_cleans_output_work_folder(tmp_path, monkeypatch):
    source = tmp_path / "project"
    addon = source / "BrokenAddon"
    output = tmp_path / "out"
    addon.mkdir(parents=True)
    (addon / "config.cpp").write_text("class CfgPatches { class BrokenAddon { requiredAddons[] = {}; }; };", encoding="utf-8")

    monkeypatch.setattr("rag_build_pipeline.load_build_cache", lambda: {})
    monkeypatch.setattr("rag_build_pipeline.save_build_cache", lambda _cache: None)

    def fail_pack(*_args, **_kwargs):
        raise BuildError("simulated pack failure")

    monkeypatch.setattr("rag_build_pipeline.pack_pbo", fail_pack)

    settings = {
        "source_root": str(source),
        "output_root_dir": str(output),
        "temp_dir": str(tmp_path / "temp"),
        "use_binarize": False,
        "convert_config": False,
        "sign_pbos": False,
        "update_paa_from_sources": False,
        "binarize_exe": "",
        "cfgconvert_exe": "",
        "imagetopaa_exe": "",
        "dssignfile_exe": "",
        "private_key": "",
        "exclude_patterns": "",
        "project_root": str(source),
        "pbo_name": "",
        "max_processes": 1,
        "selected_addons": ["BrokenAddon"],
        "force_rebuild": True,
        "preflight_before_build": False,
        "log_file": str(tmp_path / "build.log"),
    }

    try:
        build_all(settings, lambda _message: None, lambda _current, _total: None)
    except BuildError as error:
        assert "simulated pack failure" in str(error)
    else:
        raise AssertionError("Expected simulated pack failure")

    assert not (output / "Addons" / "_rag_build_tmp").exists()
