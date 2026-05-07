# Changelog

## 0.7.0 Beta

- Split PBO inspection/extraction into standalone `RaG_PBO_Inspector.exe`.
- Added shared `pbo_core.py` for PBO parsing and safe extraction.
- Added PBO header parsing with prefix, file count, size, timestamp, and packing method display.
- Added selected-file and full-archive extraction for stored/uncompressed PBO entries.
- Added safe extraction path checks to prevent absolute paths and parent-folder traversal.
- Removed inspector UI from the builder so `RaG_PBO_Builder.exe` stays focused on builds.
- Added a separate inspector PyInstaller build script.

## 0.6.10 Beta

- Improved config `#include` resolution across local, addon, prefix, and project-root paths.
- Improved preflight detection for included `CfgMods`, `CfgWorlds`, terrain `worldName`, and terrain road shape references.
- Made config collection respect excluded folders during preflight and post-conversion verification.
- Fixed temp cleanup log wording and small UI text issues.

## 0.6.9 Beta

- Added named Project Source and Build Output path presets.
- Added Preflight v2 with configurable DayZ-focused checks.
- Added terrain/WRP mapper checks and automatic preflight report export.
