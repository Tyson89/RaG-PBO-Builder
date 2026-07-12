import argparse
import json
import os
import sys
from pathlib import Path

from rag_build_pipeline import build_all, detect_addon_targets, get_default_max_processes
from rag_builder_common import BuildError, parse_exclude_patterns
from rag_builder_storage import create_build_log_path, load_saved_settings
from rag_preflight import run_preflight_for_targets
from rag_version import APP_VERSION


DEFAULT_EXCLUDE_PATTERNS = "*.h,*.hpp,*.png,*.cpp,*.txt,thumbs.db,*.dep,*.bak,*.log,*.pew,source,*.tga,*.bat,*.psd,*.cmd,*.mcr,*.fbx,*.max"


def add_boolean_override(parser, name, destination, help_text):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--{name}", dest=destination, action="store_true", help=help_text)
    group.add_argument(f"--no-{name}", dest=destination, action="store_false", help=f"Disable {help_text.lower()}")
    parser.set_defaults(**{destination: None})


def build_parser():
    parser = argparse.ArgumentParser(prog="RaG_PBO_Builder", description="Build and preflight DayZ PBO addons without opening the GUI.")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    commands = parser.add_subparsers(dest="command", required=True)

    def add_common(command):
        command.add_argument("--source", help="Project Source folder. Defaults to saved GUI setting.")
        command.add_argument("--addons", nargs="+", metavar="NAME", help="Addon names to process. Defaults to saved selection, then all detected addons.")
        command.add_argument("--project-root", help="Project root, usually P:. Defaults to saved GUI setting.")
        command.add_argument("--temp", dest="temp_dir", help="Temporary build folder. Defaults to saved GUI setting.")
        command.add_argument("--cfgconvert", dest="cfgconvert_exe", help="Path to CfgConvert.exe.")
        command.add_argument("--exclude", dest="exclude_patterns", help="Comma-separated exclude patterns.")
        command.add_argument("--log-file", help="Log path. Defaults to Builder log folder.")
        command.add_argument("--json", action="store_true", help="Write final result as JSON; send normal log lines to stderr.")

    preflight = commands.add_parser("preflight", help="Run preflight checks.")
    add_common(preflight)

    build = commands.add_parser("build", help="Build selected PBO addons.")
    add_common(build)
    build.add_argument("--output", help="Build Output folder. Defaults to saved GUI setting.")
    build.add_argument("--pbo-name", help="PBO filename override for a single selected addon.")
    build.add_argument("--binarize-exe", help="Path to binarize.exe.")
    build.add_argument("--imagetopaa-exe", help="Path to ImageToPAA.exe.")
    build.add_argument("--dssignfile-exe", help="Path to DSSignFile.exe.")
    build.add_argument("--private-key", help="Path to .biprivatekey file.")
    build.add_argument("--max-processes", type=int, help="Maximum Binarize worker processes.")
    add_boolean_override(build, "force", "force_rebuild", "Force rebuild")
    add_boolean_override(build, "binarize", "use_binarize", "Run Binarize")
    add_boolean_override(build, "convert-config", "convert_config", "Convert config.cpp to config.bin")
    add_boolean_override(build, "update-paa", "update_paa_from_sources", "Update stale PAA files")
    add_boolean_override(build, "sign", "sign_pbos", "Sign built PBOs")
    add_boolean_override(build, "preflight", "preflight_before_build", "Run preflight before building")
    return parser


def get_value(args, saved, argument, setting=None, default=None):
    value = getattr(args, argument, None)
    if value is not None:
        return value
    return saved.get(setting or argument, default)


def select_targets(source_root, output_root, exclude_patterns, requested_names, saved_names):
    if not source_root:
        raise BuildError("Project Source is required. Use --source or save it in the GUI.")
    if not os.path.isdir(source_root):
        raise BuildError(f"Project Source does not exist: {source_root}")
    output_addons = os.path.join(output_root, "Addons") if output_root else ""
    targets = detect_addon_targets(source_root, output_addons, parse_exclude_patterns(exclude_patterns))
    available = {name: path for name, path in targets}
    names = requested_names or [name for name in saved_names if name in available] or list(available)
    missing = [name for name in names if name not in available]
    if missing:
        raise BuildError("Unknown addon target(s): " + ", ".join(missing))
    if not names:
        raise BuildError("No addon targets found.")
    return [(name, available[name]) for name in names]


