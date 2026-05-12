from rag_config_tools import find_class_body, parse_array_values, strip_cpp_comments


def test_strip_cpp_comments_ignores_broken_commented_config():
    content = """
class CfgPatches
{
    class RealAddon
    {
        requiredAddons[] = {"DZ_Data"};
    };
};

// class BrokenLine { requiredAddons[] = {"bad";
/*
class BrokenBlock
{
    requiredAddons[] = {"bad";
};
*/
class CfgMods
{
    class RealMod {};
};
"""

    clean = strip_cpp_comments(content, preserve_lines=True)

    assert "BrokenLine" not in clean
    assert "BrokenBlock" not in clean
    assert find_class_body(clean, "CfgPatches")
    assert parse_array_values(clean, "requiredAddons") == ["DZ_Data"]
