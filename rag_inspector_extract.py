import os
import subprocess
from pathlib import Path

TEXHEADERS_BIN_NAME = "texheaders.bin"
RAP_TEXT_CONVERT_EXTENSIONS = {
    ".bisurf",
    ".mat",
    ".rvmat",
    ".surface",
}

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

    return str(destination_file)
