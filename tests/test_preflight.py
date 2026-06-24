from pathlib import Path

from rag_preflight import run_preflight_for_targets


def write_valid_config(path):
    path.write_text(
        """
class CfgPatches
{
    class GoodAddon
    {
        units[] = {};
        weapons[] = {};
        requiredAddons[] = {};
    };
};
""",
        encoding="utf-8",
    )


def base_preflight_settings(tmp_path):
    return {
        "project_root": str(tmp_path),
        "temp_dir": str(tmp_path / "temp"),
        "exclude_patterns": "",
        "cfgconvert_exe": "",
        "log_file": str(tmp_path / "preflight.log"),
        "preflight_check_required_addons_hints": True,
        "preflight_check_texture_freshness": True,
        "preflight_check_risky_paths": True,
        "preflight_check_case_conflicts": True,
        "preflight_check_script_checks": True,
        "preflight_check_p3d_internal": True,
        "preflight_check_terrain_cfgworlds": True,
        "preflight_check_terrain_navmesh": False,
        "preflight_check_terrain_road_shapes": True,
        "preflight_check_terrain_structure": True,
        "preflight_check_terrain_layers": True,
        "preflight_check_terrain_2d_map": False,
        "preflight_check_terrain_size": True,
        "preflight_check_wrp_internal": False,
    }


def test_preflight_only_checks_selected_target(tmp_path):
    good = tmp_path / "GoodAddon"
    bad = tmp_path / "BadAddon"
    good.mkdir()
    bad.mkdir()
    write_valid_config(good / "config.cpp")
    (bad / "config.cpp").write_text("class NotCfgPatches {};", encoding="utf-8")

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("GoodAddon", str(good))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "BadAddon" not in joined_logs
    assert Path(result.report_txt).is_file()
    assert Path(result.report_json).is_file()


