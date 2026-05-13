import struct

from pbo_core import PboArchiveEntry
from rag_inspector_extract import (
    is_cfgconvert_candidate_bin_path,
    is_rap_text_convert_candidate_path,
    is_rapified_data,
    is_texheaders_bin_path,
)
from rag_inspector_p3d import build_p3d_info_report, get_p3d_metadata
from rag_inspector_viewer import decode_text_data, get_entry_path_parts, get_syntax_mode, is_text_viewable_entry


def make_entry(name, size=0):
    return PboArchiveEntry(name, 0, size, 0, 0, size)


def test_inspector_extract_path_helpers():
    assert is_texheaders_bin_path("data/texHeaders.bin")
    assert not is_cfgconvert_candidate_bin_path("data/texHeaders.bin")
    assert is_cfgconvert_candidate_bin_path("config.bin")
    assert is_rap_text_convert_candidate_path("data/material.rvmat")
    assert is_rapified_data(b"\x00raP\x00\x00\x00")


def test_inspector_viewer_helpers():
    content, encoding = decode_text_data("class Test {};".encode("utf-16"))

    assert content == "class Test {};"
    assert encoding == "utf-16"
    assert get_entry_path_parts("scripts\\4_World/file.c") == ["scripts", "4_World", "file.c"]
    assert get_syntax_mode("config.cpp") == "c_like"
    assert is_text_viewable_entry("material.rvmat")


def test_p3d_metadata_report_for_odol_lods_and_related_model_cfg():
    p3d_entry = make_entry("models/cabin/rag_hunting_cabin.p3d", 128)
    model_cfg_entry = make_entry("models/cabin/model.cfg")
    lod_count = 3
    data = (
        b"ODOL"
        + struct.pack("<I", 73)
        + struct.pack("<I", lod_count)
        + struct.pack("<f", 0.0)
        + struct.pack("<I", 0x551184E7)
        + struct.pack("<I", 0x58635FA9)
        + b"\x00rag\\cabin\\data\\wall_co.paa\x00rag\\cabin\\data\\wall.rvmat\x00proxy\\door.p3d\x00"
    )

    metadata = get_p3d_metadata(p3d_entry, [p3d_entry, model_cfg_entry], data)
    report = build_p3d_info_report(p3d_entry, metadata)

    assert metadata["format_label"] == "ODOL (binarized P3D)"
    assert metadata["version"] == 73
    assert metadata["related_model_cfg"] == ["models/cabin/model.cfg"]
    assert metadata["categorized_lods"]["Visual / resolution LODs"][0]["label"] == "Resolution 0"
    assert metadata["categorized_lods"]["Geometry / collision LODs"][0]["label"] == "Geometry"
    assert metadata["categorized_lods"]["Memory LODs"][0]["label"] == "Memory"
    assert "does not extract, recover, reconstruct, or debinarize model.cfg" in report
    assert "separate files already present in the PBO" in report
    assert "rag\\cabin\\data\\wall_co.paa" in report
    assert "proxy\\door.p3d" in report
