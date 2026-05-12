from pathlib import Path

from pbo_core import read_pbo_archive
from rag_pbo_writer import pack_pbo, pbo_entry_bytes_match_file, verify_packed_pbo


def test_pack_pbo_respects_excludes_but_keeps_config_cpp(tmp_path):
    source = tmp_path / "addon"
    data = source / "data"
    data.mkdir(parents=True)
    wrp = data / "world.wrp"
    wrp.write_bytes(b"WRP-BYTES-UNCHANGED")
    (data / "script.c").write_text("class Smoke {};", encoding="utf-8")
    (data / "notes.txt").write_text("excluded", encoding="utf-8")
    (data / "source.png").write_bytes(b"PNG")
    (source / "config.cpp").write_text("class CfgPatches {};", encoding="utf-8")
    (source / ".gitignore").write_text("ignored", encoding="utf-8")

    output = tmp_path / "out" / "addon.pbo"
    logs = []

    pack_pbo(str(source), str(output), "Test\\Addon", logs.append, ["*.txt", "*.png", "*.cpp"])
    verify_packed_pbo(str(output), "Test\\Addon", logs.append)

    archive = read_pbo_archive(str(output))
    names = {entry.name.lower() for entry in archive["entries"]}

    assert "config.cpp" in names
    assert "data\\script.c" in names
    assert "data\\world.wrp" in names
    assert "data\\notes.txt" not in names
    assert "data\\source.png" not in names
    assert ".gitignore" not in names

    entry = next(entry for entry in archive["entries"] if entry.name.lower() == "data\\world.wrp")
    matches, reason = pbo_entry_bytes_match_file(str(output), entry, str(wrp))

    assert matches, reason
