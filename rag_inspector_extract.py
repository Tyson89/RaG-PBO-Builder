import os
import re
import subprocess
from pathlib import Path

TEXHEADERS_BIN_NAME = "texheaders.bin"
RAP_TEXT_CONVERT_EXTENSIONS = {
    ".bisurf",
    ".mat",
    ".rvmat",
    ".surface",
}
TEXT_DECODINGS = ("utf-8-sig", "utf-8", "cp1250", "cp1252", "latin-1")
NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+\.\d+|\.\d+)(?![\w.])")
HEALTHLEVEL_NUMBER_LINE_RE = re.compile(r"^(\s*)([-+]?(?:\d+(?:\.\d+)?|\.\d+))(\s*,?\s*)$")

def is_texheaders_bin_path(path):
    return Path(str(path).replace("\\", "/")).name.lower() == TEXHEADERS_BIN_NAME


def is_cfgconvert_candidate_bin_path(path):
    file_path = Path(str(path).replace("\\", "/"))
    return file_path.suffix.lower() == ".bin" and file_path.name.lower() != TEXHEADERS_BIN_NAME


def is_rapified_data(data):
    return data[:8].find(b"raP") in {0, 1}


def is_rap_text_convert_candidate_path(path):
    file_path = Path(str(path).replace("\\", "/"))
    return file_path.suffix.lower() in RAP_TEXT_CONVERT_EXTENSIONS


def get_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def normalize_cfgconvert_number_token(match):
    token = match.group(0)

    try:
        value = float(token)
    except Exception:
        return token

    decimals = token.split(".", 1)[1]

    if len(decimals) < 6:
        return token

    for places in range(0, 5):
        rounded = round(value, places)

        if abs(value - rounded) <= 0.0000002:
            text = f"{rounded:.{places}f}" if places else str(int(rounded))
            return text.rstrip("0").rstrip(".") if "." in text else text

    return token


def normalize_cfgconvert_line_endings(content):
    content = content.replace("\r\r\n", "\n")
    content = content.replace("\r\n", "\n")
    content = content.replace("\r", "\n")
    return content.replace("\n", "\r\n")


def normalize_cfgconvert_blank_lines(content):
    lines = content.split("\r\n")
    trailing_newline = lines[-1] == ""

    while lines and not lines[-1].strip():
        lines = lines[:-1]

    blank_count = sum(1 for line in lines if not line.strip())
    nonblank_count = len(lines) - blank_count

    if nonblank_count and blank_count >= nonblank_count * 0.75:
        lines = [line for line in lines if line.strip()]
    else:
        compacted = []
        pending_blank = False

        for line in lines:
            if line.strip():
                compacted.append(line)
                pending_blank = False
            elif not pending_blank:
                compacted.append(line)
                pending_blank = True

        lines = compacted

    content = "\r\n".join(lines)

    if trailing_newline:
        content += "\r\n"

    return content


def normalize_float_artifacts_outside_strings(content):
    result = []
    segment = []
    index = 0
    in_string = ""
    escaped = False

    def flush_segment():
        if segment:
            result.append(NUMBER_TOKEN_RE.sub(normalize_cfgconvert_number_token, "".join(segment)))
            segment.clear()

    while index < len(content):
        char = content[index]

        if in_string:
            result.append(char)

            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""

            index += 1
            continue

        if char in {'"', "'"}:
            flush_segment()
            in_string = char
            result.append(char)
            index += 1
            continue

        segment.append(char)
        index += 1

    flush_segment()
    return "".join(result)


def normalize_healthlevels_thresholds(content):
    lines = content.splitlines(keepends=True)
    output = []
    in_healthlevels = False
    depth = 0

    for line in lines:
        stripped = line.strip()

        if not in_healthlevels and re.search(r"\bhealthLevels\s*\[\s*\]\s*=", line):
            in_healthlevels = True
            depth = 0

        if in_healthlevels:
            match = HEALTHLEVEL_NUMBER_LINE_RE.match(line.rstrip("\r\n"))

            if match:
                try:
                    value = float(match.group(2))
                except Exception:
                    value = None

                if value is not None and 0.0 <= value <= 1.0:
                    newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                    line = f"{match.group(1)}{value:.2f}{match.group(3)}{newline}"

            depth += line.count("{") - line.count("}")

            if ";" in stripped and depth <= 0:
                in_healthlevels = False

        output.append(line)

    return "".join(output)


def normalize_cfgconvert_text(content):
    content = normalize_cfgconvert_line_endings(content)
    content = normalize_cfgconvert_blank_lines(content)
    content = normalize_float_artifacts_outside_strings(content)
    return normalize_healthlevels_thresholds(content)


def read_generated_text(path):
    data = Path(path).read_bytes()

    for encoding in TEXT_DECODINGS:
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue

    return data.decode("utf-8", errors="replace")


def normalize_cfgconvert_output_file(path):
    output_path = Path(path)
    content = read_generated_text(output_path)
    normalized = normalize_cfgconvert_text(content)

    if normalized != content:
        output_path.write_text(normalized, encoding="utf-8", newline="")


def convert_bin_to_cpp(cfgconvert_exe, bin_path, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise RuntimeError("CfgConvert.exe not found.")

    bin_file = Path(bin_path)

    if bin_file.suffix.lower() != ".bin":
        return ""

    if is_texheaders_bin_path(bin_file):
        raise RuntimeError("texHeaders.bin is not a config bin. Leave it as .bin.")

    cpp_path = str(bin_file.with_suffix(".cpp"))

    if os.path.isfile(cpp_path):
        os.remove(cpp_path)

    cmd = [cfgconvert_exe, "-txt", "-dst", cpp_path, str(bin_file)]
    result = subprocess.run(cmd, cwd=str(bin_file.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())

    output = result.stdout or ""

    if output:
        for line in output.splitlines():
            log(line)

    if result.returncode != 0 or not os.path.isfile(cpp_path):
        raise RuntimeError(f"CfgConvert failed for {bin_path} with exit code {result.returncode}")

    normalize_cfgconvert_output_file(cpp_path)

    return cpp_path


def convert_rap_to_text(cfgconvert_exe, source_path, destination_path, log):
    if not cfgconvert_exe or not os.path.isfile(cfgconvert_exe):
        raise RuntimeError("CfgConvert.exe not found.")

    source_file = Path(source_path)
    destination_file = Path(destination_path)

    if not source_file.is_file():
        raise RuntimeError(f"Source file does not exist: {source_path}")

    destination_file.parent.mkdir(parents=True, exist_ok=True)

    if destination_file.is_file():
        destination_file.unlink()

    cmd = [cfgconvert_exe, "-txt", "-dst", str(destination_file), str(source_file)]
    result = subprocess.run(cmd, cwd=str(source_file.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=get_subprocess_creationflags(), startupinfo=get_hidden_startupinfo())

    output = result.stdout or ""

    if output:
        for line in output.splitlines():
            log(line)

    if result.returncode != 0 or not destination_file.is_file():
        raise RuntimeError(f"CfgConvert failed for {source_path} with exit code {result.returncode}")

    normalize_cfgconvert_output_file(destination_file)

    return str(destination_file)
