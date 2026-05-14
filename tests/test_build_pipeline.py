from pbo_core import read_pbo_archive
from rag_build_pipeline import build_all, detect_addon_targets, get_effective_pbo_prefix


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
