import struct

from pbo_core import PBO_COMPRESSED_METHOD, read_pbo_archive, read_pbo_entry_payload


def make_cstring(value):
    return value.encode("ascii") + b"\x00"


def make_literal_lzss_payload(data):
    output = bytearray()
    index = 0

    while index < len(data):
        chunk = data[index:index + 8]
        output.append((1 << len(chunk)) - 1)
        output.extend(chunk)
        index += len(chunk)

    output.extend(struct.pack("<I", sum(data) & 0xFFFFFFFF))
    return bytes(output)


def test_read_compressed_pbo_entry_payload(tmp_path):
    original = b"ambient[]={1,1,1,1};\ntexture=\"data\\test_co.paa\";\n"
    compressed = make_literal_lzss_payload(original)
    pbo = tmp_path / "compressed.pbo"

    header = bytearray()
    header.extend(make_cstring("data\\material.rvmat"))
    header.extend(struct.pack("<IIIII", PBO_COMPRESSED_METHOD, len(original), 0, 0, len(compressed)))
    header.extend(make_cstring(""))
    header.extend(struct.pack("<IIIII", 0, 0, 0, 0, 0))
    header.extend(compressed)

    pbo.write_bytes(bytes(header))

    archive = read_pbo_archive(str(pbo))

    assert len(archive["entries"]) == 1

    with pbo.open("rb") as source:
        payload = read_pbo_entry_payload(source, archive["entries"][0])

    assert payload == original
