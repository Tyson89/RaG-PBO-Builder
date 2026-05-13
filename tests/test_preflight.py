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
