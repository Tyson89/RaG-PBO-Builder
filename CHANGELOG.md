# Changelog

## 0.7.0 Beta

- Added a separate PBO Inspector / Extractor window.
- Added PBO header parsing with prefix, file count, size, timestamp, and packing method display.
- Added selected-file and full-archive extraction for stored/uncompressed PBO entries.
- Added safe extraction path checks to prevent absolute paths and parent-folder traversal.
- Updated README documentation for the inspector/extractor.

## 0.6.10 Beta

- Improved config `#include` resolution across local, addon, prefix, and project-root paths.
- Improved preflight detection for included `CfgMods`, `CfgWorlds`, terrain `worldName`, and terrain road shape references.
- Made config collection respect excluded folders during preflight and post-conversion verification.
- Fixed temp cleanup log wording and small UI text issues.

## 0.6.9 Beta

- Added named Project Source and Build Output path presets.
- Added Preflight v2 with configurable DayZ-focused checks.
- Added terrain/WRP mapper checks and automatic preflight report export.
