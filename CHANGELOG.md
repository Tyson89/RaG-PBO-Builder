# Changelog

## 0.7.19 Beta

- Added a scrollable Builder Options window so all settings remain reachable on smaller screens.
- Added one master preflight Script checks toggle covering modded-class inheritance, duplicate script classes, SetActions super-call warnings, and lightweight script sanity checks.
- Improved Inspector action layout by making Extract all the primary action and increasing the Inspector log area.
- Improved P3D info safety wording and added actions for viewing/extracting loose model.cfg/model.bin entries that already exist in the PBO.
- Centralized the app version in rag_version.py and added release readiness validation for version, changelog, release zip, and checksum contents.

## 0.7.18 Beta

- Split Builder helper, preflight, and build pipeline code into dedicated modules for safer maintenance.
- Added a pytest regression suite covering PBO writing, config comment parsing, build target detection, preflight scope, compressed PBO entries, and build packing behavior.
- Added GitHub Actions CI to run tests and Python compile checks on pushes and pull requests.
- Added a preflight warning for DayZ script `modded class` declarations that incorrectly use `extends` or `:` to declare a base class.
- Rebuilt release packaging from the refactored source layout.

## 0.7.17 Beta

- Terrain `.wrp` files now trigger the Binarize step even when the addon contains no `.p3d` files.
- Binarize overlay now allows processed `.wrp` output so terrain worlds can be converted into the engine-ready format expected by DayZ.
- Post-pack WRP verification now compares packed WRP entries against the processed staging WRP and logs when Binarize changed the WRP from the original source.

## 0.7.16 Beta

- Improved config.cpp syntax error reporting so failed CfgConvert checks show the exact relative config path and the full source/staged path.
- Build-time CPP to BIN failures now identify which nested `config.cpp` failed instead of only reporting a generic CfgConvert failure.

## 0.7.15 Beta

- Fixed preflight scans reading references, `worldName`, terrain shapes, map image hints, and `#include` lines from commented-out config code.
- Fixed C-style comment handling so commented includes are not resolved into active validation content.
- Added post-pack WRP verification for terrain PBOs to confirm packed `.wrp` entries exist and match the staged source bytes.
- Added post-pack `worldName` to PBO-entry checks for terrain PBOs so obvious prefix/path mismatches are caught before publishing.

## 0.7.14 Beta

- Reduced false-positive terrain warnings for modular DayZ map PBO layouts such as separate `world`, `data`, `terrain`, `roads`, `nature`, `navmesh`, `city`, and `military` addons.
- Terrain structure checks now report classic-layout differences as info notes instead of warnings, while keeping real source/export packing risks as warnings.
- Broadened terrain layer folder detection beyond only `data\layers` and root `layers`.
- Improved modular config handling so nested `config.cpp` fragments are not treated as broken addon root configs unless they declare their own `CfgPatches`.
- Improved CfgMods selection so the preflight prefers the config that actually contains the best CfgMods/script-module definition instead of stopping on a weaker nested match.

## 0.7.13 Beta

- Fixed addon target detection for map projects so terrain source/export work folders such as `source`, `exports`, `terrainbuilder`, and `tb` are not offered as build targets.
- Addon discovery now uses the same exclude patterns as packing, keeping the visible build target list aligned with what the builder will actually pack.

## 0.7.12 Beta

- Added optional `Update PAA` build step using DayZ Tools `ImageToPAA.exe`.
- The builder can now convert missing or stale staged `.paa` files from newer `.png`/`.tga` source textures before Binarize and packing.
- Conversion writes only to the staging folder; source textures and source `.paa` files are not overwritten.
- Source `.png`/`.tga` fingerprints now participate in the build cache when `Update PAA` is enabled, so changed source textures trigger rebuilds.

## 0.7.11 Beta