def validate_tool(path, label, enabled):
    if not enabled:
        return
    if not path:
        raise BuildError(f"{label} is required. Configure it in the GUI or pass its CLI option.")
    if not os.path.isfile(path):
        raise BuildError(f"{label} does not exist: {path}")


def make_settings(args, saved):
    settings = dict(saved)
    settings["source_root"] = get_value(args, saved, "source", "source_root", "")
    settings["output_root_dir"] = get_value(args, saved, "output", "output_root", "")
    settings["project_root"] = get_value(args, saved, "project_root", default="P:") or "P:"
    settings["temp_dir"] = get_value(args, saved, "temp_dir", default="P:/Temp") or "P:/Temp"
    settings["cfgconvert_exe"] = get_value(args, saved, "cfgconvert_exe", default="")
    settings["exclude_patterns"] = get_value(args, saved, "exclude_patterns", default=DEFAULT_EXCLUDE_PATTERNS)
    settings["log_file"] = args.log_file or str(create_build_log_path())

    if args.command == "build":
        boolean_defaults = {
            "use_binarize": True,
            "convert_config": True,
            "update_paa_from_sources": False,
            "sign_pbos": True,
            "preflight_before_build": False,
            "force_rebuild": False,
        }
        for name, default in boolean_defaults.items():
            value = getattr(args, name)
            settings[name] = bool(saved.get(name, default) if value is None else value)
        settings["pbo_name"] = get_value(args, saved, "pbo_name", default="")
        settings["binarize_exe"] = get_value(args, saved, "binarize_exe", default="")
        settings["imagetopaa_exe"] = get_value(args, saved, "imagetopaa_exe", default="")
        settings["dssignfile_exe"] = get_value(args, saved, "dssignfile_exe", default="")
        settings["private_key"] = get_value(args, saved, "private_key", default="")
        settings["max_processes"] = max(1, get_value(args, saved, "max_processes", default=get_default_max_processes()))
    return settings


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    saved = load_saved_settings()
    log_file = None

    def emit(message):
        text = str(message)
        print(text, file=sys.stderr if args.json else sys.stdout, flush=True)
        if log_file:
            log_file.write(text + "\n")
            log_file.flush()

    try:
        settings = make_settings(args, saved)
        log_path = Path(settings["log_file"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", encoding="utf-8")
        targets = select_targets(
            settings["source_root"],
            settings.get("output_root_dir", ""),
            settings["exclude_patterns"],
            args.addons,
            saved.get("selected_addons", []),
        )
        settings["selected_addons"] = [name for name, _path in targets]
        emit(f"Log file: {log_path}")

        if args.command == "preflight":
            result = run_preflight_for_targets(settings, targets, emit, lambda current, total: None)
            payload = {"command": "preflight", "errors": result.errors, "warnings": result.warnings, "checked_files": result.checked_files, "log_file": str(log_path)}
            exit_code = 2 if result.errors else 0
        else:
            if not settings["output_root_dir"]:
                raise BuildError("Build Output is required. Use --output or save it in the GUI.")
            if settings.get("pbo_name") and len(targets) > 1:
                raise BuildError("PBO Name override can only be used with one addon.")
            validate_tool(settings.get("binarize_exe", ""), "binarize.exe", settings["use_binarize"])
            validate_tool(settings.get("cfgconvert_exe", ""), "CfgConvert.exe", settings["convert_config"])
            validate_tool(settings.get("imagetopaa_exe", ""), "ImageToPAA.exe", settings["update_paa_from_sources"])
            validate_tool(settings.get("dssignfile_exe", ""), "DSSignFile.exe", settings["sign_pbos"])
            validate_tool(settings.get("private_key", ""), ".biprivatekey", settings["sign_pbos"])
            summary = build_all(settings, emit, lambda current, total: None)
            payload = {"command": "build", **summary}
            exit_code = 1 if summary.get("failed") else 0

        if args.json:
            print(json.dumps(payload, indent=2), flush=True)
        return exit_code
    except BuildError as exc:
        payload = {"status": "error", "error": str(exc)}
        emit(f"ERROR: {exc}")
        if args.json:
            print(json.dumps(payload), flush=True)
        return 2
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
        emit(f"ERROR: {exc}")
        if args.json:
            print(json.dumps(payload), flush=True)
        return 1
    finally:
        if log_file:
            log_file.close()
