from pbo_core import read_pbo_archive
from rag_build_pipeline import (
    build_all,
    copy_source_to_staging,
    detect_addon_targets,
    ensure_p3d_files_in_staging,
    get_effective_pbo_prefix,
    has_binarizable_p3d_files,
    rewrite_staging_rvmat_texture_refs,
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


def test_rewrite_rvmat_retargets_refs_only_when_paa_exists(tmp_path):
    staging = tmp_path / "staging"
    data = staging / "data"
    data.mkdir(parents=True)
    (data / "wall_co.paa").write_bytes(b"paa")
    (data / "wall_nohq.paa").write_bytes(b"paa")
    # No .paa generated for the smdi map; its reference must be left as-is.
    rvmat = data / "wall.rvmat"
    rvmat.write_text(
        'class Stage1\n'
        '{\n'
        '    texture="MyMod\\data\\wall_co.png";\n'
        '};\n'
        'class Stage2\n'
        '{\n'
        '    texture="data\\wall_nohq.tga";\n'
        '};\n'
        'class Stage3\n'
        '{\n'
        '    texture="MyMod\\data\\wall_smdi.tga";\n'
        '};\n',
        encoding="utf-8",
    )

    logs = []
    rewritten = rewrite_staging_rvmat_texture_refs(str(staging), logs.append, [])

    assert rewritten == 1
    text = rvmat.read_text(encoding="utf-8")
    assert 'texture="MyMod\\data\\wall_co.paa";' in text
    assert 'texture="data\\wall_nohq.paa";' in text
    assert 'texture="MyMod\\data\\wall_smdi.tga";' in text


def test_rewrite_rvmat_skips_rapified_files(tmp_path):
    staging = tmp_path / "staging"
    data = staging / "data"
    data.mkdir(parents=True)
    (data / "wall_co.paa").write_bytes(b"paa")
    rvmat = data / "wall.rvmat"
    original = b"\x00raP" + b'texture="data\\wall_co.png";'
    rvmat.write_bytes(original)

    logs = []
    rewritten = rewrite_staging_rvmat_texture_refs(str(staging), logs.append, [])

    assert rewritten == 0
    assert rvmat.read_bytes() == original