- Added release packaging support with `package_release.ps1` for producing a GitHub Release zip containing both EXEs, docs, licence, changelog, and SHA256 checksums.
- Added a GitHub Actions workflow that builds the Windows release package and uploads it to tagged GitHub Releases.
- Tightened preflight scope so the broad file/reference scan ignores files excluded from the packed PBO, preventing unrelated source files from blocking selected addon builds.

## 0.7.10 Beta

- Added `raP` detection for material-style files such as `.rvmat`, `.bisurf`, `.surface`, and `.mat`.
- `View selected` now derapifies rapified material files with `CfgConvert.exe` before showing them, so PboProject-packed `.rvmat` files preview as readable text.
- Extraction now converts rapified material files back to readable text in place when the conversion option and `CfgConvert.exe` are configured.

## 0.7.9 Beta

- Added BI LZSS `Cprs` decompression for compressed PBO entries, including PboProject-packed `.rvmat` files.
- Compressed entries can now be previewed and extracted in decompressed form instead of being treated as unsupported.
- Skipped `texHeaders.bin` during `.bin` to `.cpp` conversion because it is not a config bin and CfgConvert errors on it.

## 0.7.8 Beta

- Removed the misleading generated baked config hints/model.cfg output from P3D inspection.
- P3D inspection now focuses on defensible metadata only: format/version, ODOL resolution-array LODs, resources, proxies, RTMs, and related loose config entries when present.
- P3D reports now point users to Mikero DeP3d/ExtractModelCfg for real model.cfg recovery from supported ODOL versions.

## 0.7.7 Beta

- Reworked P3D LOD reporting to use the ODOL resolution array when it can be read safely instead of categorizing random embedded strings.
- Deprecated the earlier generated `model.cfg` experiment after it proved too noisy for real ODOL files.
- This version is superseded by 0.7.8, which removes the baked config-hints UI entirely.

## 0.7.6 Beta

- P3D info now groups exposed LOD markers into practical categories such as visual, geometry/collision, view, fire/hit, memory, shadow, and crew/cargo.
- Added an experimental baked-metadata model.cfg summary for selected `.p3d` files.
- This experiment is superseded by 0.7.8 because string-scanned ODOL metadata is not reliable enough to present as recovered config.

## 0.7.5 Beta

- Added best-effort P3D metadata inspection from `View selected` for `.p3d` entries.
- P3D info reports now show format/version, expected model.cfg class, related model.cfg/model.bin entries, exposed LOD markers, model.cfg/skeleton markers, textures, materials, proxies, RTMs, and likely named selections/bones.
- P3D inspection reads metadata only and does not debinarize ODOL models.

## 0.7.4 Beta

- Replaced the flat inspector contents list with an expandable folder tree.
- Added syntax highlighting for C/config-style preview files such as `.cpp`, `.c`, `.hpp`, `.rvmat`, `.sqf`, and converted `.bin` previews.
- Folder rows can now be selected for extraction; extracting a selected folder extracts all files underneath it.
- The inspector `.bin` conversion toggle now uses the same checkmark button style as the builder options.

## 0.7.3 Beta

- Replaced the redundant primary `Inspect` button with `View selected`.
- Added `Reload PBO` for manually typed paths or refreshing a PBO after it changes on disk.
- Pressing Enter in the PBO path field now reloads the PBO.

## 0.7.2 Beta

- Added Windows drag/drop support for dropping `.pbo` files directly into `RaG_PBO_Inspector.exe`.
- Fixed the inspector drag/drop hook to avoid hard-closing the EXE when a file is dropped.
- Switched inspector drag/drop to `tkinterdnd2` / `tkdnd` instead of a hand-rolled Win32 message hook.
- Added a built-in read-only text viewer for configs, scripts, RVMATs, and other text-like PBO entries.
- Double-clicking a PBO entry now opens the text viewer when the entry can be previewed.
- Added optional extracted `.bin` to `.cpp` conversion using DayZ Tools `CfgConvert.exe`.
- Inspector now saves the selected `CfgConvert.exe` path and conversion preference.
- `pbo_core.py` now reports extracted file paths so post-extract tooling can process them.

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