def test_preflight_accepts_project_relative_terrain_worldname(tmp_path):
    addon = tmp_path / "outpost" / "world"
    addon.mkdir(parents=True)
    (addon / "outpost.wrp").write_bytes(b"wrp")
    (addon / "config.cpp").write_text(
        r"""
class CfgPatches
{
    class outpost_world
    {
        units[] = {};
        weapons[] = {};
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
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("world", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "terrain worldName implies prefix 'outpost\\world'" in joined_logs
    assert "worldName path does not match the effective PBO prefix" not in joined_logs
    assert "WRP file(s) are present but not referenced by worldName" not in joined_logs


def test_preflight_warns_for_modded_class_with_explicit_base(tmp_path):
    addon = tmp_path / "ScriptAddon"
    addon.mkdir()
    write_valid_config(addon / "config.cpp")
    (addon / "scripts.c").write_text(
        """
modded class Good_Base
{
};

modded class Container_Base extends ItemBase
{
};

modded class Barrel_ColorBase : Container_Base
{
};

// modded class Commented_Line extends ItemBase
/*
modded class Commented_Block : ItemBase
*/
""",
        encoding="utf-8",
    )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("ScriptAddon", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert joined_logs.count("Modded class should not declare a base class") == 2
    assert "modded class Container_Base extends ItemBase" in joined_logs
    assert "modded class Barrel_ColorBase : Container_Base" in joined_logs
    assert "Commented_Line" not in joined_logs
    assert "Commented_Block" not in joined_logs


def test_preflight_warns_for_odol_p3d(tmp_path):
    addon = tmp_path / "ModelAddon"
    addon.mkdir()
    write_valid_config(addon / "config.cpp")
    (addon / "packed_model.p3d").write_bytes(b"ODOL already binarized")

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("ModelAddon", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "P3D is already binarized ODOL" in joined_logs
    assert "packed_model.p3d" in joined_logs


def test_preflight_does_not_treat_excluded_config_includes_as_runtime_references(tmp_path):
    addon = tmp_path / "TerrainConfig"
    addon.mkdir()
    (addon / "config.cpp").write_text(
        """
class CfgPatches
{
    class TerrainConfig
    {
        units[] = {};
        weapons[] = {};
        requiredAddons[] = {};
    };
};
class CfgWorlds
{
    #include "cfgLightingNew.hpp"
    // #include "commented.hpp"
};
""",
        encoding="utf-8",
    )
    (addon / "cfgLightingNew.hpp").write_text("class Lighting {};\n", encoding="utf-8")
    (addon / "commented.hpp").write_text("class Commented {};\n", encoding="utf-8")
    settings = base_preflight_settings(tmp_path)
    settings["exclude_patterns"] = "*.hpp"

    logs = []
    result = run_preflight_for_targets(
        settings,
        [("TerrainConfig", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "Referenced file exists but is excluded" not in joined_logs
    assert "cfgLightingNew.hpp" not in joined_logs
    assert "commented.hpp" not in joined_logs


def test_preflight_warns_for_script_duplicates_setactions_and_syntax(tmp_path):
    addon = tmp_path / "ScriptChecks"
    addon.mkdir()
    write_valid_config(addon / "config.cpp")
    (addon / "scripts_one.c").write_text(
        """
class DuplicateThing
{
};

class MissingSuperActions
{
    override void SetActions()
    {
        AddAction(ActionOpen);
    }
};

// class CommentedDuplicateThing {};
""",
        encoding="utf-8",
    )
    (addon / "scripts_two.c").write_text(
        """
class DuplicateThing
{
};

class GoodActions
{
    override void SetActions()
    {
        super.SetActions();
        AddAction(ActionClose);
    }
};
""",
        encoding="utf-8",
    )
    (addon / "broken_script.c").write_text(
        """
class BrokenScript
{
    void Broken()
    {
        if (true)
        {
            Print("still open");
""",
        encoding="utf-8",
    )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("ScriptChecks", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "Duplicate script class definition in ScriptChecks: class DuplicateThing" in joined_logs
    assert "CommentedDuplicateThing" not in joined_logs
    assert "SetActions() does not call super.SetActions()" in joined_logs
    assert "class MissingSuperActions" in joined_logs
    assert "class GoodActions" not in joined_logs
    assert "Unclosed '{' in script file" in joined_logs


def test_preflight_can_disable_script_checks(tmp_path):
    addon = tmp_path / "ScriptChecksOff"
    addon.mkdir()
    write_valid_config(addon / "config.cpp")
    (addon / "scripts_one.c").write_text(
        """
class DuplicateThing
{
    override void SetActions()
    {
        AddAction(ActionOpen);
    }
};

modded class Container_Base extends ItemBase
{
};
""",
        encoding="utf-8",
    )
    (addon / "scripts_two.c").write_text(
        """
class DuplicateThing
{
    void Broken()
    {
        if (true)
        {
""",
        encoding="utf-8",
    )
    settings = base_preflight_settings(tmp_path)
    settings["preflight_check_script_checks"] = False

    logs = []
    result = run_preflight_for_targets(
        settings,
        [("ScriptChecksOff", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "Script checks disabled." in joined_logs
    assert "Duplicate script class definition" not in joined_logs
    assert "SetActions() does not call super.SetActions()" not in joined_logs
    assert "Modded class should not declare a base class" not in joined_logs
    assert "Unclosed '{' in script file" not in joined_logs


def test_preflight_ignores_dynamic_script_asset_suffixes(tmp_path):
    addon = tmp_path / "DynamicScriptRefs"
    addon.mkdir()
    write_valid_config(addon / "config.cpp")
    (addon / "scripts.c").write_text(
        r"""
class DynamicScriptRefs
{
    void BuildPath(string baseName)
    {
        string materialPath = baseName + "_rvmatname.rvmat";
    }
};
""",
        encoding="utf-8",
    )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("DynamicScriptRefs", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "_rvmatname.rvmat" not in joined_logs


def test_preflight_summarizes_terrain_layer_source_texture_spam(tmp_path):
    addon = tmp_path / "LayerSourceTextures"
    layers = addon / "data" / "layers"
    layers.mkdir(parents=True)
    write_valid_config(addon / "config.cpp")

    for index in range(12):
        texture_name = f"layer_{index:03d}.png"
        (layers / texture_name).write_bytes(b"png")
        (layers / f"material_{index:03d}.rvmat").write_text(
            f'texture="LayerSourceTextures\\data\\layers\\{texture_name}";\n',
            encoding="utf-8",
        )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("LayerSourceTextures", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "Terrain layer RVMATs reference source texture formats instead of .paa: 12 reference(s)" in joined_logs
    assert "Terrain layer source textures have no matching .paa: 12 file(s)" in joined_logs
    assert joined_logs.count("RVMAT references a source texture format instead of .paa") == 0
    assert joined_logs.count("Source texture exists without matching .paa: data\\layers") == 0

def test_preflight_reports_project_relative_prefix_for_nested_addon(tmp_path):
    addon = tmp_path / "rag_baseitems" / "rag_baseitems_scripts"
    addon.mkdir(parents=True)
    (addon / "config.cpp").write_text(
        """
class CfgPatches
{
    class rag_baseitems_scripts
    {
        requiredAddons[] = {};
    };
};
""",
        encoding="utf-8",
    )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("rag_baseitems_scripts", str(addon))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "project-relative path will be used as prefix: rag_baseitems\\rag_baseitems_scripts" in joined_logs


def test_preflight_warns_when_required_addons_uses_selected_pbo_name(tmp_path):
    base = tmp_path / "rag_baseitems_scripts"
    cards = tmp_path / "rag_cards"
    base.mkdir()
    cards.mkdir()
    (base / "config.cpp").write_text(
        """
class CfgPatches
{
    class RaG_BaseItems_Scripts
    {
        requiredAddons[] = {};
    };
};
""",
        encoding="utf-8",
    )
    (cards / "config.cpp").write_text(
        """
class CfgPatches
{
    class RaG_Cards
    {
        requiredAddons[] = {"rag_baseitems_scripts"};
    };
};
""",
        encoding="utf-8",
    )

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("rag_baseitems_scripts", str(base)), ("rag_cards", str(cards))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "requiredAddons[] references selected addon/PBO name 'rag_baseitems_scripts'" in joined_logs
    assert "RaG_BaseItems_Scripts" in joined_logs
    assert "CfgPatches class names, not PBO names" in joined_logs


def test_preflight_warns_when_script_uses_selected_addon_class_without_required_addon(tmp_path):
    base = tmp_path / "rag_baseitems_scripts"
    cards = tmp_path / "rag_cards"
    base_scripts = base / "scripts" / "4_World" / "rag_baseitems"
    cards_scripts = cards / "scripts" / "4_World" / "rag_cards"
    base_scripts.mkdir(parents=True)
    cards_scripts.mkdir(parents=True)
    (base / "config.cpp").write_text(
        """
class CfgPatches
{
    class RaG_BaseItems_Scripts
    {
        requiredAddons[] = {};
    };
};
class CfgMods
{
    class RaG_BaseItems_Scripts
    {
        type = "mod";
        dependencies[] = {"World"};
        class defs
        {
            class worldScriptModule
            {
                files[] = {"rag_baseitems_scripts/scripts/4_World"};
            };
        };
    };
};
""",
        encoding="utf-8",
    )
    (cards / "config.cpp").write_text(
        """
class CfgPatches
{
    class RaG_Cards
    {
        requiredAddons[] = {};
    };
};
class CfgMods
{
    class RaG_Cards
    {
        type = "mod";
        dependencies[] = {"World"};
        class defs
        {
            class worldScriptModule
            {
                files[] = {"rag_cards/scripts/4_World"};
            };
        };
    };
};
""",
        encoding="utf-8",
    )
    (base_scripts / "container.c").write_text("class rag_baseitems_container_base {};", encoding="utf-8")
    (cards_scripts / "displaycase.c").write_text("class rag_card_displaycase { rag_baseitems_container_base cargo; };", encoding="utf-8")

    logs = []
    result = run_preflight_for_targets(
        base_preflight_settings(tmp_path),
        [("rag_baseitems_scripts", str(base)), ("rag_cards", str(cards))],
        logs.append,
    )

    joined_logs = "\n".join(logs)

    assert result.errors == 0
    assert "Script class reference may need requiredAddons[]" in joined_logs
    assert "rag_cards uses class rag_baseitems_container_base" in joined_logs
    assert "RaG_BaseItems_Scripts" in joined_logs
    assert "not the PBO filename" in joined_logs
